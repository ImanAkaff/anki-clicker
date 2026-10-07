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
import tempfile
import time
import uuid

from aqt import gui_hooks, mw
from aqt.qt import QAction, QDialog, QMenu, QUrl, QVBoxLayout, qconnect
from aqt.reviewer import Reviewer
from aqt.utils import askUser, getText, showInfo, tooltip
from aqt.webview import AnkiWebView

from . import analytics, storage, sync, updater

# ---------------------------------------------------------------- paths & setup

# user_files/ is the one folder Anki keeps when an add-on is updated.
ADDON_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(ADDON_DIR, "user_files")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "clicker.sqlite3")

# This copy's version, from manifest.json. The release pipeline stamps it
# there from the git tag (v1.2.0 -> "1.2.0"), so it never has to be edited by hand.
try:
    with open(os.path.join(ADDON_DIR, "manifest.json"), encoding="utf-8") as _f:
        VERSION = json.load(_f).get("human_version", "0.0.0")
except Exception:
    VERSION = "0.0.0"

# The add-on's folder name, as Anki knows it (needed for web file URLs).
ADDON_PACKAGE = mw.addonManager.addonFromModule(__name__)

# Allow Anki's web views to load files from our web/ folder.
mw.addonManager.setWebExports(__name__, r"web/.*\.(js|css|html|svg|json)")

SESSION_ID = uuid.uuid4().hex[:12]  # new ID each time Anki starts

ACTIONS_TO_EASE = {"again": 1, "hard": 2, "good": 3, "easy": 4}
SCROLL_STEP_PX = 250  # one d-pad press scrolls this far

# Defaults that apply even if someone's saved config predates them. Anki keeps
# a user's own button_map as-is, so new default buttons wouldn't otherwise
# reach people who installed an earlier version. Their own mappings still win.
DEFAULT_BUTTONS = {"12": "scroll_up", "13": "scroll_down"}  # d-pad up/down
EASE_NAMES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def config() -> dict:
    # Anki merges config.json with any changes the user made in
    # Tools > Add-ons > Config, and returns the result as a dict.
    return mw.addonManager.getConfig(__name__) or {}


# The main-thread connection. Anki's hooks all run on the main thread, so
# this one connection is only ever used from there.
_db = storage.connect(DB_PATH)

# Diagnostic log: each step of opening the stats window is written here,
# so problems can be traced without guessing. Kept small: newest 200 lines.
DEBUG_LOG = os.path.join(DATA_DIR, "debug.log")


def debug(msg: str) -> None:
    try:
        lines = []
        if os.path.exists(DEBUG_LOG):
            with open(DEBUG_LOG, encoding="utf-8") as f:
                lines = f.readlines()[-199:]
        lines.append(time.strftime("%H:%M:%S ") + msg.replace("\n", " ")[:500] + "\n")
        with open(DEBUG_LOG, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except Exception:
        pass  # logging must never break the add-on


# ---------------------------------------------------------------- 1. load gamepad.js into the reviewer

def on_webview_set_content(web_content, context) -> None:
    # Many screens in Anki are web views; only add our script to the reviewer.
    if isinstance(context, Reviewer):
        debug("review screen: adding controller script")
        cfg = config()
        # Settings gamepad.js reads when it starts: which stick axis scrolls.
        web_content.head += (
            "<script>"
            f"window.ankiClickerScrollAxis = {int(cfg.get('scroll_axis', 1))};"
            f"window.ankiClickerScrollInvert = {'true' if cfg.get('scroll_invert') else 'false'};"
            "</script>"
        )
        web_content.js.append(f"/_addons/{ADDON_PACKAGE}/web/gamepad.js")


# ---------------------------------------------------------------- 2. messages from gamepad.js

def on_js_message(handled, message: str, context):
    """Every pycmd(...) from any web view arrives here. We only claim messages
    starting with 'ankiclicker:' and pass everything else along untouched,
    so we don't break other add-ons."""
    if not message.startswith("ankiclicker:"):
        return handled

    if message == "ankiclicker:stats":
        # The stats page asking for its data. Whatever we return here, Anki
        # converts to JSON and hands to the page's callback.
        debug(f"page asked for data; have payload: {_stats_payload is not None}")
        return (True, _stats_payload)
    if message.startswith("ankiclicker:log:"):
        debug("page: " + message[len("ankiclicker:log:"):])
        return (True, None)

    _, kind, payload = message.split(":", 2)
    if kind in ("status", "connected", "diag"):
        debug(f"controller {kind}: {payload}")
    if kind == "btn":
        debug(f"controller button pressed: {payload}")
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

    action = {**DEFAULT_BUTTONS, **config().get("button_map", {})}.get(button_id)
    if action is None:
        # This doubles as the way to discover button numbers.
        tooltip(f"Button {button_id} isn't mapped. Add it in Tools > Add-ons > Config.")
        return

    if action == "undo":
        mw.undo()
        return

    if action in ("scroll_up", "scroll_down"):
        # Works on both sides of the card (question and answer).
        dy = SCROLL_STEP_PX if action == "scroll_down" else -SCROLL_STEP_PX
        reviewer.web.eval(f"window.scrollBy({{top: {dy}, behavior: 'smooth'}});")
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
            showInfo("Not connected yet. Use Tools > Anki Clicker > Connect… and paste your code.")
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
            # period: milliseconds on screen. Long enough to notice after a
            # sync that waited up to a minute for the server to wake up.
            tooltip(_last_sync_message, period=8000)

    # Anki's built-in helper: run `task` on a worker thread, then `on_done`
    # on the main thread with the result.
    mw.taskman.run_in_background(task, on_done)


# ---------------------------------------------------------------- 5. connect dialog

TOKEN_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def connect_dialog() -> None:
    """
    Friends paste the code you sent them; we save it to the add-on config and
    sync right away. This replaces hand-editing the JSON config, which is easy
    to break (a missing quote or comma and Anki refuses to save).
    """
    code, ok = getText(
        "Paste the code Azeem sent you:",
        parent=mw,
        title="Connect Anki Clicker",
    )
    if not ok:
        return
    code = code.strip().strip('"').strip("'").strip()  # forgive stray quotes/spaces
    if "/" in code or "=" in code:
        # The admin token is base64 (has / and =); user tokens never do.
        showInfo("That looks like an admin token, not your personal code. "
                 "Use the code Azeem sent you.")
        return
    if len(code) < 20 or not set(code) <= TOKEN_CHARS:
        showInfo("That doesn't look like a valid code. Copy it again, without "
                 "any extra text, and try once more.")
        return
    cfg = config()
    cfg["token"] = code
    cfg["sync_enabled"] = True
    if not cfg.get("server_url"):
        cfg["server_url"] = "https://anki-clicker-api.onrender.com"
    # writeConfig saves to meta.json in the add-on folder (the same place the
    # Config window saves to). It survives add-on updates.
    mw.addonManager.writeConfig(__name__, cfg)
    tooltip("Connected. Uploading your reviews… (can take up to a minute)", period=6000)
    start_sync(manual=True)


# ---------------------------------------------------------------- 6. stats window (React)

class StatsDialog(QDialog):
    """
    A window holding an AnkiWebView: the same built-in browser Anki uses for
    cards. It loads our React app (web/stats/index.html, built by Vite) from
    Anki's local web server, then hands it the numbers as JSON.

    Python computes everything (analytics.py); React only draws. That split
    keeps the maths testable without a browser, and the page simple.
    """

    def __init__(self, payload: dict):
        super().__init__(mw)
        self.setWindowTitle("Anki Clicker: your study insights")
        self.resize(1000, 780)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = AnkiWebView(parent=self)
        layout.addWidget(self.web)
        self._payload = json.dumps(payload)
        # When the page has finished loading, push the data into it.
        qconnect(self.web.loadFinished, self._on_loaded)
        url = f"{mw.serverURL()}_addons/{ADDON_PACKAGE}/web/stats/index.html"
        debug(f"opening stats window: {url} (payload {len(self._payload)} chars)")
        # By default Anki treats any page that isn't one of its own as an
        # external link: it refuses to load it in the window (loadFinished
        # reports ok=False) and opens it in the system browser instead.
        # This tells the window to load our page itself.
        self.web.set_open_links_externally(False)
        self.web.load_url(QUrl(url))

    def _on_loaded(self, ok: bool) -> None:
        debug(f"page finished loading, ok={ok}")
        if ok:
            self.web.eval(f"window.ankiClickerSetData({self._payload});")


def _card_preview(card_id: int) -> str | None:
    """Front of the card as plain text, for the shaky-cards list. Shown only
    on this computer; card text is never uploaded."""
    try:
        html = mw.col.get_card(card_id).question()
    except Exception:
        return None  # card was deleted since it was reviewed
    return analytics.html_to_text(html)


def _next_answers(rows: list[dict]) -> dict[int, int]:
    """For each logged review, find the NEXT time Anki saw that card and what
    button was pressed then (from Anki's revlog). This is how we test whether
    a slow 'Good' today predicts an 'Again' next time."""
    by_card: dict[int, list[tuple[int, int]]] = {}
    card_ids = list({r["card_id"] for r in rows if r.get("revlog_id")})
    for start in range(0, len(card_ids), 500):
        chunk = card_ids[start:start + 500]
        q = ",".join("?" * len(chunk))
        for rid, cid, ease in mw.col.db.all(
            f"SELECT id, cid, ease FROM revlog WHERE cid IN ({q}) AND ease > 0 ORDER BY id", *chunk
        ):
            by_card.setdefault(cid, []).append((rid, ease))
    nxt = {}
    for r in rows:
        rid = r.get("revlog_id")
        if not rid:
            continue
        for later_id, ease in by_card.get(r["card_id"], []):
            if later_id > rid:
                nxt[rid] = ease
                break
    return nxt


_stats_window = None  # keep a reference, or Python garbage-collects the window
_stats_payload = None  # the numbers the open stats page asks for


def show_stats() -> None:
    global _stats_window, _stats_payload
    debug("--- Stats clicked")
    removed = remove_undone_reviews()
    if removed:
        tooltip(f"Removed {removed} undone review(s) from the log.")
    rows = storage.all_reviews(_db)
    try:
        from aqt.theme import theme_manager
        night = bool(theme_manager.night_mode)
    except Exception:
        night = False
    payload = analytics.compute(
        rows,
        next_answers=_next_answers(rows),
        card_preview=_card_preview,
        deck_name=lambda did: (mw.col.decks.name_if_exists(did) or "Deleted deck"),
    )
    payload["meta"] = {
        "night_mode": night,
        "unsynced": storage.summary(_db)["unsynced"],
        "last_sync": _last_sync_message,
        "connected": bool(config().get("token")),
    }
    _stats_payload = payload
    _stats_window = StatsDialog(payload)
    _stats_window.show()


# ---------------------------------------------------------------- 7. register everything with Anki

# ---------------------------------------------------------------- 8. updates from GitHub Releases

def _is_dev_copy() -> bool:
    """True on Azeem's own machine, where the add-on folder is a link to his
    project. Installing an update there would overwrite his source code
    through the link, so the updater stays off."""
    try:
        if os.path.islink(ADDON_DIR) or getattr(os.path, "isjunction", lambda p: False)(ADDON_DIR):
            return True
        return os.path.realpath(ADDON_DIR) != os.path.abspath(ADDON_DIR)
    except Exception:
        return True  # unsure = don't touch anything


def check_for_updates(manual: bool = False) -> None:
    if _is_dev_copy():
        if manual:
            showInfo("Updates are off on this computer: it runs the add-on straight from "
                     "your project folder.")
        return
    if not manual and config().get("auto_update") is False:
        return

    def ask_github():
        return updater.check_latest(VERSION)  # background thread: network only

    def on_checked(future):
        try:
            info = future.result()
        except Exception as e:
            debug(f"update check failed: {e}")
            info = None
        if not info:
            if manual:
                tooltip(f"Anki Clicker {VERSION} is the latest version.", period=5000)
            return
        notes = f"\n\nWhat's new:\n{info['notes']}" if info["notes"] else ""
        if not askUser(f"Anki Clicker {info['version']} is available (you have {VERSION}).{notes}"
                       "\n\nUpdate now? Your review history and settings are kept."):
            return
        tooltip("Downloading the update…", period=4000)
        dest = os.path.join(tempfile.gettempdir(), updater.ASSET_NAME)
        mw.taskman.run_in_background(lambda: updater.download(info["download_url"], dest),
                                     lambda f: _install_update(f, info["version"]))

    mw.taskman.run_in_background(ask_github, on_checked)


def _install_update(future, version: str) -> None:
    global _db
    try:
        path = future.result()
    except Exception as e:
        debug(f"update download failed: {e}")
        showInfo("Couldn't download the update. It will be offered again next time Anki starts.")
        return
    # Anki moves user_files aside while it replaces the add-on. On Windows a
    # folder can't be moved while a file inside it is open, so close our log
    # database first, then reopen it once the new files are in place.
    _db.close()
    try:
        result = mw.addonManager.install(path)
    finally:
        _db = storage.connect(DB_PATH)
    if getattr(result, "errmsg", None):
        debug(f"update install failed: {result.errmsg}")
        showInfo(f"The update couldn't be installed ({result.errmsg}). Nothing was changed.")
        return
    debug(f"updated to {version}")
    showInfo(f"Anki Clicker updated to {version}. Restart Anki to start using it.")


def _safe(fn, passthrough_first_arg=False):
    """
    Wrap a hook so a bug in THIS add-on can never interrupt someone's
    reviews. Anki calls these during every card; if one raised an error,
    Anki would show an error popup mid-review. Instead the error goes to
    debug.log and the review carries on exactly as if the add-on weren't
    installed. (The cost: a broken feature fails silently, so check
    debug.log when something seems off.)
    """
    def wrapper(*args):
        try:
            return fn(*args)
        except Exception as e:
            debug(f"error in {fn.__name__}: {type(e).__name__}: {e}")
            # The JS-message hook must hand back what it received, or other
            # add-ons further down the chain would lose their messages.
            return args[0] if passthrough_first_arg else None
    return wrapper


gui_hooks.webview_will_set_content.append(_safe(on_webview_set_content))
gui_hooks.webview_did_receive_js_message.append(_safe(on_js_message, passthrough_first_arg=True))
gui_hooks.reviewer_did_show_question.append(_safe(on_show_question))
gui_hooks.reviewer_did_show_answer.append(_safe(on_show_answer))
gui_hooks.reviewer_did_answer_card.append(_safe(on_answer))
gui_hooks.reviewer_will_end.append(_safe(start_sync))   # upload when they leave the review screen
gui_hooks.profile_did_open.append(_safe(start_sync))    # and catch up on anything left from last time
gui_hooks.profile_did_open.append(_safe(check_for_updates))  # once per Anki start

_menu = QMenu("Anki Clicker", mw)
for _label, _fn in (
    ("Stats", show_stats),
    ("Sync now", lambda: start_sync(manual=True)),
    ("Connect…", connect_dialog),
    (f"Check for updates (v{VERSION})", lambda: check_for_updates(manual=True)),
):
    _action = QAction(_label, mw)
    qconnect(_action.triggered, _fn)
    _menu.addAction(_action)
mw.form.menuTools.addMenu(_menu)
