"""
Anki Clicker: controller input, review timing, and background sync.

Anki runs this file once at startup. It doesn't "run" continuously; it
REGISTERS functions with Anki's hooks (the observer pattern), and Anki calls
them when things happen.

How the pieces connect:

    controller ──(Gamepad API, web/gamepad.js)──> pycmd("ankiclicker:btn:3")
         │
         ▼
    on_js_message ──> handle_button ──> Anki reviewer: show answer / answer card
                                              │
           Anki hooks fire: question shown, answer shown, card answered
                                              │
                                              ▼
                               storage.record_review  (local SQLite, instantly)
                                              │
              when a review session ends ──> sync.sync_once (background thread)
                                              │
                                              ▼
                                       server POST /v1/reviews

Only this file imports from Anki (`aqt`). storage.py and sync.py are plain
Python, which is why they can be tested without Anki running.
"""

import json
import os
import time
import uuid

from aqt import gui_hooks, mw
from aqt.qt import QAction, QMenu, qconnect
from aqt.reviewer import Reviewer
from aqt.utils import showInfo, tooltip

from . import storage, sync

# ---------------------------------------------------------------- paths & setup

# user_files/ is the one folder Anki keeps when an add-on is updated.
ADDON_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(ADDON_DIR, "user_files")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "clicker.sqlite3")

# The add-on's folder name, as Anki knows it (needed for web file URLs).
ADDON_PACKAGE = mw.addonManager.addonFromModule(__name__)

# Allow Anki's web views to load files from our web/ folder.
mw.addonManager.setWebExports(__name__, r"web/.*\.js")

SESSION_ID = uuid.uuid4().hex[:12]  # new ID each time Anki starts

ACTIONS_TO_EASE = {"again": 1, "hard": 2, "good": 3, "easy": 4}
EASE_NAMES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def config() -> dict:
    # Anki merges config.json with any changes the user made in
    # Tools > Add-ons > Config, and returns the result as a dict.
    return mw.addonManager.getConfig(__name__) or {}


# The main-thread connection. Anki's hooks all run on the main thread, so
# this one connection is only ever used from there.
_db = storage.connect(DB_PATH)

# ---------------------------------------------------------------- 1. load gamepad.js into the reviewer

def on_webview_set_content(web_content, context) -> None:
    # Many screens in Anki are web views; only add our script to the reviewer.
    if isinstance(context, Reviewer):
        web_content.js.append(f"/_addons/{ADDON_PACKAGE}/web/gamepad.js")


# ---------------------------------------------------------------- 2. messages from gamepad.js

def on_js_message(handled, message: str, context):
    """Every pycmd(...) from any web view arrives here. We only claim messages
    starting with 'ankiclicker:' and pass everything else along untouched,
    so we don't break other add-ons."""
    if not message.startswith("ankiclicker:"):
        return handled

    _, kind, payload = message.split(":", 2)
    if kind == "btn":
        handle_button(payload)
    elif kind == "connected":
        info = json.loads(payload)
        tooltip(f"Controller connected: {info['id']}")
    elif kind == "status" and not json.loads(payload)["api"]:
        tooltip("Anki Clicker: this Anki version can't read controllers (Gamepad API missing).")
    return (True, None)  # "handled, nothing to return to JavaScript"


# Set when a controller triggers an answer, so the log knows the input source.
_answer_from_gamepad = False


def handle_button(button_id: str) -> None:
    global _answer_from_gamepad
    reviewer = mw.reviewer
    if mw.state != "review" or reviewer.card is None:
        return

    action = config().get("button_map", {}).get(button_id)
    if action is None:
        # This doubles as the way to discover button numbers.
        tooltip(f"Button {button_id} isn't mapped. Add it in Tools > Add-ons > Config.")
        return

    if action == "undo":
        mw.undo()
        return

    if reviewer.state == "question":
        # Any answer button on the question side just reveals the answer,
        # so nobody grades a card they haven't seen.
        if action == "show_answer" or action in ACTIONS_TO_EASE:
            reviewer._showAnswer()
        return

    if reviewer.state == "answer" and action in ACTIONS_TO_EASE:
        _answer_from_gamepad = True
        # Leading underscore = Anki's "internal" method. Many add-ons use it,
        # but an Anki update could rename it, which is worth knowing.
        reviewer._answerCard(ACTIONS_TO_EASE[action])


# ---------------------------------------------------------------- 3. timing the review

_current = {"card_id": None, "question_shown": None, "answer_shown": None}


def on_show_question(card) -> None:
    _current.update(card_id=card.id, question_shown=time.time(), answer_shown=None)


def on_show_answer(card) -> None:
    if _current["card_id"] == card.id:
        _current["answer_shown"] = time.time()


def on_answer(reviewer, card, ease: int) -> None:
    global _answer_from_gamepad
    source = "gamepad" if _answer_from_gamepad else "keyboard"
    _answer_from_gamepad = False

    if _current["card_id"] != card.id or _current["question_shown"] is None:
        return  # missed the start of this card; better no row than a wrong one

    # Anki has just written this answer to its own log (the `revlog` table).
    # revlog IDs are the answer time in milliseconds, so the newest one for
    # this card is the answer that just happened. The ">=" guard makes sure
    # we don't grab an older entry from a previous day if none was written.
    revlog_id = mw.col.db.scalar(
        "SELECT max(id) FROM revlog WHERE cid = ? AND id >= ?",
        card.id,
        int(_current["question_shown"] * 1000) - 1000,
    )

    storage.record_review(
        _db,
        session_id=SESSION_ID,
        card_id=card.id,
        deck_id=card.did,
        question_shown=_current["question_shown"],
        answer_shown=_current["answer_shown"],
        answered=time.time(),
        ease=ease,
        input_source=source,
        revlog_id=revlog_id,
    )
    _current["card_id"] = None


def remove_undone_reviews() -> int:
    """
    Undo fix. Pressing undo makes Anki delete the review from its revlog,
    but our log never hears about it. So before showing stats or uploading,
    we ask Anki which of our not-yet-uploaded reviews still exist in its
    revlog, and delete ours that don't.

    Anki's database is the "source of truth" here; ours just follows it.
    Checking against the source of truth is more reliable than trying to
    catch every undo event as it happens.
    """
    ours = storage.unsynced_revlog_ids(_db)
    if not ours:
        return 0
    still_in_anki = set()
    # Ask in chunks of 500: SQL has a limit on how many ?s one query can have.
    for start in range(0, len(ours), 500):
        chunk = ours[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        still_in_anki.update(
            mw.col.db.list(f"SELECT id FROM revlog WHERE id IN ({placeholders})", *chunk)
        )
    undone = [i for i in ours if i not in still_in_anki]
    return storage.delete_undone(_db, undone) if undone else 0


# ---------------------------------------------------------------- 4. background sync

_sync_running = False
_last_sync_message = "Not synced yet this session."


def start_sync(manual: bool = False) -> None:
    global _sync_running
    cfg = config()
    if not (cfg.get("sync_enabled") and cfg.get("server_url") and cfg.get("token")):
        if manual:
            showInfo("Sync is off. Set server_url and token in Tools > Add-ons > Config.")
        return
    if _sync_running:
        return  # one upload at a time
    # Clean up undone reviews first. This touches Anki's collection, so it
    # must happen here on the main thread, before handing off to the background.
    remove_undone_reviews()
    _sync_running = True

    def task():
        # Runs on a background thread: no Anki UI calls allowed in here.
        return sync.sync_once(DB_PATH, cfg["server_url"], cfg["token"])

    def on_done(future):
        # Runs back on the main thread, so UI calls (tooltip) are safe again.
        global _sync_running, _last_sync_message
        _sync_running = False
        try:
            result = future.result()
        except Exception as e:  # a bug in sync.py shouldn't crash Anki
            _last_sync_message = f"Sync crashed: {e}"
        else:
            _last_sync_message = result.error or f"Uploaded {result.uploaded} reviews."
        if manual:
            tooltip(_last_sync_message)

    # Anki's built-in helper: run `task` on a worker thread, then `on_done`
    # on the main thread with the result.
    mw.taskman.run_in_background(task, on_done)


# ---------------------------------------------------------------- 5. stats window (to be replaced by React)

def show_stats() -> None:
    removed = remove_undone_reviews()
    if removed:
        tooltip(f"Removed {removed} undone review(s) from the log.")
    s = storage.summary(_db)
    if not s["total"]:
        showInfo("No reviews logged yet.")
        return
    lines = [
        f"Reviews: {s['total']} across {s['sessions']} session(s)",
        f"Avg think time (question to reveal): {s['avg_think'] or 0:.1f}s",
        f"Avg hesitation (reveal to button): {s['avg_hesitation'] or 0:.1f}s",
        "",
        "Hesitation by button:",
        *[f"  {EASE_NAMES.get(e, e)}: {n} reviews, {(h or 0):.1f}s avg" for e, n, h in s["by_ease"]],
        "",
        f"Waiting to upload: {s['unsynced']}",
        f"Last sync: {_last_sync_message}",
    ]
    showInfo("\n".join(lines), title="Anki Clicker")


# ---------------------------------------------------------------- 6. register everything with Anki

gui_hooks.webview_will_set_content.append(on_webview_set_content)
gui_hooks.webview_did_receive_js_message.append(on_js_message)
gui_hooks.reviewer_did_show_question.append(on_show_question)
gui_hooks.reviewer_did_show_answer.append(on_show_answer)
gui_hooks.reviewer_did_answer_card.append(on_answer)
gui_hooks.reviewer_will_end.append(start_sync)   # upload when they leave the review screen
gui_hooks.profile_did_open.append(start_sync)    # and catch up on anything left from last time

_menu = QMenu("Anki Clicker", mw)
_stats_action = QAction("Stats", mw)
qconnect(_stats_action.triggered, show_stats)
_sync_action = QAction("Sync now", mw)
qconnect(_sync_action.triggered, lambda: start_sync(manual=True))
_menu.addAction(_stats_action)
_menu.addAction(_sync_action)
mw.form.menuTools.addMenu(_menu)
