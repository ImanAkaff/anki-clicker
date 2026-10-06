"""
Fake friends doing fake reviews, so you can build and test everything
without anyone using the add-on yet.

The important idea: we PLANT patterns on purpose and write down where we
planted them (ground_truth.json). Later, when you build fatigue detection,
you run it on this data and check: did it find the fatigue at the minute
we planted it? If yes, you have evidence it works before trusting it on
real people. This is how data teams validate models.

Planted patterns, per fake user:
  - A personal baseline think time and hesitation (everyone's different).
  - Fatigue: after `fatigue_onset_min` minutes into a session, think time,
    hesitation and the chance of pressing Again all creep upward.
  - Hard decisions are slower: hesitation is longer on Again/Hard presses.
  - Evening bias: most sessions start between 6pm and 11pm.

Usage (uses only the Python standard library, nothing to install):

  # Write to a file, no server needed:
  python simulate.py --users 5 --days 14 --out sim_reviews.jsonl

  # Or create users on your running server and upload to it:
  python simulate.py --users 5 --days 14 \
      --server http://127.0.0.1:8000 --admin-token YOUR_ADMIN_TOKEN
"""

import argparse
import json
import math
import random
import time
import urllib.request
import uuid
from dataclasses import asdict, dataclass


@dataclass
class FakeUser:
    name: str
    think_median_s: float      # typical time to recall before revealing
    hesitation_median_s: float # typical time to pick a button after revealing
    base_again_rate: float     # chance of Again when fresh
    fatigue_onset_min: float   # minutes into a session when fatigue begins
    fatigue_rate: float        # how fast things get worse after onset (per minute)


def make_user(rng: random.Random, i: int) -> FakeUser:
    return FakeUser(
        name=f"sim-user-{i}",
        think_median_s=rng.uniform(3.5, 9.0),
        hesitation_median_s=rng.uniform(0.6, 1.8),
        base_again_rate=rng.uniform(0.08, 0.18),
        fatigue_onset_min=max(15.0, rng.gauss(35, 8)),
        fatigue_rate=rng.uniform(0.015, 0.035),
    )


def lognormal_around(rng: random.Random, median: float, spread: float = 0.45) -> float:
    """Reaction times are right-skewed: mostly near the median, occasionally
    much longer, never negative. A log-normal distribution has that shape."""
    return median * math.exp(rng.gauss(0, spread))


def simulate_session(rng, user: FakeUser, start_ts: float, session_id: str, deck_id: int):
    length_min = rng.uniform(15, 60)
    t = start_ts
    reviews = []
    while (t - start_ts) / 60 < length_min:
        minutes_in = (t - start_ts) / 60
        # 1.0 while fresh; grows linearly once past the fatigue onset.
        fatigue = 1.0 + max(0.0, minutes_in - user.fatigue_onset_min) * user.fatigue_rate

        p_again = min(0.6, user.base_again_rate * fatigue**2)
        roll = rng.random()
        if roll < p_again:
            ease = 1
        elif roll < p_again + 0.12:
            ease = 2
        elif roll < 0.95:
            ease = 3
        else:
            ease = 4

        think = lognormal_around(rng, user.think_median_s * fatigue)
        hard_choice = 1.6 if ease in (1, 2) else 1.0
        hesitation = lognormal_around(rng, user.hesitation_median_s * fatigue * hard_choice)

        q = t
        a = q + think
        done = a + hesitation
        reviews.append(
            dict(
                client_id=uuid.uuid4().hex,
                session_id=session_id,
                # Realistic Anki IDs: creation time in milliseconds (~1.7 trillion).
                card_id=1_700_000_000_000 + rng.randint(1, 3000),
                deck_id=1_600_000_000_000 + deck_id,
                question_shown=round(q, 3),
                answer_shown=round(a, 3),
                answered=round(done, 3),
                ease=ease,
                input_source="gamepad",
            )
        )
        t = done + rng.uniform(0.2, 0.8)  # brief gap while the next card loads
    return reviews


def simulate_user(rng, user: FakeUser, days: int, now: float):
    reviews = []
    for d in range(days, 0, -1):
        if rng.random() < 0.2:
            continue  # skipped a day
        for _ in range(rng.choice([1, 1, 2])):
            day_start = now - d * 86400
            start = day_start - (day_start % 86400) + rng.uniform(18, 23) * 3600
            reviews += simulate_session(
                rng, user, start, session_id=uuid.uuid4().hex[:12], deck_id=rng.choice([1, 2, 3])
            )
    return reviews


# ---------------------------------------------------------------- talking to the server

def post_json(url: str, payload: dict, token: str) -> dict:
    """A raw HTTP POST using only the standard library. This is the same thing
    the add-on does, so reading it here explains the add-on's sync code too."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),  # body: JSON text as bytes
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--users", type=int, default=5)
    p.add_argument("--days", type=int, default=14)
    p.add_argument("--seed", type=int, default=42,
                   help="same seed = exactly the same fake data every run")
    p.add_argument("--out", help="write reviews to this .jsonl file")
    p.add_argument("--server", help="e.g. http://127.0.0.1:8000")
    p.add_argument("--admin-token")
    args = p.parse_args()
    if not args.out and not args.server:
        p.error("give --out, or --server with --admin-token")

    rng = random.Random(args.seed)
    now = time.time()
    truth = {}
    out = open(args.out, "w") if args.out else None

    for i in range(1, args.users + 1):
        user = make_user(rng, i)
        reviews = simulate_user(rng, user, args.days, now)
        truth[user.name] = asdict(user) | {"reviews": len(reviews)}

        if out:
            for r in reviews:
                out.write(json.dumps({"user": user.name, **r}) + "\n")

        if args.server:
            created = post_json(f"{args.server}/v1/admin/users", {"name": user.name}, args.admin_token)
            truth[user.name]["token"] = created["token"]
            for start in range(0, len(reviews), 500):  # server accepts up to 1000 per batch
                post_json(f"{args.server}/v1/reviews",
                          {"reviews": reviews[start:start + 500]}, created["token"])

        print(f"{user.name}: {len(reviews):5d} reviews, "
              f"fatigue planted at {user.fatigue_onset_min:4.1f} min")

    if out:
        out.close()
    with open("ground_truth.json", "w") as f:
        json.dump(truth, f, indent=2)
    print("Wrote ground_truth.json (what was planted, to check your analysis against)")


if __name__ == "__main__":
    main()
