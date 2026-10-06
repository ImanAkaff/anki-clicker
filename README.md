# Anki Clicker

Review Anki cards with a game controller. The add-on logs how long each card takes, syncs those timings to a small server, and (soon) turns them into stats and fatigue warnings.

## How the pieces fit

```
FRIEND'S COMPUTER                                        THE INTERNET
┌──────────────────────────────────────────────┐        ┌──────────────────────────┐
│ Anki                                         │        │ server/ (FastAPI)        │
│  ┌──────────────┐  pycmd   ┌───────────────┐ │  HTTPS │                          │
│  │ gamepad.js   │ ───────> │ __init__.py   │ │  POST  │  auth.py   who are you?  │
│  │ (review page)│          │ hooks, buttons│ │ ─────> │  schemas   is data valid?│
│  └──────────────┘          └──────┬────────┘ │        │  main.py   save, no dupes│
│                                   │          │        │      │                   │
│                       storage.py  ▼          │        │      ▼                   │
│                  ┌──────────────────────┐    │        │  Postgres / SQLite       │
│                  │ clicker.sqlite3      │────┼─ sync.py (background thread)      │
│                  │ synced=0 → queue     │    │        └──────────────────────────┘
│                  └──────────────────────┘    │                   ▲
└──────────────────────────────────────────────┘                   │
                                         simulator/ fakes friends ─┘
```

1. **gamepad.js** polls the controller ~60×/second inside Anki's review screen and sends each new press to Python.
2. **`__init__.py`** turns presses into Anki actions and listens to Anki's hooks to time every card.
3. **storage.py** writes each review to a local SQLite file immediately (it works offline).
4. **sync.py** uploads unsent rows on a background thread when a review session ends.
5. **The server** checks the token, validates the data, and stores it, ignoring anything it already has.

## Folders

| Folder | What it is |
|---|---|
| `addon/anki_clicker/` | The Anki add-on your friends install |
| `server/` | The API and database |
| `simulator/` | Generates realistic fake users and reviews, with planted patterns |
| `render.yaml`, `DEPLOY.md` | Hosting setup and step-by-step deployment guide |

## Running it on your laptop

**1. Server**
```bash
cd server
python -m venv .venv                 # isolated Python just for this project
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ADMIN_TOKEN=pick-a-long-secret  # Windows PowerShell: $env:ADMIN_TOKEN="..."
uvicorn app.main:app --reload        # --reload restarts when you edit code
```
Open http://127.0.0.1:8000/docs to try every endpoint in the browser.

**2. Tests** (from `server/`, with the venv active)
```bash
pytest -v
```

**3. Fake data** (from `simulator/`, server running)
```bash
python simulate.py --users 5 --days 14 --server http://127.0.0.1:8000 --admin-token pick-a-long-secret
```
`ground_truth.json` records where fatigue was planted for each fake user.

**4. Add-on**
Copy `addon/anki_clicker` into Anki's add-ons folder (Tools → Add-ons → View Files) and restart Anki. Then in Tools → Add-ons → Anki Clicker → Config, set `server_url` to `http://127.0.0.1:8000` and `token` to a user token created through `/docs` → `POST /v1/admin/users`.

## Onboarding a friend
1. Call `POST /v1/admin/users` with their name and copy the token it returns (it's shown only once).
2. Send them `anki_clicker.ankiaddon` (double-click installs it), the server URL, and their token.

## Key ideas used here
- **Local-first:** every review is saved on the friend's computer before any network call.
- **Idempotent uploads:** each review has a `client_id` made by the add-on, so retrying an upload never creates duplicates.
- **Hashed tokens:** the server stores only SHA-256 hashes of tokens.
- **Migrations:** the add-on's SQLite file tracks its schema version in `PRAGMA user_version`.
- **Main thread vs background thread:** network calls never run on Anki's UI thread.

## Next steps
- [ ] React stats screen inside Anki
- [ ] Deploy the server: see DEPLOY.md
- [ ] Dashboard across all users
- [ ] Fatigue detection, checked against `ground_truth.json`
- [ ] LLM help for frequently failed cards (API key stays on the server)
