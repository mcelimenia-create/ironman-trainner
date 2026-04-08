import os
import httpx
from sqlalchemy.orm import Session
from database import Training, DailyMetrics, SessionLocal
from datetime import datetime

STRAVA_CLIENT_ID = os.environ["STRAVA_CLIENT_ID"]
STRAVA_CLIENT_SECRET = os.environ["STRAVA_CLIENT_SECRET"]
STRAVA_VERIFY_TOKEN = os.environ.get("STRAVA_VERIFY_TOKEN", "ironman2026")

# Mapeo de tipos de actividad Strava → disciplinas del coach
DISCIPLINE_MAP = {
    "Run": "run",
    "Ride": "bike",
    "Swim": "swim",
    "VirtualRide": "bike",
    "VirtualRun": "run",
    "WeightTraining": "gym",
    "Workout": "gym",
}


async def exchange_code(code: str) -> dict:
    """Intercambia el código OAuth por tokens de acceso."""
    async with httpx.AsyncClient() as client:
        r = await client.post("https://www.strava.com/oauth/token", data={
            "client_id": STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code"
        })
        return r.json()


async def get_activity_detail(activity_id: int, access_token: str) -> dict:
    """Obtiene los detalles completos de una actividad."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://www.strava.com/api/v3/activities/{activity_id}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        return r.json()


def save_strava_token(user_id: str, token_data: dict):
    """Guarda el token de Strava en la base de datos."""
    from database import Base, Column, String, Integer
    # Guardamos en un archivo simple por ahora
    import json, os
    tokens = {}
    if os.path.exists("/tmp/strava_tokens.json"):
        with open("/tmp/strava_tokens.json") as f:
            tokens = json.load(f)
    tokens[user_id] = token_data
    with open("/tmp/strava_tokens.json", "w") as f:
        json.dump(tokens, f)


def get_strava_token(user_id: str) -> dict:
    import json, os
    if os.path.exists("/tmp/strava_tokens.json"):
        with open("/tmp/strava_tokens.json") as f:
            tokens = json.load(f)
        return tokens.get(user_id)
    return None


def format_activity_message(activity: dict) -> str:
    """Formatea la actividad de Strava como mensaje para el coach."""
    discipline = DISCIPLINE_MAP.get(activity.get("type", ""), "run")
    name = activity.get("name", "Actividad")
    distance_km = round(activity.get("distance", 0) / 1000, 2)
    duration_min = round(activity.get("moving_time", 0) / 60, 1)
    avg_hr = activity.get("average_heartrate")
    max_hr = activity.get("max_heartrate")
    avg_speed = activity.get("average_speed", 0)
    elevation = activity.get("total_elevation_gain", 0)

    # Calcular ritmo
    if discipline == "run" and avg_speed > 0:
        pace_sec = 1000 / avg_speed
        pace_min = int(pace_sec // 60)
        pace_s = int(pace_sec % 60)
        pace_str = f"{pace_min}:{pace_s:02d} min/km"
    elif discipline == "bike" and avg_speed > 0:
        pace_str = f"{round(avg_speed * 3.6, 1)} km/h"
    elif discipline == "swim" and avg_speed > 0:
        pace_sec = 100 / avg_speed
        pace_min = int(pace_sec // 60)
        pace_s = int(pace_sec % 60)
        pace_str = f"{pace_min}:{pace_s:02d} min/100m"
    else:
        pace_str = ""

    msg = f"📡 *Actividad de Strava registrada automáticamente:*\n"
    msg += f"🏷️ {name}\n"
    msg += f"🏃 Disciplina: {discipline}\n"
    msg += f"📏 Distancia: {distance_km} km\n"
    msg += f"⏱️ Duración: {duration_min} min\n"
    if pace_str:
        msg += f"⚡ Ritmo: {pace_str}\n"
    if avg_hr:
        msg += f"❤️ FC media: {avg_hr} bpm\n"
    if max_hr:
        msg += f"❤️‍🔥 FC máx: {max_hr} bpm\n"
    if elevation:
        msg += f"⛰️ Desnivel: {elevation}m\n"

    msg += f"\nAnaliza este entrenamiento y dime cómo fue."
    return msg