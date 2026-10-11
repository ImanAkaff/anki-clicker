"""
Uploading queued reviews to the server.

This runs on a BACKGROUND thread (see __init__.py), never on Anki's main
thread. Network calls can take seconds or hang until they time out; on the
main thread that would freeze Anki's whole window.

Uses urllib from Python's standard library instead of the popular `requests`
package, so the add-on needs nothing extra installed.

Doesn't import anything from Anki, so it can be tested outside Anki.
"""

# Lets type hints like `float | None` work on Python 3.9, which older Anki
# versions (e.g. 25.02) still ship. Without it the add-on fails to load there.
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import storage

BATCH_SIZE = 500


@dataclass
class SyncResult:
    uploaded: int = 0      # rows the server confirmed receiving
    new_on_server: int = 0 # of those, how many were new (rest were retries)
    error: str | None = None


def wake_server(server_url: str, timeout: float = 90) -> None:
    """
    Free hosting puts the server to sleep after ~15 idle minutes, and waking
    it takes up to about a minute. So before uploading, we knock on /health
    and patiently wait for an answer. We're on a background thread, so this
    wait never freezes Anki. Errors are ignored here; if the server really is
    unreachable, the upload below reports it properly.
    """
    try:
        urllib.request.urlopen(server_url.rstrip("/") + "/health", timeout=timeout).close()
    except Exception:
        pass


def sync_once(db_path: str, server_url: str, token: str, timeout: float = 15) -> SyncResult:
    result = SyncResult()
    # A fresh SQLite connection for this thread. SQLite connections must not be
    # shared between threads, so the background thread opens its own.
    conn = storage.connect(db_path)
    try:
        if storage.pending(conn, 1):
            wake_server(server_url)  # only bother if there's something to send
        while True:
            batch = storage.pending(conn, BATCH_SIZE)
            if not batch:
                return result  # everything is uploaded

            req = urllib.request.Request(
                server_url.rstrip("/") + "/v1/reviews",
                data=json.dumps({"reviews": batch}).encode(),
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                # The server answered, but with an error code.
                if e.code == 401:
                    result.error = "Server rejected the token. Check it in the add-on config."
                elif e.code == 409:
                    result.error = "Server busy with an overlapping upload; will retry."
                else:
                    result.error = f"Server error {e.code}; will retry later."
                return result
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                # Never reached the server: offline, wrong URL, server down.
                # Rows stay synced=0 and go up next time. Nothing is lost.
                result.error = f"Couldn't reach server ({e}); will retry later."
                return result

            # Only mark rows as synced AFTER the server confirms. If we crash
            # between upload and this line, the rows get re-sent next time,
            # and the server's duplicate check makes that harmless.
            storage.mark_synced(conn, [r["client_id"] for r in batch])
            result.uploaded += body["received"]
            result.new_on_server += body["inserted"]
    finally:
        conn.close()
