from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Text
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