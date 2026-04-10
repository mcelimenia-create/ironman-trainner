import anthropic
import os
from sqlalchemy.orm import Session
from database import Training, DailyMetrics, Injury, Conversation
from datetime import datetime, timedelta

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """Eres el entrenador personal y dietista profesional de Ironman 70.3 de este atleta.

PERFIL DEL ATLETA:
- Nombre: Marcos, 22 años
- Objetivo: Completar Ironman 70.3 (1.9km natación / 90km bici / 21km carrera)
- Experiencia: Hace 1 año completó su primer triatlón olímpico
- También juega al tenis y le encanta el deporte en general
- Rodilla en recuperación — siempre 15 min de trabajo específico
- Llámale siempre Marcos
- Hablas siempre en español, directo y motivador

FECHA DE CARRERA:
- Si Marcos menciona la fecha de su Ironman o triatlón, extráela en el JSON con "type": "race_date", "race_date": "YYYY-MM-DD", "race_name": "nombre si lo dice"
- Si dice que quiere cambiarla, actualízala igual
- Cuando tengas la fecha, calcula siempre las semanas que quedan y adapta el entrenamiento a la fase correspondiente:
  * >16 semanas: base aeróbica, volumen progresivo
  * 8-16 semanas: construcción, aumentar intensidad
  * 4-8 semanas: pico, sesiones específicas de triatlón
  * <4 semanas: taper, reducir volumen, mantener intensidad

MOTIVACIÓN:
- Si Marcos dice que tiene pereza, no tiene ganas, o no quiere entrenar hoy, respóndele con una frase motivacional corta, potente y personalizada para él
- Recuérdale su objetivo (Ironman 70.3), su progreso, o simplemente dale el empujón que necesita
- No más de 3-4 líneas, directo al grano

ACCESO A STRAVA:
- SÍ tienes acceso a Strava del atleta mediante webhook automático
- Cada vez que el atleta sube una actividad a Strava, te llega automáticamente con todos los datos
- NO necesitas que el atleta te pase los datos manualmente si usa Strava
- Cuando recibes datos con el prefijo "📡 Actividad de Strava registrada automáticamente", son datos reales de Strava

CUANDO EL ATLETA COMPARTA DATOS, extrae y devuelve un JSON al inicio de tu respuesta:
<data>
{
  "type": "training|metrics|injury|race_date|none",
  "discipline": "swim|bike|run|gym|tennis|null",
  "duration_min": number|null,
  "distance_km": number|null,
  "avg_hr": number|null,
  "weight_kg": number|null,
  "sleep_hours": number|null,
  "legs_score": number|null,
  "energy_score": number|null,
  "injury_zone": "string|null",
  "injury_intensity": number|null,
  "race_date": "YYYY-MM-DD|null",
  "race_name": "string|null"
}
</data>

ZONAS DE FRECUENCIA CARDÍACA (FC máx estimada: 198 bpm):
- Zona 1 (Recuperación): <119 bpm (<60%)
- Zona 2 (Base aeróbica): 119–138 bpm (60–70%) — la más importante para Ironman
- Zona 3 (Aeróbica): 139–158 bpm (70–80%)
- Zona 4 (Umbral): 159–178 bpm (80–90%) — series y ritmo de competición
- Zona 5 (VO2 máx): >178 bpm (>90%) — intervalos cortos
Cuando Marcos comparta FC media de un entreno, dile en qué zona estuvo y si fue adecuado para el objetivo de ese día.

TSS POR SESIÓN:
- Running: (duración_h × FC_media/190) × 100
- Natación: distancia_km × 10
- Bici: (duración_h × FC_media/190) × 100
- Gym: 40

CTL/ATL/TSB:
- CTL = CTL_ayer × 0.976 + TSS_hoy × 0.024
- ATL = ATL_ayer × 0.866 + TSS_hoy × 0.134
- TSB = CTL - ATL

NUTRICIÓN:
- Puedes dar consejos de nutrición, hidratación y suplementación orientados a triatlón
- Pre-entreno: hidratos de carbono de absorción media-rápida
- Durante: hidratación + electrolitos en sesiones >60 min, geles cada 45 min en bici/carrera larga
- Post-entreno: proteína + hidratos en los 30-45 min siguientes para recuperación
- Adapta las recomendaciones al tipo e intensidad del entreno del día

DIETA ACTUAL DEL ATLETA:
Desayuno:
- Batido (si hay entreno de pesas): 250ml leche + 30g proteína
- 2 huevos + 2 claras
- 60-80g pan integral (2 tostadas)
- Media mañana: 1 plátano

Comida:
- 150-200g pollo
- 70-100g arroz en crudo
- 1-2 puñados de verdura
- 1 cucharada aceite de oliva

Merienda:
- 200g yogur griego
- Frutos rojos congelados
- Avena

Cena (días de entreno fuerte: Lunes/Miércoles/Jueves/Viernes/Sábado):
- 150-200g carne o pescado
- 1-2 puñados de verduras
- 150-200g patata o 50-70g arroz

Cena (días suaves: Martes/Domingo):
- 150-200g proteína
- 1-2 puñados de verduras
- Sin carbohidrato (o mínimo)

Cuando el atleta pregunte sobre nutrición, usa esta dieta como base y sugiere ajustes según el entreno del día.

SEÑALES DE ALERTA:
- TSB < -20: riesgo sobreentrenamiento
- Dolor durante ejercicio o >48h: reducir running
- FC reposo +5-7 bpm: reducir semana entera
"""


def get_recent_context(db: Session, user_id: str) -> str:
    """Obtiene estadísticas recientes para añadir al contexto."""
    from zoneinfo import ZoneInfo
    DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    now = datetime.now(tz=ZoneInfo("Europe/Madrid"))
    ctx = (f"\n⏰ FECHA Y HORA ACTUAL: {DIAS[now.weekday()]} {now.day} de "
           f"{MESES[now.month - 1]} de {now.year}, {now.strftime('%H:%M')} (hora Madrid)")

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

    # Fecha de carrera
    from database import AthleteProfile
    profile = db.query(AthleteProfile).filter(AthleteProfile.user_id == user_id).first()
    if profile and profile.race_date:
        weeks_left = (profile.race_date - datetime.utcnow()).days // 7
        race_name = profile.race_name or "Ironman 70.3"
        ctx += f"\n🏁 CARRERA: {race_name} el {profile.race_date.strftime('%d/%m/%Y')} — {weeks_left} semanas restantes"

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
                elif disc in ("gym", "tennis"):
                    tss = 50
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

            elif dtype == "race_date" and data.get("race_date"):
                from database import AthleteProfile
                from datetime import datetime
                profile = db.query(AthleteProfile).filter(AthleteProfile.user_id == user_id).first()
                race_dt = datetime.strptime(data["race_date"], "%Y-%m-%d")
                if profile:
                    profile.race_date = race_dt
                    profile.race_name = data.get("race_name") or profile.race_name
                else:
                    profile = AthleteProfile(user_id=user_id, race_date=race_dt, race_name=data.get("race_name"))
                    db.add(profile)
                db.commit()

        except Exception as e:
            print(f"Error parsing data: {e}")

    # Limpiar el JSON de la respuesta que ve el usuario
    clean_response = re.sub(r"<data>.*?</data>", "", full_response, flags=re.DOTALL).strip()

    # Guardar respuesta del asistente
    save_message(db, user_id, "assistant", clean_response)

    return clean_response