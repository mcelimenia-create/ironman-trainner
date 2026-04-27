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
        "level_name": new_level_info["name"],
        "level_emoji": new_level_info["emoji"],
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


# ─── NUTRITION ────────────────────────────────────────────────────────────────

@app.get("/app/nutrition/today/{user_id}")
async def app_nutrition_today(user_id: str):
    """Generate personalized nutrition plan adapted to today's training session."""
    if not SUPABASE_URL:
        return {"error": "Supabase not configured"}

    import anthropic as _anthropic, json as _json, re as _re

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=10.0) as client:
        profile_r, session_r, wellness_r = await asyncio.gather(
            client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}"
                f"&select=name,weight_kg,level,race_type",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/training_sessions?user_id=eq.{user_id}&date=eq.{today}"
                f"&order=created_at.asc&limit=1"
                f"&select=discipline,title,description,duration_min,tss",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/wellness_logs?user_id=eq.{user_id}&date=eq.{today}&select=energy_level,muscle_soreness,sleep_quality",
                headers=SB_HEADERS,
            ),
        )

    profile  = (profile_r.json()  or [{}])[0]
    sessions = session_r.json()   or []
    session  = sessions[0]        if sessions else None
    wellness = (wellness_r.json() or [None])[0]

    weight       = profile.get("weight_kg") or 75
    discipline   = (session.get("discipline", "rest") if session else "rest")
    description  = ((session.get("description", "") or "") if session else "").lower()
    duration_min = (session.get("duration_min", 0)   if session else 0)

    # ── Classify session intensity ────────────────────────────────────────────
    is_rest     = discipline == "rest" or not session
    is_gym      = discipline == "gym"
    is_long     = duration_min >= 90
    is_brick    = discipline == "brick"
    is_interval = bool(_re.search(r'\d+\s*[x×]', description))
    is_z4_z5    = bool(_re.search(r'z[45]|zona\s*[45]|umbral|series|lactato|vo2|rpe\s*[89]', description))
    is_z1_z2    = bool(_re.search(r'z[12]|zona\s*[12]|suave|recuperaci|rodaje', description))

    if is_rest:
        carbs_per_kg, kcal_per_kg, intensity_label = 3.0, 30, "Día de descanso"
    elif is_gym:
        carbs_per_kg, kcal_per_kg, intensity_label = 3.5, 33, "Sesión de fuerza"
    elif is_long or is_brick:
        carbs_per_kg, kcal_per_kg = 6.0, 42
        intensity_label = "Sesión larga (brick)" if is_brick else "Sesión de larga duración"
    elif is_interval or is_z4_z5:
        carbs_per_kg, kcal_per_kg, intensity_label = 5.5, 40, "Intervalos / Alta intensidad"
    elif is_z1_z2:
        carbs_per_kg, kcal_per_kg, intensity_label = 4.0, 35, "Sesión suave Z1-Z2"
    else:
        carbs_per_kg, kcal_per_kg, intensity_label = 4.5, 37, "Sesión moderada Z3"

    if duration_min > 60:
        kcal_per_kg += (duration_min - 60) / 30 * 1.5

    protein_g = round(weight * 1.9)
    carbs_g   = round(weight * carbs_per_kg)
    calories  = round(weight * kcal_per_kg)
    fat_g     = max(50, round((calories - (carbs_g * 4 + protein_g * 4)) / 9))
    calories  = round(carbs_g * 4 + protein_g * 4 + fat_g * 9)

    # Wellness adjustments
    energy   = (wellness.get("energy_level", 3)    if wellness else 3)
    soreness = (wellness.get("muscle_soreness", 1) if wellness else 1)
    if energy <= 2:
        carbs_g  = round(carbs_g  * 1.1)
        calories = round(carbs_g * 4 + protein_g * 4 + fat_g * 9)

    session_context = f"{session['title']} · {duration_min}min" if session and session.get("title") else (
        f"{discipline.capitalize()} · {duration_min}min" if session else "Día de descanso"
    )

    # ── Supplements ───────────────────────────────────────────────────────────
    supplements = [
        {"name": "Vitamina D3",          "dose": "2000 UI", "timing": "Con el desayuno",         "icon": "sunny-outline"},
        {"name": "Omega-3",              "dose": "1g EPA+DHA","timing": "Con la comida principal","icon": "water-outline"},
        {"name": "Proteína en polvo",    "dose": "30g",     "timing": "Post-entreno (30 min)",    "icon": "fitness-outline"},
        {"name": "Magnesio glicinato",   "dose": "400mg",   "timing": "Antes de dormir",          "icon": "moon-outline"},
    ]
    if is_gym:
        supplements.insert(2, {"name": "Creatina monohidrato", "dose": "5g", "timing": "Post-entreno con agua", "icon": "barbell-outline"})
    if is_long or is_brick:
        supplements.insert(2, {"name": "Electrolitos",         "dose": "1 tableta/hora", "timing": "Durante el entrenamiento", "icon": "pulse-outline"})

    # ── Claude meal plan ──────────────────────────────────────────────────────
    meals = []
    try:
        ai_client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        prompt = (
            f"Eres nutricionista deportivo especialista en triatlón. Genera un plan de comidas personalizado.\n\n"
            f"ATLETA:\n"
            f"- Peso: {weight}kg · Nivel: {profile.get('level','intermedio')}\n"
            f"- Sesión de hoy: {session_context} ({intensity_label})\n"
            f"- Energía matutina: {energy}/5{' ⚠️ baja' if energy <= 2 else ''}\n"
            f"- Dolor muscular: {soreness}/5{' ⚠️ alto' if soreness >= 4 else ''}\n\n"
            f"OBJETIVOS: {calories} kcal · {carbs_g}g carbs · {protein_g}g proteína · {fat_g}g grasa\n\n"
            f"Devuelve SOLO JSON válido (sin texto fuera) con exactamente 4 comidas:\n"
            f'{{"meals":['
            f'{{"type":"breakfast","time":"07:30","name":"...","foods":[{{"name":"...","amount_g":80,"notes":"opcional"}}],"calories":0,"carbs_g":0,"protein_g":0,"fat_g":0,"tip":"consejo breve (opcional)"}},'
            f'{{"type":"pre_workout","time":"10:00",...}},'
            f'{{"type":"post_workout","time":"12:30",...}},'
            f'{{"type":"dinner","time":"20:00",...}}'
            f']}}\n\n'
            f"REGLAS: alimentos reales con gramos exactos · pre-entreno fácil digestión y carbos · "
            f"post-entreno proteína+carbos en 30-60min · macros totales ≈ objetivos · español"
        )
        msg = ai_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        meals = _json.loads(raw).get("meals", [])
    except Exception:
        q = calories // 4
        meals = [
            {"type": "breakfast",   "time": "07:30", "name": "Desayuno",
             "foods": [{"name": "Avena", "amount_g": 80}, {"name": "Plátano", "amount_g": 120}, {"name": "Huevos revueltos", "amount_g": 150}],
             "calories": round(q * 1.05), "carbs_g": round(carbs_g * 0.30), "protein_g": round(protein_g * 0.25), "fat_g": round(fat_g * 0.30)},
            {"type": "pre_workout", "time": "10:00", "name": "Pre-entreno",
             "foods": [{"name": "Plátano", "amount_g": 120}, {"name": "Arroz con miel", "amount_g": 100}],
             "calories": round(q * 0.60), "carbs_g": round(carbs_g * 0.25), "protein_g": round(protein_g * 0.08), "fat_g": round(fat_g * 0.05)},
            {"type": "post_workout","time": "12:30", "name": "Post-entreno",
             "foods": [{"name": "Batido de proteína", "amount_g": 30}, {"name": "Arroz blanco", "amount_g": 200}, {"name": "Pechuga de pollo", "amount_g": 150}],
             "calories": round(q * 1.05), "carbs_g": round(carbs_g * 0.25), "protein_g": round(protein_g * 0.40), "fat_g": round(fat_g * 0.20)},
            {"type": "dinner",      "time": "20:00", "name": "Cena",
             "foods": [{"name": "Salmón", "amount_g": 180}, {"name": "Boniato asado", "amount_g": 200}, {"name": "Ensalada", "amount_g": 100}],
             "calories": round(q * 1.30), "carbs_g": round(carbs_g * 0.20), "protein_g": round(protein_g * 0.27), "fat_g": round(fat_g * 0.45)},
        ]

    # ── Upsert to Supabase ────────────────────────────────────────────────────
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/nutrition_plans",
            json={"user_id": user_id, "date": today, "session_type": intensity_label,
                  "calories": calories, "carbs_g": carbs_g, "protein_g": protein_g,
                  "fat_g": fat_g, "meals": meals, "supplements": supplements},
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
        )

    return {
        "calories": calories, "carbs_g": carbs_g,
        "protein_g": protein_g, "fat_g": fat_g,
        "meals": meals, "supplements": supplements,
        "session_context": session_context,
        "intensity_label": intensity_label,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── SOCIAL / TRIBE ───────────────────────────────────────────────────────────

_CTL_K = 0.02352  # 1 - exp(-1/42)

def _compute_ctl(activities: list) -> int:
    if not activities:
        return 0
    acts_sorted = sorted(activities, key=lambda a: (a.get("date") or "")[:10])
    ctl = 0.0
    for a in acts_sorted:
        tss = a.get("tss") or round((a.get("duration_min") or 0) * 0.8)
        ctl += _CTL_K * (tss - ctl)
    return round(ctl)


@app.get("/app/social/groups/{user_id}")
async def social_groups(user_id: str):
    async with httpx.AsyncClient() as client:
        pr = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=race_type",
            headers=SB_HEADERS,
        )
    race_type = (pr.json()[0].get("race_type") or "full_ironman") if pr.json() else "full_ironman"

    async with httpx.AsyncClient() as client:
        mem_r, sug_r = await asyncio.gather(
            client.get(
                f"{SUPABASE_URL}/rest/v1/race_group_members?user_id=eq.{user_id}&select=group_id,goal_time,joined_at",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/race_groups?race_type=eq.{race_type}&order=member_count.desc&limit=20",
                headers=SB_HEADERS,
            ),
        )

    memberships = mem_r.json() if isinstance(mem_r.json(), list) else []
    all_groups  = sug_r.json() if isinstance(sug_r.json(), list) else []
    my_ids = {m["group_id"] for m in memberships if isinstance(m, dict)}

    my_groups        = [g for g in all_groups if isinstance(g, dict) and g.get("id") in my_ids]
    suggested_groups = [g for g in all_groups if isinstance(g, dict) and g.get("id") not in my_ids][:6]

    # Attach goal_time to my_groups
    gmap = {m["group_id"]: m.get("goal_time") for m in memberships if isinstance(m, dict)}
    for g in my_groups:
        g["goal_time"] = gmap.get(g["id"])

    return {"my_groups": my_groups, "suggested_groups": suggested_groups, "race_type": race_type}


@app.post("/app/social/groups/join")
async def social_groups_join(body: dict):
    user_id  = body.get("user_id")
    group_id = body.get("group_id")
    goal_time = body.get("goal_time")

    async with httpx.AsyncClient() as client:
        await client.post(
            f"{SUPABASE_URL}/rest/v1/race_group_members",
            json={"user_id": user_id, "group_id": group_id, "goal_time": goal_time},
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
        )
        gr = await client.get(
            f"{SUPABASE_URL}/rest/v1/race_groups?id=eq.{group_id}&select=member_count",
            headers=SB_HEADERS,
        )
        groups = gr.json()
        if isinstance(groups, list) and groups:
            count = (groups[0].get("member_count") or 0) + 1
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/race_groups?id=eq.{group_id}",
                json={"member_count": count},
                headers=SB_HEADERS,
            )
    return {"ok": True}


@app.get("/app/social/group/{group_id}/members")
async def social_group_members(group_id: str):
    async with httpx.AsyncClient() as client:
        members_r = await client.get(
            f"{SUPABASE_URL}/rest/v1/race_group_members?group_id=eq.{group_id}&select=user_id,goal_time,joined_at",
            headers=SB_HEADERS,
        )
    members = members_r.json() if isinstance(members_r.json(), list) else []
    if not members:
        return {"members": []}

    user_ids = [m["user_id"] for m in members if isinstance(m, dict)]
    ids_str  = ",".join(user_ids)
    forty_two_ago = (datetime.now(timezone.utc) - timedelta(days=42)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient() as client:
        profiles_r, acts_r = await asyncio.gather(
            client.get(f"{SUPABASE_URL}/rest/v1/profiles?id=in.({ids_str})&select=id,name", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/activities?user_id=in.({ids_str})&date=gte.{forty_two_ago}&select=user_id,date,tss,duration_min", headers=SB_HEADERS),
        )

    profiles = {p["id"]: p for p in (profiles_r.json() or []) if isinstance(p, dict)}
    acts_by_user: dict = {}
    for a in (acts_r.json() or []):
        if isinstance(a, dict):
            acts_by_user.setdefault(a["user_id"], []).append(a)

    result = []
    for m in members:
        if not isinstance(m, dict):
            continue
        uid  = m["user_id"]
        name = profiles.get(uid, {}).get("name", "Atleta")
        ctl  = _compute_ctl(acts_by_user.get(uid, []))
        result.append({"user_id": uid, "name": name, "goal_time": m.get("goal_time"), "ctl": ctl, "joined_at": m.get("joined_at")})

    result.sort(key=lambda x: x["ctl"], reverse=True)
    return {"members": result}


@app.get("/app/social/leaderboard/{user_id}")
async def social_leaderboard(user_id: str):
    async with httpx.AsyncClient() as client:
        pr = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=race_type",
            headers=SB_HEADERS,
        )
    race_type = (pr.json()[0].get("race_type") or "full_ironman") if pr.json() else "full_ironman"

    async with httpx.AsyncClient() as client:
        profiles_r, gami_r = await asyncio.gather(
            client.get(f"{SUPABASE_URL}/rest/v1/profiles?race_type=eq.{race_type}&select=id,name&limit=100", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/user_gamification?select=user_id,xp_total,streak_days,level_name,level_emoji,last_activity_date&limit=200", headers=SB_HEADERS),
        )

    profiles  = [p for p in (profiles_r.json() or []) if isinstance(p, dict)]
    gami_map  = {g["user_id"]: g for g in (gami_r.json() or []) if isinstance(g, dict)}
    user_ids  = [p["id"] for p in profiles]

    acts_by_user: dict = {}
    if user_ids:
        forty_two_ago = (datetime.now(timezone.utc) - timedelta(days=42)).strftime("%Y-%m-%d")
        ids_str = ",".join(user_ids[:50])
        async with httpx.AsyncClient() as client:
            ar = await client.get(
                f"{SUPABASE_URL}/rest/v1/activities?user_id=in.({ids_str})&date=gte.{forty_two_ago}&select=user_id,date,tss,duration_min&limit=5000",
                headers=SB_HEADERS,
            )
        for a in (ar.json() or []):
            if isinstance(a, dict):
                acts_by_user.setdefault(a["user_id"], []).append(a)

    entries = []
    for p in profiles:
        uid = p.get("id")
        g   = gami_map.get(uid, {})
        entries.append({
            "user_id":     uid,
            "name":        p.get("name", "Atleta"),
            "xp_total":    g.get("xp_total", 0) or 0,
            "level_name":  g.get("level_name", "Rookie"),
            "level_emoji": g.get("level_emoji", "🥉"),
            "streak_days": g.get("streak_days", 0) or 0,
            "ctl":         _compute_ctl(acts_by_user.get(uid, [])),
            "is_me":       uid == user_id,
        })

    entries.sort(key=lambda e: e["xp_total"], reverse=True)
    for i, e in enumerate(entries):
        e["position"] = i + 1

    my_entry = next((e for e in entries if e["is_me"]), None)
    top20    = entries[:20]

    return {
        "leaderboard":    top20,
        "my_position":    my_entry["position"] if my_entry else None,
        "my_entry":       my_entry,
        "race_type":      race_type,
        "total_athletes": len(entries),
    }


@app.post("/app/social/rival/challenge")
async def rival_challenge(body: dict):
    challenger_id = body.get("challenger_id")
    challenged_id = body.get("challenged_id")

    today      = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/rival_challenges",
            json={
                "challenger_id": challenger_id,
                "challenged_id": challenged_id,
                "week_start":    week_start.isoformat(),
                "status":        "active",
            },
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
        )
    data = r.json()
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict) and not data.get("code"):
        return data
    return {"ok": True, "week_start": week_start.isoformat()}


@app.get("/app/social/rival/active/{user_id}")
async def rival_active(user_id: str):
    today      = datetime.now(timezone.utc).date()
    week_start = today - timedelta(days=today.weekday())

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/rival_challenges"
            f"?or=(challenger_id.eq.{user_id},challenged_id.eq.{user_id})"
            f"&status=eq.active&order=created_at.desc&limit=1",
            headers=SB_HEADERS,
        )
    challenges = r.json() if isinstance(r.json(), list) else []
    if not challenges:
        return {"challenge": None}

    ch             = challenges[0]
    challenger_id  = ch["challenger_id"]
    challenged_id  = ch["challenged_id"]

    async with httpx.AsyncClient() as client:
        p1r, p2r, a1r, a2r = await asyncio.gather(
            client.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{challenger_id}&select=name", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{challenged_id}&select=name", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{challenger_id}&date=gte.{week_start}&select=tss,duration_min", headers=SB_HEADERS),
            client.get(f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{challenged_id}&date=gte.{week_start}&select=tss,duration_min", headers=SB_HEADERS),
        )

    def _week_tss(acts):
        total = 0
        for a in (acts if isinstance(acts, list) else []):
            total += (a.get("tss") or 0) or round((a.get("duration_min") or 0) * 0.8)
        return round(total)

    days_left      = 6 - today.weekday()  # Mon=0 → Sun=6
    challenger_tss = _week_tss(a1r.json())
    challenged_tss = _week_tss(a2r.json())

    return {
        "challenge": {
            **ch,
            "challenger_name": (p1r.json()[0].get("name") or "Atleta") if p1r.json() else "Atleta",
            "challenged_name": (p2r.json()[0].get("name") or "Atleta") if p2r.json() else "Atleta",
            "challenger_tss":  challenger_tss,
            "challenged_tss":  challenged_tss,
            "days_left":       days_left,
            "week_start":      week_start.isoformat(),
            "is_challenger":   challenger_id == user_id,
        }
    }


# ── Race modalities ───────────────────────────────────────────────────────────

@app.get("/app/race-modalities")
async def get_race_modalities():
    if not SUPABASE_URL:
        return []
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/race_modalities?select=*&order=category,id",
            headers=SB_HEADERS,
        )
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


@app.get("/app/race-modalities/{category}")
async def get_race_modalities_by_category(category: str):
    if not SUPABASE_URL:
        return []
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/race_modalities?category=eq.{category}&select=*&order=id",
            headers=SB_HEADERS,
        )
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


# ─── SESSION CRUD ─────────────────────────────────────────────────────────────

def _estimate_tss(duration_min: int, discipline: str) -> int:
    factors = {"swim": 0.7, "bike": 0.75, "run": 0.85, "gym": 0.6, "brick": 0.9, "rest": 0.0}
    return round(duration_min * factors.get(discipline, 0.8))


@app.put("/app/plan/session/{session_id}")
async def update_session(session_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id or not SUPABASE_URL:
        return {"success": False, "error": "missing user_id"}

    allowed = {"discipline", "duration_min", "title", "description", "custom_notes", "custom_blocks"}
    updates = {k: v for k, v in body.items() if k in allowed and v is not None}

    if not updates:
        return {"success": False, "error": "no fields to update"}

    # Recalculate TSS when duration or discipline changes
    if "duration_min" in updates or "discipline" in updates:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{SUPABASE_URL}/rest/v1/training_sessions?id=eq.{session_id}&user_id=eq.{user_id}&select=discipline,duration_min",
                headers=SB_HEADERS,
            )
        rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
        if rows:
            cur = rows[0]
            discipline = updates.get("discipline", cur.get("discipline", "run"))
            duration_min = updates.get("duration_min", cur.get("duration_min", 60))
            updates["tss"] = _estimate_tss(duration_min, discipline)

    updates["user_custom"] = True

    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/training_sessions?id=eq.{session_id}&user_id=eq.{user_id}",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json=updates,
        )

    if r.status_code in (200, 204):
        rows = r.json() if isinstance(r.json(), list) else []
        updated = rows[0] if rows else None
        return {"success": True, "updated_session": updated, "new_tss": updated.get("tss") if updated else None}
    return {"success": False, "error": r.text}


@app.delete("/app/plan/session/{session_id}")
async def delete_session(session_id: str, user_id: str):
    if not SUPABASE_URL:
        return {"success": False, "error": "no db"}
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/training_sessions?id=eq.{session_id}&user_id=eq.{user_id}",
            headers=SB_HEADERS,
        )
    return {"success": r.status_code in (200, 204)}


@app.post("/app/plan/session/{session_id}/duplicate")
async def duplicate_session(session_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    target_date = body.get("target_date")
    if not user_id or not target_date or not SUPABASE_URL:
        return {"success": False, "error": "missing fields"}

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/training_sessions?id=eq.{session_id}&user_id=eq.{user_id}&select=*",
            headers=SB_HEADERS,
        )
    rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    if not rows:
        return {"success": False, "error": "session not found"}

    original = dict(rows[0])
    original.pop("id", None)
    original["date"] = target_date
    original["completed"] = False
    original["activity_id"] = None
    original["rpe"] = None
    original["notes"] = None
    original["feeling"] = None
    original["completion"] = None

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/training_sessions",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json=original,
        )

    if r.status_code in (200, 201):
        rows = r.json() if isinstance(r.json(), list) else []
        new_session = rows[0] if rows else None
        return {"success": True, "session": new_session}
    return {"success": False, "error": r.text}


@app.post("/app/plan/session/{session_id}/move")
async def move_session(session_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    target_date = body.get("target_date")
    if not user_id or not target_date or not SUPABASE_URL:
        return {"success": False, "error": "missing fields"}

    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/training_sessions?id=eq.{session_id}&user_id=eq.{user_id}",
            headers=SB_HEADERS,
            json={"date": target_date},
        )
    return {"success": r.status_code in (200, 204)}


# ─── ACTIVITIES (GPS) ─────────────────────────────────────────────────────────

XP_PER_KM: dict = {"run": 10, "bike": 5, "swim": 15, "walk": 3, "other": 5}

TSS_FACTORS: dict = {"run": 0.85, "bike": 0.75, "swim": 0.7, "walk": 0.5, "other": 0.6}


def _activity_xp(sport: str, distance_m: float) -> int:
    km = distance_m / 1000
    return round(km * XP_PER_KM.get(sport, 5))


def _activity_tss(sport: str, duration_sec: int) -> int:
    duration_min = duration_sec / 60
    return round(duration_min * TSS_FACTORS.get(sport, 0.7))


@app.post("/app/activities")
async def create_activity(request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id or not SUPABASE_URL:
        return {"success": False, "error": "missing user_id"}

    sport = body.get("sport", "other")
    duration_sec = body.get("duration_seconds", 0)
    distance_m = body.get("distance_meters", 0)
    now_iso = datetime.now(timezone.utc).isoformat()

    row = {
        "user_id": user_id,
        "sport": sport,
        "started_at": now_iso,
        "ended_at": now_iso,
        "duration_seconds": duration_sec,
        "distance_meters": distance_m,
        "avg_pace": body.get("avg_pace"),
        "avg_speed": body.get("avg_speed"),
        "max_speed": body.get("max_speed"),
        "elevation_gain": body.get("elevation_gain"),
        "elevation_loss": body.get("elevation_loss"),
        "gps_track": body.get("gps_track", []),
        "splits": body.get("splits", []),
        "notes": body.get("notes"),
        "source": body.get("source", "pulse_gps"),
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/activities",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json=row,
        )

    if r.status_code not in (200, 201):
        return {"success": False, "error": r.text}

    rows = r.json() if isinstance(r.json(), list) else []
    activity_id = rows[0].get("id") if rows else None

    xp_earned = _activity_xp(sport, distance_m)
    tss = _activity_tss(sport, duration_sec)

    # Award XP
    if xp_earned > 0:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{BASE_URL}/app/gamification/award/{user_id}",
                json={"xp": xp_earned, "reason": f"activity_{sport}"},
                timeout=10,
            )

    # Auto-complete today's training session if matching discipline/date
    date_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with httpx.AsyncClient() as client:
        r2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/training_sessions"
            f"?user_id=eq.{user_id}&date=eq.{date_today}&discipline=eq.{sport}&completed=eq.false"
            f"&select=id&limit=1",
            headers=SB_HEADERS,
        )
    matched = r2.json() if r2.status_code == 200 and isinstance(r2.json(), list) else []
    if matched:
        session_id = matched[0]["id"]
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{SUPABASE_URL}/rest/v1/training_sessions?id=eq.{session_id}&user_id=eq.{user_id}",
                headers=SB_HEADERS,
                json={"completed": True, "activity_id": activity_id},
            )
        xp_earned += 25  # bonus for matching session

    return {"success": True, "activity_id": activity_id, "xp_earned": xp_earned, "tss": tss}


@app.get("/app/activities/{user_id}")
async def get_activities(user_id: str, limit: int = 20, offset: int = 0):
    if not SUPABASE_URL:
        return []
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/activities"
            f"?user_id=eq.{user_id}&order=created_at.desc&limit={limit}&offset={offset}"
            f"&select=id,sport,duration_seconds,distance_meters,avg_pace,avg_speed,elevation_gain,splits,notes,created_at,source",
            headers=SB_HEADERS,
        )
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


@app.put("/app/activities/{activity_id}")
async def update_activity(activity_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id or not SUPABASE_URL:
        return {"success": False, "error": "missing user_id"}

    allowed = {"sport", "notes", "avg_pace", "avg_speed"}
    updates = {k: v for k, v in body.items() if k in allowed}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/activities?id=eq.{activity_id}&user_id=eq.{user_id}",
            headers=SB_HEADERS,
            json=updates,
        )
    return {"success": r.status_code in (200, 204)}


# ─── FRIENDS & CHAT ───────────────────────────────────────────────────────────

@app.post("/app/social/friends/add")
async def add_friend(request: Request):
    body = await request.json()
    from_id = body.get("from_user_id")
    to_id = body.get("to_user_id")
    if not from_id or not to_id or not SUPABASE_URL:
        return {"success": False, "error": "missing fields"}
    if from_id == to_id:
        return {"success": False, "error": "cannot add yourself"}

    # Canonical order: smaller UUID first
    a, b = (from_id, to_id) if from_id < to_id else (to_id, from_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    async with httpx.AsyncClient() as client:
        # Check existing
        chk = await client.get(
            f"{SUPABASE_URL}/rest/v1/friendships?user_a=eq.{a}&user_b=eq.{b}&select=id,status",
            headers=SB_HEADERS,
        )
        existing = chk.json() if chk.status_code == 200 and isinstance(chk.json(), list) else []
        if existing:
            return {"success": False, "error": "already_exists", "status": existing[0].get("status")}

        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/friendships",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json={"user_a": a, "user_b": b, "status": "pending",
                  "requester_id": from_id, "created_at": now_iso, "updated_at": now_iso},
        )
    if r.status_code in (200, 201):
        rows = r.json() if isinstance(r.json(), list) else []
        return {"success": True, "friendship": rows[0] if rows else None}
    return {"success": False, "error": r.text}


@app.post("/app/social/friends/accept/{friendship_id}")
async def accept_friend(friendship_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id or not SUPABASE_URL:
        return {"success": False, "error": "missing user_id"}

    now_iso = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as client:
        # Verify user is part of this friendship
        chk = await client.get(
            f"{SUPABASE_URL}/rest/v1/friendships?id=eq.{friendship_id}"
            f"&or=(user_a.eq.{user_id},user_b.eq.{user_id})&select=id",
            headers=SB_HEADERS,
        )
        rows = chk.json() if chk.status_code == 200 and isinstance(chk.json(), list) else []
        if not rows:
            return {"success": False, "error": "not found"}

        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/friendships?id=eq.{friendship_id}",
            headers=SB_HEADERS,
            json={"status": "accepted", "updated_at": now_iso},
        )
    return {"success": r.status_code in (200, 204)}


@app.get("/app/social/friends/{user_id}")
async def get_friends(user_id: str):
    if not SUPABASE_URL:
        return []
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/friendships"
            f"?or=(user_a.eq.{user_id},user_b.eq.{user_id})&status=eq.accepted&select=*",
            headers=SB_HEADERS,
        )
    return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []


@app.get("/app/social/friends/pending/{user_id}")
async def get_pending_friends(user_id: str):
    if not SUPABASE_URL:
        return []
    async with httpx.AsyncClient() as client:
        # Requests TO this user (requester_id != user_id means someone else sent it)
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/friendships"
            f"?or=(user_a.eq.{user_id},user_b.eq.{user_id})"
            f"&status=eq.pending&select=*",
            headers=SB_HEADERS,
        )
    rows = r.json() if r.status_code == 200 and isinstance(r.json(), list) else []
    # Only return requests where current user is not the requester
    return [f for f in rows if f.get("requester_id") != user_id]


@app.post("/app/social/chat/direct")
async def create_direct_chat(request: Request):
    body = await request.json()
    user_a = body.get("user_id")
    user_b = body.get("other_user_id")
    if not user_a or not user_b or not SUPABASE_URL:
        return {"success": False, "error": "missing fields"}

    now_iso = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as client:
        # Find existing direct room shared by both users
        ra = await client.get(
            f"{SUPABASE_URL}/rest/v1/chat_room_members?user_id=eq.{user_a}&select=room_id",
            headers=SB_HEADERS,
        )
        rb = await client.get(
            f"{SUPABASE_URL}/rest/v1/chat_room_members?user_id=eq.{user_b}&select=room_id",
            headers=SB_HEADERS,
        )
        rooms_a = {m["room_id"] for m in (ra.json() or []) if isinstance(ra.json(), list)}
        rooms_b = {m["room_id"] for m in (rb.json() or []) if isinstance(rb.json(), list)}
        shared = rooms_a & rooms_b

        if shared:
            for room_id in shared:
                rr = await client.get(
                    f"{SUPABASE_URL}/rest/v1/chat_rooms?id=eq.{room_id}&type=eq.direct&select=*",
                    headers=SB_HEADERS,
                )
                existing = rr.json() if isinstance(rr.json(), list) else []
                if existing:
                    return {"success": True, "room": existing[0], "created": False}

        # Create new room
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/chat_rooms",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json={"type": "direct", "created_by": user_a,
                  "created_at": now_iso, "updated_at": now_iso},
        )
        rooms = r.json() if isinstance(r.json(), list) else []
        if not rooms:
            return {"success": False, "error": "could not create room"}
        new_room = rooms[0]
        room_id = new_room["id"]

        # Add both members
        await client.post(
            f"{SUPABASE_URL}/rest/v1/chat_room_members",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
            json={"room_id": room_id, "user_id": user_a, "role": "member", "joined_at": now_iso},
        )
        await client.post(
            f"{SUPABASE_URL}/rest/v1/chat_room_members",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
            json={"room_id": room_id, "user_id": user_b, "role": "member", "joined_at": now_iso},
        )
    return {"success": True, "room": new_room, "created": True}


@app.post("/app/social/chat/groups/create")
async def create_group_chat(request: Request):
    body = await request.json()
    creator_id = body.get("user_id")
    name = body.get("name", "").strip()
    members: list = body.get("members", [])
    description = body.get("description", "")
    if not creator_id or not name or not SUPABASE_URL:
        return {"success": False, "error": "missing fields"}
    if len(members) < 1:
        return {"success": False, "error": "need at least 1 other member"}

    now_iso = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/chat_rooms",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json={"type": "group", "name": name, "description": description,
                  "created_by": creator_id, "created_at": now_iso, "updated_at": now_iso},
        )
        rooms = r.json() if isinstance(r.json(), list) else []
        if not rooms:
            return {"success": False, "error": "could not create group"}
        room_id = rooms[0]["id"]

        all_members = list({creator_id} | set(members))
        for uid in all_members:
            role = "admin" if uid == creator_id else "member"
            await client.post(
                f"{SUPABASE_URL}/rest/v1/chat_room_members",
                headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
                json={"room_id": room_id, "user_id": uid, "role": role, "joined_at": now_iso},
            )
    return {"success": True, "room": rooms[0]}


@app.post("/app/social/chat/groups/{group_id}/leave")
async def leave_group(group_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    if not user_id or not SUPABASE_URL:
        return {"success": False, "error": "missing user_id"}
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/chat_room_members?room_id=eq.{group_id}&user_id=eq.{user_id}",
            headers=SB_HEADERS,
        )
    return {"success": r.status_code in (200, 204)}


# ─── Race Catalog ─────────────────────────────────────────────────────────────

@app.get("/app/race-catalog")
async def get_race_catalog(
    modality: str = None,
    date_from: str = None,
    date_to: str = None,
    region: str = None,
    search: str = None,
):
    if not SUPABASE_URL:
        return {"races": []}
    params = "select=*&order=race_date.asc"
    if modality:
        params += f"&modality=eq.{modality}"
    if date_from:
        params += f"&race_date=gte.{date_from}"
    if date_to:
        params += f"&race_date=lte.{date_to}"
    if region:
        params += f"&region=ilike.*{region}*"
    if search:
        params += f"&name=ilike.*{search}*"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/races?{params}",
            headers=SB_HEADERS,
        )
    if r.status_code != 200:
        return {"races": []}
    return {"races": r.json()}


@app.get("/app/race-catalog/{race_id}")
async def get_race_detail(race_id: str):
    if not SUPABASE_URL:
        return {"race": None, "athletes": []}
    async with httpx.AsyncClient() as client:
        race_r, reg_r = await asyncio.gather(
            client.get(
                f"{SUPABASE_URL}/rest/v1/races?id=eq.{race_id}&select=*",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/race_registrations?race_id=eq.{race_id}&select=user_id,goal_time,profiles(name,xp,level)",
                headers=SB_HEADERS,
            ),
        )
    races = race_r.json() if race_r.status_code == 200 else []
    regs = reg_r.json() if reg_r.status_code == 200 else []
    athletes = [
        {
            "user_id": reg.get("user_id"),
            "name": (reg.get("profiles") or {}).get("name", "Atleta"),
            "xp": (reg.get("profiles") or {}).get("xp", 0),
            "level": (reg.get("profiles") or {}).get("level", "beginner"),
            "goal_time": reg.get("goal_time"),
        }
        for reg in regs
    ]
    return {"race": races[0] if races else None, "athletes": athletes}


@app.post("/app/race-catalog/{race_id}/register")
async def register_for_race(race_id: str, request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    goal_time = body.get("goal_time")
    if not user_id or not SUPABASE_URL:
        return {"success": False, "error": "missing user_id"}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/race_registrations",
            headers={**SB_HEADERS, "Prefer": "resolution=merge-duplicates"},
            json={"race_id": race_id, "user_id": user_id, "goal_time": goal_time},
        )
    return {"success": r.status_code in (200, 201)}


# ─── Ranking ──────────────────────────────────────────────────────────────────

@app.get("/app/ranking/worldwide/{modality}")
async def get_worldwide_ranking(modality: str):
    if not SUPABASE_URL:
        return {"ranking": []}
    params = "select=id,name,xp,level&order=xp.desc&limit=50"
    if modality and modality != "all":
        params += f"&preferred_discipline=eq.{modality}"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?{params}",
            headers=SB_HEADERS,
        )
    if r.status_code != 200:
        return {"ranking": []}
    profiles = r.json()
    return {
        "ranking": [
            {**p, "position": i + 1}
            for i, p in enumerate(profiles)
        ]
    }


@app.get("/app/ranking/friends/{user_id}/{modality}")
async def get_friends_ranking(user_id: str, modality: str):
    if not SUPABASE_URL:
        return {"ranking": []}
    async with httpx.AsyncClient() as client:
        fs_r = await client.get(
            f"{SUPABASE_URL}/rest/v1/friendships?status=eq.accepted&select=requester_id,addressee_id&or=(requester_id.eq.{user_id},addressee_id.eq.{user_id})",
            headers=SB_HEADERS,
        )
    friendships = fs_r.json() if fs_r.status_code == 200 else []
    friend_ids = set()
    for fs in friendships:
        fid = fs["addressee_id"] if fs["requester_id"] == user_id else fs["requester_id"]
        friend_ids.add(fid)
    friend_ids.add(user_id)

    if not friend_ids:
        return {"ranking": []}

    id_filter = ",".join(friend_ids)
    params = f"select=id,name,xp,level&id=in.({id_filter})&order=xp.desc"
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/profiles?{params}",
            headers=SB_HEADERS,
        )
    if r.status_code != 200:
        return {"ranking": []}
    profiles = r.json()
    return {
        "ranking": [
            {**p, "position": i + 1, "is_me": p["id"] == user_id}
            for i, p in enumerate(profiles)
        ]
    }


# ─── Profile (extended) ───────────────────────────────────────────────────────

@app.get("/app/profile/{user_id}")
async def get_public_profile(user_id: str):
    if not SUPABASE_URL:
        return {"profile": None}
    async with httpx.AsyncClient() as client:
        prof_r, gam_r, race_r, act_r = await asyncio.gather(
            client.get(
                f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}&select=id,name,level,race_type,race_date,ftp,max_hr,weight_kg,avatar_url,cover_url,bio,location,strava_connected",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/user_gamification?user_id=eq.{user_id}&select=xp_total,streak_days,level_name",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/race_results?user_id=eq.{user_id}&select=id&order=race_date.desc",
                headers=SB_HEADERS,
            ),
            client.get(
                f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{user_id}&select=id,tss&order=date.desc&limit=90",
                headers=SB_HEADERS,
            ),
        )
    profiles = prof_r.json() if prof_r.status_code == 200 else []
    gam = gam_r.json()[0] if gam_r.status_code == 200 and gam_r.json() else {}
    races = race_r.json() if race_r.status_code == 200 else []
    acts = act_r.json() if act_r.status_code == 200 else []
    if not profiles:
        return {"profile": None}
    p = profiles[0]
    K_CTL = 1 - (1 / 42)
    ctl = 0.0
    for a in reversed(acts):
        tss = a.get("tss") or 0
        ctl = ctl * K_CTL + tss * (1 - K_CTL)
    return {
        "profile": {
            **p,
            "xp_total": gam.get("xp_total", 0),
            "streak_days": gam.get("streak_days", 0),
            "level_name": gam.get("level_name"),
            "races_completed": len(races),
            "ctl": round(ctl, 1),
        }
    }


@app.put("/app/profile/{user_id}")
async def update_profile(user_id: str, request: Request):
    body = await request.json()
    allowed = {"name", "avatar_url", "cover_url", "bio", "location"}
    patch = {k: v for k, v in body.items() if k in allowed}
    if not patch or not SUPABASE_URL:
        return {"success": False, "error": "nothing to update"}
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/profiles?id=eq.{user_id}",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json=patch,
        )
    if r.status_code in (200, 204):
        data = r.json()
        return {"success": True, "profile": data[0] if data else patch}
    return {"success": False, "error": r.text}


# ─── Devices ──────────────────────────────────────────────────────────────────

@app.get("/app/devices/{user_id}")
async def get_devices(user_id: str):
    if not SUPABASE_URL:
        return {"devices": []}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/user_devices?user_id=eq.{user_id}&select=*&order=created_at.desc",
            headers=SB_HEADERS,
        )
    return {"devices": r.json() if r.status_code == 200 else []}


@app.post("/app/devices/add")
async def add_device(request: Request):
    body = await request.json()
    user_id = body.get("user_id")
    device_type = body.get("device_type")
    if not user_id or not device_type or not SUPABASE_URL:
        return {"success": False, "error": "missing fields"}
    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "user_id": user_id,
        "device_type": device_type,
        "sync_enabled": True,
        "connected": True,
        "last_sync": now_iso,
        "created_at": now_iso,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/user_devices",
            headers={**SB_HEADERS, "Prefer": "return=representation"},
            json=payload,
        )
    if r.status_code in (200, 201):
        data = r.json()
        return {"success": True, "device": data[0] if data else payload}
    return {"success": False, "error": r.text}


@app.delete("/app/devices/{device_id}")
async def remove_device(device_id: str):
    if not SUPABASE_URL:
        return {"success": False}
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{SUPABASE_URL}/rest/v1/user_devices?id=eq.{device_id}",
            headers=SB_HEADERS,
        )
    return {"success": r.status_code in (200, 204)}


@app.post("/app/devices/{device_id}/sync")
async def sync_device(device_id: str):
    if not SUPABASE_URL:
        return {"success": False}
    now_iso = datetime.now(timezone.utc).isoformat()
    async with httpx.AsyncClient() as client:
        r = await client.patch(
            f"{SUPABASE_URL}/rest/v1/user_devices?id=eq.{device_id}",
            headers=SB_HEADERS,
            json={"last_sync": now_iso},
        )
    return {"success": r.status_code in (200, 204), "last_sync": now_iso}


# ─── Progress ─────────────────────────────────────────────────────────────────

@app.get("/app/progress/ctl-atl-tsb/{user_id}")
async def get_progress_fitness(user_id: str, days: int = 90):
    from datetime import date as _date, timedelta as _td
    from collections import defaultdict
    import math
    if not SUPABASE_URL:
        return {"history": []}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{user_id}&select=date,tss,duration_min&order=date.asc&limit=500",
            headers=SB_HEADERS,
        )
    acts = r.json() if r.status_code == 200 else []

    daily_tss: dict = defaultdict(float)
    for a in acts:
        raw_date = str(a.get("date", ""))[:10]
        tss = a.get("tss") or round((a.get("duration_min") or 0) * 0.8)
        if raw_date:
            daily_tss[raw_date] += tss

    K_CTL = 1 - math.exp(-1 / 42)
    K_ATL = 1 - math.exp(-1 / 7)
    ctl, atl = 0.0, 0.0

    today = _date.today()
    window_start = today - _td(days=days - 1)

    # Warm up CTL/ATL on data before the window
    all_sorted = sorted(daily_tss.keys())
    if all_sorted:
        d = _date.fromisoformat(all_sorted[0])
        while d < window_start:
            tss = daily_tss.get(d.isoformat(), 0)
            ctl = ctl + K_CTL * (tss - ctl)
            atl = atl + K_ATL * (tss - atl)
            d += _td(days=1)

    history = []
    d = window_start
    while d <= today:
        tss = daily_tss.get(d.isoformat(), 0)
        ctl = ctl + K_CTL * (tss - ctl)
        atl = atl + K_ATL * (tss - atl)
        history.append({
            "date": d.isoformat(),
            "ctl": round(ctl, 1),
            "atl": round(atl, 1),
            "tsb": round(ctl - atl, 1),
            "tss": round(tss, 1),
        })
        d += _td(days=1)

    return {"history": history}


@app.get("/app/progress/activities-summary/{user_id}")
async def get_activities_summary(user_id: str):
    from collections import defaultdict
    if not SUPABASE_URL:
        return {"summary": [], "totals": {}}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{user_id}&select=discipline,distance_km,duration_min,tss&limit=500",
            headers=SB_HEADERS,
        )
    acts = r.json() if r.status_code == 200 else []

    by_disc: dict = defaultdict(lambda: {"count": 0, "total_km": 0.0, "total_min": 0, "total_tss": 0})
    for a in acts:
        disc = a.get("discipline") or "other"
        by_disc[disc]["count"] += 1
        by_disc[disc]["total_km"] += a.get("distance_km") or 0
        by_disc[disc]["total_min"] += a.get("duration_min") or 0
        by_disc[disc]["total_tss"] += a.get("tss") or 0

    summary = [
        {
            "discipline": disc,
            "count": v["count"],
            "total_km": round(v["total_km"], 1),
            "total_hours": round(v["total_min"] / 60, 1),
            "total_tss": round(v["total_tss"]),
        }
        for disc, v in sorted(by_disc.items(), key=lambda x: -x[1]["total_min"])
    ]
    return {
        "summary": summary,
        "totals": {
            "activities": len(acts),
            "total_hours": round(sum(a.get("duration_min") or 0 for a in acts) / 60, 1),
            "total_km": round(sum(a.get("distance_km") or 0 for a in acts), 1),
            "total_tss": round(sum(a.get("tss") or 0 for a in acts)),
        },
    }


@app.get("/app/progress/prs/{user_id}")
async def get_personal_records(user_id: str):
    if not SUPABASE_URL:
        return {"prs": []}
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/activities?user_id=eq.{user_id}&select=discipline,distance_km,duration_min,date&limit=500",
            headers=SB_HEADERS,
        )
    acts = r.json() if r.status_code == 200 else []

    best_dist: dict = {}
    best_pace: dict = {}
    for a in acts:
        disc = a.get("discipline") or "other"
        km = a.get("distance_km") or 0
        dur = a.get("duration_min") or 0
        if km <= 0 or dur <= 0:
            continue
        if disc not in best_dist or km > best_dist[disc]["km"]:
            best_dist[disc] = {"km": km, "dur": dur, "date": a.get("date", "")}
        pace = dur / km
        if disc not in best_pace or pace < best_pace[disc]["pace"]:
            best_pace[disc] = {"pace": pace, "km": km, "dur": dur, "date": a.get("date", "")}

    prs = []
    for disc, b in best_dist.items():
        pr: dict = {
            "discipline": disc,
            "best_km": round(b["km"], 2),
            "best_duration_min": b["dur"],
            "best_date": b["date"],
        }
        if disc in best_pace:
            f = best_pace[disc]
            total_sec = f["pace"] * 60
            pm, ps = int(total_sec // 60), int(total_sec % 60)
            pr["fastest_pace"] = f"{pm}:{str(ps).zfill(2)} /km"
            pr["fastest_pace_km"] = round(f["km"], 2)
            pr["fastest_pace_date"] = f["date"]
        prs.append(pr)

    order = {"swim": 0, "bike": 1, "run": 2, "gym": 3, "brick": 4}
    return {"prs": sorted(prs, key=lambda x: order.get(x["discipline"], 5))}


# ─── Gym Virtual Coach ────────────────────────────────────────────────────────

@app.post("/app/gym/coach-message")
async def gym_coach_message(request: Request):
    """Generate a real-time coaching message during a gym set."""
    body = await request.json()
    exercise    = body.get("exercise_name", "ejercicio")
    current_set = int(body.get("current_set", 1))
    total_sets  = int(body.get("total_sets", 4))
    reps        = body.get("reps", "10")
    weight      = float(body.get("weight_kg", 0))
    phase       = body.get("phase", "pre")   # pre | post_set | rest | finish
    rpe         = int(body.get("rpe", 7))
    history     = body.get("history_summary", "")

    weight_str = f"{weight} kg" if weight > 0 else "peso corporal"

    if phase == "pre":
        prompt = (
            f"El atleta va a hacer {exercise}. {total_sets} series de {reps} reps a {weight_str}. "
            f"Da un mensaje motivacional muy corto (máx 2 frases) con el cue de técnica más importante para este ejercicio. "
            f"Sin emojis. Habla de tú, segunda persona. Español."
        )
    elif phase == "post_set":
        prompt = (
            f"El atleta completó la serie {current_set} de {total_sets} de {exercise} a {weight_str}. "
            f"RPE: {rpe}/10. {'Historial previo: ' + history if history else ''}"
            f"Da feedback concreto de 1-2 frases sobre el esfuerzo y qué ajustar en la siguiente serie. "
            f"Sin emojis. Segunda persona. Español."
        )
    elif phase == "rest":
        prompt = (
            f"El atleta descansa entre series de {exercise}. "
            f"Da un tip de recuperación o de técnica para la próxima serie. Máx 1 frase. Sin emojis. Español."
        )
    else:  # finish
        prompt = (
            f"El atleta completó {total_sets} series de {exercise} a {weight_str}. "
            f"RPE medio {rpe}/10. {'Progreso: ' + history if history else ''}"
            f"Celébralo brevemente y da un tip de mejora para la próxima sesión. Máx 2 frases. Sin emojis. Español."
        )

    try:
        import anthropic as _anthropic
        ai_client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        msg = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            system="Eres un coach de gimnasio experto para triatletas. Responde siempre en español, de forma breve y directa.",
            messages=[{"role": "user", "content": prompt}],
        )
        message = msg.content[0].text.strip()
    except Exception:
        # Fallback messages
        fallbacks = {
            "pre":      f"Vamos con {exercise}. Activa el core y controla el tempo.",
            "post_set": f"Serie {current_set} completada. {'Buen esfuerzo.' if rpe >= 7 else 'Puedes subir el peso.'}",
            "rest":     "Respira profundo. Prepara la posición para la siguiente.",
            "finish":   f"{exercise} completado. ¡Buen trabajo hoy!",
        }
        message = fallbacks.get(phase, "Sigue así.")

    return {"message": message}
