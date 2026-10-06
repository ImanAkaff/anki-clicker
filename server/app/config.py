"""
Settings, read from environment variables.

Why environment variables instead of hard-coding values?
  - Secrets (the admin token, the database password) never end up in your code,
    so they never end up on GitHub.
  - The same code runs on your laptop and on the hosting platform; only the
    environment changes. On your laptop you set them in the terminal; on the
    host you paste them into its "Environment variables" settings page.
"""

import os

# Where the data lives.
#   Laptop default: a SQLite file called dev.db next to where you start the server.
#   Production:     postgresql+psycopg://user:password@host:5432/dbname
DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./dev.db")

# Neon (and most hosts) hand you an address starting with "postgresql://" or
# "postgres://". SQLAlchemy reads the part before "://" to pick a driver, and
# we want psycopg (version 3), so we rewrite the prefix. The rest is unchanged.
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_prefix):]
        break

# The one password that lets YOU create user accounts (see POST /v1/admin/users).
# If it's not set, admin endpoints are switched off entirely.
ADMIN_TOKEN: str | None = os.environ.get("ADMIN_TOKEN")

# Largest batch the add-on may upload in one request. Keeps a single request
# from being huge; the add-on just sends several batches if it has more.
MAX_BATCH = 1000
