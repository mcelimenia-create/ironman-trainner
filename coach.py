import anthropic
import os
from sqlalchemy.orm import Session
from database import Training, DailyMetrics, Injury, Conversation, RaceResult, MemoryNote, PlannedSession
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ_MADRID = ZoneInfo("Europe/Madrid")

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """Eres el entrenador personal y dietista profesional de Ironman 70.3 de este atleta.

FECHA Y HORA:
- En el contexto siempre recibirás la fecha y hora exacta actual (Madrid).
- SIEMPRE úsala. Cuando Marcos pregunte qué toca hoy, qué hacer, o algo relacionado con el momento actual, menciona explícitamente el día y la hora ("Son las 14:32 del viernes...").
- Nunca uses fechas ni horas inventadas. Si no ves la fecha en el contexto, di que no tienes ese dato.

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

PLAN SEMANAL:
- Cuando generes un plan semanal (ya sea por /plan o cuando Marcos te lo pida), incluye SIEMPRE el bloque <data> con "type": "weekly_plan" Y las sesiones estructuradas.
- El plan debe cubrir exactamente los 7 días a partir del próximo lunes (o hoy si es lunes).
- Cada sesión debe tener fecha exacta en formato YYYY-MM-DD.
- Los días de descanso también se incluyen con discipline "rest".
- Tras guardar el plan puedes mostrarlo de forma legible al atleta.

CUANDO EL ATLETA COMPARTA DATOS, extrae y devuelve un JSON al inicio de tu respuesta:
<data>
{
  "type": "training|metrics|injury|race_date|race_result|memory|weekly_plan|none",
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
  "race_name": "string|null",
  "race_result_date": "YYYY-MM-DD|null",
  "race_result_name": "string|null",
  "race_result_type": "ironman|olimpico|sprint|maraton|media_maraton|10k|otro|null",
  "race_result_time": "HH:MM:SS|null",
  "race_result_position": "string|null",
  "race_result_notes": "string|null",
  "memory_key": "string|null",
  "memory_value": "string|null",
  "sessions": [
    {
      "date": "YYYY-MM-DD",
      "discipline": "swim|bike|run|gym|rest",
      "duration_min": number|null,
      "intensity": "Z1|Z2|Z3|Z4|series|fuerza|recuperación|null",
      "description": "descripción breve de la sesión"
    }
  ]
}
</data>

CUÁNDO USAR CADA TIPO:
- "race_result": cuando Marcos cuenta que completó/participó en una carrera o evento pasado (distinto de la próxima carrera)
- "memory": para guardar hechos importantes que debes recordar siempre (metas personales, PRs, datos relevantes del atleta). Usa una key descriptiva única, p.ej. "pr_10k", "peso_objetivo", "primer_triatlon"
- "race_date": solo para la PRÓXIMA carrera objetivo

SIEMPRE guarda como "memory" cualquier dato relevante que Marcos mencione y que no sea un entreno ni una métrica: su mejor marca, una carrera completada, su meta de peso, un hito importante.

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
    now_madrid = datetime.now(TZ_MADRID)
    now_utc = datetime.utcnow()

    # Fecha y hora actual (siempre presente)
    dia_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"][now_madrid.weekday()]
    ctx = f"\n🕐 FECHA Y HORA ACTUAL: {dia_semana} {now_madrid.strftime('%d/%m/%Y a las %H:%M')} (Madrid)"

    # Últimos 60 días de entrenamientos (todos, sin límite reducido)
    since = now_utc - timedelta(days=60)
    trainings = db.query(Training).filter(
        Training.user_id == user_id,
        Training.date >= since
    ).order_by(Training.date.desc()).all()

    # CTL/ATL/TSB actual
    latest = db.query(DailyMetrics).filter(
        DailyMetrics.user_id == user_id
    ).order_by(DailyMetrics.date.desc()).first()

    # Fecha de carrera
    from database import AthleteProfile
    profile = db.query(AthleteProfile).filter(AthleteProfile.user_id == user_id).first()
    if profile and profile.race_date:
        weeks_left = (profile.race_date - now_utc).days // 7
        days_left = (profile.race_date - now_utc).days
        race_name = profile.race_name or "Ironman 70.3"
        ctx += f"\n🏁 CARRERA: {race_name} el {profile.race_date.strftime('%d/%m/%Y')} — {weeks_left} semanas y {days_left % 7} días restantes"

    if latest:
        tsb = latest.tsb or 0
        emoji = "🟢" if tsb > 0 else "🟡" if tsb > -10 else "🟠" if tsb > -20 else "🔴"
        ctx += f"\n📊 FORMA ACTUAL: CTL={latest.ctl:.1f} | ATL={latest.atl:.1f} | TSB={tsb:.1f} {emoji}"
        if latest.weight_kg:
            ctx += f" | Peso: {latest.weight_kg}kg"

    if trainings:
        ctx += f"\n📅 Entrenamientos últimos 60 días ({len(trainings)} sesiones):"
        for t in trainings[:20]:
            dist = f" {t.distance_km:.1f}km" if t.distance_km else ""
            hr = f" FC:{t.avg_hr}bpm" if t.avg_hr else ""
            ctx += f"\n  - {t.date.strftime('%d/%m/%Y')} {t.discipline}: {t.duration_min}min{dist}{hr}"

    # Historial completo de pesos (todos los registros)
    all_weights = db.query(DailyMetrics).filter(
        DailyMetrics.user_id == user_id,
        DailyMetrics.weight_kg.isnot(None)
    ).order_by(DailyMetrics.date.asc()).all()
    if all_weights:
        ctx += f"\n⚖️ Historial de peso completo ({len(all_weights)} registros):"
        for w in all_weights:
            ctx += f"\n  - {w.date.strftime('%d/%m/%Y')}: {w.weight_kg}kg"

    # Historial de carreras completadas
    race_results = db.query(RaceResult).filter(
        RaceResult.user_id == user_id
    ).order_by(RaceResult.date.asc()).all()
    if race_results:
        ctx += f"\n🏅 Carreras completadas:"
        for r in race_results:
            time_str = f" — {r.finish_time}" if r.finish_time else ""
            pos_str = f" (pos. {r.position})" if r.position else ""
            notes_str = f" — {r.notes}" if r.notes else ""
            ctx += f"\n  - {r.date.strftime('%d/%m/%Y')} {r.race_name} ({r.race_type}){time_str}{pos_str}{notes_str}"

    # Notas de memoria permanentes
    memory_notes = db.query(MemoryNote).filter(
        MemoryNote.user_id == user_id
    ).order_by(MemoryNote.updated_at.asc()).all()
    if memory_notes:
        ctx += f"\n🧠 Memoria permanente:"
        for n in memory_notes:
            ctx += f"\n  - [{n.key}] {n.value}"

    # Lesiones (historial completo)
    injuries = db.query(Injury).filter(
        Injury.user_id == user_id,
        Injury.date >= now_utc - timedelta(days=180)
    ).order_by(Injury.date.desc()).all()
    if injuries:
        ctx += f"\n⚠️ Lesiones/molestias registradas:"
        for inj in injuries:
            ctx += f"\n  - {inj.date.strftime('%d/%m/%Y')} {inj.zone} (intensidad {inj.intensity}/10)"

    # Plan semanal activo (semana actual y siguiente si existe)
    week_start = (now_utc - timedelta(days=now_utc.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    plan_sessions = db.query(PlannedSession).filter(
        PlannedSession.user_id == user_id,
        PlannedSession.week_start >= week_start,
        PlannedSession.week_start < week_start + timedelta(days=14)
    ).order_by(PlannedSession.date.asc()).all()
    if plan_sessions:
        DIAS_ES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        ctx += f"\n📋 Plan semanal activo:"
        for s in plan_sessions:
            check = "✅" if s.completed else "⬜"
            dur = f" {s.duration_min}min" if s.duration_min else ""
            intens = f" [{s.intensity}]" if s.intensity else ""
            dia = DIAS_ES[s.date.weekday()]
            ctx += f"\n  {check} {dia} {s.date.strftime('%d/%m')} — {s.discipline}{dur}{intens}: {s.description or ''}"
        # Cumplimiento de la semana actual
        semana_actual = [s for s in plan_sessions if s.week_start.date() == week_start.date()]
        no_rest = [s for s in semana_actual if s.discipline != "rest"]
        completadas = [s for s in no_rest if s.completed]
        if no_rest:
            pct = int(len(completadas) / len(no_rest) * 100)
            ctx += f"\n  → Cumplimiento semana: {len(completadas)}/{len(no_rest)} sesiones ({pct}%)"

    return ctx


def get_conversation_history(db: Session, user_id: str, limit: int = 50):
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


def save_weekly_plan(db: Session, user_id: str, sessions: list):
    """Guarda el plan semanal, borrando el anterior de esa semana."""
    if not sessions:
        return
    dates = [datetime.strptime(s["date"], "%Y-%m-%d") for s in sessions]
    week_start = min(dates) - timedelta(days=min(dates).weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    # Borrar plan anterior de esa semana para reemplazarlo
    db.query(PlannedSession).filter(
        PlannedSession.user_id == user_id,
        PlannedSession.week_start == week_start
    ).delete()
    for s in sessions:
        session_date = datetime.strptime(s["date"], "%Y-%m-%d")
        db.add(PlannedSession(
            user_id=user_id,
            date=session_date,
            week_start=week_start,
            discipline=s.get("discipline", "rest"),
            duration_min=s.get("duration_min"),
            intensity=s.get("intensity"),
            description=s.get("description"),
        ))
    db.commit()


def mark_sessions_completed(db: Session, user_id: str, discipline: str, training_date: datetime):
    """Marca como completada la sesión planificada que coincida con disciplina y fecha."""
    day_start = training_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    session = db.query(PlannedSession).filter(
        PlannedSession.user_id == user_id,
        PlannedSession.date >= day_start,
        PlannedSession.date < day_end,
        PlannedSession.discipline == discipline,
        PlannedSession.completed == False
    ).first()
    if session:
        session.completed = True
        db.commit()


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

    # Contexto con datos recientes del atleta
    context = get_recent_context(db, user_id)
    # Solo cargamos historial de mensajes con datos personales (no Q&A genérico)
    history = get_conversation_history(db, user_id)

    # Construir mensajes (el mensaje actual se incluye pero aún no se guarda)
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
    has_personal_data = False

    if data_match:
        try:
            data = json.loads(data_match.group(1).strip())
            dtype = data.get("type")
            has_personal_data = dtype != "none"

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
                # Marcar la sesión planificada como completada automáticamente
                if disc:
                    mark_sessions_completed(db, user_id, disc, datetime.utcnow())

            elif dtype == "weekly_plan" and data.get("sessions"):
                save_weekly_plan(db, user_id, data["sessions"])

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

            elif dtype == "race_result" and data.get("race_result_date"):
                result = RaceResult(
                    user_id=user_id,
                    date=datetime.strptime(data["race_result_date"], "%Y-%m-%d"),
                    race_name=data.get("race_result_name", ""),
                    race_type=data.get("race_result_type", "otro"),
                    finish_time=data.get("race_result_time"),
                    position=data.get("race_result_position"),
                    notes=data.get("race_result_notes"),
                )
                db.add(result)
                db.commit()

            elif dtype == "memory" and data.get("memory_key") and data.get("memory_value"):
                existing = db.query(MemoryNote).filter(
                    MemoryNote.user_id == user_id,
                    MemoryNote.key == data["memory_key"]
                ).first()
                if existing:
                    existing.value = data["memory_value"]
                    existing.updated_at = datetime.utcnow()
                else:
                    note = MemoryNote(
                        user_id=user_id,
                        key=data["memory_key"],
                        value=data["memory_value"],
                    )
                    db.add(note)
                db.commit()

        except Exception as e:
            print(f"Error parsing data: {e}")

    # Limpiar el JSON de la respuesta que ve el usuario
    clean_response = re.sub(r"<data>.*?</data>", "", full_response, flags=re.DOTALL).strip()

    # Solo guardar en historial si el mensaje contenía datos personales reales.
    # Las preguntas genéricas (dieta, técnica, motivación...) no se persisten:
    # el bot las responde bien con el system prompt sin necesidad de recordarlas.
    if has_personal_data:
        save_message(db, user_id, "user", user_message)
        save_message(db, user_id, "assistant", clean_response)

    return clean_response