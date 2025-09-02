# hhcli/db.py
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _dsn() -> str:
    user = os.getenv("DB_USER", "")
    pwd = os.getenv("DB_PASSWORD", "")
    host = os.getenv("DB_HOST", "db")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "")
    return f"postgresql+psycopg://{user}:{pwd}@{host}:{port}/{name}"


engine = create_engine(_dsn(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


# FastAPI dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
