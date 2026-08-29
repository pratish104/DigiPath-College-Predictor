"""Database configuration and session lifecycle management for DigiPath."""

import logging
import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger("digipath.database")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:YOUR_ACTUAL_PASSWORD@localhost:3306/digipath_db",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base shared by every persisted DigiPath model."""


def get_db() -> Generator[Session, None, None]:
    """Provide one SQLAlchemy session per request and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_database_tables() -> None:
    """Create missing tables and fail startup with an actionable logged error."""
    try:
        Base.metadata.create_all(bind=engine)
        log.info("Database schema is ready.")
    except SQLAlchemyError as exc:
        log.exception("Database schema initialization failed.")
        raise RuntimeError("Unable to initialize the DigiPath database schema.") from exc
