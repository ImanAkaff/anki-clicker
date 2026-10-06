"""
The shapes of the JSON going in and out of the API (Pydantic models).

models.py  = what's stored in the database
schemas.py = what travels over the network

They look similar but are kept separate on purpose: the API shouldn't expose
internal columns (like token_hash), and incoming data needs validation rules
the database doesn't care about.

When a request comes in, FastAPI parses the JSON into these classes. If a
field is missing or has the wrong type or fails a rule, FastAPI rejects the
request with a 422 error explaining exactly what was wrong, before your code
even runs.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from . import config


class ReviewIn(BaseModel):
    client_id: str = Field(min_length=8, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    card_id: int
    deck_id: int | None = None
    question_shown: float
    answer_shown: float | None = None
    answered: float
    ease: int = Field(ge=1, le=4)  # ge/le = greater-or-equal / less-or-equal
    input_source: Literal["keyboard", "gamepad"]

    @model_validator(mode="after")
    def times_in_order(self):
        # Catch corrupted data early: an answer can't come before its question.
        if self.answered < self.question_shown:
            raise ValueError("answered is earlier than question_shown")
        if self.answer_shown is not None and not (
            self.question_shown <= self.answer_shown <= self.answered
        ):
            raise ValueError("answer_shown must be between question_shown and answered")
        return self


class ReviewBatch(BaseModel):
    reviews: list[ReviewIn] = Field(max_length=config.MAX_BATCH)


class BatchResult(BaseModel):
    received: int    # how many were in the request
    inserted: int    # how many were new
    duplicates: int  # how many we already had (a retried upload)


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class UserCreated(BaseModel):
    id: int
    name: str
    token: str  # shown exactly once; only the hash is stored


class EaseStats(BaseModel):
    ease: int
    count: int
    avg_hesitation_s: float | None


class Summary(BaseModel):
    total_reviews: int
    sessions: int
    avg_think_s: float | None
    avg_hesitation_s: float | None
    by_ease: list[EaseStats]
