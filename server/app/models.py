"""
Database tables, written as Python classes (this is called an ORM:
Object-Relational Mapper). Each class is a table, each attribute a column.
SQLAlchemy turns these into CREATE TABLE statements and turns query results
back into Python objects.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """One row per friend using the add-on."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))

    # We never store the token itself, only a hash of it (see auth.py).
    # If someone ever got a copy of this database, they still couldn't
    # use it to upload as your friends.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    reviews: Mapped[list["Review"]] = relationship(back_populates="user")


class Review(Base):
    """One row per card answered. Mirrors the add-on's local table."""

    __tablename__ = "reviews"

    # The server's own row number. Internal only.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    # A random ID the ADD-ON creates when the review happens. This is what
    # makes uploads safe to retry: if the same client_id arrives twice, the
    # second copy is ignored. See the UniqueConstraint below and main.py.
    client_id: Mapped[str] = mapped_column(String(64))

    session_id: Mapped[str] = mapped_column(String(64), index=True)
    # BigInteger (64-bit), not Integer (32-bit): Anki IDs are millisecond
    # timestamps around 1.7 trillion, past the 32-bit limit of ~2.1 billion.
    # SQLite silently allowed it; Postgres raised "integer out of range".
    card_id: Mapped[int] = mapped_column(BigInteger)
    deck_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # All times are Unix timestamps (seconds since 1970-01-01 UTC, as floats).
    # Simple to subtract: hesitation = answered - answer_shown.
    question_shown: Mapped[float] = mapped_column(Float)
    answer_shown: Mapped[float | None] = mapped_column(Float, nullable=True)
    answered: Mapped[float] = mapped_column(Float, index=True)

    ease: Mapped[int] = mapped_column(Integer)  # 1 Again, 2 Hard, 3 Good, 4 Easy
    input_source: Mapped[str] = mapped_column(String(16))  # 'keyboard' or 'gamepad'

    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped[User] = relationship(back_populates="reviews")

    # The database itself enforces "one copy per (user, client_id)".
    # Even if our Python check in main.py had a bug, duplicates can't get in.
    __table_args__ = (UniqueConstraint("user_id", "client_id", name="uq_review_user_client"),)
