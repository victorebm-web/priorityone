"""Database engine, session factory and the app's notion of "today"."""
import os
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PRIORITYONE_DB", BASE_DIR / "priorityone.db"))

engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def today() -> date:
    """Today's date. Override with PRIORITYONE_TODAY=YYYY-MM-DD for reproducible demos."""
    override = os.environ.get("PRIORITYONE_TODAY")
    return date.fromisoformat(override) if override else date.today()


def week_start_of(d: date) -> date:
    """Monday of the week containing d."""
    return d - timedelta(days=d.weekday())


def current_week_start() -> date:
    return week_start_of(today())
