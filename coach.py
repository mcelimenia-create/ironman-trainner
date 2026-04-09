import anthropic
import os
from sqlalchemy.orm import Session
from database import Training, DailyMetrics, Injury, Conversation
from datetime import datetime, timedelta

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """Eres el entrenador personal profesional de Ironman 70.3 de este atleta.

PERFIL:
- Objetivo: Completar Ironman 70.3 (1.9km natación / 90km bici / 21km carrera)
- Rodilla en recuperación — siempre 15 min de trabajo específico
- Hablas siempre en español, directo y motivador

ACCESO A STRAVA:
- SÍ tienes acceso a Strava del atleta mediante webhook automático
- Cada vez que el atleta sube una actividad a Strava, te llega automáticamente con todos los datos
- NO necesitas que el atleta te pase los datos manualmente si usa Strava
- Cuando recibes datos con el prefijo "📡 Actividad de Strava registrada automáticamente", son datos reales de Strava

CUANDO EL ATLETA COMPARTA DATOS, extrae y devuelve un JSON al inicio de tu respuesta:
<data>
{
  "type": "training|metrics|injury|none",
  "discipline": "swim|bike|run|gym|null",
  "duration_min": number|null,
  "distance_km": number|null,
  "avg_hr": number|null,
  "weight_kg": number|null,
  "sleep_hours": number|null,
  "legs_score": number|null,
  "energy_score": number|null,
  "injury_zone": "string|null",
  "injury_intensity": number|null
}
</data>

TSS POR SESIÓN:
- Running: (duración_h × FC_media/190) × 100
- Natación: distancia_km × 10
- Bici: (duración_h × FC_media/190) × 100
- Gym: 40

CTL/ATL/TSB:
- CTL = CTL_ayer × 0.976 + TSS_hoy × 0.024
- ATL = ATL_ayer × 0.866 + TSS_hoy × 0.134
- TSB = CTL - ATL

SEÑALES DE ALERTA:
- TSB < -20: riesgo sobreentrenamiento
- Dolor durante ejercicio o >48h: reducir running
- FC reposo +5-7 bpm: reducir semana entera
"""


def get_recent_context(db: Session, user_id: str) -> str:
    """Obtiene estadísticas recientes para añadir al contexto."""
    # Últimos 30 días de entrenamientos
    since = datetime.utcnow() - timedelta(days=30)
    trainings = db.query(Training).filter(
        Training.user_id == user_id,
        Training.date >= since
    ).order_by(Training.date.desc()).limit(10).all()

    # CTL/ATL/TSB actual
    latest = db.query(DailyMetrics).filter(
        DailyMetrics.user_id == user_id
    ).order_by(DailyMetrics.date.desc()).first()

    ctx = ""
    if latest:
        tsb = latest.tsb or 0
        emoji = "🟢" if tsb > 0 else "🟡" if tsb > -10 else "🟠" if tsb > -20 else "🔴"
        ctx += f"\n📊 FORMA ACTUAL: CTL={latest.ctl:.1f} | ATL={latest.atl:.1f} | TSB={tsb:.1f} {emoji}"
        if latest.weight_kg:
            ctx += f" | Peso: {latest.weight_kg}kg"

    if trainings:
        ctx += f"\n📅 Últimos entrenamientos ({len(trainings)}):"
        for t in trainings[:5]:
            ctx += f"\n  - {t.date.strftime('%d/%m')} {t.discipline}: {t.duration_min}min"

    # Lesiones activas
    injuries = db.query(Injury).filter(
        Injury.user_id == user_id,
        Injury.date >= datetime.utcnow() - timedelta(days=14)
    ).all()
    if injuries:
        ctx += f"\n⚠️ Lesiones recientes: {', '.join(i.zone for i in injuries)}"

    return ctx


def get_conversation_history(db: Session, user_id: str, limit: int = 10):
    """Recupera historial de conversación."""
    msgs = db.query(Conversation).filter(
        Conversation.user_id == user_id
    ).order_by(Conversation.timestamp.desc()).limit(limit).all()
    return [{"role": m.role, "content": m.content} for m in reversed(msgs)]


def save_message(db: Session, user_id: str, role: str, content: str):
    msg = Conversation(user_id=user_id, role=role, content=content)
    db.add(msg)
    db.commit()


def update_ctl_atl(db: Session, user_id: str, tss: float):
    """Actualiza CTL/ATL/TSB con el TSS de la sesión."""
    latest = db.query(DailyMetrics).filter(
        DailyMetrics.user_id == user_id
    ).order_by(DailyMetrics.date.desc()).first()

    ctl = (latest.ctl if latest else 0) * 0.976 + tss * 0.024
    atl = (latest.atl if latest else 0) * 0.866 + tss * 0.134
    tsb = ctl - atl

    today = db.query(DailyMetrics).filter(
        DailyMetrics.user_id == user_id,
        DailyMetrics.date >= datetime.utcnow().replace(hour=0, minute=0)
    ).first()

    if today:
        today.ctl = ctl
        today.atl = atl
        today.tsb = tsb
    else:
        record = DailyMetrics(user_id=user_id, ctl=ctl, atl=atl, tsb=tsb)
        db.add(record)
    db.commit()
    return ctl, atl, tsb


def save_training_data(db: Session, user_id: str, data: dict, tss: float):
    training = Training(
        user_id=user_id,
        discipline=data.get("discipline"),
        duration_min=data.get("duration_min"),
        distance_km=data.get("distance_km"),
        avg_hr=data.get("avg_hr"),
        tss=tss,
        notes=""
    )
    db.add(training)
    db.commit()


async def ask_coach(db: Session, user_id: str, user_message: str) -> str:
    import json, re

    # Contexto con datos recientes
    context = get_recent_context(db, user_id)
    history = get_conversation_history(db, user_id)

    # Guardar mensaje del usuario
    save_message(db, user_id, "user", user_message)

    # Construir mensajes
    messages = history + [{"role": "user", "content": user_message}]

    system = SYSTEM_PROMPT
    if context:
        system += f"\n\nCONTEXTO ACTUAL DEL ATLETA:{context}"

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=system,
        messages=messages
    )

    full_response = response.content[0].text

    # Extraer y guardar datos estructurados
    data_match = re.search(r"<data>(.*?)</data>", full_response, re.DOTALL)
    if data_match:
        try:
            data = json.loads(data_match.group(1).strip())
            dtype = data.get("type")

            if dtype == "training":
                # Calcular TSS
                dur_h = (data.get("duration_min") or 0) / 60
                hr = data.get("avg_hr") or 140
                dist = data.get("distance_km") or 0
                disc = data.get("discipline", "")

                if disc == "swim":
                    tss = dist * 10
                elif disc == "gym":
                    tss = 40
                else:
                    tss = dur_h * (hr / 190) * 100

                save_training_data(db, user_id, data, tss)
                update_ctl_atl(db, user_id, tss)

            elif dtype == "metrics":
                today = db.query(DailyMetrics).filter(
                    DailyMetrics.user_id == user_id,
                    DailyMetrics.date >= datetime.utcnow().replace(hour=0, minute=0)
                ).first()
                if not today:
                    today = DailyMetrics(user_id=user_id)
                    db.add(today)
                if data.get("weight_kg"):
                    today.weight_kg = data["weight_kg"]
                if data.get("sleep_hours"):
                    today.sleep_hours = data["sleep_hours"]
                if data.get("legs_score"):
                    today.legs_score = data["legs_score"]
                if data.get("energy_score"):
                    today.energy_score = data["energy_score"]
                db.commit()

            elif dtype == "injury":
                inj = Injury(
                    user_id=user_id,
                    zone=data.get("injury_zone", ""),
                    intensity=data.get("injury_intensity", 0),
                    notes=user_message
                )
                db.add(inj)
                db.commit()

        except Exception as e:
            print(f"Error parsing data: {e}")

    # Limpiar el JSON de la respuesta que ve el usuario
    clean_response = re.sub(r"<data>.*?</data>", "", full_response, flags=re.DOTALL).strip()

    # Guardar respuesta del asistente
    save_message(db, user_id, "assistant", clean_response)

    return clean_response