import os
import io
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
    next_sessions = body.get("next_sessions", [])

    race_names = {"sprint": "Sprint", "olympic": "Triatlón Olímpico", "half_ironman": "Ironman 70.3", "full_ironman": "Ironman Full"}
    race_label = race_names.get(race_type, race_type)

    prompt = f"""Eres un entrenador de triatlón de élite. Analiza el estado actual del atleta y da recomendaciones concretas y personalizadas en español.

DATOS DEL ATLETA:
- Carrera objetivo: {race_label} en {days_to_race} días
- Forma física (CTL): {ctl} TSS/día — fitness crónica acumulada
- Fatiga (ATL): {atl} TSS/día — carga de los últimos 7 días
- Forma (TSB): {tsb} — positivo=fresco, negativo=cansado
- Volumen esta semana: Natación {swim_km}km · Bici {bike_km}km · Carrera {run_km}km · Total {hours}h
- Próximas sesiones: {', '.join(next_sessions) if next_sessions else 'sin sesiones programadas'}

INSTRUCCIONES:
- Responde en exactamente 3-4 puntos numerados (1. 2. 3. 4.)
- Cada punto en una línea separada
- Sin asteriscos, sin markdown, sin negritas, sin guiones
- Sin introducción ni cierre, ve directo a los puntos
- Usa un emoji al inicio de cada punto
- Máximo 180 palabras en total"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        advice = message.content[0].text
        return {"advice": advice}
    except Exception as e:
        return {"error": str(e)}
