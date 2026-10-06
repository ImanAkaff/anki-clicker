"""
Run from the server/ folder with:  pytest -v

Each test describes one promise the server makes. If you change the code
later and break a promise, the matching test fails and tells you which one.
"""

import uuid


def review(**overrides):
    base = dict(
        client_id=uuid.uuid4().hex,
        session_id="s1",
        card_id=1,
        deck_id=1,
        question_shown=1000.0,
        answer_shown=1004.0,
        answered=1005.5,
        ease=3,
        input_source="gamepad",
    )
    base.update(overrides)
    return base


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_upload_requires_token(client):
    r = client.post("/v1/reviews", json={"reviews": [review()]})
    assert r.status_code == 401


def test_wrong_token_rejected(client):
    r = client.post("/v1/reviews", json={"reviews": [review()]}, headers=auth("nope"))
    assert r.status_code == 401


def test_only_admin_can_create_users(client, user_token):
    # A normal friend's token must not be able to create accounts.
    r = client.post("/v1/admin/users", json={"name": "x"}, headers=auth(user_token))
    assert r.status_code == 403


def test_upload_and_retry_is_idempotent(client, user_token):
    batch = {"reviews": [review(), review(), review()]}

    first = client.post("/v1/reviews", json=batch, headers=auth(user_token)).json()
    assert first == {"received": 3, "inserted": 3, "duplicates": 0}

    # Same batch again, as if the first "200 OK" got lost and the add-on retried.
    second = client.post("/v1/reviews", json=batch, headers=auth(user_token)).json()
    assert second == {"received": 3, "inserted": 0, "duplicates": 3}


def test_duplicate_inside_one_batch(client, user_token):
    r = review()
    out = client.post("/v1/reviews", json={"reviews": [r, r]}, headers=auth(user_token)).json()
    assert out["inserted"] == 1 and out["duplicates"] == 1


def test_real_anki_ids_fit(client, user_token):
    # Anki card and deck IDs are creation times in milliseconds, around
    # 1,700,000,000,000. That's far past the ~2.1 billion limit of a normal
    # 32-bit integer column, so the columns must be 64-bit (BIGINT).
    # SQLite doesn't enforce the limit; Postgres does. This test caught it.
    big = 1_712_345_678_901
    r = client.post(
        "/v1/reviews",
        json={"reviews": [review(card_id=big, deck_id=big + 1)]},
        headers=auth(user_token),
    )
    assert r.status_code == 200 and r.json()["inserted"] == 1


def test_bad_data_rejected(client, user_token):
    # ease 7 doesn't exist
    r = client.post("/v1/reviews", json={"reviews": [review(ease=7)]}, headers=auth(user_token))
    assert r.status_code == 422
    # answered before the question was shown
    r = client.post(
        "/v1/reviews", json={"reviews": [review(answered=10.0)]}, headers=auth(user_token)
    )
    assert r.status_code == 422


def test_summary_math(client, user_token):
    rows = [
        review(question_shown=0, answer_shown=4, answered=5, ease=3),   # think 4, hes 1
        review(question_shown=10, answer_shown=16, answered=19, ease=1),  # think 6, hes 3
    ]
    client.post("/v1/reviews", json={"reviews": rows}, headers=auth(user_token))
    s = client.get("/v1/me/summary", headers=auth(user_token)).json()
    assert s["total_reviews"] == 2
    assert s["avg_think_s"] == 5
    assert s["avg_hesitation_s"] == 2
    assert [e["ease"] for e in s["by_ease"]] == [1, 3]


def test_friends_cannot_see_each_other(client, user_token):
    client.post("/v1/reviews", json={"reviews": [review()]}, headers=auth(user_token))
    other = client.post(
        "/v1/admin/users", json={"name": "other"}, headers=auth("test-admin")
    ).json()["token"]
    s = client.get("/v1/me/summary", headers=auth(other)).json()
    assert s["total_reviews"] == 0
