import os
import io
import asyncio
import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openai import AsyncOpenAI
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from database import init_db, SessionLocal
from coach import ask_coach
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
from strava import (
    STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_VERIFY_TOKEN,
    exchange_code, get_activity_detail, save_strava_token,
    get_strava_token, refresh_token_if_needed, format_activity_message
)

app = FastAPI()
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BASE_URL = os.environ.get("BASE_URL", "https://ironman-trainner-production.up.railway.app")

strava_user_map = {}
processed_activities: set = set()
scheduler = AsyncIOScheduler(timezone="Europe/Madrid")


# ─── STARTUP ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    scheduler.add_job(send_weekly_summary_all, CronTrigger(day_of_week="mon", hour=9, minute=0))
    scheduler.start()


# ─── HELPERS ──────────────────────────────────────────────────────────────────

async def send_message(chat_id: int, text: str, parse_mode: str = "Markdown"):
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=body)


async def send_photo(chat_id: int, image_bytes: bytes, caption: str = ""):
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{TELEGRAM_API}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("resumen.png", image_bytes, "image/png")}
        )


async def transcribe_voice(file_id: str) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{TELEGRAM_API}/getFile?file_id={file_id}")
        file_path = r.json()["result"]["file_path"]
        audio = await client.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}")
    transcription = await openai_client.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.ogg", audio.content, "audio/ogg"),
    )
    return transcription.text


# ─── RESUMEN / IMAGEN ─────────────────────────────────────────────────────────

async def generate_summary_image(user_id: str) -> bytes:
    from database import Training, DailyMetrics
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=30)
        trainings = db.query(Training).filter(
            Training.user_id == user_id,
            Training.date >= since
        ).order_by(Training.date.asc()).all()
        metrics = db.query(DailyMetrics).filter(
            DailyMetrics.user_id == user_id,
            DailyMetrics.date >= since
        ).order_by(DailyMetrics.date.asc()).all()
    finally:
        db.close()

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.patch.set_facecolor("#1a1a2e")
    colors = {"run": "#e94560", "bike": "#f5a623", "swim": "#4ecdc4", "gym": "#a29bfe", "tennis": "#00b894"}
    labels = {"run": "Carrera", "bike": "Bici", "swim": "Natación", "gym": "Gym", "tennis": "Tenis"}

    for ax in axes.flat:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="white")
        ax.xaxis.label.set_color("white")
        ax.yaxis.label.set_color("white")
        ax.title.set_color("white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444")

    # 1. Km por disciplina
    ax1 = axes[0, 0]
    disc_km = {}
    for t in trainings:
        d = t.discipline or "gym"
        disc_km[d] = disc_km.get(d, 0) + (t.distance_km or 0)
    if disc_km:
        bars = ax1.bar([labels.get(k, k) for k in disc_km], disc_km.values(),
                       color=[colors.get(k, "#888") for k in disc_km])
        for bar, val in zip(bars, disc_km.values()):
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                     f"{val:.1f}km", ha="center", color="white", fontsize=9)
    ax1.set_title("Kilómetros por disciplina (30 días)", fontweight="bold")
    ax1.set_ylabel("km")

    # 2. Horas por disciplina (tarta)
    ax2 = axes[0, 1]
    disc_h = {}
    for t in trainings:
        d = t.discipline or "gym"
        disc_h[d] = disc_h.get(d, 0) + (t.duration_min or 0) / 60
    if disc_h:
        ax2.pie(disc_h.values(),
                labels=[labels.get(k, k) for k in disc_h],
                colors=[colors.get(k, "#888") for k in disc_h],
                autopct="%1.0f%%", textprops={"color": "white"})
    ax2.set_title("Horas por disciplina (30 días)", fontweight="bold")

    # 3. CTL / ATL / TSB
    ax3 = axes[1, 0]
    if metrics:
        dates = [m.date for m in metrics]
        ax3.plot(dates, [m.ctl for m in metrics], color="#4ecdc4", label="CTL (forma)", linewidth=2)
        ax3.plot(dates, [m.atl for m in metrics], color="#e94560", label="ATL (fatiga)", linewidth=2)
        ax3.plot(dates, [m.tsb or 0 for m in metrics], color="#f5a623", label="TSB (frescura)", linewidth=2)
        ax3.axhline(0, color="#666", linestyle="--", linewidth=0.8)
        ax3.legend(facecolor="#1a1a2e", labelcolor="white", fontsize=8)
        fig.autofmt_xdate()
    ax3.set_title("Forma física (CTL / ATL / TSB)", fontweight="bold")

    # 4. Peso
    ax4 = axes[1, 1]
    weight_data = [(m.date, m.weight_kg) for m in metrics if m.weight_kg]
    if weight_data:
        dates_w, weights = zip(*weight_data)
        ax4.plot(dates_w, weights, color="#a29bfe", linewidth=2, marker="o", markersize=4)
        ax4.set_ylabel("kg")
        fig.autofmt_xdate()
    else:
        ax4.text(0.5, 0.5, "Sin datos de peso", ha="center", va="center",
                 color="white", transform=ax4.transAxes)
    ax4.set_title("Evolución del peso (kg)", fontweight="bold")

    plt.suptitle("📊 Resumen — Marcos Ironman 70.3", color="white", fontsize=14, fontweight="bold", y=1.01)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ─── RESUMEN SEMANAL AUTOMÁTICO ───────────────────────────────────────────────

async def send_weekly_summary_all():
    """Envía el resumen semanal a todos los usuarios activos (lunes 9h)."""
    from database import Training
    db = SessionLocal()
    try:
        since = datetime.utcnow() - timedelta(days=7)
        rows = db.query(Training.user_id).filter(Training.date >= since).distinct().all()
        user_ids = [r[0] for r in rows]
    finally:
        db.close()

    for user_id in user_ids:
        try:
            image = await generate_summary_image(user_id)
            await send_photo(int(user_id), image, "📊 Resumen semanal — ¡Buena semana, Marcos!")
        except Exception as e:
            print(f"Error resumen semanal para {user_id}: {e}")


# ─── ALERTA SOBREENTRENAMIENTO ────────────────────────────────────────────────

async def check_overtraining(user_id: str, chat_id: int):
    from database import DailyMetrics
    db = SessionLocal()
    try:
        latest = db.query(DailyMetrics).filter(
            DailyMetrics.user_id == user_id
        ).order_by(DailyMetrics.date.desc()).first()
    finally:
        db.close()

    if latest and (latest.tsb or 0) < -20:
        await send_message(
            chat_id,
            f"⚠️ *Alerta de sobreentrenamiento*\nTu TSB está en {latest.tsb:.1f}. "
            f"Estás acumulando demasiada fatiga. Considera un día de recuperación o sesión muy suave mañana.",
        )


# ─── DETECCIÓN BRICK ──────────────────────────────────────────────────────────

def check_brick(user_id: str) -> bool:
    """Devuelve True si hoy ya hay bici Y carrera registradas (brick)."""
    from database import Training
    db = SessionLocal()
    try:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        disciplines_today = {
            t.discipline for t in db.query(Training).filter(
                Training.user_id == user_id,
                Training.date >= today
            ).all()
        }
        return "bike" in disciplines_today and "run" in disciplines_today
    finally:
        db.close()


# ─── TELEGRAM WEBHOOK ────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_id = str(message.get("from", {}).get("id", chat_id))
    text = message.get("text", "")
    voice = message.get("voice") or message.get("audio")

    if not chat_id:
        return {"ok": True}

    if not text and voice:
        try:
            text = await transcribe_voice(voice["file_id"])
        except Exception as e:
            print(f"Error transcribiendo audio: {e}")
            await send_message(chat_id, "No pude entender el audio. Inténtalo de nuevo.", parse_mode=None)
            return {"ok": True}

    if not text:
        return {"ok": True}

    # ── Comandos ──────────────────────────────────────────────────────────────

    if text == "/ayuda":
        ayuda = (
            "🤖 *Comandos disponibles:*\n\n"
            "/start — Saludo inicial\n"
            "/strava — Conectar tu cuenta de Strava\n"
            "/stats — Ver tu forma actual (CTL/ATL/TSB)\n"
            "/resumen — Imagen con resumen de los últimos 30 días\n"
            "/plan — Plan de entrenamientos para esta semana\n"
            "/ayuda — Ver esta lista\n\n"
            "💬 *También puedes escribirme directamente:*\n"
            "• Contarme un entreno (distancia, tiempo, FC)\n"
            "• Tu peso del día\n"
            "• Cómo te encuentras (piernas, energía)\n"
            "• Si tienes alguna molestia o lesión\n"
            "• La fecha de tu próxima carrera\n"
            "• Preguntas de nutrición\n"
            "• Si tienes pereza, ¡te motivo! 💪\n\n"
            "📡 Las actividades de Strava llegan automáticamente."
        )
        await send_message(chat_id, ayuda)
        return {"ok": True}

    if text == "/start":
        await send_message(chat_id, "💪 *¡Hola, Marcos!* Soy tu entrenador Ironman. Cuéntame cómo va el entrenamiento. Escribe /ayuda para ver todo lo que puedo hacer.")
        return {"ok": True}

    if text == "/strava":
        auth_url = (
            "https://www.strava.com/oauth/authorize"
            f"?client_id={STRAVA_CLIENT_ID}"
            f"&redirect_uri={BASE_URL}/strava/callback/{user_id}"
            "&response_type=code"
            "&scope=activity:read_all"
        )
        await send_message(chat_id, f"🔗 Conecta tu Strava aquí:\n{auth_url}", parse_mode=None)
        return {"ok": True}

    if text == "/stats":
        from database import DailyMetrics
        db = SessionLocal()
        latest = db.query(DailyMetrics).filter(
            DailyMetrics.user_id == user_id
        ).order_by(DailyMetrics.date.desc()).first()
        db.close()
        if latest:
            tsb = latest.tsb or 0
            emoji = "🟢" if tsb > 0 else "🟡" if tsb > -10 else "🟠" if tsb > -20 else "🔴"
            stats = f"📊 *Tu forma física:*\nCTL: {latest.ctl:.1f}\nATL: {latest.atl:.1f}\nTSB: {tsb:.1f} {emoji}"
        else:
            stats = "Aún no hay datos. ¡Conecta Strava con /strava o cuéntame un entreno!"
        await send_message(chat_id, stats)
        return {"ok": True}

    if text == "/resumen":
        await send_message(chat_id, "Generando tu resumen... ⏳", parse_mode=None)
        try:
            image = await generate_summary_image(user_id)
            await send_photo(chat_id, image, "📊 Tu resumen de los últimos 30 días")
        except Exception as e:
            print(f"Error generando resumen: {e}")
            await send_message(chat_id, "No pude generar el resumen. ¿Tienes entrenamientos registrados?", parse_mode=None)
        return {"ok": True}

    if text == "/plan":
        await send_message(chat_id, "Analizando tu estado y preparando el plan... ⏳", parse_mode=None)
        db = SessionLocal()
        try:
            response = await ask_coach(db, user_id, "Dame el plan de entrenamientos para esta semana basándote en mi forma actual, las semanas que quedan para mi carrera y mi estado de fatiga. Sé específico con días, disciplinas, duraciones e intensidades. Incluye el bloque <data> con weekly_plan y todas las sesiones estructuradas.")
            await send_message(chat_id, response)
        except Exception as e:
            print(f"Error generando plan: {e}")
        finally:
            db.close()
        return {"ok": True}

    if text == "/semana":
        from database import PlannedSession
        db = SessionLocal()
        try:
            now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
            week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            sessions = db.query(PlannedSession).filter(
                PlannedSession.user_id == user_id,
                PlannedSession.week_start == week_start
            ).order_by(PlannedSession.date.asc()).all()
            if not sessions:
                await send_message(chat_id, "No tienes plan para esta semana. Usa /plan para generar uno.", parse_mode=None)
            else:
                DIAS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
                no_rest = [s for s in sessions if s.discipline != "rest"]
                completadas = sum(1 for s in no_rest if s.completed)
                pct = int(completadas / len(no_rest) * 100) if no_rest else 0
                msg = f"📋 *Plan semana {week_start.strftime('%d/%m')}* — {completadas}/{len(no_rest)} completadas ({pct}%)\n\n"
                for s in sessions:
                    if s.discipline == "rest":
                        msg += f"😴 *{DIAS[s.date.weekday()]} {s.date.strftime('%d/%m')}* — Descanso\n"
                    else:
                        check = "✅" if s.completed else "⬜"
                        dur = f" {s.duration_min}min" if s.duration_min else ""
                        intens = f" [{s.intensity}]" if s.intensity else ""
                        msg += f"{check} *{DIAS[s.date.weekday()]} {s.date.strftime('%d/%m')}* — {s.discipline.upper()}{dur}{intens}\n"
                        if s.description:
                            msg += f"   _{s.description}_\n"
                await send_message(chat_id, msg)
        finally:
            db.close()
        return {"ok": True}

    # ── Coach general ─────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        response = await ask_coach(db, user_id, text)
        await send_message(chat_id, response)
        # Comprobar sobreentrenamiento tras registrar entreno
        await check_overtraining(user_id, chat_id)
        # Detectar brick
        if check_brick(user_id):
            await send_message(chat_id, "🧱 *Sesión brick detectada* — bici + carrera el mismo día. Perfecto para adaptarte a la transición del Ironman. ¿Cómo fueron las piernas en la carrera?")
    except Exception as e:
        await send_message(chat_id, "⚠️ Error del sistema. Inténtalo de nuevo.")
        print(f"Error: {e}")
    finally:
        db.close()

    return {"ok": True}


# ─── STRAVA OAUTH ─────────────────────────────────────────────────────────────

@app.get("/strava/callback/{user_id}")
async def strava_callback(user_id: str, code: str = None, error: str = None):
    if error or not code:
        return {"error": "Autorización cancelada"}

    token_data = await exchange_code(code)
    if "access_token" not in token_data:
        return {"error": "Error al obtener token"}

    athlete_id = str(token_data["athlete"]["id"])
    save_strava_token(user_id, token_data)
    strava_user_map[athlete_id] = user_id

    await send_message(int(user_id),
        f"✅ *¡Strava conectado!*\n"
        f"Hola {token_data['athlete']['firstname']}, "
        f"a partir de ahora registraré tus entrenamientos automáticamente. 🎯"
    )
    return {"ok": True, "message": "Strava conectado correctamente. Puedes cerrar esta ventana."}


# ─── STRAVA WEBHOOK ───────────────────────────────────────────────────────────

@app.get("/strava/webhook")
async def strava_webhook_verify(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    verify_token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and verify_token == STRAVA_VERIFY_TOKEN:
        return {"hub.challenge": challenge}
    return {"error": "Token inválido"}


async def process_strava_activity(user_id: str, activity_id: int):
    access_token = await refresh_token_if_needed(user_id)
    if not access_token:
        return
    try:
        activity = await get_activity_detail(activity_id, access_token)
        activity_msg = format_activity_message(activity)
        await send_message(int(user_id), activity_msg)
        db = SessionLocal()
        try:
            coach_response = await ask_coach(db, user_id, activity_msg)
            await send_message(int(user_id), coach_response)
            await check_overtraining(user_id, int(user_id))
            if check_brick(user_id):
                await send_message(int(user_id), "🧱 *Sesión brick detectada* — bici + carrera el mismo día. Perfecto para el Ironman. ¿Cómo fueron las piernas en la carrera?")
        finally:
            db.close()
    except Exception as e:
        print(f"Error procesando actividad Strava: {e}")


@app.post("/strava/webhook")
async def strava_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    if data.get("object_type") != "activity" or data.get("aspect_type") != "create":
        return {"ok": True}

    activity_id = data.get("object_id")
    if activity_id in processed_activities:
        return {"ok": True}
    processed_activities.add(activity_id)

    athlete_id = str(data.get("owner_id"))
    user_id = strava_user_map.get(athlete_id)
    if not user_id:
        from strava import get_user_id_by_athlete
        user_id = get_user_id_by_athlete(athlete_id)
        if user_id:
            strava_user_map[athlete_id] = user_id

    if not user_id:
        app_user_id = await get_supabase_user_by_athlete(athlete_id)
        if app_user_id:
            background_tasks.add_task(process_strava_activity_app, app_user_id, activity_id)
        else:
            print(f"Atleta {athlete_id} no vinculado a ningún usuario")
        return {"ok": True}

    background_tasks.add_task(process_strava_activity, user_id, activity_id)
    return {"ok": True}


# ─── APP STRAVA OAUTH ─────────────────────────────────────────────────────────

import time
from fastapi.responses import HTMLResponse

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SB_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
    "Content-Type": "application/json",
}

SUCCESS_HTML = "<html><body style='background:#000;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px;text-align:center'><div style='font-size:60px'>✅</div><h2>¡Strava conectado!</h2><p style='color:#888'>Cierra esta ventana y vuelve a la app.</p></body></html>"
ERROR_HTML   = "<html><body style='background:#000;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;flex-direction:column;gap:16px;text-align:center'><div style='font-size:60px'>❌</div><h2>Error al conectar</h2><p style='color:#888'>Cierra esta ventana e inténtalo de nuevo.</p></body></html>"


async def get_supabase_user_by_athlete(athlete_id: str):
    if not SUPABASE_URL:
        return None
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?strava_athlete_id=eq.{athlete_id}&select=id",
            headers=SB_HEADERS,
        )
        data = r.json()
        return data[0]["id"] if data else None


async def update_supabase_profile(user_id: str, data: dict):
    if not SUPABASE_URL:
        return
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
            json=data,
            headers=SB_HEADERS,
        )


async def upsert_supabase_activity(activity: dict, user_id: str):
    if not SUPABASE_URL:
        return
    disc_map = {"Run": "run", "Ride": "bike", "VirtualRide": "bike", "Swim": "swim", "WeightTraining": "gym", "Workout": "gym"}
    headers = {**SB_HEADERS, "Prefer": "resolution=merge-duplicates"}
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/activities",
            json={
                "user_id": user_id,
                "strava_id": str(activity["id"]),
                "discipline": disc_map.get(activity.get("type", ""), "run"),
                "title": activity.get("name", "Actividad"),
                "date": activity.get("start_date_local", "")[:10],
                "duration_min": round(activity.get("moving_time", 0) / 60),
                "distance_km": round(activity.get("distance", 0) / 1000, 2),
                "avg_hr": activity.get("average_heartrate"),
                "max_hr": activity.get("max_heartrate"),
                "avg_speed": round(activity.get("average_speed", 0) * 3.6, 1),
                "elevation_gain": activity.get("total_elevation_gain"),
            },
            headers=headers,
        )


async def process_strava_activity_app(user_id: str, activity_id: int):
    if not SUPABASE_URL:
        return
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=strava_access_token,strava_refresh_token,strava_token_expires_at",
            headers=SB_HEADERS,
        )
        data = r.json()
    if not data:
        return
    profile = data[0]
    access_token = profile["strava_access_token"]
    if profile.get("strava_token_expires_at") and profile["strava_token_expires_at"] < time.time():
        async with httpx.AsyncClient() as client:
            r = await client.post("https://www.strava.com/oauth/token", data={
                "client_id": STRAVA_CLIENT_ID, "client_secret": STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token", "refresh_token": profile["strava_refresh_token"],
            })
            new_tokens = r.json()
        if "access_token" in new_tokens:
            access_token = new_tokens["access_token"]
            await update_supabase_profile(user_id, {
                "strava_access_token": new_tokens["access_token"],
                "strava_refresh_token": new_tokens["refresh_token"],
                "strava_token_expires_at": new_tokens["expires_at"],
            })
    try:
        activity = await get_activity_detail(activity_id, access_token)
        await upsert_supabase_activity(activity, user_id)
    except Exception as e:
        print(f"Error sync app activity: {e}")


@app.get("/app/strava/auth/{user_id}")
async def app_strava_auth(user_id: str):
    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={STRAVA_CLIENT_ID}"
        f"&redirect_uri={BASE_URL}/app/strava/callback/{user_id}"
        f"&response_type=code&scope=activity:read_all"
    )
    return RedirectResponse(auth_url)


@app.get("/app/strava/callback/{user_id}")
async def app_strava_callback(user_id: str, code: str = None, error: str = None):
    if error or not code:
        return HTMLResponse(ERROR_HTML)
    token_data = await exchange_code(code)
    if "access_token" not in token_data:
        return HTMLResponse(ERROR_HTML)
    athlete_id = str(token_data["athlete"]["id"])
    await update_supabase_profile(user_id, {
        "strava_connected": True,
        "strava_athlete_id": athlete_id,
        "strava_access_token": token_data["access_token"],
        "strava_refresh_token": token_data["refresh_token"],
        "strava_token_expires_at": token_data["expires_at"],
    })
    strava_user_map[f"app_{athlete_id}"] = user_id
    return HTMLResponse(SUCCESS_HTML)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/app/strava/sync/{user_id}")
async def app_strava_sync(user_id: str):
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=strava_access_token,strava_refresh_token,strava_token_expires_at",
            headers=SB_HEADERS,
        )
        data = r.json()
    if not data or not data[0].get("strava_access_token"):
        return {"error": "Strava not connected"}

    profile = data[0]
    access_token = profile["strava_access_token"]

    if profile.get("strava_token_expires_at") and profile["strava_token_expires_at"] < time.time():
        async with httpx.AsyncClient() as client:
            r = await client.post("https://www.strava.com/oauth/token", data={
                "client_id": STRAVA_CLIENT_ID, "client_secret": STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token", "refresh_token": profile["strava_refresh_token"],
            })
            new_tokens = r.json()
        if "access_token" in new_tokens:
            access_token = new_tokens["access_token"]
            await update_supabase_profile(user_id, {
                "strava_access_token": new_tokens["access_token"],
                "strava_refresh_token": new_tokens["refresh_token"],
                "strava_token_expires_at": new_tokens["expires_at"],
            })

    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://www.strava.com/api/v3/athlete/activities?per_page=60&page=1",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        activities = r.json()

    if not isinstance(activities, list):
        return {"error": "Error fetching activities", "detail": activities}

    synced = 0
    for activity in activities:
        try:
            await upsert_supabase_activity(activity, user_id)
            synced += 1
        except Exception as e:
            print(f"Error syncing activity {activity.get('id')}: {e}")

    return {"synced": synced}


# ─── AI COACHING ──────────────────────────────────────────────────────────────

@app.post("/app/ai/coach/{user_id}")
async def app_ai_coach(user_id: str, request: Request):
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    body = await request.json()
    ctl = body.get("ctl", 0)
    atl = body.get("atl", 0)
    tsb = body.get("tsb", 0)
    swim_km = body.get("swim_km", 0)
    bike_km = body.get("bike_km", 0)
    run_km = body.get("run_km", 0)
    hours = body.get("hours", 0)
    race_type = body.get("race_type", "full_ironman")
    days_to_race = body.get("days_to_race", 0)

    race_names = {"sprint": "Sprint", "olympic": "Triatlón Olímpico", "half_ironman": "Ironman 70.3", "full_ironman": "Ironman Full"}
    race_label = race_names.get(race_type, race_type)

    # Fetch upcoming 7 sessions from Supabase
    upcoming_sessions = []
    try:
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        week_later = (datetime.now(timezone.utc) + timedelta(days=8)).strftime('%Y-%m-%d')
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/training_sessions"
                f"?user_id=eq.{user_id}&date=gte.{today}&date=lte.{week_later}"
                f"&completed=eq.false&order=date.asc&limit=8"
                f"&select=id,date,discipline,title,duration_min,distance_km,tss",
                headers=SB_HEADERS,
            )
            if r.status_code == 200:
                upcoming_sessions = r.json() if isinstance(r.json(), list) else []
    except Exception:
        pass

    sessions_block = ""
    for s in upcoming_sessions:
        line = f'  - id:{s["id"]} | {s["date"]} | {s["discipline"]} | {s["title"]} | {s["duration_min"]}min'
        if s.get("distance_km"):
            line += f' | {s["distance_km"]}km'
        if s.get("tss"):
            line += f' | TSS {s["tss"]}'
        sessions_block += line + "\n"
    if not sessions_block:
        sessions_block = "  (sin sesiones pendientes esta semana)"

    prompt = f"""Eres un entrenador de triatlón de élite. Analiza el estado del atleta y devuelve ÚNICAMENTE un objeto JSON válido, sin texto adicional antes ni después.

DATOS DEL ATLETA:
- Carrera: {race_label} en {days_to_race} días
- CTL (fitness crónica): {ctl} TSS/día
- ATL (fatiga aguda): {atl} TSS/día
- TSB (forma): {tsb}  →  >10=fresco  0-10=en forma  -10-0=algo cansado  <-10=muy cargado
- Volumen esta semana: Natación {swim_km}km · Bici {bike_km}km · Carrera {run_km}km · {hours}h total

SESIONES PENDIENTES PRÓXIMOS 7 DÍAS:
{sessions_block}

RESPONDE EXACTAMENTE CON ESTE JSON (sin texto fuera del JSON):
{{
  "advice": [
    "🔥 Frase 1 concreta sin markdown",
    "📊 Frase 2 concreta",
    "💡 Frase 3 concreta"
  ],
  "suggestions": [
    {{
      "session_id": "el-id-exacto-de-la-sesion",
      "date": "YYYY-MM-DD",
      "original_title": "título actual",
      "new_title": "nuevo título",
      "new_duration_min": 45,
      "new_description": "descripción breve de la sesión ajustada",
      "change_type": "reduce" | "increase" | "swap_rest" | "keep",
      "reason": "razón corta del cambio"
    }}
  ]
}}

REGLAS:
- advice: exactamente 3 frases cortas, con emoji al inicio, sin asteriscos ni markdown
- suggestions: SOLO incluir sesiones que realmente necesiten cambio (0-3 máximo)
- Si TSB < -10: reducir duración o convertir a descanso las sesiones duras
- Si TSB > 10 y faltan >30 días: puedes aumentar ligeramente
- Si el atleta está bien, suggestions puede ser []
- Usa los id exactos de las sesiones listadas arriba"""

    try:
        import anthropic, json as _json
        ai_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        message = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        # Extract JSON if wrapped in code block
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = _json.loads(raw)
        return {
            "advice": result.get("advice", []),
            "suggestions": result.get("suggestions", []),
        }
    except Exception as e:
        return {"error": str(e)}


# ─── WEEKLY AI SUMMARY ────────────────────────────────────────────────────────

@app.post("/app/ai/weekly-summary/{user_id}")
async def app_ai_weekly_summary(user_id: str, request: Request):
    """Monday morning narrative brief from Claude — full season context."""
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    body = await request.json()
    ctl = body.get("ctl", 0)
    atl = body.get("atl", 0)
    tsb = body.get("tsb", 0)
    race_type = body.get("race_type", "full_ironman")
    days_to_race = body.get("days_to_race", 0)
    swim_km = body.get("swim_km", 0)
    bike_km = body.get("bike_km", 0)
    run_km = body.get("run_km", 0)
    hours = body.get("hours", 0)
    sessions_done = body.get("sessions_done", 0)
    sessions_total = body.get("sessions_total", 0)

    # Fetch last 4 weeks of completed sessions
    completed_sessions_block = ""
    try:
        four_weeks_ago = (datetime.now(timezone.utc) - timedelta(days=28)).strftime('%Y-%m-%d')
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/training_sessions"
                f"?user_id=eq.{user_id}&date=gte.{four_weeks_ago}&date=lte.{today_str}"
                f"&completed=eq.true&order=date.desc&limit=30"
                f"&select=date,discipline,title,duration_min,distance_km,tss,rpe",
                headers=SB_HEADERS,
            )
            if r.status_code == 200 and isinstance(r.json(), list):
                for s in r.json():
                    line = f"  {s['date']} | {s['discipline']} | {s['title']} | {s['duration_min']}min"
                    if s.get('distance_km'):
                        line += f" | {s['distance_km']}km"
                    if s.get('tss'):
                        line += f" | TSS {s['tss']}"
                    if s.get('rpe'):
                        line += f" | RPE {s['rpe']}"
                    completed_sessions_block += line + "\n"
    except Exception:
        pass

    race_names = {
        "sprint": "Sprint", "olympic": "Triatlón Olímpico",
        "half_ironman": "Ironman 70.3", "full_ironman": "Ironman Full"
    }
    race_label = race_names.get(race_type, race_type)

    prompt = f"""Eres el entrenador de triatlón de élite de este atleta. Hoy es lunes y toca el resumen semanal.
Genera un resumen motivador, concreto y personalizado. Devuelve SOLO JSON válido, sin texto fuera.

ESTADO DEL ATLETA:
- Objetivo: {race_label} en {days_to_race} días
- CTL (fitness crónica): {ctl} TSS/día
- ATL (fatiga aguda): {atl} TSS/día
- TSB (forma): {tsb} → >10=fresco / 0-10=en forma / -10-0=algo cansado / <-10=muy cargado
- Semana pasada: Natación {swim_km}km · Bici {bike_km}km · Carrera {run_km}km · {hours}h total
- Sesiones: {sessions_done}/{sessions_total} completadas

SESIONES COMPLETADAS ÚLTIMAS 4 SEMANAS:
{completed_sessions_block or "  (sin datos)"}

RESPONDE EXACTAMENTE CON ESTE JSON:
{{
  "title": "Título corto motivador (max 8 palabras)",
  "headline": "Una frase impactante sobre su semana (max 20 palabras)",
  "paragraphs": [
    "Párrafo 1: análisis de la semana pasada (2-3 frases, con datos concretos)",
    "Párrafo 2: estado de forma y carga (CTL/ATL/TSB explicado de forma humana)",
    "Párrafo 3: foco y objetivo para esta semana (concreto y motivador)"
  ],
  "highlights": [
    {{"icon": "trending-up", "color": "#30D158", "text": "logro o punto positivo 1"}},
    {{"icon": "flame", "color": "#FF9F0A", "text": "logro o punto positivo 2"}},
    {{"icon": "flag", "color": "#0A84FF", "text": "objetivo clave esta semana"}}
  ],
  "phase": "Base|Construcción|Pico|Taper",
  "readiness_score": 0-100
}}

REGLAS:
- Habla siempre en español, segunda persona (tú/tu)
- Tono: coach cercano, directo, motivador — no genérico
- Si el atleta no entrenó mucho, no seas duro; si entrenó bien, celebra
- readiness_score: 100=perfecto, 70-80=bien, 50-70=aceptable, <50=recuperar"""

    try:
        import anthropic as _anthropic, json as _json
        ai_client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        message = ai_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = message.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = _json.loads(raw)
        return result
    except Exception as e:
        return {"error": str(e)}


# ─── GYM LOGS ANALYSIS ────────────────────────────────────────────────────────

@app.get("/app/ai/gym-analysis/{user_id}")
async def app_gym_analysis(user_id: str):
    """Return AI insights on the user's gym progression per exercise."""
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/gym_logs"
                f"?user_id=eq.{user_id}&order=date.asc&limit=200"
                f"&select=date,exercise_name,sets",
                headers=SB_HEADERS,
            )
            logs = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    except Exception:
        return {"exercises": []}

    if not logs:
        return {"exercises": []}

    # Group by exercise
    by_exercise: dict = {}
    for log in logs:
        name = log["exercise_name"]
        if name not in by_exercise:
            by_exercise[name] = []
        by_exercise[name].append(log)

    results = []
    for name, exercise_logs in by_exercise.items():
        if len(exercise_logs) < 2:
            continue
        # Extract max weight per session
        sessions = []
        for log in exercise_logs[-6:]:
            sets = log.get("sets", [])
            if isinstance(sets, list) and sets:
                max_w = max((s.get("weight_kg", 0) for s in sets), default=0)
                total_vol = sum(s.get("reps", 0) * s.get("weight_kg", 0) for s in sets)
                sessions.append({"date": log["date"], "max_w": max_w, "volume": total_vol})
        if len(sessions) < 2:
            continue
        last = sessions[-1]
        prev = sessions[-2]
        # Simple rule-based insight (same as frontend, duplicated for backend use)
        if last["max_w"] > prev["max_w"]:
            insight = f"+{round(last['max_w'] - prev['max_w'], 1)} kg respecto a la sesión anterior"
            insight_type = "progress"
        elif last["volume"] > prev["volume"] * 1.05:
            insight = f"Más volumen total (+{round(last['volume'] - prev['volume'])} kg·rep)"
            insight_type = "progress"
        else:
            insight = f"Mantén {last['max_w']} kg, intenta añadir 1 rep más por serie"
            insight_type = "info"

        results.append({
            "exercise": name,
            "sessions": len(exercise_logs),
            "last_max_weight": last["max_w"],
            "insight": insight,
            "insight_type": insight_type,
        })

    return {"exercises": results}


# ─── AI COACH CONVERSACIONAL ──────────────────────────────────────────────────

COACH_SYSTEM_PROMPT = """Eres el AI Coach de TriRace, el entrenador personal de triatlón de élite del atleta.
Eres experto en fisiología del deporte, periodización del entrenamiento, nutrición para resistencia y preparación mental para Ironman.

CARÁCTER:
- Directo, motivador y honesto. Nunca condescendiente.
- Usas los datos reales del atleta en cada respuesta (números, fechas, tiempos).
- Respuestas cortas y accionables, a menos que se pida análisis profundo.
- Hablas en español, segunda persona singular (tú/tu).
- Puedes usar emojis con moderación para dar energía.
- Cuando hay señales de sobreentrenamiento (TSB < -15), lo mencionas proactivamente.
- Adaptas el consejo al wellness del día (si tiene dolor muscular o poca energía, no mandas sesión dura).
- Sabes que el atleta usa TriRace para prepararse para su objetivo de carrera.

DATOS ACTUALES DEL ATLETA:
{context}

REGLAS:
- Mantén el hilo de la conversación. Recuerda lo que se ha hablado.
- Si el atleta pregunta si puede cambiar una sesión, evalúa el TSB y el wellness antes de responder.
- Si faltan menos de 21 días para la carrera (taper), recuérdalo cuando sea relevante.
- Respuestas de 1-4 párrafos cortos. Nunca hagas listas largas sin que se pidan.
- Si no sabes algo con certeza, dilo claramente."""


async def fetch_coach_context(user_id: str) -> str:
    """Fetch all athlete data from Supabase and return a formatted context block."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime('%Y-%m-%d')

    async with httpx.AsyncClient(timeout=8.0) as client:
        profile_r, wellness_r, sessions_r, activities_r = await asyncio.gather(
            client.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=name,race_type,race_date,ftp,swim_css,run_threshold_pace,max_hr,weight_kg,level", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/wellness_logs?user_id=eq.{user_id}&date=eq.{today}&select=*", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/training_sessions?user_id=eq.{user_id}&date=eq.{today}&select=discipline,title,duration_min,distance_km,tss,completed", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{user_id}&date=gte.{three_days_ago}&order=date.desc&limit=3&select=discipline,title,date,duration_min,distance_km,avg_hr,tss", headers=SB_HEADERS),
        )

    profile = (profile_r.json() or [{}])[0]
    wellness = (wellness_r.json() or [None])[0]
    sessions_today = sessions_r.json() or []
    recent_activities = activities_r.json() or []

    # Compute days to race
    days_to_race = "?"
    if profile.get("race_date"):
        try:
            rd = datetime.strptime(profile["race_date"], "%Y-%m-%d")
            days_to_race = max(0, (rd - datetime.now()).days)
        except Exception:
            pass

    race_names = {"sprint": "Sprint", "olympic": "Triatlón Olímpico", "half_ironman": "Ironman 70.3", "full_ironman": "Ironman Full"}
    race_label = race_names.get(profile.get("race_type", ""), "Ironman")

    lines = [
        f"Nombre: {profile.get('name', 'atleta')}",
        f"Objetivo: {race_label} el {profile.get('race_date', '?')} ({days_to_race} días)",
        f"Nivel: {profile.get('level', '?')}",
        f"Umbrales: FTP {profile.get('ftp', '?')}W · CSS {profile.get('swim_css', '?')}s/100m · Umbral carrera {profile.get('run_threshold_pace', '?')}s/km · FC máx {profile.get('max_hr', '?')} bpm",
        f"Peso: {profile.get('weight_kg', '?')} kg",
    ]

    if wellness:
        w_parts = []
        if wellness.get("hrv"):         w_parts.append(f"HRV {wellness['hrv']} ms")
        if wellness.get("sleep_hours"): w_parts.append(f"Sueño {wellness['sleep_hours']}h")
        if wellness.get("sleep_quality"):  w_parts.append(f"Calidad sueño {wellness['sleep_quality']}/5")
        if wellness.get("energy_level"):   w_parts.append(f"Energía {wellness['energy_level']}/5")
        if wellness.get("muscle_soreness"):w_parts.append(f"Dolor muscular {wellness['muscle_soreness']}/5")
        if wellness.get("mood"):           w_parts.append(f"Ánimo {wellness['mood']}/5")
        lines.append("Wellness hoy: " + " · ".join(w_parts) if w_parts else "Wellness hoy: no registrado")
    else:
        lines.append("Wellness hoy: no registrado")

    if sessions_today:
        for s in sessions_today:
            status = "✅ completada" if s.get("completed") else "⏳ pendiente"
            line = f"Sesión hoy ({status}): {s['discipline']} — {s['title']} {s['duration_min']}min"
            if s.get("distance_km"): line += f" {s['distance_km']}km"
            if s.get("tss"):         line += f" TSS {s['tss']}"
            lines.append(line)
    else:
        lines.append("Sesión hoy: día de descanso o sin plan")

    if recent_activities:
        lines.append("Últimas actividades:")
        for a in recent_activities:
            line = f"  {a['date']} {a['discipline']} — {a['title']} {a['duration_min']}min"
            if a.get("distance_km"): line += f" {a['distance_km']}km"
            if a.get("avg_hr"):      line += f" FC {a['avg_hr']}bpm"
            if a.get("tss"):         line += f" TSS {a['tss']}"
            lines.append(line)

    return "\n".join(lines)


@app.post("/app/ai/coach/chat/{user_id}")
async def app_coach_chat(user_id: str, request: Request):
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    import anthropic as _anthropic, json as _json

    body = await request.json()
    user_message = body.get("message", "").strip()
    conversation_id = body.get("conversation_id")
    ctl = body.get("ctl", 0)
    atl = body.get("atl", 0)
    tsb = body.get("tsb", 0)

    if not user_message:
        return {"error": "message is required"}

    # ── Load or create conversation ───────────────────────────────────────────
    existing_messages = []
    if conversation_id:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/coach_conversations?id=eq.{conversation_id}&user_id=eq.{user_id}&select=id,messages",
                headers=SB_HEADERS,
            )
            rows = r.json() if r.status_code == 200 else []
            if rows:
                existing_messages = rows[0].get("messages", [])
    else:
        # Try to load latest conversation for this user
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/coach_conversations?user_id=eq.{user_id}&order=updated_at.desc&limit=1&select=id,messages",
                headers=SB_HEADERS,
            )
            rows = r.json() if r.status_code == 200 else []
            if rows:
                conversation_id = rows[0]["id"]
                existing_messages = rows[0].get("messages", [])

    # ── Build context ─────────────────────────────────────────────────────────
    try:
        athlete_context = await fetch_coach_context(user_id)
    except Exception as e:
        athlete_context = f"(Error cargando datos: {e})"

    tsb_label = "fresco" if tsb > 10 else "en forma" if tsb >= 0 else "algo cansado" if tsb >= -10 else "muy cargado"
    athlete_context += f"\nForma física: CTL {ctl} · ATL {atl} · TSB {tsb} ({tsb_label})"

    system_prompt = COACH_SYSTEM_PROMPT.format(context=athlete_context)

    # ── Call Claude ───────────────────────────────────────────────────────────
    ai_messages = []
    # Include last 20 turns of history to keep context manageable
    for msg in existing_messages[-20:]:
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            ai_messages.append({"role": msg["role"], "content": msg["content"]})

    ai_messages.append({"role": "user", "content": user_message})

    try:
        ai_client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        response = ai_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=ai_messages,
        )
        reply = response.content[0].text.strip()
    except Exception as e:
        return {"error": f"AI error: {e}"}

    # ── Save conversation ─────────────────────────────────────────────────────
    from datetime import datetime as _dt
    now_iso = _dt.now(timezone.utc).isoformat()
    updated_messages = existing_messages + [
        {"role": "user",      "content": user_message, "timestamp": now_iso},
        {"role": "assistant", "content": reply,        "timestamp": now_iso},
    ]

    async with httpx.AsyncClient() as client:
        if conversation_id:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/coach_conversations?id=eq.{conversation_id}&user_id=eq.{user_id}",
                json={"messages": updated_messages},
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
            )
        else:
            r = await client.post(
                f"{SUPABASE_URL}/rest/v1/coach_conversations",
                json={"user_id": user_id, "messages": updated_messages},
                headers={**SB_HEADERS, "Prefer": "return=representation"},
            )
            created = r.json()
            if isinstance(created, list) and created:
                conversation_id = created[0]["id"]

    return {"reply": reply, "conversation_id": conversation_id}


@app.delete("/app/ai/coach/conversation/{user_id}")
async def clear_coach_conversation(user_id: str):
    """Delete all conversations for a user so the next chat starts fresh."""
    if not SUPABASE_URL:
        return {"error": "not configured"}
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{SUPABASE_URL}/rest/v1/coach_conversations?user_id=eq.{user_id}",
            headers=SB_HEADERS,
        )
    return {"ok": True}


# ─── RACE SIMULATOR ───────────────────────────────────────────────────────────

# ─── GAMIFICATION ─────────────────────────────────────────────────────────────

XP_EVENTS = {
    "session_complete": 50,
    "session_full":     25,   # bonus when completion == 'full'
    "pr":              100,
    "wellness":         15,
    "streak":           10,
}

LEVELS = [
    {"level": 1, "name": "Rookie",    "emoji": "🥉", "min_xp": 0,    "max_xp": 299  },
    {"level": 2, "name": "Finisher",  "emoji": "🥈", "min_xp": 300,  "max_xp": 799  },
    {"level": 3, "name": "Triatleta", "emoji": "🥇", "min_xp": 800,  "max_xp": 1999 },
    {"level": 4, "name": "Ironman",   "emoji": "⭐", "min_xp": 2000, "max_xp": 4999 },
    {"level": 5, "name": "Legend",    "emoji": "👑", "min_xp": 5000, "max_xp": None  },
]

def get_level(xp: int) -> dict:
    for l in reversed(LEVELS):
        if xp >= l["min_xp"]:
            return l
    return LEVELS[0]


@app.post("/app/gamification/award/{user_id}")
async def award_gamification(user_id: str, request: Request):
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    body = await request.json()
    event = body.get("event", "")
    metadata = body.get("metadata") or {}

    xp_earned = XP_EVENTS.get(event, 0)
    if event == "session_complete" and metadata.get("completion") == "full":
        xp_earned += XP_EVENTS["session_full"]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Load current gamification row
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/user_gamification?user_id=eq.{user_id}&select=*",
            headers=SB_HEADERS,
        )
        rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []

    current = rows[0] if rows else {
        "user_id": user_id, "xp_total": 0, "level": 1,
        "streak_days": 0, "last_activity_date": None, "prs": {}, "badges": [],
    }

    old_xp = current.get("xp_total", 0)
    old_level_info = get_level(old_xp)

    # Streak logic (only for session events)
    streak = current.get("streak_days", 0)
    last_date = current.get("last_activity_date")
    if event in ("session_complete",):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        if last_date == today:
            pass  # already trained today, no streak change
        elif last_date == yesterday:
            streak += 1
            xp_earned += XP_EVENTS["streak"]  # streak bonus
        else:
            streak = 1  # reset
        last_date = today

    new_xp = old_xp + xp_earned
    new_level_info = get_level(new_xp)
    level_up = new_level_info["level"] > old_level_info["level"]

    payload = {
        "user_id": user_id,
        "xp_total": new_xp,
        "level": new_level_info["level"],
        "streak_days": streak,
        "last_activity_date": last_date,
    }

    async with httpx.AsyncClient() as client:
        if rows:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/user_gamification?user_id=eq.{user_id}",
                json=payload,
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
            )
        else:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/user_gamification",
                json={**payload, "prs": {}, "badges": []},
                headers={**SB_HEADERS, "Prefer": "return=minimal"},
            )

    return {
        "xp_earned": xp_earned,
        "new_total": new_xp,
        "level_up": level_up,
        "new_level": new_level_info["name"],
        "new_level_emoji": new_level_info["emoji"],
        "streak_days": streak,
    }


@app.get("/app/race/predict/{user_id}")
async def app_race_predict(user_id: str):
    """Compute per-segment race prediction + Claude narrative from athlete data."""
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    import anthropic as _anthropic

    # Fetch profile + activities in parallel
    async with httpx.AsyncClient(timeout=10.0) as client:
        profile_r, activities_r = await asyncio.gather(
            client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}"
                f"&select=name,race_type,race_date,ftp,swim_css_sec,run_threshold_pace_sec,weight_kg,max_hr",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{user_id}"
                f"&order=date.asc&limit=300&select=date,tss",
                headers=SB_HEADERS,
            ),
        )

    profile = (profile_r.json() or [{}])[0]
    activities_raw = activities_r.json() if activities_r.status_code == 200 and isinstance(activities_r.json(), list) else []

    # ── Compute CTL / ATL / TSB via EMA ──────────────────────────────────────
    ctl_alpha = 2 / 43  # 42-day
    atl_alpha = 2 / 8   # 7-day
    ctl = 0.0
    atl = 0.0

    tss_by_date: dict = {}
    for a in activities_raw:
        d = (a.get("date") or "")[:10]
        tss_by_date[d] = tss_by_date.get(d, 0) + (a.get("tss") or 0)

    if tss_by_date:
        from datetime import date as _date
        start = datetime.strptime(sorted(tss_by_date)[0], "%Y-%m-%d").date()
        end = datetime.now().date()
        cur = start
        while cur <= end:
            ds = cur.strftime("%Y-%m-%d")
            daily = tss_by_date.get(ds, 0)
            ctl = ctl + ctl_alpha * (daily - ctl)
            atl = atl + atl_alpha * (daily - atl)
            cur += timedelta(days=1)

    ctl = round(ctl, 1)
    atl = round(atl, 1)
    tsb = round(ctl - atl, 1)

    # ── Race prediction (port of racePredictor.ts) ────────────────────────────
    DISTANCES = {
        "sprint":       {"swim": 0.75,  "bike": 20,   "run": 5    },
        "olympic":      {"swim": 1.5,   "bike": 40,   "run": 10   },
        "half_ironman": {"swim": 1.9,   "bike": 90,   "run": 21.1 },
        "full_ironman": {"swim": 3.8,   "bike": 180,  "run": 42.2 },
    }
    T1_T2 = {
        "sprint":       {"t1": 2,  "t2": 1},
        "olympic":      {"t1": 3,  "t2": 2},
        "half_ironman": {"t1": 5,  "t2": 3},
        "full_ironman": {"t1": 7,  "t2": 5},
    }
    RACE_NAMES = {
        "sprint": "Sprint", "olympic": "Triatlón Olímpico",
        "half_ironman": "Ironman 70.3", "full_ironman": "Ironman Full",
    }

    race_type = profile.get("race_type") or "full_ironman"
    dist = DISTANCES.get(race_type, DISTANCES["full_ironman"])
    trans = T1_T2.get(race_type, T1_T2["full_ironman"])
    missing = 0

    swim_css = profile.get("swim_css_sec")
    if swim_css:
        pace_per_km_sec = (swim_css / 100) * 1000 * 1.08
        swim_min = (dist["swim"] * pace_per_km_sec) / 60
    else:
        swim_min = dist["swim"] * 10 * 2
        missing += 1

    ftp = profile.get("ftp")
    weight_kg = profile.get("weight_kg")
    if ftp:
        race_pct = {"sprint": 0.85, "olympic": 0.80, "half_ironman": 0.75, "full_ironman": 0.70}.get(race_type, 0.70)
        wkg = (ftp * race_pct) / weight_kg if weight_kg else 3.2
        bike_min = (dist["bike"] / (8 + wkg * 7)) * 60
    else:
        default_speed = {"sprint": 28, "olympic": 30, "half_ironman": 30, "full_ironman": 28}.get(race_type, 28)
        bike_min = (dist["bike"] / default_speed) * 60
        missing += 1

    run_pace = profile.get("run_threshold_pace_sec")
    if run_pace:
        fatigue = {"sprint": 1.02, "olympic": 1.05, "half_ironman": 1.12, "full_ironman": 1.22}.get(race_type, 1.22)
        ctl_bonus = min(0.05, (ctl - 40) * 0.001) if ctl > 40 else 0
        run_min = (dist["run"] * run_pace * (fatigue - ctl_bonus)) / 60
    else:
        run_min = dist["run"] * 5.5
        missing += 1

    confidence = "high" if missing == 0 else ("medium" if missing == 1 else "low")
    confidence_pct = {"high": 90, "medium": 70, "low": 40}[confidence]

    t1_min = trans["t1"]
    t2_min = trans["t2"]
    total_min = swim_min + t1_min + bike_min + t2_min + run_min

    def fmt_min(m: float) -> str:
        h = int(m // 60)
        mn = round(m % 60)
        return f"{h}h {str(mn).zfill(2)}min" if h > 0 else f"{mn}min"

    days_to_race = None
    if profile.get("race_date"):
        try:
            rd = datetime.strptime(profile["race_date"], "%Y-%m-%d")
            days_to_race = max(0, (rd - datetime.now()).days)
        except Exception:
            pass

    race_label = RACE_NAMES.get(race_type, race_type)

    # ── Claude narrative ──────────────────────────────────────────────────────
    narrative = f"Predicción basada en tus umbrales actuales. CTL {ctl} TSS/día indica tu base de fitness crónico."
    try:
        ai_client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        nar_prompt = (
            f"Eres el Race Simulator de TriRace. Genera un análisis narrativo de 2-3 frases sobre esta predicción.\n\n"
            f"Datos del atleta:\n"
            f"- Carrera: {race_label} ({days_to_race or '?'} días)\n"
            f"- Predicción: Natación {round(swim_min)}min · T1 {t1_min}min · Bici {round(bike_min)}min · T2 {t2_min}min · Carrera {round(run_min)}min = {fmt_min(total_min)} total\n"
            f"- CTL: {ctl} · ATL: {atl} · TSB: {tsb}\n"
            f"- FTP: {ftp or 'no configurado'}W · CSS: {swim_css or 'no configurado'}s/100m · Umbral carrera: {run_pace or 'no configurado'}s/km\n"
            f"- Confianza: {confidence} ({confidence_pct}%)\n\n"
            f"Responde en español, 2-3 frases, sin markdown, sin emoji. "
            f"Identifica el factor limitante principal y qué mejoraría más el tiempo final."
        )
        msg = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=[{"role": "user", "content": nar_prompt}],
        )
        narrative = msg.content[0].text.strip()
    except Exception:
        pass

    return {
        "swim": round(swim_min),
        "t1": t1_min,
        "bike": round(bike_min),
        "t2": t2_min,
        "run": round(run_min),
        "total": round(total_min),
        "total_formatted": fmt_min(total_min),
        "narrative": narrative,
        "confidence": confidence,
        "confidence_pct": confidence_pct,
        "ctl": ctl,
        "atl": atl,
        "tsb": tsb,
        "race_type": race_type,
        "race_label": race_label,
        "days_to_race": days_to_race,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
