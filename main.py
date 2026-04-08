import os
import json
import httpx
from fastapi import FastAPI, Request
from database import init_db, SessionLocal
from coach import ask_coach

app = FastAPI()
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


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


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    user_id = str(message.get("from", {}).get("id", chat_id))

    if not text or not chat_id:
        return {"ok": True}

    # Comandos especiales
    if text == "/start":
        await send_message(chat_id, "💪 *¡Hola, campeón!* Soy tu entrenador Ironman. Cuéntame cómo va el entrenamiento.")
        return {"ok": True}

    if text == "/stats":
        db = SessionLocal()
        from database import DailyMetrics, Training
        latest = db.query(DailyMetrics).filter(
            DailyMetrics.user_id == user_id
        ).order_by(DailyMetrics.date.desc()).first()
        db.close()
        if latest:
            tsb = latest.tsb or 0
            emoji = "🟢" if tsb > 0 else "🟡" if tsb > -10 else "🟠" if tsb > -20 else "🔴"
            stats = f"📊 *Tu forma física:*\nCTL: {latest.ctl:.1f}\nATL: {latest.atl:.1f}\nTSB: {tsb:.1f} {emoji}"
        else:
            stats = "Aún no hay datos registrados. ¡Cuéntame tu primer entrenamiento!"
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


@app.get("/health")
def health():
    return {"status": "ok"}