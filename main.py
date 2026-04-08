import os
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from database import init_db, SessionLocal
from coach import ask_coach
from strava import (
    STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_VERIFY_TOKEN,
    exchange_code, get_activity_detail, save_strava_token,
    get_strava_token, format_activity_message
)

app = FastAPI()
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BASE_URL = os.environ.get("BASE_URL", "https://ironman-trainner-production.up.railway.app")

# Mapeo telegram_user_id → strava_athlete_id (en memoria, suficiente para uso personal)
strava_user_map = {}


@app.on_event("startup")
def startup():
    init_db()


async def send_message(chat_id: int, text: str):
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })


# ─── TELEGRAM WEBHOOK ────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_id = str(message.get("from", {}).get("id", chat_id))
    text = message.get("text", "")

    if not chat_id:
        return {"ok": True}

    if text == "/start":
        await send_message(chat_id, "💪 *¡Hola, campeón!* Soy tu entrenador Ironman. Cuéntame cómo va el entrenamiento.")
        return {"ok": True}

    if text == "/strava":
        auth_url = (
            f"https://www.strava.com/oauth/authorize"
            f"?client_id={STRAVA_CLIENT_ID}"
            f"&redirect_uri={BASE_URL}/strava/callback/{user_id}"
            f"&response_type=code"
            f"&scope=activity:read_all"
        )
        await send_message(chat_id, f"🔗 Conecta tu Strava aquí:\n{auth_url}")
        return {"ok": True}

    if text == "/stats":
        db = SessionLocal()
        from database import DailyMetrics
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

    # Respuesta del coach
    db = SessionLocal()
    try:
        response = await ask_coach(db, user_id, text)
        await send_message(chat_id, response)
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

    # Notificar al usuario por Telegram
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


@app.post("/strava/webhook")
async def strava_webhook(request: Request):
    """Recibe eventos de Strava cuando hay una actividad nueva."""
    data = await request.json()

    if data.get("object_type") != "activity" or data.get("aspect_type") != "create":
        return {"ok": True}

    athlete_id = str(data.get("owner_id"))
    activity_id = data.get("object_id")

    # Buscar el user_id de Telegram asociado
    user_id = strava_user_map.get(athlete_id)
    if not user_id:
        # Intentar cargar desde tokens guardados
        import json, os
        if os.path.exists("/tmp/strava_tokens.json"):
            with open("/tmp/strava_tokens.json") as f:
                tokens = json.load(f)
            for uid, token_data in tokens.items():
                if str(token_data.get("athlete", {}).get("id")) == athlete_id:
                    user_id = uid
                    strava_user_map[athlete_id] = uid
                    break

    if not user_id:
        print(f"Atleta {athlete_id} no vinculado a ningún usuario Telegram")
        return {"ok": True}

    # Obtener token de acceso
    token_data = get_strava_token(user_id)
    if not token_data:
        return {"ok": True}

    # Obtener detalles de la actividad
    try:
        activity = await get_activity_detail(activity_id, token_data["access_token"])
        activity_msg = format_activity_message(activity)

        # Notificar al usuario
        await send_message(int(user_id), activity_msg)

        # Pasar al coach para análisis
        db = SessionLocal()
        try:
            coach_response = await ask_coach(db, user_id, activity_msg)
            await send_message(int(user_id), coach_response)
        finally:
            db.close()

    except Exception as e:
        print(f"Error procesando actividad Strava: {e}")

    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}