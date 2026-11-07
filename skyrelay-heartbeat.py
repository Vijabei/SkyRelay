"""
SkyRelay - the heartbeat: notices when ticker or feed are in trouble, and says
so to a person.

Most of the time SkyRelay just runs. When it stops, it is rarely the Pi - it
is WhatsApp letting an older client version in no longer, or Instagram
changing how it answers. Both bots note in a status file what went right and
what went wrong (skyrelay_health.py); this program reads those files, decides
whether something needs a person, and tells them:

  * as a Bluesky direct message from the bot account to [heartbeat]
    bluesky_recipient - with what happened and what to do about it,
  * to a watcher outside the Pi ([heartbeat] ping_url): a check on
    healthchecks.io or an Uptime Kuma push monitor. It raises the alarm on
    its own when the pings stop coming - which is the only way to learn that
    the Pi itself is down.

Only changes are reported: a problem when it appears, a reminder every
[heartbeat] reminder_hours while it lasts, and the all-clear when it is over.

Run it from cron every 15 minutes:
    */15 * * * * BLUESKY_HEARTBEAT_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx /path/to/SkyRelay/venv/bin/python3 /path/to/SkyRelay/skyrelay-heartbeat.py >/dev/null 2>&1

By hand:
    skyrelay-heartbeat.py --status   what it would report now; sends nothing
    skyrelay-heartbeat.py --test     sends a test message through every channel

The messages follow [general] language like the setup assistant does - they
are written for a person. The log stays English, like the bots' logs.

Deliberately light: it starts every 15 minutes on a Raspberry Pi, so the
Bluesky library is only loaded when there is actually something to send.
"""

import configparser
import glob
import importlib.util
import json
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import time
import zipfile
from datetime import datetime

# Like the bots: questions about the configuration are answered before
# anything else is loaded.
if "--check-config" in sys.argv or "--show-config" in sys.argv:
    from skyrelay_config import check_config, show_config
    _base = os.path.dirname(os.path.abspath(__file__))
    sys.exit(check_config(_base) if "--check-config" in sys.argv
             else show_config(_base))

import requests

import skyrelay_i18n
from skyrelay_config import config_path
from skyrelay_health import TRANSIENT_KINDS, pid_alive, read_status
from skyrelay_i18n import _, _f

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_ONLY = "--status" in sys.argv
TEST = "--test" in sys.argv


# ------------------------------------------------------------- configuration
CONFIG_FILE = config_path(BASE_DIR)
_parser = configparser.ConfigParser(interpolation=None)
try:
    with open(CONFIG_FILE, encoding="utf-8") as _config:
        _parser.read_file(_config)
except (OSError, configparser.Error) as _error:
    print(f"Error reading {CONFIG_FILE}: {_error}", file=sys.stderr)
    sys.exit(1)


def cfg(section, key, default=None):
    return _parser.get(section, key, fallback=default)


def cfg_int(section, key, default):
    try:
        return int(str(_parser.get(section, key, fallback=default)).strip())
    except (ValueError, TypeError):
        return default


def cfg_bool(section, key, default):
    try:
        return _parser.getboolean(section, key, fallback=default)
    except ValueError:
        return default


ENABLED = cfg_bool("heartbeat", "enabled", False)
RECIPIENT = cfg("heartbeat", "bluesky_recipient", "").strip().lstrip("@")
TICKER_HANDLE = cfg("bluesky", "handle", "").strip()
FEED_HANDLE = cfg("feed", "bluesky_handle", "").strip() or TICKER_HANDLE
SENDER = cfg("heartbeat", "bluesky_sender", "").strip().lstrip("@") or TICKER_HANDLE
# The ping URL of the outside watcher, as healthchecks.io or Kuma shows it.
# Which of the two it is, the URL itself says (Kuma's contain /api/push/).
# The environment may override it, for whoever keeps the token out of files.
# kuma_push_url and kuma_mode were the names for a few days, when Kuma was the
# only watcher - still read, so nobody's setting falls away.
PING_URL = (os.environ.get("SKYRELAY_PING_URL")
            or os.environ.get("SKYRELAY_KUMA_PUSH_URL")
            or cfg("heartbeat", "ping_url", "")
            or cfg("heartbeat", "kuma_push_url", "")).strip()
PING_MODE = (cfg("heartbeat", "ping_mode", "")
             or cfg("heartbeat", "kuma_mode", "") or "problems").strip().lower()
REMINDER_HOURS = cfg_int("heartbeat", "reminder_hours", 24)
WHATSAPP_PROBE = cfg_bool("heartbeat", "whatsapp_probe", True)
TICKER_STALE_MINUTES = cfg_int("heartbeat", "ticker_stale_minutes", 5)
FEED_STALE_HOURS = cfg_int("heartbeat", "feed_stale_hours", 3)
DISK_MIN_MB = cfg_int("heartbeat", "disk_min_mb", 500)
UPDATE_CHECK = cfg_bool("heartbeat", "update_check", True)
STATE_FILE = os.path.join(BASE_DIR, cfg("heartbeat", "state",
                                        "skyrelay_heartbeat_state.json"))
LOG_FILE = os.path.join(BASE_DIR, cfg("heartbeat", "log", "skyrelay-heartbeat.log"))
LOG_MAX_BYTES = cfg_int("logging", "max_bytes", 2_000_000)
LOG_BACKUP_COUNT = cfg_int("logging", "backup_count", 5)

TICKER_HEALTH = os.path.join(BASE_DIR, cfg("files", "health", "skyrelay_health.json"))
FEED_HEALTH = os.path.join(BASE_DIR, cfg("feed", "health", "skyrelay_feed_health.json"))
SESSION_DB = cfg("files", "session", "skyrelay_session.sqlite3")
TICKER_LOG = cfg("files", "log", "skyrelay.log")
FEED_LOG = cfg("feed", "log", "skyrelay-feed.log")
INSTA_BOT_USER = cfg("feed", "instagram_session_user", "")

# Which bots are in use at all. A bot nobody set up must not be reported
# missing every day.
_channel = cfg("source", "channel_invite_link", "")
TICKER_IN_USE = bool(_channel) and "HIER-DEN" not in _channel
_profile = cfg("feed", "instagram_profile", "")
FEED_IN_USE = bool(_profile) and bool(INSTA_BOT_USER) and not INSTA_BOT_USER.startswith("dein")

# The ticker starts every morning from cron. With the daily probe it then
# talks to WhatsApp every day - a day and a half without a start means the
# cron line is gone, or the Pi was off.
TICKER_MISSING_HOURS = 36

# A ticker that is alive but silent may simply be busy: a video upload waits
# up to [limits] video_job_timeout_seconds for Bluesky, and the listening loop
# shows no sign of life meanwhile. Only twice that, and then some, counts as a
# hang. A process that is gone is reported straight away.
HANG_SECONDS = max(TICKER_STALE_MINUTES * 60,
                   2 * cfg_int("limits", "video_job_timeout_seconds", 600) + 300)

# A message on Bluesky may be 1000 graphemes long; a little room is kept.
DM_LIMIT = 900

# Packages whose new versions have fixed SkyRelay every time so far.
WATCHED_PACKAGES = ("neonize", "instaloader")

skyrelay_i18n.use(cfg("general", "language", ""))


# -------------------------------------------------------------------- logging
def _rotate_log():
    try:
        if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) < LOG_MAX_BYTES:
            return
        for i in range(LOG_BACKUP_COUNT - 1, 0, -1):
            if os.path.exists(f"{LOG_FILE}.{i}"):
                os.replace(f"{LOG_FILE}.{i}", f"{LOG_FILE}.{i + 1}")
        os.replace(LOG_FILE, f"{LOG_FILE}.1")
    except OSError:
        pass


def log(*parts):
    """Console and log file. Not the tee of the two bots: there is no Go part
    here whose output would have to be caught, and it keeps this light."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] " + " ".join(str(p) for p in parts)
    print(line, flush=True)
    if STATUS_ONLY:
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as logfile:
            logfile.write(line + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------- helpers
def when(timestamp):
    """A point in time the way a person reads it: 07.11. 06:01"""
    if not timestamp:
        return "?"
    return datetime.fromtimestamp(timestamp).strftime("%d.%m. %H:%M")


def hours_since(timestamp, now):
    return int((now - (timestamp or 0)) / 3600)


def installed_version(package):
    try:
        from importlib.metadata import version
        return version(package)
    except Exception:
        return None


def _version_key(text):
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def newer(candidate, installed):
    return bool(candidate and installed) and _version_key(candidate) > _version_key(installed)


def read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as state:
            data = json.load(state)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def write_state(state):
    temporary = STATE_FILE + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=1)
        os.replace(temporary, STATE_FILE)
    except OSError as error:
        log(f"⚠️ Could not write {STATE_FILE}: {error}")


# --------------------------------------------------------- new versions (PyPI)
def latest_versions(state, now):
    """The newest version of each watched package on PyPI - asked once a day,
    remembered in between. A PyPI that does not answer costs nothing."""
    pypi = state.setdefault("pypi", {})
    if not UPDATE_CHECK:
        return {}
    if now - pypi.get("checked", 0) < 86400:
        return pypi.get("latest", {})
    latest = dict(pypi.get("latest", {}))
    for package in WATCHED_PACKAGES:
        try:
            answer = requests.get(f"https://pypi.org/pypi/{package}/json", timeout=10)
            answer.raise_for_status()
            latest[package] = answer.json()["info"]["version"]
        except Exception as error:
            log(f"(PyPI did not say which {package} is current: {error})")
    pypi["checked"] = now
    pypi["latest"] = latest
    return latest


# ------------------------------------------- which WhatsApp does neonize bring?
#
# A new neonize is only worth anything against "Client outdated (405)" if it
# brings a newer whatsmeow along: whatsmeow decides which client version
# WhatsApp is shown. The version number of neonize says nothing about that -
# 0.6.1 to 0.10.0 all carry the very whatsmeow of 0.5.2 (21.09.2026). Go
# writes its dependencies into the binary, so the answer is in the wheel, and
# reading it costs one download of about 20 MB per new neonize version.
WHATSMEOW_MARK = re.compile(rb"dep\tgo\.mau\.fi/whatsmeow\t(v[0-9A-Za-z.+\-]+)")

# Filled by binding_status(): what the installed and the newest neonize carry.
BINDING = {}


def _whatsmeow_in(blob):
    match = WHATSMEOW_MARK.search(blob)
    return match.group(1).decode() if match else None


def whatsmeow_date(version):
    """v0.0.0-20260921121126-35ae40906e74 -> 21.09.2026. whatsmeow is not
    tagged, so its pseudo-versions carry the date of the commit."""
    match = re.search(r"-(\d{4})(\d{2})(\d{2})\d{6}-", version or "")
    if not match:
        return version or "?"
    year, month, day = match.groups()
    return f"{day}.{month}.{year}"


def _whatsmeow_key(version):
    match = re.search(r"-(\d{14})-", version or "")
    return match.group(1) if match else None


def installed_whatsmeow():
    """The whatsmeow inside the installed neonize, read from its library."""
    try:
        spec = importlib.util.find_spec("neonize")
        folder = list(spec.submodule_search_locations)[0]
    except Exception:
        return None
    for library in glob.glob(os.path.join(folder, "*.so")) + \
            glob.glob(os.path.join(folder, "*.dylib")):
        try:
            with open(library, "rb") as binary:
                found = _whatsmeow_in(binary.read())
            if found:
                return found
        except OSError:
            continue
    return None


def published_whatsmeow(version):
    """The whatsmeow inside a neonize release on PyPI. Downloads the wheel for
    this machine (the Go dependencies are the same in every one of them)."""
    answer = requests.get(f"https://pypi.org/pypi/neonize/{version}/json", timeout=15)
    answer.raise_for_status()
    wheels = [f for f in answer.json().get("urls", [])
              if f.get("filename", "").endswith(".whl")]
    machine = platform.machine().lower()
    wheels.sort(key=lambda f: (machine not in f["filename"].lower(),
                               "linux" not in f["filename"]))
    if not wheels:
        raise RuntimeError(f"no wheel for neonize {version} on PyPI")
    with tempfile.TemporaryFile() as download:
        with requests.get(wheels[0]["url"], stream=True, timeout=60) as wheel:
            wheel.raise_for_status()
            for chunk in wheel.iter_content(1 << 20):
                download.write(chunk)
        download.seek(0)
        with zipfile.ZipFile(download) as archive:
            for member in archive.namelist():
                if member.endswith((".so", ".dylib", ".dll")):
                    found = _whatsmeow_in(archive.read(member))
                    if found:
                        return found
    raise RuntimeError(f"no whatsmeow version found in {wheels[0]['filename']}")


def binding_status(state, now, latest):
    """Compares the whatsmeow of the installed neonize with that of the newest
    one on PyPI. Remembered per version, so each is looked at only once; a
    failed look is tried again the next day."""
    BINDING.clear()
    installed = installed_version("neonize")
    if not installed:
        return
    cache = state.setdefault("whatsmeow", {})
    if cache.get("installed_for") != installed:
        cache["installed_for"] = installed
        cache["installed"] = installed_whatsmeow()
    BINDING["installed"] = cache.get("installed")

    candidate = latest.get("neonize")
    if not newer(candidate, installed):
        return
    BINDING["version"] = candidate
    known = cache.setdefault("published", {}).get(candidate)
    if isinstance(known, str):
        BINDING["latest"] = known
    elif not known or now - known.get("failed_at", 0) >= 86400:
        try:
            log(f"Looking into neonize {candidate} on PyPI to see which whatsmeow it brings...")
            BINDING["latest"] = cache["published"][candidate] = published_whatsmeow(candidate)
            log(f"neonize {candidate} brings whatsmeow {BINDING['latest']} "
                f"(installed: {BINDING['installed'] or '?'}).")
        except Exception as error:
            log(f"(Could not tell which whatsmeow neonize {candidate} brings: {error})")
            cache["published"][candidate] = {"failed_at": now}

    mine, theirs = _whatsmeow_key(BINDING["installed"]), _whatsmeow_key(BINDING.get("latest"))
    if not BINDING.get("latest") or not BINDING["installed"]:
        BINDING["verdict"] = "unknown"
    elif BINDING["latest"] == BINDING["installed"]:
        BINDING["verdict"] = "same"
    elif mine and theirs:
        BINDING["verdict"] = "newer" if theirs > mine else "older"
    else:
        BINDING["verdict"] = "different"


def update_hint(package, latest):
    """One sentence for the message about a refusal: is there something newer
    - and for neonize: would it help at all?"""
    installed = installed_version(package)
    if package == "neonize" and newer(latest.get(package), installed):
        verdict = BINDING.get("verdict")
        if verdict in ("same", "older"):
            return _f("neonize {latest} auf PyPI bringt keine neuere WhatsApp-Anbindung "
                      "mit (whatsmeow vom {date}, wie installiert) – ein Wechsel hilft "
                      "dagegen nicht. Es braucht eine Fassung mit neuerer whatsmeow.",
                      latest=latest[package],
                      date=whatsmeow_date(BINDING.get("latest")))
        if verdict in ("newer", "different"):
            return _f("Auf PyPI gibt es neonize {latest} mit neuerer WhatsApp-Anbindung "
                      "(whatsmeow vom {theirs}, installiert: vom {mine}) – das ist "
                      "der Kandidat.",
                      latest=latest[package], theirs=whatsmeow_date(BINDING.get("latest")),
                      mine=whatsmeow_date(BINDING.get("installed")))
        return _f("Auf PyPI gibt es neonize {latest} (installiert: {installed}); ob sie "
                  "eine neuere WhatsApp-Anbindung mitbringt, ließ sich nicht prüfen.",
                  latest=latest[package], installed=installed)
    if newer(latest.get(package), installed):
        return _f("Auf PyPI gibt es {package} {latest} (installiert: {installed}).",
                  package=package, latest=latest[package], installed=installed)
    if latest.get(package):
        return _f("{package} {installed} ist die neueste Fassung auf PyPI.",
                  package=package, installed=installed or "?")
    return ""


# ----------------------------------------------------------------- the checks
class Finding:
    """Something a person should hear about.

    lasting=False is for things that happened once - a post that did not go
    out. They are told once, without reminders and without an all-clear:
    nothing is "in order again" about a post that stays missing."""

    def __init__(self, key, kind, title, body, since, lasting=True):
        self.key = key
        self.kind = kind          # a different kind of trouble is news again
        self.title = title
        self.body = body
        self.since = since
        self.lasting = lasting


def _whatsapp_finding(entry, run, now, latest):
    kind = entry.get("kind", "")
    detail = entry.get("detail", "")
    since = entry.get("since") or entry.get("at")
    if kind in TRANSIENT_KINDS or kind == "connect_failed":
        # whatsmeow reconnects by itself - only a lasting outage counts, and
        # only while the ticker is still meant to be running.
        alive = (run.get("mode") == "listen" and not run.get("ended")
                 and pid_alive(run.get("pid")))
        minutes = int((now - (since or now)) / 60)
        if not alive or minutes < TICKER_STALE_MINUTES:
            return None
        return Finding("whatsapp", "unstable", _("WhatsApp-Verbindung gestört"),
                       _f("Seit {minutes} Minuten: {detail}. whatsmeow versucht es "
                          "weiter; hält es an, den Ticker neu starten.",
                          minutes=minutes, detail=detail), since)

    texts = {
        "outdated": (
            _("WhatsApp lässt den Ticker nicht mehr ein"),
            _f("WhatsApp weist die Client-Version ab, die neonize {version} "
               "mitbringt („Client outdated“, 405).\n"
               "→ ./update.sh ausführen. Hält requirements.txt noch dieselbe "
               "Fassung fest, braucht es zuerst eine neuere neonize.",
               version=installed_version("neonize") or "?")
            + ("\n" + update_hint("neonize", latest) if update_hint("neonize", latest) else "")),
        "logged_out": (
            _("WhatsApp hat den Ticker abgemeldet"),
            _f("Das verknüpfte Gerät wurde abgemeldet – am Handy entfernt oder "
               "abgelaufen.\n→ Neu koppeln, interaktiv im Terminal:\n"
               "rm {session}\n"
               "SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py",
               session=SESSION_DB)),
        "not_paired": (
            _("Der Ticker ist nicht mit WhatsApp gekoppelt"),
            _("WhatsApp verlangt einen QR-Scan.\n→ Einmal interaktiv koppeln:\n"
              "SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py")),
        "banned": (
            _("WhatsApp hat die Nummer gesperrt"),
            _f("Vorübergehende Sperre ({detail}). SkyRelay kann daran nichts "
               "ändern – abwarten und am Handy nachsehen.", detail=detail)),
        "replaced": (
            _("Zwei Verbindungen mit derselben WhatsApp-Sitzung"),
            _("Ein anderes Programm hat sich mit derselben Sitzung angemeldet und "
              "den Ticker verdrängt – ein zweiter Ticker oder der Testlauf. Es darf "
              "immer nur einer laufen.")),
        "no_connection": (
            _("Keine Verbindung zu WhatsApp"),
            _f("{detail}.\n→ Netz des Pi prüfen, sonst ins Protokoll sehen: "
               "tail -50 {log}", detail=detail, log=TICKER_LOG)),
        "channel": (
            _("WhatsApp-Kanal nicht erreichbar"),
            _f("Verbunden, aber der Kanal ließ sich nicht auflösen: {detail}\n"
               "→ Einladungslink in [source] channel_invite_link prüfen.",
               detail=detail)),
    }
    title, body = texts.get(kind, (_("WhatsApp-Störung"), detail))
    return Finding("whatsapp", kind, title, body, since)


def _bluesky_finding(key, who, entry, handle):
    kind = entry.get("kind", "")
    detail = entry.get("detail", "")
    texts = {
        "password": _f("Handle und App-Passwort passen nicht zusammen ({detail}).\n"
                       "→ App-Passwort in der crontab prüfen; wurde es bei Bluesky "
                       "widerrufen, ein neues anlegen.", detail=detail),
        "password_missing": _f("{variable} ist nicht gesetzt.\n→ Gehört oben in "
                               "die crontab (crontab -e), ohne Anführungszeichen.",
                               variable=detail),
        "ratelimit": _("Bluesky lässt heute keine weiteren Anmeldungen zu "
                       "(Tageslimit). Das gibt sich von selbst – weitere Versuche "
                       "bringen nichts."),
    }
    return Finding(key, kind,
                   _f("Bluesky-Anmeldung ({who}) scheitert: @{handle}",
                      who=who, handle=handle),
                   texts.get(kind, detail), entry.get("since"))


def _posting_finding(key, who, entry, logfile, now):
    """Only while it is fresh - a failed post on Saturday is no news on
    Wednesday, and the next one that works says nothing either way."""
    if now - (entry.get("at") or 0) > 12 * 3600:
        return []
    # Each failed post is news of its own, hence the time in the kind.
    return [Finding(key, f"error@{int(entry.get('at') or 0)}",
                    _f("Ein Beitrag ließ sich nicht übertragen ({who})", who=who),
                    _f("{detail}\nEinzelheiten im Protokoll: {log}",
                       detail=entry.get("detail", ""), log=logfile),
                    entry.get("at"), lasting=False)]


def check_ticker(state, now, latest):
    findings = []
    status = read_status(TICKER_HEALTH)
    if status is None:
        if TICKER_IN_USE and ENABLED and WHATSAPP_PROBE and \
                hours_since(state.get("first_run", now), now) >= TICKER_MISSING_HOURS:
            findings.append(Finding(
                "ticker_missing", "never",
                _("Der Ticker hat noch nie einen Status geschrieben"),
                _("Seit der Wächter läuft, ist der Ticker nicht gestartet.\n"
                  "→ Stimmt der tägliche cron-Eintrag (crontab -l)?"),
                state.get("first_run")))
        return findings

    run = status.get("run") or {}
    checks = status.get("checks") or {}

    # Crashed or hanging in the middle of a matchday. Not limited to the
    # listening window on purpose: a crash at three in the afternoon is not
    # "in order again" at midnight - only the next start mends it.
    if run.get("mode") == "listen" and not run.get("ended"):
        dead = not pid_alive(run.get("pid")) and \
            now - (run.get("beat") or 0) > 60
        silent = now - (run.get("beat") or 0) > HANG_SECONDS
        if dead or silent:
            # Worked out beforehand: Babel does not find a _() nested inside
            # the arguments of _f(), and the sentence would stay German.
            process = (_("Der Prozess läuft nicht mehr.") if dead
                       else _("Der Prozess hängt."))
            findings.append(Finding(
                "ticker_down", "dead" if dead else "silent",
                _("Der Ticker ist ausgefallen"),
                _f("Er sollte bis {until} lauschen, das letzte Lebenszeichen kam "
                   "um {beat}. {state} Die Bio steht vermutlich noch auf „Bot ist an“.\n"
                   "→ Protokoll ansehen: tail -50 {log}\n"
                   "→ Neu starten und Verpasstes nachholen:\n"
                   "SKYRELAY_CATCHUP=5 nohup venv/bin/python skyrelay-matchday.py "
                   ">/dev/null 2>&1 &",
                   until=when(run.get("until")), beat=when(run.get("beat")),
                   state=process,
                   log=TICKER_LOG),
                run.get("beat")))

    if ENABLED and WHATSAPP_PROBE and \
            hours_since(run.get("started"), now) >= TICKER_MISSING_HOURS:
        findings.append(Finding(
            "ticker_missing", "stale",
            _f("Der Ticker ist seit {hours} Stunden nicht gestartet",
               hours=hours_since(run.get("started"), now)),
            _("Damit fehlt auch die tägliche WhatsApp-Prüfung.\n"
              "→ Stimmt der cron-Eintrag (crontab -l)? War der Pi aus?"),
            run.get("started")))

    whatsapp = checks.get("whatsapp") or {}
    if whatsapp.get("ok") is False:
        finding = _whatsapp_finding(whatsapp, run, now, latest)
        if finding:
            findings.append(finding)

    openligadb = checks.get("openligadb") or {}
    if openligadb.get("ok") is False:
        findings.append(Finding(
            "openligadb", "error", _("Spielplan nicht abrufbar"),
            _f("OpenLigaDB antwortete nicht ({detail}). An einem Spieltag startet "
               "der Ticker dann nicht.\n→ Wird heute gespielt, von Hand starten:\n"
               "SKYRELAY_FORCE=1 nohup venv/bin/python skyrelay-matchday.py "
               ">/dev/null 2>&1 &", detail=openligadb.get("detail", "")),
            openligadb.get("since")))

    bluesky = checks.get("bluesky") or {}
    if bluesky.get("ok") is False:
        findings.append(_bluesky_finding("bluesky_ticker", _("Ticker"), bluesky,
                                         TICKER_HANDLE))

    posting = checks.get("posting") or {}
    if posting.get("ok") is False:
        findings += _posting_finding("posting_ticker", _("Ticker"), posting,
                                     TICKER_LOG, now)

    # The safety net: the last run ended with an error that none of the
    # checks above explains - a broken configuration, an exception nobody
    # foresaw. The log knows more.
    if run.get("outcome") == "error" and not any(
            f.key in ("whatsapp", "openligadb", "bluesky_ticker") for f in findings):
        findings.append(Finding(
            "ticker_error", "error", _("Der Ticker hat sich mit einem Fehler beendet"),
            _f("{detail}\n→ Protokoll ansehen: tail -50 {log}",
               detail=run.get("detail", ""), log=TICKER_LOG),
            run.get("ended")))
    return findings


def check_feed(state, now, latest):
    findings = []
    status = read_status(FEED_HEALTH)
    stale_seconds = FEED_STALE_HOURS * 3600
    if status is None:
        if FEED_IN_USE and ENABLED and FEED_STALE_HOURS > 0 and \
                now - state.get("first_run", now) >= stale_seconds:
            findings.append(Finding(
                "feed_missing", "never",
                _("Der Feed hat noch nie einen Status geschrieben"),
                _("Seit der Wächter läuft, ist der Feed nicht gestartet.\n"
                  "→ Stimmt der cron-Eintrag (crontab -l)?"),
                state.get("first_run")))
        return findings

    run = status.get("run") or {}
    checks = status.get("checks") or {}

    not_running = FEED_STALE_HOURS > 0 and now - (run.get("started") or 0) >= stale_seconds
    if not_running:
        findings.append(Finding(
            "feed_missing", "stale",
            _f("Der Feed ist seit {hours} Stunden nicht gelaufen",
               hours=hours_since(run.get("started"), now)),
            _("→ Stimmt der cron-Eintrag (crontab -l)? War der Pi aus?"),
            run.get("started")))

    instagram = checks.get("instagram") or {}
    kind = instagram.get("kind", "")
    if instagram.get("ok") is False and kind == "blocked":
        until = instagram.get("until")
        body = _f("Instagram antwortet mit {marker}.", marker=instagram.get("marker", "?"))
        if until and until > now:
            body += " " + _f("Der Feed pausiert bis {until}.", until=when(until))
        body += "\n" + _f("→ Meist liegt es am Konto: mit {user} in der App anmelden "
                          "und bestätigen, was Instagram verlangt; danach "
                          "venv/bin/instaloader -l {user}\n"
                          "→ Wird auch ein frisches Konto abgewiesen, ist "
                          "instaloader veraltet: ./update.sh", user=INSTA_BOT_USER)
        hint = update_hint("instaloader", latest)
        if hint:
            body += "\n" + hint
        findings.append(Finding("instagram", "blocked",
                                _("Instagram sperrt das Abrufkonto"), body,
                                instagram.get("since")))
    elif instagram.get("ok") is False and kind == "session":
        findings.append(Finding(
            "instagram", "session", _("Instagram-Sitzung fehlt oder ist kaputt"),
            _f("{detail}\n→ Neu anlegen: venv/bin/instaloader -l {user}",
               detail=instagram.get("detail", ""), user=INSTA_BOT_USER),
            instagram.get("since")))
    elif not not_running and FEED_STALE_HOURS > 0 and instagram and \
            now - (instagram.get("last_ok") or instagram.get("since") or now) >= stale_seconds:
        # Single failed queries happen; only a lasting silence is reported.
        detail = instagram.get("detail", "") if instagram.get("ok") is False else ""
        body = (_f("Letzter Fehler: {detail}", detail=detail) if detail
                else _("Der Feed läuft, aber keine Abfrage gelang."))
        body += "\n" + _f("→ Protokoll ansehen: tail -50 {log}", log=FEED_LOG)
        hint = update_hint("instaloader", latest)
        if hint:
            body += "\n" + hint
        findings.append(Finding(
            "instagram", "stale",
            _f("Der Feed holt seit {hours} Stunden nichts von Instagram",
               hours=hours_since(instagram.get("last_ok") or instagram.get("since"), now)),
            body, instagram.get("last_ok")))

    bluesky = checks.get("bluesky") or {}
    if bluesky.get("ok") is False:
        findings.append(_bluesky_finding("bluesky_feed", _("Feed"), bluesky, FEED_HANDLE))

    posting = checks.get("posting") or {}
    if posting.get("ok") is False:
        findings += _posting_finding("posting_feed", _("Feed"), posting, FEED_LOG, now)
    return findings


def check_disk(now):
    if DISK_MIN_MB <= 0:
        return []
    try:
        free_mb = shutil.disk_usage(BASE_DIR).free // (1024 * 1024)
    except OSError:
        return []
    if free_mb >= DISK_MIN_MB:
        return []
    return [Finding("disk", "low", _("Speicher fast voll"),
                    _f("Nur noch {free} MB frei für {path}.\n→ Alte Protokolle "
                       "und tmp/ aufräumen; sudo apt clean hilft oft auch.",
                       free=free_mb, path=BASE_DIR), now)]


def evaluate(state, now):
    latest = latest_versions(state, now)
    if UPDATE_CHECK:
        binding_status(state, now, latest)
    findings = []
    if TICKER_IN_USE or os.path.exists(TICKER_HEALTH):
        findings += check_ticker(state, now, latest)
    if FEED_IN_USE or os.path.exists(FEED_HEALTH):
        findings += check_feed(state, now, latest)
    findings += check_disk(now)
    return findings, latest


def new_releases(state, latest):
    """Versions that appeared since the last notice - said once each. Not an
    alarm: as long as everything runs there is no reason to switch."""
    announced = state.setdefault("announced", {})
    notes = []
    for package in WATCHED_PACKAGES:
        installed = installed_version(package)
        candidate = latest.get(package)
        if not newer(candidate, installed) or announced.get(package) == candidate:
            continue
        if package == "neonize":
            verdict = BINDING.get("verdict")
            if verdict == "unknown":
                continue          # not yet known what it brings - ask again tomorrow
            if verdict in ("same", "older"):
                # Same WhatsApp underneath: nothing to tell, only to remember.
                notes.append((package, candidate, None))
                continue
            notes.append((package, candidate, _f(
                "ℹ️ Neue Fassung: neonize {latest} mit neuerer WhatsApp-Anbindung "
                "(whatsmeow vom {theirs}, installiert: neonize {installed} mit "
                "whatsmeow vom {mine}). Das ist die Fassung, die bei der nächsten "
                "Abweisung durch WhatsApp („Client outdated“) helfen dürfte. Nicht "
                "ungeprüft wechseln – erst mit SKYRELAY_DRY_RUN=1 testen, siehe "
                "UPGRADE-TEST.md.",
                latest=candidate, theirs=whatsmeow_date(BINDING.get("latest")),
                installed=installed, mine=whatsmeow_date(BINDING.get("installed")))))
            continue
        notes.append((package, candidate, _f(
            "ℹ️ Neue Fassung: {package} {latest} (installiert: {installed}). "
            "Nicht ungeprüft wechseln – erst mit SKYRELAY_DRY_RUN=1 testen, "
            "siehe UPGRADE-TEST.md. Solange alles läuft, besteht kein Anlass.",
            package=package, latest=candidate, installed=installed)))
    return notes


# ------------------------------------------------------------------ messages
def recovered_label(key, fallback):
    """What is in order again - by what it is, not by how it broke. The title
    of the alarm would read oddly here ("in order again: the feed has not run
    for 25 hours")."""
    return {
        "whatsapp": _("WhatsApp-Verbindung des Tickers"),
        "ticker_down": _("Der Ticker läuft wieder"),
        "ticker_missing": _("Täglicher Start des Tickers"),
        "ticker_error": _("Der Ticker endet wieder ohne Fehler"),
        "openligadb": _("Spielplan von OpenLigaDB"),
        "bluesky_ticker": _("Bluesky-Anmeldung des Tickers"),
        "bluesky_feed": _("Bluesky-Anmeldung des Feeds"),
        "instagram": _("Instagram-Abruf des Feeds"),
        "feed_missing": _("Regelmäßiger Start des Feeds"),
        "disk": _("Speicherplatz"),
    }.get(key, fallback)


def header():
    name = socket.gethostname()
    # Several clubs on one Pi run on configurations of their own.
    if os.path.basename(CONFIG_FILE) != "skyrelay.conf":
        name += f" · {os.path.basename(CONFIG_FILE)}"
    return f"SkyRelay · {name}"


def compose(new, reminders, recovered, releases):
    parts = []
    for finding in new:
        parts.append(f"⚠️ {finding.title}\n{finding.body}\n"
                     + _f("seit {since}", since=when(finding.since)))
    for finding in reminders:
        parts.append(_f("⏰ Weiterhin: {title}", title=finding.title) + "\n"
                     + finding.body + "\n" + _f("seit {since}", since=when(finding.since)))
    for entry in recovered:
        parts.append(_f("✅ Wieder in Ordnung: {title}",
                        title=recovered_label(entry.get("key"), entry.get("title", "?")))
                     + "\n" + _f("Gestört seit {since}", since=when(entry.get("since"))))
    for _package, _version, text in releases:
        if text:
            parts.append(text)
    return parts


def split_for_dm(parts):
    """Groups the paragraphs into messages short enough for Bluesky."""
    messages, current = [], header()
    for part in parts:
        if len(part) > DM_LIMIT:
            part = part[:DM_LIMIT - 1] + "…"
        if len(current) + 2 + len(part) > DM_LIMIT:
            messages.append(current)
            current = header()
        current += "\n\n" + part
    messages.append(current)
    return messages


# ------------------------------------------------------------------ channels
def sender_password():
    """The app password for the account that sends the messages.

    BLUESKY_HEARTBEAT_APP_PASSWORD first: sending direct messages needs an app
    password created with "Allow access to your direct messages", and the
    ticker's own password is usually without it. Otherwise the password of
    whichever bot owns the sending account - which is all it takes when that
    bot's password is replaced by one with direct message access: such a
    password can post just the same."""
    names = ["BLUESKY_HEARTBEAT_APP_PASSWORD"]
    if SENDER == TICKER_HANDLE:
        names += ["BLUESKY_TICKER_APP_PASSWORD"]
    if SENDER == FEED_HANDLE:
        names += ["BLUESKY_FEED_APP_PASSWORD", "SKYRELAY_FEED_APP_PASSWORD"]
    names.append("BLUESKY_APP_PASSWORD")
    for name in names:
        if os.environ.get(name):
            return os.environ[name], name
    return None, names[0]


def send_dm(messages):
    """Sends the messages as Bluesky direct messages. True = delivered."""
    if RECIPIENT.lower() == SENDER.lower():
        # Bluesky offers no chat with oneself - and if it did, the message
        # would wait in the bot's inbox rather than reach a person.
        log(f"✗ Direct message not sent: @{RECIPIENT} is the sending account "
            f"itself. Enter your own, personal account as [heartbeat] "
            f"bluesky_recipient - or leave it empty and rely on ping_url.")
        return False
    password, variable = sender_password()
    if not password:
        log(f"✗ Direct message not sent: {variable} is not set (sender @{SENDER}).")
        return False
    try:
        from atproto import models
        from skyrelay_common import make_bluesky_client
        client = make_bluesky_client()
        client.login(SENDER, password)
        recipient = client.resolve_handle(RECIPIENT).did
        chat = client.with_bsky_chat_proxy().chat.bsky.convo
        convo = chat.get_convo_for_members(
            models.ChatBskyConvoGetConvoForMembers.Params(members=[recipient])).convo
        for text in messages:
            chat.send_message(models.ChatBskyConvoSendMessage.Data(
                convo_id=convo.id,
                message=models.ChatBskyConvoDefs.MessageInput(text=text)))
        log(f"✓ Direct message sent to @{RECIPIENT} ({len(messages)} part(s)).")
        return True
    except Exception as error:
        text = str(error)
        log(f"✗ Direct message to @{RECIPIENT} failed: {error}")
        if "Bad token scope" in text or "scope" in text.lower():
            log("   The app password does not allow direct messages. Create one with")
            log("   'Allow access to your direct messages' ticked and put it into")
            log("   BLUESKY_HEARTBEAT_APP_PASSWORD.")
        elif "follow" in text.lower() or "recipient" in text.lower():
            log(f"   @{RECIPIENT} does not accept messages from @{SENDER}. Follow the")
            log("   bot with your own account, or allow messages from everyone")
            log("   (Settings -> Privacy and security -> Chat).")
        elif "RateLimitExceeded" in text:
            log("   Bluesky's login limit for the sender is used up - waiting helps.")
        return False


def ping_service():
    return "Uptime Kuma" if "/api/push/" in PING_URL else "healthchecks.io"


def ping(down, summary, details=""):
    """Tells the outside watcher how things stand. True = it took the ping.

    healthchecks.io: the URL itself means "fine", URL/fail means "something is
               wrong"; the text goes along as the body and shows up in the
               check's log and its notifications.
    Uptime Kuma: status=up or status=down, with a short message.
    Both raise the alarm on their own when the pings stop coming."""
    service = ping_service()
    try:
        if service == "Uptime Kuma":
            answer = requests.get(PING_URL.split("?", 1)[0],
                                  params={"status": "down" if down else "up",
                                          "msg": summary[:250], "ping": ""},
                                  timeout=15)
            answer.raise_for_status()
            ok = answer.json().get("ok", True)
        else:
            url = PING_URL.split("?", 1)[0].rstrip("/")
            answer = requests.post(url + "/fail" if down else url,
                                   data=(details or summary).encode("utf-8")[:10000],
                                   timeout=15)
            answer.raise_for_status()
            # An unknown check answers 200 "OK (not found)" when the URL
            # carries a slug; a mistyped UUID gets a 404 above.
            ok = "not found" not in answer.text.lower()
        if not ok:
            log(f"✗ {service} refused the ping: {answer.text[:200]}")
        return bool(ok)
    except Exception as error:
        log(f"✗ {service} did not take the ping: {error}")
        return False


# ---------------------------------------------------------------------- runs
def run_test():
    if not RECIPIENT and not PING_URL:
        log("Nothing to test: neither [heartbeat] bluesky_recipient nor "
            "ping_url is set.")
        return 1
    failed = False
    text = _("🧪 Testmeldung: Wenn du das liest, kommen die Meldungen des "
             "Wächters bei dir an.")
    if RECIPIENT:
        failed |= not send_dm([f"{header()}\n\n{text}"])
    if PING_URL:
        if ping(False, "SkyRelay test", f"{header()}\n\n{text}"):
            log(f"✓ {ping_service()} took the ping - the check shows it as a sign "
                f"of life. To see a notification arrive, use the test button of "
                f"the notification method there.")
        else:
            failed = True
    return 1 if failed else 0


def run_check():
    now = time.time()
    state = read_state()
    state.setdefault("first_run", now)
    conditions = state.setdefault("conditions", {})

    findings, latest = evaluate(state, now)
    current = {finding.key: finding for finding in findings}

    new, reminders = [], []
    for key, finding in current.items():
        known = conditions.get(key)
        if not known or known.get("kind") != finding.kind:
            new.append(finding)
        elif finding.lasting and REMINDER_HOURS > 0 and \
                now - known.get("notified", 0) >= REMINDER_HOURS * 3600:
            reminders.append(finding)
    gone = [dict(entry, key=key) for key, entry in conditions.items()
            if key not in current]
    recovered = [entry for entry in gone if entry.get("lasting", True)]
    releases = new_releases(state, latest)

    if STATUS_ONLY:
        if not findings:
            print("\n✓ " + _("Alles in Ordnung."))
        for finding in findings:
            print(f"\n⚠️ {finding.title}\n{finding.body}\n"
                  + _f("seit {since}", since=when(finding.since)))
        for _package, _version, text in releases:
            if text:
                print("\n" + text)
        if BINDING.get("installed"):
            line = (f"\nneonize {installed_version('neonize')}: whatsmeow "
                    f"{whatsmeow_date(BINDING['installed'])}")
            if BINDING.get("version"):
                line += (f"  ·  PyPI: neonize {BINDING['version']}, whatsmeow "
                         f"{whatsmeow_date(BINDING.get('latest'))}")
            print(line)
        # What was looked up on PyPI is kept, so the next look costs nothing.
        saved = read_state()
        for key in ("pypi", "whatsmeow"):
            if key in state:
                saved[key] = state[key]
        write_state(saved)
        print(f"\nBluesky: {'@' + RECIPIENT if RECIPIENT else '-'}  ·  "
              f"{ping_service() + ': yes' if PING_URL else 'Ping: -'}  ·  "
              f"enabled: {'yes' if ENABLED else 'no'}")
        return 0

    parts = compose(new, reminders, recovered, releases)
    delivered = True
    if parts and RECIPIENT:
        backoff_until = state.get("dm_retry_after", 0)
        if now < backoff_until:
            log(f"Direct message held back until {when(backoff_until)} "
                f"after an earlier failure.")
            delivered = False
        else:
            delivered = send_dm(split_for_dm(parts))
            if delivered:
                state.pop("dm_retry_after", None)
                state.pop("dm_backoff", None)
            else:
                # Do not knock every 15 minutes: a wrong password would use
                # up the login limit of the bot account.
                backoff = min(state.get("dm_backoff", 1800) * 2, 6 * 3600)
                state["dm_backoff"] = backoff
                state["dm_retry_after"] = now + backoff

    if delivered:
        for finding in new + reminders:
            conditions[finding.key] = {"kind": finding.kind, "title": finding.title,
                                       "since": finding.since or now, "notified": now,
                                       "lasting": finding.lasting}
        for entry in gone:
            conditions.pop(entry["key"], None)
        for package, version, _text in releases:
            state["announced"][package] = version

    if PING_URL:
        down = bool(findings) and PING_MODE != "alive"
        summary = "; ".join(f.title for f in findings) if down else "OK"
        details = "\n\n".join([header()] + [f"⚠️ {f.title}\n{f.body}" for f in findings]) \
            if down else f"{header()}\n\nOK"
        ping(down, summary, details)

    if findings:
        log(f"{len(findings)} problem(s): " + "; ".join(f"{f.key}/{f.kind}" for f in findings))
    elif parts or not os.path.exists(STATE_FILE):
        log("All fine.")
    for line in parts:
        log("   " + line.replace("\n", "\n   "))
    write_state(state)
    return 0


def main():
    if not STATUS_ONLY:
        _rotate_log()
    if TEST:
        return run_test()
    if not ENABLED and not STATUS_ONLY:
        # The cron line may already be there while the switch is still off.
        return 0
    if not RECIPIENT and not PING_URL and not STATUS_ONLY:
        log("[heartbeat] is switched on, but neither bluesky_recipient nor "
            "ping_url is set - nobody to tell.")
        return 1
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
