import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use the Neon PostgreSQL URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://neondb_owner:npg_QatBHmg20eUA@ep-empty-bonus-abfzgdwl-pooler.eu-west-2.aws.neon.tech/neondb?sslmode=require"
)

# SQLAlchemy engine setup
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    future=True
)

# SessionLocal setup
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base declarative class
Base = declarative_base()

def get_db():
    """
    Dependency for getting a SQLAlchemy database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
