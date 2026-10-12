"""
Loads the whole add-on against a minimal stand-in for Anki (tests/aqt/) and
runs its main paths: answering cards, the stats payload, the Connect dialog,
and crash isolation.

Run:  python addon/tests/smoke_test.py
GitHub runs it on the oldest and newest Python that Anki ships (3.9 and 3.13)
before every release, so code that only works on newer Python can't ship.
That's exactly how v1.0.2 broke on Anki 25.02.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
# Copy the add-on somewhere temporary so the test never writes into the repo.
work = tempfile.mkdtemp()
shutil.copytree(os.path.join(HERE, "..", "anki_clicker"), os.path.join(work, "anki_clicker"),
                ignore=shutil.ignore_patterns("user_files", "__pycache__", "meta.json"))
sys.path[:0] = [HERE, work]

import aqt  # noqa: E402  (the stand-in)
import aqt.utils  # noqa: E402
import aqt.webview  # noqa: E402
import anki_clicker as ac  # noqa: E402  <- fails here if the add-on can't load on this Python

failures = []
def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)

# Answer a few cards through Anki's hooks
db = aqt.mw.col.db
for i in range(5):
    card = types.SimpleNamespace(id=1_712_000_000_000 + i, did=1)
    ac.on_show_question(card); time.sleep(0.005); ac.on_show_answer(card); time.sleep(0.005)
    db.c.execute("insert into revlog values (?,?,?)", (int(time.time() * 1000) + i, card.id, 3))
    ac.on_answer(None, card, 3)
check("reviews are logged", ac.storage.summary(ac._db)["total"] == 5)
check("log database uses WAL", ac._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal")

# Two more: one in MCAT::Chem, one answered while in a filtered deck (custom
# study). The filtered one should be logged under its real deck (odid = 2).
for i, (did, odid) in enumerate([(2, 0), (99, 2)]):
    card = types.SimpleNamespace(id=1_712_000_000_100 + i, did=did, odid=odid)
    ac.on_show_question(card); time.sleep(0.005); ac.on_show_answer(card); time.sleep(0.005)
    ac.on_answer(None, card, 1)
check("filtered-deck review logged under its home deck",
      [r[0] for r in ac._db.execute("select deck_id from reviews order by rowid")][-2:] == [2, 2])
# And an old-style row (before 1.0.4) that still points at the filtered deck:
ac._db.execute("update reviews set deck_id = 99 where card_id = ?", (1_712_000_000_101,)); ac._db.commit()
db.c.execute("insert into cards values (?, ?, ?)", (1_712_000_000_101, 99, 2))

# Stats window gets a full payload, and the page's data request is answered
ac.show_stats()
web = aqt.webview.AnkiWebView.last
check("stats page URL", web.url.endswith("/_addons/anki_clicker/web/stats/index.html"))
handled, payload = ac.on_js_message((False, None), "ankiclicker:stats", None)
check("stats payload", handled and payload["overview"]["reviews"] == 7)
check("old filtered-deck rows count under the home deck",
      [d["name"] for d in payload["filters"]["decks"]] == ["MCAT", "MCAT::Bio", "MCAT::Chem"])
check("deck table", {d["deck"]: d["reviews"] for d in payload["decks"]} == {"MCAT::Bio": 5, "MCAT::Chem": 2})
check("today has a daily point", payload["daily"][-1]["reviews"] == 7)

# The page asks again with a deck and date range picked
handled, chem = ac.on_js_message((False, None), 'ankiclicker:stats:{"deck": "MCAT::Chem", "range": "7d"}', None)
check("deck filter", handled and chem["overview"]["reviews"] == 2 and chem["overview"]["again_rate_pct"] == 100.0)
check("7-day range gives 7 daily points", len(chem["daily"]) == 7)
_, parent = ac.on_js_message((False, None), 'ankiclicker:stats:{"deck": "MCAT", "range": "today"}', None)
check("parent deck includes subdecks", parent["overview"]["reviews"] == 7)
check("other add-ons' messages pass through", ac.on_js_message((False, "x"), "other:msg", None) == (False, "x"))

# Connect dialog rejects an admin token and saves a real code
aqt.utils.answers[:] = [("S82iGw3wG8ubgg6Tvpnv9kLuEOa9tly/L9KAdKAm9DY=", True), ("abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG", True)]
ac.connect_dialog(); ac.connect_dialog()
check("connect saves a valid code", aqt.mw.addonManager.written["token"] == "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG")

# A crash inside a hook is contained
aqt.mw.col.db.scalar = lambda *a: (_ for _ in ()).throw(RuntimeError("simulated"))
for h in aqt.gui_hooks.reviewer_did_answer_card:
    h(None, types.SimpleNamespace(id=1, did=1), 3)
check("hook errors don't reach Anki", True)

print(f"\nPython {sys.version.split()[0]}: {'all passed' if not failures else str(len(failures)) + ' failed'}")
sys.exit(1 if failures else 0)
