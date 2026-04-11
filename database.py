from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Text, UniqueConstraint, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

DATABASE_URL = os.environ["DATABASE_URL"]

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Training(Base):
    __tablename__ = "trainings"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    date = Column(DateTime, default=datetime.utcnow)
    discipline = Column(String)        # swim / bike / run / gym
    duration_min = Column(Float)
    distance_km = Column(Float)
    avg_hr = Column(Integer)
    tss = Column(Float)
    notes = Column(Text)


class DailyMetrics(Base):
    __tablename__ = "daily_metrics"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    date = Column(DateTime, default=datetime.utcnow)
    weight_kg = Column(Float)
    sleep_hours = Column(Float)
    legs_score = Column(Integer)       # 1-10
    energy_score = Column(Integer)     # 1-10
    ctl = Column(Float, default=0)
    atl = Column(Float, default=0)
    tsb = Column(Float, default=0)


class Injury(Base):
    __tablename__ = "injuries"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    date = Column(DateTime, default=datetime.utcnow)
    zone = Column(String)
    intensity = Column(Integer)        # 1-10
    notes = Column(Text)


class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    role = Column(String)              # user / assistant
    content = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)


class AthleteProfile(Base):
    __tablename__ = "athlete_profile"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True)
    race_date = Column(DateTime, nullable=True)
    race_name = Column(String, nullable=True)


class RaceResult(Base):
    """Historial de carreras completadas."""
    __tablename__ = "race_results"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    date = Column(DateTime)
    race_name = Column(String)
    race_type = Column(String)          # ironman / olimpico / maraton / etc.
    finish_time = Column(String, nullable=True)   # "HH:MM:SS"
    position = Column(String, nullable=True)
    notes = Column(Text, nullable=True)


class MemoryNote(Base):
    """Notas permanentes que el bot debe recordar siempre."""
    __tablename__ = "memory_notes"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    key = Column(String)                # identificador único, p.ej. "peso_objetivo"
    value = Column(Text)                # contenido de la nota
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("user_id", "key"),)


class PlannedSession(Base):
    """Sesiones del plan semanal generado por el coach."""
    __tablename__ = "planned_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(String)
    date = Column(DateTime)             # fecha exacta de la sesión
    week_start = Column(DateTime)       # lunes de esa semana (para agrupar)
    discipline = Column(String)         # swim / bike / run / gym / rest
    duration_min = Column(Integer, nullable=True)
    intensity = Column(String, nullable=True)   # Z1 / Z2 / Z3 / series / etc.
    description = Column(Text, nullable=True)
    completed = Column(Boolean, default=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

class StravaToken(Base):
    __tablename__ = "strava_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(String, unique=True)
    access_token = Column(String)
    refresh_token = Column(String)
    expires_at = Column(Integer)
    athlete_id = Column(String)