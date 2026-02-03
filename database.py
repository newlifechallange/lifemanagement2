from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
import datetime
import pytz

Base = declarative_base()
WIB = pytz.timezone('Asia/Jakarta')

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    phone_number = Column(String, unique=True, nullable=True) # Optional for MVP
    name = Column(String)
    timezone = Column(String, default='Asia/Jakarta')
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

class Attribute(Base):
    __tablename__ = 'attributes'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    key = Column(String)
    value = Column(String) # Store as string, parse as needed
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

class TimeLog(Base):
    __tablename__ = 'timelogs'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    activity = Column(String)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    duration_minutes = Column(Integer)
    category = Column(String, nullable=True)

class FuturePlan(Base):
    __tablename__ = 'future_plans'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    activity = Column(String)
    planned_start = Column(DateTime)
    planned_end = Column(DateTime, nullable=True)
    status = Column(String, default='pending') # pending, completed, cancelled
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(WIB))

import os
from dotenv import load_dotenv

load_dotenv()

# ... imports ...

# Database Setup
# Use DATABASE_URL if available (Supabase), else fallback to SQLite
database_url = os.getenv('DATABASE_URL')
if not database_url:
    database_url = 'sqlite:///lifeos.db'
    print("WARNING: Using local SQLite. Set DATABASE_URL for Supabase.")

engine = create_engine(database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Create default user for MVP if not exists
    session = SessionLocal()
    if not session.query(User).first():
        default_user = User(name="User")
        session.add(default_user)
        session.commit()
    session.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized.")
