"""
The API itself: every URL the server answers.

Run it on your laptop (from the server/ folder):
    uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs for an interactive page where you can
try every endpoint. FastAPI builds that page automatically from this file.

Endpoints:
    GET  /health              is the server up? (hosting platforms ping this)
    POST /v1/admin/users      you create an account for a friend -> get their token
    POST /v1/reviews          the add-on uploads a batch of reviews
    GET  /v1/me/summary       a friend's own stats

The "v1" in the paths is API versioning: if you ever need to change how
uploads work in an incompatible way, you add /v2/... and old copies of the
add-on that still call /v1 keep working.

Note the endpoint functions use plain `def`, not `async def`. FastAPI runs
plain `def` functions on a pool of worker threads, which is the right choice
when the function does blocking work like database calls with SQLAlchemy.
"""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import auth, schemas
from .db import Base, engine, get_db
from .models import Review, User


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup: create any tables that don't exist yet.
    # Fine for a project this size. Once you have real data and need to
    # CHANGE a table (add a column, say), switch to a migration tool called
    # Alembic, which applies versioned changes without losing data.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Anki Clicker API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True}


@app.post(
    "/v1/admin/users",
    response_model=schemas.UserCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(auth.require_admin)],
)
def create_user(body: schemas.UserCreate, db: Session = Depends(get_db)):
    token = auth.new_token()
    user = User(name=body.name, token_hash=auth.hash_token(token))
    db.add(user)
    db.commit()       # actually write it to the database
    db.refresh(user)  # reload so user.id is filled in
    # This is the only time the plain token exists anywhere. Give it to your
    # friend (it goes in their add-on config); after this, only the hash remains.
    return schemas.UserCreated(id=user.id, name=user.name, token=token)


@app.post("/v1/reviews", response_model=schemas.BatchResult)
def upload_reviews(
    batch: schemas.ReviewBatch,
    user: User = Depends(auth.current_user),
    db: Session = Depends(get_db),
):
    """
    Idempotent upload: sending the same batch twice has the same effect as
    sending it once.

    Why that matters: the add-on uploads, the server saves the rows, and then
    the Wi-Fi drops before the "200 OK" reaches the add-on. The add-on thinks
    it failed and retries. Without this, every flaky connection would create
    duplicate reviews and quietly skew all your stats.
    """
    incoming_ids = [r.client_id for r in batch.reviews]

    # One query: which of these do we already have for this user?
    already_have = set(
        db.scalars(
            select(Review.client_id).where(
                Review.user_id == user.id, Review.client_id.in_(incoming_ids)
            )
        )
    )

    new_rows, seen = [], set()
    for r in batch.reviews:
        if r.client_id in already_have or r.client_id in seen:
            continue  # duplicate: already stored, or repeated inside this batch
        seen.add(r.client_id)
        # model_dump() turns the Pydantic object into a dict, and ** unpacks
        # it as keyword arguments: Review(client_id=..., card_id=..., ...)
        new_rows.append(Review(user_id=user.id, **r.model_dump()))

    db.add_all(new_rows)
    try:
        db.commit()
    except IntegrityError:
        # Rare race: two uploads of the same rows arrived at the same moment,
        # both passed the check above, and the database's unique constraint
        # stopped the second one. Undo, and tell the add-on to try again;
        # on the retry, the check above will see the rows and skip them.
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Concurrent upload, please retry")

    return schemas.BatchResult(
        received=len(batch.reviews),
        inserted=len(new_rows),
        duplicates=len(batch.reviews) - len(new_rows),
    )


@app.get("/v1/me/summary", response_model=schemas.Summary)
def my_summary(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    """
    The heavy lifting happens IN the database: AVG, COUNT and GROUP BY run
    there and only the small results come back. Much faster than loading
    every review into Python and averaging in a loop.
    """
    think = Review.answer_shown - Review.question_shown
    hesitation = Review.answered - Review.answer_shown
    mine = Review.user_id == user.id

    total, sessions, avg_think, avg_hes = db.execute(
        select(
            func.count(Review.id),
            func.count(func.distinct(Review.session_id)),
            func.avg(think),
            func.avg(hesitation),
        ).where(mine)
    ).one()

    by_ease = db.execute(
        select(Review.ease, func.count(Review.id), func.avg(hesitation))
        .where(mine)
        .group_by(Review.ease)
        .order_by(Review.ease)
    ).all()

    return schemas.Summary(
        total_reviews=total,
        sessions=sessions,
        avg_think_s=avg_think,
        avg_hesitation_s=avg_hes,
        by_ease=[
            schemas.EaseStats(ease=e, count=c, avg_hesitation_s=h) for e, c, h in by_ease
        ],
    )
