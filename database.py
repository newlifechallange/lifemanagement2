from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, JSON, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import pytz

Base = declarative_base()
WIB = pytz.timezone('Asia/Jakarta')

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    phone_number = Column(String, unique=True, nullable=True)
    name = Column(String)
    timezone = Column(String, default='Asia/Jakarta')
    current_streak = Column(Integer, default=0)
    last_active_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class Attribute(Base):
    __tablename__ = 'attributes'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    key = Column(String)
    value = Column(String)
    unit = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class AttributeHistory(Base):
    __tablename__ = 'attribute_history'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    key = Column(String)
    value = Column(String)
    unit = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    recorded_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class ChatHistory(Base):
    __tablename__ = 'chat_history'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    role = Column(String)
    content = Column(String)
    message_id = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class Achievement(Base):
    __tablename__ = 'achievements'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String)
    icon = Column(String)
    description = Column(String)
    tier = Column(Integer, default=1)
    max_tier = Column(Integer, default=5)
    unlocked_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))
    last_updated_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class Reminder(Base):
    __tablename__ = 'reminders'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    message = Column(String)
    remind_at = Column(DateTime)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class TimeLog(Base):
    __tablename__ = 'timelogs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    activity = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    duration_minutes = Column(Integer)
    category = Column(String, nullable=True)
    tag = Column(String, nullable=True)
    notes = Column(String, nullable=True)

class Stopwatch(Base):
    __tablename__ = 'stopwatches'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    label = Column(String)
    category = Column(String, nullable=True)
    tag = Column(String, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))
    ended_at = Column(DateTime, nullable=True)
    is_logged = Column(Boolean, default=False)
    status = Column(String, default='running')
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class Timer(Base):
    __tablename__ = 'timers'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    label = Column(String)
    category = Column(String, nullable=True)
    tag = Column(String, nullable=True)
    duration_minutes = Column(Integer)
    started_at = Column(DateTime, nullable=True)
    ended_at = Column(DateTime, nullable=True)
    is_logged = Column(Boolean, default=False)
    status = Column(String, default='pending')
    sequence_group_id = Column(String, nullable=True)
    sequence_order = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class ScheduledTask(Base):
    __tablename__ = 'scheduled_tasks'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    message = Column(String)
    frequency_minutes = Column(Integer)
    start_hour_wib = Column(Integer, default=0)
    end_hour_wib = Column(Integer, default=23)
    next_run_at = Column(DateTime)
    status = Column(String, default='active')
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

import os
from dotenv import load_dotenv

load_dotenv()

database_url = os.getenv('DATABASE_URL')
if not database_url:
    database_url = 'sqlite:///lifeos.db'
    print("WARNING: Using local SQLite. Set DATABASE_URL for Supabase.")

engine = create_engine(database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    if not session.query(User).first():
        default_user = User(name="User")
        session.add(default_user)
        session.commit()
    session.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
