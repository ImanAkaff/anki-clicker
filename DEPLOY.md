# Deploying the server

## What you're setting up

```
 your laptop          GitHub              Render                    Neon
 ───────────   push   ───────────  pulls  ───────────────────  SQL  ──────────────
 code + git   ─────>  your repo   ─────>  runs FastAPI 24/7   ────> Postgres
                                          https://...onrender.com   (the data)
                                                  ▲
                                friends' add-ons ─┘  POST /v1/reviews
```

- **GitHub** holds the code. It's the single source of truth.
- **Render** runs the server. It copies your code from GitHub, installs the requirements, starts uvicorn, and gives you an `https://` address. The certificate that makes it HTTPS (encrypted) is handled for you.
- **Neon** runs the Postgres database.

Why two services instead of one? Render's free Postgres is deleted 30 days after creation. Render's free web servers also wipe their disk whenever they sleep, so a SQLite file there would vanish. Neon's free Postgres doesn't expire. Keeping the server and the database separate is also how most real apps are built: servers come and go, and the data stays put.

Everything here is free. No credit card needed for the free tiers at the time of writing, but check each site's current terms.

---

## Step 1: Put the code on GitHub

Install Git if you don't have it (https://git-scm.com), and make a GitHub account.

```bash
cd anki-clicker
git init                       # start tracking this folder
git add .                      # stage every file (.gitignore keeps secrets and databases out)
git commit -m "First version"  # save a snapshot
```

On github.com, click **New repository**, name it `anki-clicker`, set it to **Private**, and **don't** add a README (you already have one). GitHub then shows you commands like these. Run them:

```bash
git remote add origin https://github.com/YOUR-USERNAME/anki-clicker.git
git branch -M main
git push -u origin main
```

Refresh the GitHub page and your files should be there.

## Step 2: Create the database on Neon

1. Sign up at https://neon.com (signing in with GitHub is easiest).
2. Create a project. Pick a region near you or your friends, e.g. US East.
3. On the dashboard, find **Connection string** and copy it. It looks like:
   ```
   postgresql://neondb_owner:abc123...@ep-cool-name-123.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
   That string contains the database password. Treat it like one: don't paste it into code or commit it.

You don't need to create any tables. The server creates them the first time it starts.

## Step 3: Run the server on Render

1. Sign up at https://render.com with your GitHub account.
2. Click **New → Blueprint**, then pick your `anki-clicker` repo. Render finds `render.yaml` and shows the service it's about to create.
3. It asks for `DATABASE_URL`. Paste the Neon connection string.
4. Click **Apply**. Open the service and watch the **Logs** tab. You'll see `pip install ...` (the build), then
   `Application startup complete`. That means it's live.
5. Your address is at the top of the page, e.g. `https://anki-clicker-api.onrender.com`.

(If Blueprint gives you trouble: use **New → Web Service** instead and copy the build command, start command, root directory and environment variables from `render.yaml` into the form by hand.)

## Step 4: Check it works

- Visit `https://YOUR-ADDRESS/health`. You should see `{"ok":true}`. The first visit can take about a minute if the server was asleep.
- Visit `https://YOUR-ADDRESS/docs` to see the same interactive page you had on your laptop.

## Step 5: Get your admin token

In Render, go to your service → **Environment** → `ADMIN_TOKEN` and click to reveal it. Render generated it randomly. Save it in a password manager.

**Never put the admin token in the add-on.** Anyone can unzip an add-on and read it. Friends only ever get their own user token.

## Step 6: Create accounts for friends

1. On `/docs`, click **Authorize** (top right), paste the admin token, then click **Authorize** again.
2. Open **POST /v1/admin/users** → **Try it out**, enter `{"name": "Sara"}`, then click **Execute**.
3. Copy the `token` from the response. It's shown only once. If you lose it, create a new account.

Send each friend:
- the server address (`https://...onrender.com`)
- their token

They paste both into Tools → Add-ons → Anki Clicker → Config, then use Tools → Anki Clicker → Sync now. Everything they reviewed before this uploads.

## Updating the server later

```bash
git add .
git commit -m "Describe the change"
git push
```

Render notices the push and redeploys automatically, which takes a couple of minutes. Run `pytest` before pushing, so a broken change never reaches your friends.

**Updating the add-on:** send friends a new `.ankiaddon`. Their data in `user_files/` is kept.

## Free tier limits and what they mean for this project

| Limit | Effect | How the code handles it |
|---|---|---|
| Render sleeps after ~15 idle minutes, and waking takes up to about a minute | First request after a quiet period is slow | The add-on calls `/health` and waits up to 90 seconds before uploading, on a background thread so Anki doesn't freeze |
| Neon pauses after 5 idle minutes | First database query is a bit slower and old connections die | `pool_pre_ping=True` in `db.py` checks connections before reusing them |
| Render: 750 free hours/month | Enough for one always-available service | Don't create a second free service in the same account |
| Neon: limited storage (around 0.5–1 GB) | One review row is about 200 bytes, so you have room for millions | Nothing needed at this scale |

If the wake-up delay ever bothers you, Render's paid tier keeps the server always on.

## When something goes wrong

- **Build failed:** read the Render **Logs**. Usually a typo in `requirements.txt` or the wrong Python version.
- **`/health` works but uploads get a 500 error:** Render **Logs** show the Python traceback. The most common cause is a bad `DATABASE_URL`, so re-copy it from Neon.
- **401 in the add-on:** the friend's token is wrong or has a stray space.
- **Data looks wrong:** Neon has a **SQL Editor** in its dashboard, so you can run queries like
  `SELECT name, COUNT(*) FROM reviews JOIN users ON users.id = reviews.user_id GROUP BY name;`
