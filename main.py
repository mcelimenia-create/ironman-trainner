import os
import httpx
from fastapi import FastAPI, Request
from database import init_db, SessionLocal
from coach import ask_coach
from openai import OpenAI

app = FastAPI()
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
whisper_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


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


async def transcribe_voice(file_id: str) -> str:
    """Descarga el audio de Telegram y lo transcribe con Whisper."""
    async with httpx.AsyncClient() as client:
        # 1. Obtener la ruta del archivo
        r = await client.get(f"{TELEGRAM_API}/getFile?file_id={file_id}")
        file_path = r.json()["result"]["file_path"]

        # 2. Descargar el archivo
        audio_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        audio_response = await client.get(audio_url)
        audio_bytes = audio_response.content

    # 3. Transcribir con Whisper
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    with open(tmp_path, "rb") as audio_file:
        transcription = whisper_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="es"
        )

    os.unlink(tmp_path)
    return transcription.text


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    user_id = str(message.get("from", {}).get("id", chat_id))

    if not chat_id:
        return {"ok": True}

    # Detectar si es audio/voz
    text = message.get("text", "")
    voice = message.get("voice") or message.get("audio")

    if voice:
        await send_message(chat_id, "🎤 Escuchando...")
        try:
            text = await transcribe_voice(voice["file_id"])
            await send_message(chat_id, f"📝 _{text}_")  # Muestra la transcripción
        except Exception as e:
            await send_message(chat_id, "⚠️ No pude entender el audio. Inténtalo de nuevo.")
            print(f"Whisper error: {e}")
            return {"ok": True}

    if not text:
        return {"ok": True}

    # Comandos especiales
    if text == "/start":
        await send_message(chat_id, "💪 *¡Hola, campeón!* Soy tu entrenador Ironman. Cuéntame cómo va el entrenamiento.")
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