"""Database configuration and session lifecycle management for DigiPath.

Environment is loaded from the .env file in this file's parent directory at
import time, *before* any os.getenv call is evaluated.  This guarantees that
Uvicorn's worker-process fork does not race against dotenv loading.
"""

# ── 1. Environment hydration ─────────────────────────────────────────────────
# Must happen before any os.getenv() call in this module or its dependants.
import pathlib

from dotenv import load_dotenv

_ENV_PATH: pathlib.Path = pathlib.Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)
# ─────────────────────────────────────────────────────────────────────────────

import logging
import os
import ssl
import sys
import re
from collections.abc import Generator

import pymysql  # noqa: F401 – imported so its OperationalError subclass is available
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError as SAOperationalError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

log = logging.getLogger("digipath.database")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEPARATOR = "-" * 72


def _mask_url(url: str) -> str:
    """Replace the password segment of a DB URL with asterisks for safe logging."""
    return re.sub(r"(:)[^:@]+(@)", r"\1****\2", url)


def _swap_host(url: str, old_host: str, new_host: str) -> str:
    """Return url with old_host swapped for new_host (port-aware)."""
    return re.sub(
        rf"@{re.escape(old_host)}(:|/)",
        f"@{new_host}\\1",
        url,
    )


def _print_error_box(host_tried: str, db_name: str, err_code: int | None) -> None:
    """Print a clearly visible diagnostic block to stderr on connection failure."""
    lines = [
        _SEPARATOR,
        f"DATABASE ERROR ({err_code or '?'}): MySQL connection / authentication failed.",
        f"  Target Host : {host_tried}",
        f"  Port        : 3306",
        f"  Database    : {db_name}",
        "",
        "  Checklist:",
        "  1. Is MySQL running?  →  Run: mysqladmin -u root -p status",
        "  2. Does the password in .env match your MySQL root password?",
        "  3. Does the database exist?  →  Run: SHOW DATABASES;",
        "  4. Can the user connect from this host?  →  Run:",
        "       GRANT ALL PRIVILEGES ON digipath_db.* TO 'root'@'127.0.0.1' IDENTIFIED BY 'yourpassword';",
        "       FLUSH PRIVILEGES;",
        f"  5. .env loaded from: {_ENV_PATH}",
        _SEPARATOR,
    ]
    for line in lines:
        print(line, file=sys.stderr)


def _extract_db_name(url: str) -> str:
    """Parse the database name from the end of a MySQL DSN."""
    match = re.search(r"/([^/?#]+)(?:\?|#|$)", url)
    return match.group(1) if match else "unknown"


def _prepare_pymysql_connection(url: str):
    """Translate Aiven's MySQL TLS URI option into PyMySQL connection args.

    ``ssl-mode`` is understood by MySQL clients, but SQLAlchemy forwards it as
    a keyword argument and PyMySQL does not accept it.  A verified SSLContext
    keeps the connection encrypted and validates Aiven's server certificate.
    """
    parsed_url = make_url(url)
    ssl_mode = parsed_url.query.get("ssl-mode")

    if (
        parsed_url.drivername == "mysql+pymysql"
        and ssl_mode is not None
        and ssl_mode.upper() == "REQUIRED"
    ):
        query = dict(parsed_url.query)
        query.pop("ssl-mode")
        ssl_context = ssl.create_default_context()
        aiven_ca_path = pathlib.Path("/etc/secrets/ca.pem")
        if aiven_ca_path.is_file():
            ssl_context.load_verify_locations(cafile=str(aiven_ca_path))
        return parsed_url.set(query=query), {"ssl": ssl_context}

    return url, {}


# ---------------------------------------------------------------------------
# Connection strategy: try 127.0.0.1 first, fall back to localhost
# ---------------------------------------------------------------------------

def _build_candidate_urls() -> list[str]:
    """Return an ordered list of DB URLs to attempt, primary first."""
    raw_url: str | None = os.getenv("DATABASE_URL")

    if raw_url:
        primary = raw_url.strip()
    else:
        db_user = os.getenv("DB_USER", "root")
        db_pass = os.getenv("DB_PASSWORD", os.getenv("DB_PASS", ""))
        db_name = os.getenv("DB_NAME", "digipath_db")
        db_port = os.getenv("DB_PORT", "3306")
        primary = f"mysql+pymysql://{db_user}:{db_pass}@127.0.0.1:{db_port}/{db_name}"

    candidates: list[str] = [primary]

    # Build a complementary URL by swapping localhost ↔ 127.0.0.1
    if "127.0.0.1" in primary:
        candidates.append(_swap_host(primary, "127.0.0.1", "localhost"))
    elif "localhost" in primary:
        candidates.append(_swap_host(primary, "localhost", "127.0.0.1"))

    return candidates


def _create_verified_engine():
    """Attempt each candidate URL in order; return the first working engine.

    If every attempt fails with an OperationalError the diagnostic box is
    printed and the last exception is re-raised so Uvicorn can exit cleanly.
    """
    candidates = _build_candidate_urls()
    last_exc: Exception | None = None

    log.info("[DB INIT] Loading environment from %s", _ENV_PATH)

    for url in candidates:
        masked = _mask_url(url)
        log.info("[DB INIT] Attempting MySQL connection to %s", masked)
        print(f"INFO:     [DB INIT] Attempting MySQL connection to {masked}", file=sys.stderr)

        try:
            engine_url, connect_args = _prepare_pymysql_connection(url)
            candidate_engine = create_engine(
                engine_url,
                pool_pre_ping=True,
                pool_recycle=3600,
                pool_size=10,
                max_overflow=20,
                connect_args=connect_args,
            )
            # Eagerly probe the connection so failures surface immediately.
            with candidate_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("[DB INIT] Connected successfully via %s", masked)
            print(f"INFO:     [DB INIT] Connected successfully via {masked}", file=sys.stderr)
            return candidate_engine

        except SAOperationalError as exc:
            last_exc = exc
            # Extract the underlying PyMySQL error code when available.
            err_code: int | None = None
            cause = exc.__cause__
            if hasattr(cause, "args") and cause.args:
                try:
                    err_code = int(cause.args[0])
                except (TypeError, ValueError):
                    pass

            _print_error_box(
                host_tried=_extract_host(url),
                db_name=_extract_db_name(url),
                err_code=err_code,
            )
            log.warning("[DB INIT] Connection attempt failed for %s: %s", masked, exc)

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("[DB INIT] Unexpected error for %s: %s", masked, exc)

    # All candidates exhausted – raise so startup fails loudly.
    raise RuntimeError(
        "All MySQL connection attempts failed. Review the diagnostic output above."
    ) from last_exc


def _extract_host(url: str) -> str:
    """Parse the host (without port) from a SQLAlchemy MySQL DSN."""
    match = re.search(r"@([^:/]+)", url)
    return match.group(1) if match else "unknown"


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

engine = _create_verified_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Declarative base shared by every persisted DigiPath model."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """Provide one SQLAlchemy session per request and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_database_tables() -> None:
    """Create missing tables and ensure essential columns exist; print a clear error and raise if it fails."""
    log.info("[DB INIT] Creating / verifying database schema …")
    print("INFO:     [DB INIT] Creating / verifying database schema …", file=sys.stderr)
    try:
        Base.metadata.create_all(bind=engine)
        # Self-healing migration for added columns on existing tables
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN profile_settings JSON NULL;"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN credits_balance INT NOT NULL DEFAULT 50;"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN admin_level INT NOT NULL DEFAULT 0;"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE users MODIFY COLUMN role VARCHAR(30) NOT NULL DEFAULT 'USER';"))
            except Exception:
                pass
        log.info("[DB INIT] Database schema is ready.")
        print("INFO:     [DB INIT] Database schema is ready.", file=sys.stderr)
    except SAOperationalError as exc:
        cause = exc.__cause__
        err_code: int | None = None
        if hasattr(cause, "args") and cause.args:
            try:
                err_code = int(cause.args[0])
            except (TypeError, ValueError):
                pass
        _print_error_box(
            host_tried="see above",
            db_name=_extract_db_name(str(engine.url)),
            err_code=err_code,
        )
        log.exception("[DB INIT] Schema creation failed.")
        raise RuntimeError("Unable to initialize the DigiPath database schema.") from exc
    except SQLAlchemyError as exc:
        log.exception("[DB INIT] Schema creation failed.")
        raise RuntimeError("Unable to initialize the DigiPath database schema.") from exc
