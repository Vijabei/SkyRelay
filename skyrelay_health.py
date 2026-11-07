"""
SkyRelay - what the bots know about their own state.

Both bots note down here what went right and what went wrong; the heartbeat
(skyrelay-heartbeat.py) reads it and decides whether somebody has to hear about
it. The bots themselves never send a message: a ticker in the middle of a
match has better things to do than to wait for a notification service, and a
bot that cannot reach WhatsApp may well be unable to reach anything else.

One file per bot, written whole every time (a temporary file renamed over the
old one), so a reader never sees half of it. Only one process writes each file:
the ticker and its daily probe never run at the same time (see the lock in
skyrelay-matchday.py), and the feed has a file of its own.

Deliberately free of third party imports, and deliberately unable to break
anything: a status file that cannot be written costs a line in the log, never
a post.
"""

import json
import os
import time

SCHEMA = 1

# Failures that whatsmeow usually mends by itself, by reconnecting. They are
# only worth a message when they last - see [heartbeat] ticker_stale_minutes.
TRANSIENT_KINDS = {"keepalive", "disconnected"}


def read_status(path):
    """The status of a bot, or None if there is none (yet) or it is unreadable."""
    try:
        with open(path, encoding="utf-8") as status:
            data = json.load(status)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    """Does a process with this number still exist?"""
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # exists, but belongs to somebody else
    except (OSError, ValueError, TypeError):
        return False
    return True


def bluesky_error_kind(error):
    """Sorts a failed Bluesky login into what the person has to do about it."""
    text = str(error)
    if "RateLimitExceeded" in text:
        return "ratelimit"
    if "Invalid identifier or password" in text or "AuthenticationRequired" in text:
        return "password"
    return "login"


class Health:
    """The status file of one bot.

    run     this run: process number, mode, when it started, when it last
            showed a sign of life, until when it means to run, how it ended
    checks  one entry per thing that can fail (whatsapp, instagram, bluesky,
            openligadb, posting), each with whether it worked last time, since
            when that is so, and when it last worked at all

    Every method swallows its own errors. A full disk must not end a match."""

    def __init__(self, path, program, log=None):
        self.path = path
        self.program = program
        self._log = log
        self._warned = False
        self._last_beat = 0.0
        self.guest = False
        data = read_status(path) or {}
        if data.get("schema") != SCHEMA:
            data = {}
        data["schema"] = SCHEMA
        data["program"] = program
        data.setdefault("checks", {})
        data.setdefault("run", {})
        self.data = data

    # -------------------------------------------------------------- the run
    def owned_by_other(self):
        """Is another, living run of the same bot writing this file?

        That happens when somebody starts a second ticker by hand while the
        one from cron is listening - to set the bio, say. The second one must
        then keep its hands off the file, or the heartbeat would take the
        listener for finished and miss it if it crashed later on. The sign of
        life counts as well as the process number, because after a restart
        the old number may belong to some other process entirely."""
        run = self.data.get("run") or {}
        return (run.get("pid") not in (None, os.getpid())
                and not run.get("ended")
                and time.time() - (run.get("beat") or 0) < 600
                and pid_alive(run.get("pid")))

    def start(self, mode="run", until=None):
        if self.owned_by_other():
            self.guest = True
            if self._log:
                self._log(f"ℹ️ Another {self.program} (process "
                          f"{self.data['run'].get('pid')}) is running - this "
                          f"run leaves its status file alone.")
            return
        now = time.time()
        self.data["run"] = {"pid": os.getpid(), "mode": mode, "started": now,
                            "beat": now, "until": until, "ended": None,
                            "outcome": None, "detail": ""}
        self.save()

    def mode(self, mode, until=None):
        """The run turns into something else - the ticker starts listening."""
        run = self.data.setdefault("run", {})
        run["mode"] = mode
        run["until"] = until
        run["beat"] = time.time()
        self.save()

    def beat(self, every=60):
        """A sign of life. Written at most once a minute - the listening loop
        calls this every few seconds, and an SD card has better things to do."""
        now = time.time()
        if now - self._last_beat < every:
            return
        self._last_beat = now
        self.data.setdefault("run", {})["beat"] = now
        self.save()

    def end(self, outcome="ok", detail=""):
        run = self.data.setdefault("run", {})
        if run.get("ended"):
            return             # the first word on how it ended stands
        run["ended"] = time.time()
        run["outcome"] = outcome
        run["detail"] = str(detail)[:500]
        self.save()

    # ------------------------------------------------------------- the checks
    def ok(self, check, detail=""):
        now = time.time()
        entry = self.data["checks"].setdefault(check, {})
        if entry.get("ok") is not True:
            entry["since"] = now
        entry.update(ok=True, at=now, last_ok=now, kind="", detail=str(detail)[:500])
        entry.pop("until", None)   # a pause that has ended
        self.save()

    def fail(self, check, kind, detail="", **extra):
        now = time.time()
        entry = self.data["checks"].setdefault(check, {})
        if entry.get("ok") is not False or entry.get("kind") != kind:
            entry["since"] = now
        entry.update(ok=False, at=now, kind=kind, detail=str(detail)[:500], **extra)
        self.save()

    def check(self, check):
        return self.data["checks"].get(check) or {}

    # ------------------------------------------------------------- writing
    def save(self):
        if self.guest:
            return False
        temporary = f"{self.path}.tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as status:
                json.dump(self.data, status, ensure_ascii=False, indent=1)
            os.replace(temporary, self.path)
            return True
        except OSError as error:
            if self._log and not self._warned:
                self._warned = True
                self._log(f"⚠️ Could not write the status file {self.path}: {error}")
            return False
