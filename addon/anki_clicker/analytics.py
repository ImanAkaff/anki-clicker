"""
Study insights, computed from the local review log.

Pure Python with no Anki imports, so it can be tested on simulated data.
__init__.py gathers the inputs (our log rows, plus a few lookups into Anki),
calls compute(), and hands the result to the React stats screen as JSON.

Every insight has a minimum-data gate. Until there's enough data to say
something trustworthy, the insight reports what it's waiting for instead of
guessing. Three sessions are not enough to call someone a "morning person".

Two timing measures are used throughout:
    think      = answer_shown - question_shown   (recalling before revealing)
    hesitation = answered - answer_shown         (deciding which button to press)
"""

# Lets type hints like `float | None` work on Python 3.9, which older Anki
# versions (e.g. 25.02) still ship. Without it the add-on fails to load there.
from __future__ import annotations

import html as _html
import re
import time
from statistics import median

SESSION_GAP_S = 10 * 60   # a pause longer than this starts a new session
AWAY_S = 120              # a single step longer than this = walked away; excluded
BIN_MIN = 5               # fatigue curve resolution, in minutes
EASE_NAMES = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


def html_to_text(s: str, limit: int = 90) -> str:
    """Card HTML -> one short line of plain text for display."""
    s = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</div>|</p>", " ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\[sound:[^\]]+\]", "", s)
    s = re.sub(r"\s+", " ", _html.unescape(s)).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _clean(rows: list[dict]) -> list[dict]:
    """Add think/hesitation/total, drop steps where the person walked away."""
    out = []
    for r in rows:
        if r.get("answer_shown") is None:
            continue
        think = r["answer_shown"] - r["question_shown"]
        hes = r["answered"] - r["answer_shown"]
        if think < 0 or hes < 0 or think > AWAY_S or hes > AWAY_S:
            continue
        out.append({**r, "think": think, "hes": hes, "total": think + hes})
    return out


def _sessions(rows: list[dict]) -> list[list[dict]]:
    """Group reviews into study sessions by gaps, not by Anki restarts:
    an hour-long break with Anki left open is really two sessions."""
    sessions, current, last_end = [], [], None
    for r in rows:
        if last_end is not None and r["question_shown"] - last_end > SESSION_GAP_S:
            sessions.append(current)
            current = []
        current.append(r)
        last_end = r["answered"]
    if current:
        sessions.append(current)
    return sessions


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


# ---------------------------------------------------------------- insights

def _overview(rows, sessions):
    now = time.time()
    lengths = [(s[-1]["answered"] - s[0]["question_shown"]) / 60 for s in sessions]
    return {
        "reviews": len(rows),
        "sessions": len(sessions),
        "study_minutes": round(sum(lengths)),
        "median_session_min": round(median(lengths), 1) if lengths else None,
        "median_think_s": round(median(r["think"] for r in rows), 2) if rows else None,
        "median_hesitation_s": round(median(r["hes"] for r in rows), 2) if rows else None,
        "again_rate_pct": _pct(sum(r["ease"] == 1 for r in rows), len(rows)),
        "reviews_last_7d": sum(r["answered"] > now - 7 * 86400 for r in rows),
    }


def _by_button(rows):
    out = []
    for ease in (1, 2, 3, 4):
        hs = [r["hes"] for r in rows if r["ease"] == ease]
        out.append({
            "ease": ease, "label": EASE_NAMES[ease], "count": len(hs),
            "median_hesitation_s": round(median(hs), 2) if hs else None,
        })
    return out


def _fatigue(sessions, min_sessions=3, min_len_min=20, min_bin_n=15, threshold=1.10):
    """
    How your pace changes as a session goes on.

    Each session is compared with ITSELF: a card's total time is divided by
    that session's median for its first 10 minutes. So 1.3 means "30% slower
    than you were when you started", which cancels out easy vs hard decks.
    Then we take the median of those ratios across sessions for each 5-minute bin.

    The slowdown point is the first bin (after the first 10 minutes) where
    you're at least 10% slower, and the bin after it is too, so a single
    bad stretch doesn't count.
    """
    long_sessions = [s for s in sessions
                     if (s[-1]["answered"] - s[0]["question_shown"]) / 60 >= min_len_min]
    if len(long_sessions) < min_sessions:
        return {"ready": False,
                "needs": f"{min_sessions} sessions of {min_len_min}+ minutes",
                "have": len(long_sessions)}

    bins: dict[int, dict] = {}
    for s in long_sessions:
        start = s[0]["question_shown"]
        warmup = [r["total"] for r in s if r["question_shown"] - start < 10 * 60]
        if len(warmup) < 5:
            continue
        base = median(warmup)
        for r in s:
            b = int((r["question_shown"] - start) / 60 // BIN_MIN) * BIN_MIN
            if b >= 60:
                continue
            e = bins.setdefault(b, {"ratios": [], "again": 0, "n": 0})
            e["ratios"].append(r["total"] / base)
            e["again"] += r["ease"] == 1
            e["n"] += 1

    points = []
    for b in sorted(bins):
        e = bins[b]
        if e["n"] < min_bin_n:
            continue
        points.append({
            "minute": b,
            "slowdown_pct": round((median(e["ratios"]) - 1) * 100, 1),
            "again_rate_pct": _pct(e["again"], e["n"]),
            "n": e["n"],
        })

    slow_at = None
    for i, p in enumerate(points):
        if p["minute"] < 10:
            continue
        nxt = points[i + 1] if i + 1 < len(points) else p
        if p["slowdown_pct"] >= (threshold - 1) * 100 and nxt["slowdown_pct"] >= (threshold - 1) * 100:
            slow_at = p["minute"]
            break

    return {"ready": True, "sessions_used": len(long_sessions), "points": points,
            "slowdown_minute": slow_at, "threshold_pct": round((threshold - 1) * 100)}


def _shaky_cards(rows, card_preview, deck_name, min_good=30, limit=10):
    """
    Cards you got right, but slowly. For each card we look at its most recent
    Good/Easy press. If the hesitation before that press was in your slowest
    20% of Good/Easy presses, you knew it, but only just.
    """
    goods = [r for r in rows if r["ease"] in (3, 4)]
    if len(goods) < min_good:
        return {"ready": False, "needs": f"{min_good} Good/Easy answers", "have": len(goods)}
    cutoff = max(_quantile([r["hes"] for r in goods], 0.8), 1.5)

    latest: dict[int, dict] = {}
    for r in rows:  # rows are oldest-first, so the last one written wins
        latest[r["card_id"]] = r
    reviews_per_card: dict[int, int] = {}
    for r in rows:
        reviews_per_card[r["card_id"]] = reviews_per_card.get(r["card_id"], 0) + 1

    shaky = sorted(
        (r for r in latest.values() if r["ease"] in (3, 4) and r["hes"] >= cutoff),
        key=lambda r: r["hes"], reverse=True,
    )
    cards = []
    for r in shaky[:limit]:
        cards.append({
            "card_id": r["card_id"],
            "text": (card_preview(r["card_id"]) if card_preview else None) or "(card deleted)",
            "deck": deck_name(r["deck_id"]) if deck_name and r.get("deck_id") else None,
            "hesitation_s": round(r["hes"], 1),
            "button": EASE_NAMES[r["ease"]],
            "times_reviewed": reviews_per_card[r["card_id"]],
        })
    return {"ready": True, "cutoff_s": round(cutoff, 1), "count": len(shaky), "cards": cards}


def _hesitation_predicts(rows, next_answers, min_pairs=40):
    """
    The research question: does a slow Good today predict forgetting next time?
    Pairs each Good/Easy press with what happened the NEXT time Anki showed
    that card, then compares how often slow vs fast presses led to Again.
    """
    pairs = [(r["hes"], next_answers[r["revlog_id"]])
             for r in rows
             if r["ease"] in (3, 4) and r.get("revlog_id") in next_answers]
    if len(pairs) < min_pairs:
        return {"ready": False, "needs": f"{min_pairs} cards seen again after a Good/Easy",
                "have": len(pairs)}
    mid = median(h for h, _ in pairs)
    slow = [nxt for h, nxt in pairs if h >= mid]
    fast = [nxt for h, nxt in pairs if h < mid]
    return {
        "ready": True, "pairs": len(pairs), "split_s": round(mid, 1),
        "slow_forgot_pct": _pct(sum(e == 1 for e in slow), len(slow)), "slow_n": len(slow),
        "fast_forgot_pct": _pct(sum(e == 1 for e in fast), len(fast)), "fast_n": len(fast),
    }


TIME_BLOCKS = [("Morning", 5, 12), ("Afternoon", 12, 17), ("Evening", 17, 22), ("Late night", 22, 29)]


def _time_of_day(rows, min_per_block=30):
    """Accuracy and pace by time of day (local time). A block is only
    compared once it has enough reviews; the 'best' needs two to compare."""
    blocks = []
    for name, start, end in TIME_BLOCKS:
        sel = []
        for r in rows:
            h = time.localtime(r["question_shown"]).tm_hour
            h = h + 24 if h < 5 else h
            if start <= h < end:
                sel.append(r)
        blocks.append({
            "block": name, "reviews": len(sel),
            "again_rate_pct": _pct(sum(r["ease"] == 1 for r in sel), len(sel)),
            "median_s_per_card": round(median(r["total"] for r in sel), 1) if sel else None,
            "enough": len(sel) >= min_per_block,
        })
    qualified = [b for b in blocks if b["enough"]]
    best = min(qualified, key=lambda b: b["again_rate_pct"])["block"] if len(qualified) >= 2 else None
    return {"blocks": blocks, "best": best, "min_per_block": min_per_block}


# ---------------------------------------------------------------- filters: deck and date range
#
# Every review row carries its deck and its timestamps, so "one deck" or
# "this week" is just a filter applied before the insights are computed.
# The insights themselves don't change; they just see fewer rows.

RANGES = {"today": 1, "7d": 7, "30d": 30, "all": None}   # days, counting today


def _day_start(ts: float) -> float:
    """Local midnight at the start of the day containing ts."""
    t = time.localtime(ts)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def range_start(range_key: str, now: float) -> float | None:
    """'7d' -> local midnight 6 days ago (today plus the 6 days before it).
    Calendar days, not 'the last 168 hours', so 'today' means since midnight."""
    days = RANGES.get(range_key)
    if days is None:
        return None
    start = _day_start(now)
    for _ in range(days - 1):
        start = _day_start(start - 12 * 3600)   # step back one calendar day (DST-safe)
    return start


def in_deck(name: str | None, selected: str | None) -> bool:
    """Picking a deck includes its subdecks: 'Bio' matches 'Bio::Cells'.
    Anki separates deck levels with '::'."""
    if not selected:
        return True
    return name == selected or (name or "").startswith(selected + "::")


def deck_options(rows: list[dict]) -> list[dict]:
    """Every deck with reviews, plus its parent decks, for the deck picker.
    A parent's count includes its subdecks, since picking it includes them."""
    counts: dict[str, int] = {}
    for r in rows:
        parts = (r.get("deck") or "Unknown deck").split("::")
        for i in range(1, len(parts) + 1):
            name = "::".join(parts[:i])
            counts[name] = counts.get(name, 0) + 1
    return [{"name": n, "reviews": c} for n, c in sorted(counts.items(), key=lambda kv: kv[0].lower())]


def _daily(rows: list[dict], start: float | None, now: float) -> list[dict]:
    """One point per calendar day from the start of the range (or the first
    review) to today. Days with no reviews are kept, with empty values, so
    the chart shows the gap instead of joining Monday straight to Friday."""
    if not rows and start is None:
        return []
    first = start if start is not None else _day_start(rows[0]["question_shown"])
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(time.strftime("%Y-%m-%d", time.localtime(r["question_shown"])), []).append(r)
    out, day = [], _day_start(first)
    while day <= now:
        key = time.strftime("%Y-%m-%d", time.localtime(day))
        sel = by_day.get(key, [])
        out.append({
            "date": key,
            "label": time.strftime("%b %d", time.localtime(day)).replace(" 0", " "),
            "reviews": len(sel),
            "median_hesitation_s": round(median(r["hes"] for r in sel), 2) if sel else None,
            "median_think_s": round(median(r["think"] for r in sel), 2) if sel else None,
            "again_rate_pct": _pct(sum(r["ease"] == 1 for r in sel), len(sel)),
        })
        day = _day_start(day + 36 * 3600)   # next calendar day (DST-safe)
    return out


def _deck_table(rows: list[dict], min_reviews=10) -> list[dict]:
    """Each deck's numbers side by side, hardest first (highest Again rate).
    Decks with only a few reviews go to the bottom: 1 Again out of 2 cards
    is a 50% Again rate, but it says almost nothing."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("deck") or "Unknown deck", []).append(r)
    table = [{
        "deck": name,
        "reviews": len(sel),
        "median_hesitation_s": round(median(r["hes"] for r in sel), 2),
        "again_rate_pct": _pct(sum(r["ease"] == 1 for r in sel), len(sel)),
        "enough": len(sel) >= min_reviews,
    } for name, sel in groups.items()]
    table.sort(key=lambda d: (not d["enough"], -(d["again_rate_pct"] or 0), -d["reviews"]))
    return table


def compute(rows, next_answers=None, card_preview=None, deck_name=None,
            deck: str | None = None, range_key: str = "all", now: float | None = None) -> dict:
    """
    rows:  every logged review, oldest first.
    deck:  a deck name to focus on (includes its subdecks), or None for all.
    range_key: 'today', '7d', '30d' or 'all'.

    Everything is computed from the filtered rows EXCEPT the fatigue curve,
    which always uses all decks and all time: it measures how you slow down
    over a sitting, and sittings often mix decks, so splitting it would mostly
    cut sessions in half and leave too few long ones to measure.
    """
    now = now or time.time()
    if range_key not in RANGES:
        range_key = "all"
    names: dict = {}
    def name_of(did):
        if did not in names:
            names[did] = (deck_name(did) if deck_name and did else None) or "Unknown deck"
        return names[did]

    everything = [{**r, "deck": name_of(r.get("deck_id"))} for r in _clean(rows)]
    start = range_start(range_key, now)
    clean = [r for r in everything
             if (start is None or r["question_shown"] >= start) and in_deck(r["deck"], deck)]
    sessions = _sessions(clean)
    return {
        "generated_at": now,
        "filters": {
            "deck": deck, "range": range_key,
            "decks": deck_options(everything),
            "total_reviews": len(everything),
        },
        "overview": _overview(clean, sessions),
        "by_button": _by_button(clean),
        "daily": _daily(clean, start, now),
        # The deck table follows the date range. With a deck picked, it lists
        # that deck and its subdecks, so you can see which part is hardest.
        "decks": _deck_table(clean),
        "fatigue": _fatigue(_sessions(everything)),
        "shaky": _shaky_cards(clean, card_preview, deck_name),
        "predicts": _hesitation_predicts(clean, next_answers or {}),
        "time_of_day": _time_of_day(clean),
    }
