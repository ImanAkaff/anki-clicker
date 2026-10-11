"""
The add-on's local database (SQLite): a single file in user_files/.

Every review is written here FIRST, the instant it happens. Uploading to the
server happens later, in the background. This "local-first" design means:
  - Anki never waits on the network.
  - Reviews done offline (on a plane, bad Wi-Fi) aren't lost; they sit here
    with synced = 0 until an upload succeeds.

This file doesn't import anything from Anki, so it can be tested outside Anki.
"""

# Lets type hints like `float | None` work on Python 3.9, which older Anki
# versions (e.g. 25.02) still ship. Without it the add-on fails to load there.
from __future__ import annotations

import sqlite3
import uuid

# Schema migrations, local edition.
# SQLite has a spare integer in every file called `user_version`. We use it to
# record which version of our table layout the file has. When the add-on
# updates and needs a new column, we add a step 2 below; old files run step 2
# on next launch and new files run 1 then 2. Nobody loses data.
_MIGRATIONS = {
    1: """
        CREATE TABLE reviews (
            client_id       TEXT PRIMARY KEY,   -- random ID made here; server uses it to ignore duplicates
            session_id      TEXT    NOT NULL,
            card_id         INTEGER NOT NULL,
            deck_id         INTEGER,
            question_shown  REAL    NOT NULL,   -- Unix timestamps, seconds
            answer_shown    REAL,
            answered        REAL    NOT NULL,
            ease            INTEGER NOT NULL,   -- 1 Again 2 Hard 3 Good 4 Easy
            input_source    TEXT    NOT NULL,   -- 'keyboard' or 'gamepad'
            synced          INTEGER NOT NULL DEFAULT 0   -- 0 = waiting to upload
        );
        -- An index is like a book's index: lets "find unsynced rows" skip
        -- straight to them instead of scanning the whole table.
        CREATE INDEX idx_reviews_synced ON reviews(synced);
    """,
    # Version 2: remember which entry in ANKI's own review log (the `revlog`
    # table) each of our rows matches. When someone presses undo, Anki deletes
    # that revlog entry. So if our row points at a revlog entry that no longer
    # exists, the review was undone and our row should go too.
    # This is the first real migration: files created by the earlier version
    # (like your 42 test reviews) get this column added on next launch, with
    # existing rows left as NULL.
    2: """
        ALTER TABLE reviews ADD COLUMN revlog_id INTEGER;
    """,
}

UPLOAD_FIELDS = (
    "client_id", "session_id", "card_id", "deck_id", "question_shown",
    "answer_shown", "answered", "ease", "input_source",
)


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["ease"]
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in sorted(_MIGRATIONS):
        if version > current:
            conn.executescript(_MIGRATIONS[version])
            conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def record_review(conn: sqlite3.Connection, **fields) -> str:
    client_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO reviews (client_id, session_id, card_id, deck_id, question_shown,"
        " answer_shown, answered, ease, input_source, revlog_id)"
        " VALUES (:client_id, :session_id, :card_id, :deck_id, :question_shown,"
        " :answer_shown, :answered, :ease, :input_source, :revlog_id)",
        {"client_id": client_id, "revlog_id": None, **fields},
    )
    conn.commit()
    return client_id


def pending(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(UPLOAD_FIELDS)} FROM reviews WHERE synced = 0"
        " ORDER BY answered LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_synced(conn: sqlite3.Connection, client_ids: list[str]) -> None:
    conn.executemany("UPDATE reviews SET synced = 1 WHERE client_id = ?",
                     [(c,) for c in client_ids])
    conn.commit()


def unsynced_revlog_ids(conn: sqlite3.Connection) -> list[int]:
    """revlog IDs of rows not yet uploaded: the ones we can still correct."""
    return [r[0] for r in conn.execute(
        "SELECT revlog_id FROM reviews WHERE synced = 0 AND revlog_id IS NOT NULL"
    )]


def delete_undone(conn: sqlite3.Connection, revlog_ids: list[int]) -> int:
    """Remove our rows for reviews Anki says were undone. Only unsynced rows:
    once a row has been uploaded we leave it alone (undoing after the sync
    is rare, and handling it would need a 'delete' endpoint on the server)."""
    cur = conn.executemany(
        "DELETE FROM reviews WHERE synced = 0 AND revlog_id = ?",
        [(i,) for i in revlog_ids],
    )
    conn.commit()
    return cur.rowcount


def all_reviews(conn: sqlite3.Connection) -> list[dict]:
    """Every logged review, oldest first, for the stats screen."""
    return [dict(r) for r in conn.execute(
        "SELECT card_id, deck_id, question_shown, answer_shown, answered, ease,"
        " input_source, revlog_id FROM reviews ORDER BY question_shown"
    )]


def summary(conn: sqlite3.Connection) -> dict:
    total, sessions, think, hes, unsynced = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT session_id),"
        " AVG(answer_shown - question_shown), AVG(answered - answer_shown),"
        " SUM(synced = 0)"
        " FROM reviews"
    ).fetchone()
    by_ease = conn.execute(
        "SELECT ease, COUNT(*), AVG(answered - answer_shown) FROM reviews"
        " GROUP BY ease ORDER BY ease"
    ).fetchall()
    return {
        "total": total, "sessions": sessions, "avg_think": think,
        "avg_hesitation": hes, "unsynced": unsynced or 0,
        "by_ease": [tuple(r) for r in by_ease],
    }
