"""
Database connection setup (SQLAlchemy).

The three pieces:
  engine        - the connection pool to the database. Created once at startup.
  SessionLocal  - a factory for "sessions". A session is one unit of work:
                  you add/query objects, then commit (save) or roll back (undo).
  get_db()      - hands each HTTP request its own session and closes it after.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

# SQLite normally refuses to let a connection be used from a different thread.
# FastAPI runs our endpoint functions in a pool of worker threads, so we turn
# that check off. (Each request still gets its own session, so this is safe.)
# Postgres doesn't need this.
_connect_args = {"check_same_thread": False} if config.DATABASE_URL.startswith("sqlite") else {}

# pool_pre_ping: before reusing a pooled connection, check it's still alive.
# Neon's free tier shuts the database down after 5 idle minutes, which
# silently kills open connections; without this, the first request after a
# quiet period would fail with a "connection closed" error.
engine = create_engine(config.DATABASE_URL, connect_args=_connect_args, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    """Every table class in models.py inherits from this, which is how
    SQLAlchemy knows about them when it creates the tables."""


def get_db():
    """
    A FastAPI "dependency". Any endpoint that declares `db = Depends(get_db)`
    gets a fresh session. The `yield` pauses here while the endpoint runs;
    the `finally` closes the session afterward, even if the endpoint crashed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
