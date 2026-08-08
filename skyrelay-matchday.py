"""
SkyRelay - Spieltags-Ticker: spiegelt einen WhatsApp-Kanal nach Bluesky,
aber nur an Spieltagen (Prüfung über OpenLigaDB).

Alle vereins- und kontospezifischen Angaben stehen in "skyrelay.conf"
(Vorlage: skyrelay.conf.example). Ein abweichender Pfad lässt sich über die
Umgebungsvariable SKYRELAY_CONFIG angeben - damit sind mehrere Vereine parallel
möglich. Das Bluesky-App-Passwort kommt aus BLUESKY_APP_PASSWORD, nie aus einer Datei.

Funktionsweise:
  1. Beim Start wird über OpenLigaDB geprüft, ob die eigene Mannschaft heute
     spielt. Kein Spiel -> das Programm beendet sich sofort, ohne überhaupt eine
     Verbindung aufzubauen (gedacht für einen täglichen Start per cron).
  2. An Spieltagen wird über neonize (whatsmeow) eine Verbindung zu WhatsApp
     aufgebaut; bis zum konfigurierten Tagesende lauscht das Programm auf
     Ereignisse des Kanals. Neue Beiträge gehen sofort nach Bluesky, ebenso beim
     Verbinden nachgelieferte Beiträge von HEUTE - ältere werden verworfen.
     (Bewusst kein regelmäßiges Abrufen im Dauerbetrieb: get_newsletter_messages
     stürzt im Go-Teil ab, sobald der Abruf eine unsichtbare Meta-Nachricht
     erwischt - etwa die Bearbeitung oder Löschung eines Beitrags. Nur REPLAY und
     CATCHUP nutzen diesen Abruf noch, dort ist ein Absturz verschmerzbar.)
  3. Der Spiel-Hashtag (z.B. #DSCWOB heim, #WOBDSC auswärts) wird aus den
     OpenLigaDB-Daten gebildet (Kürzel aus [team_codes], Heimteam zuerst) - oder
     von Hand über SKYRELAY_HASHTAG gesetzt.
  4. Doppelte Beiträge nach einem Neustart am selben Tag verhindert die monoton
     steigende MessageServerID des Kanals (Stand in der Datei aus [files] state).

Einrichtung (64-Bit-System erforderlich - neonize liefert keine 32-Bit-Pakete):
    ./install.sh
    cp skyrelay.conf.example skyrelay.conf   # und anpassen
    # neonize ist in requirements.txt bewusst festgelegt: 0.4.0/0.4.1 lieferten
    # beschädigte Rückgabewerte ("Wire format was corrupt", Issue #199). Behoben
    # seit 0.4.2, hier aber ungetestet - vor einem Wechsel die Sitzungsdatei
    # sichern und mit Trockenlauf prüfen. Der Absturz bei gelöschten Beiträgen
    # besteht auch in 0.4.3 weiterhin.

Erste Kopplung - muss interaktiv im Terminal laufen (nicht per cron; SSH genügt):
    SKYRELAY_PAIR_PHONE="4915123456789" SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
    (Nummer im internationalen Format ohne + und ohne führende 0)
    -> Es erscheint ein Kopplungscode. Im Handy: WhatsApp -> Einstellungen ->
       Verknüpfte Geräte -> Gerät hinzufügen -> "Stattdessen mit Telefonnummer
       koppeln" -> Code eingeben.
    Ohne SKYRELAY_PAIR_PHONE erscheint stattdessen ein QR-Code im Terminal. Bei
    Scan-Problemen: Fenster stark vergrößern und Bildschirm hell stellen, sonst
    fehlt der Kamera der Kontrast.
    ACHTUNG: Eine Anmeldung über web.whatsapp.com hilft NICHT - dieses Programm
    ist ein eigenes verknüpftes Gerät mit eigener Sitzung. Sie landet in der Datei
    aus [files] session; danach wird SKYRELAY_PAIR_PHONE nicht mehr gebraucht.

Beispiel für cron (täglicher Start um 6 Uhr, den Rest entscheidet das Programm).
Pfade beachten Groß- und Kleinschreibung, und eine Ausgabeumleitung in die
Protokolldatei ist NICHT nötig - das Programm schreibt sie selbst:
    0 6 * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
"""

import asyncio
import configparser
import hashlib
import html as html_utils
import io
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

import requests
import segno
from PIL import Image
from atproto import Client, models, client_utils
from atproto_client.models.blob_ref import BlobRef

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.types import MessageServerID

# httpx (der HTTP-Client der atproto-Bibliothek) loggt sonst jede Anfrage als INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)


def log(*args, **kwargs):
    """print mit vorangestelltem Zeitstempel - für nachvollziehbare Logs (z.B. aus cron)."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]", *args, **kwargs, flush=True)


# =============================== KONFIGURATION ===============================
# Alle vereins- und kontospezifischen Werte stehen in "skyrelay.conf"
# (Vorlage: skyrelay.conf.example). Hier wird nur noch gelesen.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("SKYRELAY_CONFIG") or os.path.join(BASE_DIR, "skyrelay.conf")

if not os.path.exists(CONFIG_FILE):
    print(f"Fehler: Konfigurationsdatei nicht gefunden: {CONFIG_FILE}\n"
          f"Vorlage kopieren und anpassen:\n"
          f"    cp skyrelay.conf.example skyrelay.conf\n"
          f"(oder einen anderen Pfad über die Umgebungsvariable SKYRELAY_CONFIG angeben)",
          file=sys.stderr)
    sys.exit(1)

# interpolation=None: sonst würde configparser Prozentzeichen in Texten deuten.
_cfg = configparser.ConfigParser(interpolation=None)
try:
    with open(CONFIG_FILE, encoding="utf-8") as _f:
        _cfg.read_file(_f)
except Exception as _e:
    print(f"Fehler beim Lesen von {CONFIG_FILE}: {_e}", file=sys.stderr)
    sys.exit(1)


def cfg(section, key, default=None):
    """Wert aus der Konfiguration; fehlt er, greift die Vorgabe."""
    return _cfg.get(section, key, fallback=default)


def cfg_int(section, key, default):
    try:
        return int(str(_cfg.get(section, key, fallback=default)).strip())
    except (ValueError, TypeError):
        return default


def cfg_bool(section, key, default):
    return _cfg.getboolean(section, key, fallback=default)


BLUESKY_HANDLE = cfg("bluesky", "handle", "")
CHANNEL_INVITE_LINK = cfg("source", "channel_invite_link", "")

OPENLIGADB_TEAM_FILTER = cfg("team", "openligadb_filter", "")
OPENLIGADB_TEAM_ID = cfg_int("team", "openligadb_team_id", 0)
LEAGUE_PREFIXES = tuple(
    p.strip().lower() for p in cfg("team", "league_prefixes", "bl, dfb").split(",") if p.strip()
)
LOCAL_TZ = ZoneInfo(cfg("team", "timezone", "Europe/Berlin"))

# Kürzel für die Hashtag-Bildung, z.B. {83: "DSC"}
TEAM_CODES = {}
if _cfg.has_section("team_codes"):
    for _team_id, _code in _cfg.items("team_codes"):
        try:
            TEAM_CODES[int(_team_id)] = _code.strip().upper()
        except ValueError:
            print(f"⚠️ [team_codes] '{_team_id}' ist keine Team-Nummer - übersprungen", file=sys.stderr)

POST_PREFIX = cfg("post", "prefix", "⚽ [Inoffizieller Bot]")
POST_SOURCE_LABEL = cfg("post", "source_label", "Original-Kanal")
STANDING_HASHTAG = cfg("post", "standing_hashtag", "").strip().lstrip("#")
IMAGE_PLACEHOLDER = cfg("post", "image_placeholder", "📸 Neues Bild im Kanal")
VIDEO_PLACEHOLDER = cfg("post", "video_placeholder", "🎥 Neues Video im Kanal")
VIDEO_HINT = cfg("post", "video_hint", "🎥 (Video im Original-Kanal)")
MEDIA_PREFIX = cfg("post", "media_prefix", "skyrelay")

PROFILE_STATUS_ENABLED = cfg_bool("profile", "enabled", True)
PROFILE_STATUS_MARKER = cfg("profile", "marker", "Bot ist")
PROFILE_LINE_ON = cfg("profile", "line_on", "🟢 Bot ist an - {info} {hashtag}")
PROFILE_LINE_OFF = cfg("profile", "line_off", "🔴 Bot ist aus - nächstes Spiel {hashtag}")
PROFILE_LINE_OFF_NO_MATCH = cfg("profile", "line_off_no_match", "🔴 Bot ist aus")
FALLBACK_MATCH_INFO = cfg("profile", "fallback_match_info", "Testspiel")

_day_end = cfg("schedule", "day_end", "23:59")
try:
    DAY_END_HOUR, DAY_END_MINUTE = (int(x) for x in _day_end.split(":", 1))
except ValueError:
    print(f"⚠️ [schedule] day_end='{_day_end}' unlesbar - nutze 23:59", file=sys.stderr)
    DAY_END_HOUR, DAY_END_MINUTE = 23, 59
SUBSCRIBE_RENEW_SECONDS = cfg_int("schedule", "subscribe_renew_seconds", 240)
PAUSE_BETWEEN_POSTS_SECONDS = cfg_int("schedule", "pause_between_posts_seconds", 3)

MAX_VIDEO_BYTES = cfg_int("limits", "max_video_bytes", 100_000_000)
VIDEO_JOB_TIMEOUT_SECONDS = cfg_int("limits", "video_job_timeout_seconds", 600)

LOG_TO_FILE = cfg_bool("logging", "to_file", True)
LOG_MAX_BYTES = cfg_int("logging", "max_bytes", 2_000_000)
LOG_BACKUP_COUNT = cfg_int("logging", "backup_count", 5)

SESSION_DB = os.path.join(BASE_DIR, cfg("files", "session", "skyrelay_session.sqlite3"))
STATE_FILE = os.path.join(BASE_DIR, cfg("files", "state", "skyrelay_state.txt"))
POSTS_MAP_FILE = os.path.join(BASE_DIR, cfg("files", "posts_map", "skyrelay_posts.json"))
LOG_FILE = os.path.join(BASE_DIR, cfg("files", "log", "skyrelay.log"))


def env(name, default=None):
    """Liest SKYRELAY_<name>; akzeptiert übergangsweise noch die alten
    DSC_TICKER_-Namen, damit bestehende Aufrufe und crontab-Zeilen weiterlaufen."""
    wert = os.environ.get(f"SKYRELAY_{name}")
    if wert is not None:
        return wert
    alt = os.environ.get(f"DSC_TICKER_{name}")
    if alt is not None:
        print(f"Hinweis: DSC_TICKER_{name} ist veraltet - bitte SKYRELAY_{name} verwenden.",
              file=sys.stderr)
        return alt
    return default


# SKYRELAY_DRY_RUN=1 -> nur protokollieren, nichts auf Bluesky posten.
DRY_RUN = env("DRY_RUN") == "1"
# SKYRELAY_FORCE=1 -> auch laufen, wenn OpenLigaDB heute kein Spiel kennt (Testspiele).
FORCE_RUN = env("FORCE") == "1"
# SKYRELAY_PAIR_PHONE=<Nummer> -> Erst-Kopplung per Zahlencode statt QR-Scan
# (international ohne "+", z.B. 4915123456789). Nur beim ersten Lauf nötig.
PAIR_PHONE = env("PAIR_PHONE")
# SKYRELAY_REPLAY=N -> Testlauf: verarbeitet einmalig die letzten N vorhandenen
# Kanal-Beiträge und beendet sich. Der Stand bleibt unangetastet.
REPLAY_COUNT = int(env("REPLAY", "0") or 0)
# SKYRELAY_CATCHUP=N -> wie REPLAY, überspringt aber bereits Verarbeitetes,
# schreibt den Stand fort und lauscht danach normal weiter.
CATCHUP_COUNT = int(env("CATCHUP", "0") or 0)
# SKYRELAY_HASHTAG=DSCGUE -> Spiel-Hashtag von Hand setzen (mit oder ohne "#").
MANUAL_HASHTAG = (env("HASHTAG", "") or "").strip().lstrip("#").upper()
# SKYRELAY_PROFILE=on|off -> nur die Profilzeile setzen und sofort beenden.
PROFILE_ONLY = (env("PROFILE", "") or "").strip().lower()


def uebernimm_altdatei(neu, alt_name):
    """Benennt eine Datei aus einer früheren Fassung auf den neuen Namen um.
    Verhindert, dass nach dem Umstellen auf die Konfigurationsdatei eine neue
    WhatsApp-Kopplung nötig wird oder der Verarbeitungsstand verloren geht."""
    alt = os.path.join(BASE_DIR, alt_name)
    if os.path.exists(alt) and not os.path.exists(neu):
        try:
            os.replace(alt, neu)
            print(f"Übernommen: {alt_name} -> {os.path.basename(neu)}")
        except Exception as e:
            print(f"⚠️ Konnte {alt_name} nicht übernehmen: {e}", file=sys.stderr)


for _neu, _alt in ((SESSION_DB, "dsc_ticker_session.sqlite3"),
                   (STATE_FILE, "dsc_ticker_state.txt"),
                   (POSTS_MAP_FILE, "dsc_ticker_posts.json"),
                   (LOG_FILE, "ticker.log")):
    uebernimm_altdatei(_neu, _alt)


def rotate_log(path, max_bytes, backups):
    """Rotiert die Logdatei beim Start, wenn sie zu groß geworden ist:
    ticker.log -> ticker.log.1 -> ... -> ticker.log.N (ältestes fliegt raus)."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return
        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(backups - 1, 0, -1):
            src = f"{path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
    except Exception as e:
        print(f"⚠️ Log-Rotation fehlgeschlagen: {e}", flush=True)


def start_file_logging(path):
    """Schreibt ALLE Ausgaben zusätzlich in eine Datei - auch die des Go-Layers
    (whatsmeow/neonize), die nicht durch Python laufen und die ein reines
    Python-Logging deshalb verpassen würde. Dafür werden stdout/stderr in eine
    Pipe umgehängt; ein Hintergrund-Thread schreibt jede Zeile in die Datei UND
    auf die echte Konsole (wie "tee"). Schlägt das fehl, läuft das Script
    normal weiter - nur eben ohne Logdatei."""
    try:
        rotate_log(path, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
        logfile = open(path, "ab", buffering=0)
        read_fd, write_fd = os.pipe()
        console_fd = os.dup(1)  # Kopie der echten Konsole, bevor umgehängt wird
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)

        def pump():
            with os.fdopen(read_fd, "rb", 0) as pipe:
                for line in iter(pipe.readline, b""):
                    for sink in (logfile.write, lambda b: os.write(console_fd, b)):
                        try:
                            sink(line)
                        except Exception:
                            pass

        threading.Thread(target=pump, name="log-tee", daemon=True).start()
        # stdout ist ohne Terminal sonst blockgepuffert - Zeilenpufferung sorgt
        # dafür, dass das Log auch bei "tail -f" sofort mitläuft.
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
        return True
    except Exception as e:
        print(f"⚠️ Datei-Logging konnte nicht gestartet werden: {e}", flush=True)
        return False


if LOG_TO_FILE:
    start_file_logging(LOG_FILE)
    log(f"--- Ticker-Start (Log: {LOG_FILE}) ---")

BLUESKY_APP_PASSWORD = os.environ.get("BLUESKY_APP_PASSWORD")
if not BLUESKY_APP_PASSWORD and not DRY_RUN:
    log("Fehler: Umgebungsvariable BLUESKY_APP_PASSWORD ist nicht gesetzt.")
    log('Setzen z.B. mit:  export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log("(dauerhaft: in ~/.bashrc bzw. in der crontab-Zeile vor dem python-Aufruf)")
    log("Oder für einen reinen Lese-Test ohne Bluesky:  SKYRELAY_DRY_RUN=1")
    sys.exit(1)
# ----------------------------------


# 1. Spieltags-Check über OpenLigaDB (kostenlos, ohne API-Key)
def team_code(team):
    """DFL-Kürzel zu einem OpenLigaDB-Team; Fallback für unbekannte (Pokal-)Gegner."""
    code = TEAM_CODES.get(team["teamId"])
    if code is None:
        code = re.sub(r"[^A-Za-zÄÖÜäöü]", "", team["shortName"] or team["teamName"])[:3].upper()
        log(f'⚠️ Kein DFL-Kürzel für "{team["teamName"]}" (teamId {team["teamId"]}) hinterlegt - '
            f'nutze Fallback "{code}". Bitte in TEAM_CODES nachtragen.')
    return code


def match_info_text(match):
    """Kurzinfo zum Spiel für die Profilzeile: "1. Spieltag" bzw. "DFB-Pokal, 1. Runde"."""
    group = (match.get("group") or {}).get("groupName", "").strip()
    if "pokal" in match.get("leagueName", "").lower():
        return f"DFB-Pokal, {group}" if group else "DFB-Pokal"
    return group or match.get("leagueName", "")


def fetch_team_matches(weeks_back=1, weeks_forward=1):
    """Holt die Spiele der eigenen Mannschaft aus OpenLigaDB und liefert sie als Liste von
    (kickoff_local, match) - aufsteigend sortiert, doppelte Termine entfernt.

    Filtert zwei Sorten Datenmüll heraus:
      * fremde Teams (der API-Teamfilter "bielefeld" ist unscharf) -> Prüfung auf teamId
      * Fantasie-/Testligen: OpenLigaDB listete real z.B. eine Liga "ESP8266"
        mit demselben Spiel an einem FALSCHEN Datum. Ohne diesen Filter würde der
        Ticker an einem spielfreien Tag anspringen."""
    url = (f"https://api.openligadb.de/getmatchesbyteam/{OPENLIGADB_TEAM_FILTER}"
           f"/{weeks_back}/{weeks_forward}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()

    result = []
    seen = set()
    skipped_leagues = set()
    for match in resp.json():
        if OPENLIGADB_TEAM_ID not in (match["team1"]["teamId"], match["team2"]["teamId"]):
            continue
        shortcut = (match.get("leagueShortcut") or "").lower()
        if not shortcut.startswith(LEAGUE_PREFIXES):
            skipped_leagues.add(f'{match.get("leagueShortcut")} ({match.get("leagueName")})')
            continue
        kickoff_local = datetime.fromisoformat(
            match["matchDateTimeUTC"].replace("Z", "+00:00")
        ).astimezone(LOCAL_TZ)
        # bl2 und bl2h liefern dasselbe Spiel doppelt - nach Anstoß+Gegner entdoppeln.
        key = (kickoff_local, match["team1"]["teamId"], match["team2"]["teamId"])
        if key in seen:
            continue
        seen.add(key)
        result.append((kickoff_local, match))

    if skipped_leagues:
        log(f"(OpenLigaDB: Spiele aus unbekannten Ligen ignoriert: {', '.join(sorted(skipped_leagues))})")
    return sorted(result, key=lambda item: item[0])


def describe_match(kickoff_local, match):
    """Baut (kickoff_local, beschreibung, hashtag, kurzinfo) aus einem OpenLigaDB-Spiel."""
    desc = f'{match["team1"]["teamName"]} - {match["team2"]["teamName"]} ({match["leagueName"]})'
    hashtag = team_code(match["team1"]) + team_code(match["team2"])  # team1 = Heimteam
    return kickoff_local, desc, hashtag, match_info_text(match)


def get_todays_match():
    """Liefert (kickoff_local, beschreibung, hashtag, kurzinfo) wenn die eigene Mannschaft heute
    spielt, sonst None. Der Hashtag folgt dem Schema Heimteam+Auswärtsteam, also
    z.B. DSCWOB (heim) bzw. WOBDSC (auswärts)."""
    today_local = datetime.now(LOCAL_TZ).date()
    for kickoff_local, match in fetch_team_matches(1, 1):
        if kickoff_local.date() == today_local:
            return describe_match(kickoff_local, match)
    return None


def get_next_match():
    """Liefert das nächste noch ausstehende Spiel (für die "Bot ist aus"-Profilzeile),
    sonst None. Schaut bewusst weit voraus, damit auch Winter-/Sommerpausen überbrückt
    werden."""
    now = datetime.now(LOCAL_TZ)
    for kickoff_local, match in fetch_team_matches(0, 12):
        if kickoff_local > now:
            return describe_match(kickoff_local, match)
    return None


# 2. Wasserzeichen-Verwaltung (MessageServerID ist pro Kanal monoton steigend)
def load_watermark():
    """Liest das Wasserzeichen, aber nur wenn es von HEUTE stammt (Neustart am selben Tag).
    An einem neuen Spieltag wird stattdessen frisch gebaselined."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved_date, server_id = f.read().strip().split(";")
        if saved_date == date.today().isoformat():
            return int(server_id)
    except Exception as e:
        log(f"⚠️ Konnte Wasserzeichen-Datei nicht lesen ({e}) - baseline neu.")
    return None


def save_watermark(server_id):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(f"{date.today().isoformat()};{server_id}")


def load_posts_map():
    """Lädt die Zuordnung ServerID -> gepostete Bluesky-URIs + Text-Hash (nur vom
    heutigen Tag). Wird gebraucht, um bei Kanal-Bearbeitungen die alten
    Bluesky-Posts löschen und ersetzen zu können."""
    if not os.path.exists(POSTS_MAP_FILE):
        return {}
    try:
        with open(POSTS_MAP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == date.today().isoformat():
            return data.get("posts", {})
    except Exception as e:
        log(f"⚠️ Konnte Post-Zuordnung nicht lesen ({e}) - starte mit leerer Zuordnung.")
    return {}


def save_posts_map(posts_map):
    with open(POSTS_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": date.today().isoformat(), "posts": posts_map}, f)


def text_hash(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


# 3. Hilfsfunktionen für Nachrichteninhalt & Bluesky
def unwrap_message(msg):
    """Packt generische Container-Nachrichten aus - manche Posts stecken in
    Wrappern wie ephemeralMessage, bis der eigentliche Inhalt vorliegt.
    Bearbeitungen kommen als protocolMessage.editedMessage mit dem NEUEN Inhalt."""
    for _ in range(3):
        if msg.HasField("ephemeralMessage"):
            msg = msg.ephemeralMessage.message
        elif msg.HasField("viewOnceMessage"):
            msg = msg.viewOnceMessage.message
        elif msg.HasField("deviceSentMessage"):
            msg = msg.deviceSentMessage.message
        elif msg.HasField("protocolMessage") and msg.protocolMessage.HasField("editedMessage"):
            msg = msg.protocolMessage.editedMessage
        else:
            break
    return msg


def extract_text(msg):
    """Zieht den Text aus einer WhatsApp-E2E-Message (Kanal-Posts sind meist
    conversation/extendedTextMessage, Bilder tragen den Text als caption)."""
    if msg.conversation:
        return msg.conversation
    if msg.HasField("extendedTextMessage") and msg.extendedTextMessage.text:
        return msg.extendedTextMessage.text
    if msg.HasField("imageMessage") and msg.imageMessage.caption:
        return msg.imageMessage.caption
    if msg.HasField("videoMessage") and msg.videoMessage.caption:
        return msg.videoMessage.caption
    if msg.HasField("documentMessage") and msg.documentMessage.caption:
        return msg.documentMessage.caption
    return ""


def compress_image_for_bluesky(image_bytes, max_dim=2000, max_bytes=1_500_000, start_quality=85):
    """Komprimiert ein Bild so, dass es unter Blueskys Blob-Größenlimit passt."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    quality = start_quality
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    while buf.tell() > max_bytes and quality > 50:
        buf = io.BytesIO()
        quality -= 10
        img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def split_text(text, first_limit=200, follow_limit=240):
    """Zerlegt Text in Chunks unter Blueskys 300-Zeichen-Limit. Der erste Chunk
    ist kleiner, weil er zusätzlich Präfix + Quell-Link trägt (~60 Zeichen);
    der letzte bekommt noch die Hashtags. Bricht bevorzugt an Wort-/Zeilengrenzen,
    notfalls hart."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    limit = first_limit
    while len(text) > limit:
        cut = max(text.rfind(" ", 0, limit), text.rfind("\n", 0, limit))
        if cut < limit // 2:  # keine brauchbare Grenze gefunden -> hart schneiden
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
        limit = follow_limit
    if text:
        chunks.append(text)
    return chunks


URL_REGEX = re.compile(r"https?://[^\s<>()\[\]]+")


def add_text_with_links(tb, text):
    """Fügt Text in den TextBuilder ein und macht enthaltene URLs klickbar
    (Bluesky braucht dafür Facets - reiner Text wird nicht automatisch verlinkt)."""
    pos = 0
    for m in URL_REGEX.finditer(text):
        if m.start() > pos:
            tb.text(text[pos:m.start()])
        tb.link(m.group(0), m.group(0))
        pos = m.end()
    if pos < len(text):
        tb.text(text[pos:])


def fetch_og_data(url):
    """Holt OpenGraph-Daten (Titel, Beschreibung, Vorschaubild) einer Seite für
    die Bluesky-Link-Karte. Liefert (title, description, thumb_bytes|None)."""
    resp = requests.get(
        url, timeout=15, headers={"User-Agent": "Mozilla/5.0 (X11; Linux aarch64)"}
    )
    resp.raise_for_status()
    page = resp.text

    def og(prop):
        m = re.search(
            r'<meta[^>]+property=["\']og:%s["\'][^>]+content=["\']([^"\']*)["\']' % prop,
            page, re.IGNORECASE,
        ) or re.search(
            r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']og:%s["\']' % prop,
            page, re.IGNORECASE,
        )
        return html_utils.unescape(m.group(1)) if m else ""

    title = og("title") or url
    description = og("description")
    thumb_bytes = None
    image_url = og("image")
    if image_url:
        try:
            img_resp = requests.get(image_url, timeout=15)
            img_resp.raise_for_status()
            thumb_bytes = compress_image_for_bluesky(img_resp.content)
        except Exception as thumb_err:
            log(f"   ⚠️ Vorschaubild nicht ladbar ({thumb_err}) - Karte ohne Bild.")
    return title, description, thumb_bytes


def build_external_embed(url):
    """Baut eine Bluesky-Link-Vorschaukarte (app.bsky.embed.external). Bluesky
    erzeugt Previews nicht serverseitig - ohne diese Karte bleibt ein Link nackt."""
    try:
        title, description, thumb_bytes = fetch_og_data(url)
        thumb_blob = None
        if thumb_bytes:
            thumb_blob = bsky_client.upload_blob(thumb_bytes).blob
        log(f"   ✓ Link-Vorschau erzeugt: {title[:60]!r}")
        return models.AppBskyEmbedExternal.Main(
            external=models.AppBskyEmbedExternal.External(
                uri=url,
                title=title[:300],
                description=(description or "")[:300],
                thumb=thumb_blob,
            )
        )
    except Exception as e:
        log(f"   ⚠️ Link-Vorschau fehlgeschlagen ({e}) - poste ohne Karte.")
        return None


bsky_client = None
pds_aud = None        # PDS-DID des Bot-Accounts, wird beim ersten Video-Upload aufgelöst
match_hashtag = None  # wird in main() aus den OpenLigaDB-Daten gesetzt (z.B. "DSCWOB")
match_info = None     # Kurzinfo zum heutigen Spiel, z.B. "1. Spieltag" (für die Profilzeile)
match_kickoff = None  # Anstoßzeit des heutigen Spiels (für die Profilzeile)


def ensure_bsky():
    """Stellt die Bluesky-Verbindung her (lazy, einmalig pro Lauf)."""
    global bsky_client
    if bsky_client is None:
        log("Verbinde mit Bluesky...")
        bsky_client = Client()
        bsky_client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)


# --- Profil-Statuszeile -------------------------------------------------------
profile_status_on = False  # True, sobald die Bio auf "Bot ist an" steht


def set_profile_status(on):
    """Schaltet die ERSTE Zeile der Bluesky-Bio zwischen "Bot ist an"/"Bot ist aus" um.
    Alle weiteren Bio-Zeilen sowie Avatar, Banner und Anzeigename bleiben unangetastet
    (das komplette Profil-Record wird gelesen und nur die Beschreibung geändert).
    Fehler werden nur geloggt - der Ticker läuft in jedem Fall weiter."""
    global profile_status_on

    if not PROFILE_STATUS_ENABLED:
        return

    # Zeile zusammenbauen
    try:
        if on:
            line = PROFILE_LINE_ON.format(
                info=match_info or FALLBACK_MATCH_INFO,
                hashtag=f"#{match_hashtag}" if match_hashtag else "",
                date=match_kickoff.strftime("%d.%m.") if match_kickoff else "",
                time=match_kickoff.strftime("%H:%M") if match_kickoff else "",
            )
        else:
            nxt = get_next_match()
            if nxt:
                kickoff, _desc, hashtag, info = nxt
                line = PROFILE_LINE_OFF.format(
                    info=info, hashtag=f"#{hashtag}",
                    date=kickoff.strftime("%d.%m."), time=kickoff.strftime("%H:%M"),
                )
            else:
                line = PROFILE_LINE_OFF_NO_MATCH
        line = " ".join(line.split())  # doppelte Leerzeichen bei leeren Platzhaltern vermeiden
    except Exception as e:
        log(f"⚠️ Konnte Profil-Statuszeile nicht bauen: {e}")
        return

    if DRY_RUN:
        log(f"   [DRY_RUN] Würde Profil-Statuszeile setzen: {line!r}")
        profile_status_on = on
        return

    try:
        ensure_bsky()
        resp = bsky_client.com.atproto.repo.get_record(
            models.ComAtprotoRepoGetRecord.Params(
                repo=bsky_client.me.did, collection="app.bsky.actor.profile", rkey="self"
            )
        )
        record = resp.value
        lines = (record.description or "").split("\n")
        if lines and PROFILE_STATUS_MARKER in lines[0]:
            lines[0] = line
        else:
            lines.insert(0, line)
        record.description = "\n".join(lines)[:2560]  # Bluesky-Limit

        bsky_client.com.atproto.repo.put_record(
            models.ComAtprotoRepoPutRecord.Data(
                repo=bsky_client.me.did,
                collection="app.bsky.actor.profile",
                rkey="self",
                record=record,
                swap_record=resp.cid,  # verhindert Überschreiben paralleler Bio-Änderungen
            )
        )
        profile_status_on = on
        log(f"✓ Profil-Statuszeile gesetzt: {line}")
    except Exception as e:
        log(f"⚠️ Profil-Statuszeile konnte nicht aktualisiert werden: {e}")


# --- Video-Upload: portiert aus skyrelay-feed.py ---------------------------
# TODO(Auslagerung): resolve_pds_did_web, upload_video_to_bluesky, das Bild-
# Komprimieren und die Thread-Post-Logik existieren nahezu identisch im
# Instagram-Reposter. Sobald beide Programme gemeinsame Bausteine teilen,
# veröffentlicht wird, gehören diese gemeinsamen Funktionen in ein geteiltes
# Modul - die Redundanz ist hier bewusst und nur vorübergehend.

def resolve_pds_did_web(actor_did):
    """Löst die DID der tatsächlichen PDS des Accounts auf (nötig als 'aud' für Service-Auth-Tokens)."""
    if actor_did.startswith("did:plc:"):
        resp = requests.get(f"https://plc.directory/{actor_did}", timeout=15)
    elif actor_did.startswith("did:web:"):
        host = actor_did.split(":", 2)[2]
        resp = requests.get(f"https://{host}/.well-known/did.json", timeout=15)
    else:
        raise ValueError(f"Unbekanntes DID-Format: {actor_did}")

    resp.raise_for_status()
    doc = resp.json()

    for service in doc.get("service", []):
        if service.get("id") == "#atproto_pds":
            endpoint = service["serviceEndpoint"]
            host = endpoint.split("://", 1)[-1].rstrip("/")
            return f"did:web:{host}"

    raise ValueError(f"Konnte PDS-Service-Endpoint nicht im DID-Dokument finden: {doc}")


def upload_video_to_bluesky(video_bytes, filename):
    """Lädt Video-Bytes zu Bluesky hoch und liefert das fertige Embed-Objekt zurück.
    Wirft eine Exception bei Fehlschlag - der Aufrufer kümmert sich um den Fallback.
    (Bluesky verarbeitet Videos über einen eigenen Dienst: Upload -> serverseitiges
    Transkodieren -> Job-Polling bis JOB_STATE_COMPLETED.)"""
    global pds_aud

    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise RuntimeError(
            f"Video ist {len(video_bytes)} Bytes groß und überschreitet das Bluesky-Limit "
            f"von {MAX_VIDEO_BYTES} Bytes - Upload wird gar nicht erst versucht."
        )

    if pds_aud is None:
        try:
            pds_aud = resolve_pds_did_web(bsky_client.me.did)
            log(f"   ✓ PDS-DID ermittelt: {pds_aud}")
        except Exception as pds_err:
            log(f"   ⚠️ Konnte PDS-DID nicht auflösen: {pds_err}")
            pds_aud = "did:web:bsky.social"

    service_auth = bsky_client.com.atproto.server.get_service_auth({
        'aud': pds_aud,
        'lxm': 'com.atproto.repo.uploadBlob',
        'exp': int(time.time()) + 60 * 15
    })
    token = service_auth.token

    upload_url = "https://video.bsky.app/xrpc/app.bsky.video.uploadVideo"
    upload_params = {"did": bsky_client.me.did, "name": filename}
    upload_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(len(video_bytes)),
    }

    log(f"   Sende Videodaten an: {upload_url} ({len(video_bytes)} Bytes)")

    max_attempts = 3
    upload_response = None
    job_id = None

    for attempt in range(1, max_attempts + 1):
        try:
            upload_response = requests.post(
                upload_url, params=upload_params, headers=upload_headers,
                data=video_bytes, timeout=180
            )

            if upload_response.status_code == 409:
                # Video wurde in einem früheren Lauf bereits hochgeladen und fertig
                # verarbeitet - bestehende Job-ID weiterverwenden statt neu hochzuladen.
                conflict_data = upload_response.json()
                if conflict_data.get("error") == "already_exists" and conflict_data.get("jobId"):
                    job_id = conflict_data["jobId"]
                    log(f"   ℹ️ Video wurde bereits verarbeitet, verwende bestehende Job-ID: {job_id}")
                    break

            upload_response.raise_for_status()
            break
        except requests.exceptions.HTTPError as http_err:
            body_preview = upload_response.text[:500] if upload_response is not None else "(keine Antwort)"
            log(f"   ⚠️ Upload-Versuch {attempt}/{max_attempts} fehlgeschlagen: {http_err}")
            log(f"      Server-Antwort: {body_preview}")
            if attempt == max_attempts:
                raise
            wait_seconds = 10 * attempt
            log(f"      Warte {wait_seconds}s vor dem nächsten Versuch...")
            time.sleep(wait_seconds)

    if job_id is None:
        status_data = upload_response.json()
        job_status_obj = status_data.get("jobStatus", status_data)
        job_id = job_status_obj.get("jobId")
        log(f"   ✓ Video erfolgreich übertragen! Job-ID erhalten: {job_id}")

    log("   Warte auf Server-Verarbeitung...")

    status_url = "https://video.bsky.app/xrpc/app.bsky.video.getJobStatus"
    deadline = time.time() + VIDEO_JOB_TIMEOUT_SECONDS
    video_blob_dict = None
    while True:
        if time.time() > deadline:
            raise RuntimeError(
                f"Video-Verarbeitung nicht innerhalb von {VIDEO_JOB_TIMEOUT_SECONDS}s abgeschlossen (Job {job_id})."
            )
        status_response = requests.get(
            status_url, params={"jobId": job_id},
            headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        status_response.raise_for_status()
        current_status_data = status_response.json()
        current_job_obj = current_status_data.get("jobStatus", current_status_data)
        job_state = current_job_obj.get("state")

        if job_state == "JOB_STATE_COMPLETED":
            video_blob_dict = current_job_obj.get("blob")
            log("   ✓ Video-Verarbeitung auf dem Bluesky-Server abgeschlossen!")
            break
        elif job_state == "JOB_STATE_FAILED":
            error_msg = current_job_obj.get("error", "Unbekannter Rendering-Fehler")
            raise RuntimeError(f"Bluesky-Server meldet Fehler beim Video-Rendering: {error_msg}")
        else:
            log(f"   ⏳ Status: {job_state}... (Warte 5 Sekunden)")
            time.sleep(5)

    video_blob = models.get_or_create(video_blob_dict, model=BlobRef)
    return models.AppBskyEmbedVideo.Main(video=video_blob)
# ------------------------------------------------------------------------------


def post_to_bluesky(text, image_blobs, video_bytes=None, video_thumb=None, media_name="video"):
    """Postet eine Kanal-Nachricht im Format des Instagram-Reposters: Hauptpost mit
    "[Inoffizieller Bot]"-Präfix + Quell-Link, bei Überlänge Folge-Chunks als Replies.
    URLs im Text werden klickbar. Embed-Priorität (ein Post = ein Embed):
    Video > Bilder > Link-Vorschaukarte. Scheitert der Video-Upload, dient das
    WhatsApp-Vorschaubild als Bild-Fallback. An den letzten Chunk kommen der
    generierte Spiel-Hashtag und der Dauer-Hashtag. Liefert die Liste der erzeugten
    Post-URIs zurück (Hauptpost zuerst) - wird für die Bearbeitungs-Logik
    gespeichert, um Posts später löschen zu können."""
    hashtags = ([match_hashtag] if match_hashtag else []) + \
               ([STANDING_HASHTAG] if STANDING_HASHTAG else [])

    text_chunks = split_text(text)
    if not text_chunks and not image_blobs and not video_bytes:
        log("   (leere Nachricht, wird übersprungen)")
        return []

    card_url_match = URL_REGEX.search(text) if not image_blobs and not video_bytes else None

    if DRY_RUN:
        preview = text_chunks[0][:120] if text_chunks else "(nur Medien)"
        tags = " ".join(f"#{t}" for t in hashtags)
        card = f", Link-Karte für {card_url_match.group(0)}" if card_url_match else ""
        video = f", 1 Video ({len(video_bytes)} Bytes)" if video_bytes else ""
        log(f"   [DRY_RUN] Würde posten ({len(text_chunks)} Chunk(s), "
            f"{len(image_blobs)} Bild(er){video}, Tags: {tags}{card}): {preview}...")
        return []

    ensure_bsky()

    if not text_chunks:
        text_chunks = [VIDEO_PLACEHOLDER if video_bytes else IMAGE_PLACEHOLDER]

    # Video-Upload zuerst versuchen; bei Fehlschlag Vorschaubild als Bild-Fallback.
    video_embed = None
    if video_bytes:
        try:
            video_embed = upload_video_to_bluesky(video_bytes, f"{media_name}.mp4")
        except Exception as video_err:
            log(f"   ⚠️ Video-Upload fehlgeschlagen: {video_err}")
            if video_thumb and not image_blobs:
                try:
                    image_blobs = [compress_image_for_bluesky(video_thumb)]
                    log("   Nutze Video-Vorschaubild als Fallback.")
                except Exception as thumb_err:
                    log(f"   ⚠️ Auch Vorschaubild-Fallback fehlgeschlagen: {thumb_err}")

    # Link-Vorschaukarte nur, wenn keine anderen Medien da sind (ein Post = ein Embed).
    external_embed = None
    if card_url_match:
        external_embed = build_external_embed(card_url_match.group(0))

    alt_text = text_chunks[0][:200]
    total = len(text_chunks)
    root_ref = None
    parent_ref = None
    created_uris = []

    for i, chunk in enumerate(text_chunks):
        is_first = i == 0
        is_last = i == total - 1

        embed = None
        if is_first and video_embed is not None:
            embed = video_embed
        elif is_first and image_blobs:
            uploaded = []
            for blob_data in image_blobs[:4]:  # Bluesky: max. 4 Bilder pro Post
                upload = bsky_client.upload_blob(blob_data)
                uploaded.append(models.AppBskyEmbedImages.Image(alt=alt_text, image=upload.blob))
            embed = models.AppBskyEmbedImages.Main(images=uploaded)
        elif is_first and external_embed is not None:
            embed = external_embed

        tb = client_utils.TextBuilder()
        if is_first:
            tb.text(f"{POST_PREFIX}\n🔗 Quelle: ")
            tb.link(POST_SOURCE_LABEL, CHANNEL_INVITE_LINK)
            tb.text("\n\n")
        add_text_with_links(tb, chunk if total == 1 else f"{chunk} ({i + 1}/{total})")
        if is_last:
            tb.text("\n\n")
            for idx, tag in enumerate(hashtags):
                if idx:
                    tb.text(" ")
                tb.tag(f"#{tag}", tag)

        if is_first:
            root_post = bsky_client.send_post(text=tb, embed=embed)
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=root_post.cid, uri=root_post.uri)
            parent_ref = root_ref
            created_uris.append(root_post.uri)
        else:
            reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
            reply_record = models.AppBskyFeedPost.Record(
                text=tb.build_text(),
                facets=tb.build_facets(),
                reply=reply_ref,
                created_at=bsky_client.get_current_time_iso(),
            )
            reply = bsky_client.com.atproto.repo.create_record(
                models.ComAtprotoRepoCreateRecord.Data(
                    repo=bsky_client.me.did,
                    collection=models.ids.AppBskyFeedPost,
                    record=reply_record,
                )
            )
            parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=reply.cid, uri=reply.uri)
            created_uris.append(reply.uri)

    log("   ✓ Auf Bluesky veröffentlicht.")
    return created_uris


# 4. Kanal-Nachricht verarbeiten
async def process_newsletter_message(client, raw_msg, server_id):
    """Verarbeitet eine neue Kanal-Nachricht: Text extrahieren, ggf. Bild laden, reposten."""
    msg = unwrap_message(raw_msg)

    # Diagnose: welche Felder hat die Nachricht wirklich? (Kanal-Posts können
    # anders strukturiert sein als normale Chats - so sehen wir sofort, was ankommt.)
    field_names = [fd.name for fd, _ in msg.ListFields()]
    log(f"   [DEBUG] Message-Felder: {field_names or '(keine)'}")

    text = extract_text(msg)
    if not text and not field_names:
        log("   [DEBUG] Nachricht ist komplett leer (vermutlich gelöschter Post oder Reaktions-Update).")
    elif not text:
        preview = str(msg)[:300].replace("\n", " | ")
        log(f"   [DEBUG] Kein Text extrahierbar, Roh-Vorschau: {preview}")

    image_blobs = []
    video_bytes = None
    video_thumb = None
    if msg.HasField("imageMessage"):
        try:
            raw = await client.download_any(msg)
            image_blobs.append(compress_image_for_bluesky(raw))
        except Exception as dl_err:
            # Kanal-Medien laufen teils über andere Endpunkte als normale Chats -
            # falls der Download scheitert, wird nur der Text gepostet.
            log(f"   ⚠️ Bild-Download fehlgeschlagen ({dl_err}) - poste nur Text.")
    elif msg.HasField("videoMessage"):
        video_thumb = msg.videoMessage.JPEGThumbnail or None
        try:
            log("   Lade Video aus dem Kanal herunter...")
            video_bytes = await client.download_any(msg)
            log(f"   ✓ Video geladen ({len(video_bytes)} Bytes).")
        except Exception as dl_err:
            log(f"   ⚠️ Video-Download fehlgeschlagen ({dl_err}) - poste Text"
                f"{' + Vorschaubild' if video_thumb else ''}.")
            if video_thumb:
                try:
                    image_blobs.append(compress_image_for_bluesky(video_thumb))
                except Exception:
                    pass
            if text:
                text += f"\n\n{VIDEO_HINT}"

    log(f"   Text ({len(text)} Zeichen): {text[:100]!r}...")
    return post_to_bluesky(text, image_blobs, video_bytes, video_thumb,
                           media_name=f"{MEDIA_PREFIX}_{server_id}")


async def handle_edit(client, event, server_id, posts_map):
    """Behandelt ein Event zu einer bereits verarbeiteten ServerID: Bei einer
    echten Bearbeitung werden die alten Bluesky-Posts gelöscht und die neue
    Version gepostet; unveränderte Wiederzustellungen werden ignoriert."""
    msg = unwrap_message(event.Message)
    new_text = extract_text(msg)
    has_media = msg.HasField("imageMessage") or msg.HasField("videoMessage")

    if not new_text and not has_media:
        log(f"   (ServerID {server_id}: Meta-Nachricht ohne Inhalt - übersprungen)")
        return

    entry = posts_map.get(str(server_id))
    if entry and entry.get("hash") == text_hash(new_text):
        log(f"   (ServerID {server_id}: unveränderte Wiederzustellung - übersprungen)")
        return

    if not entry:
        # Bearbeitung zu einem Post, den dieser Bot heute nicht (nachvollziehbar)
        # veröffentlicht hat - alte Tickerposts sind uninteressant, ignorieren.
        log(f"   (Bearbeitung zu ServerID {server_id} ohne gespeicherte Bluesky-Posts - "
            f"alter/unbekannter Post, wird ignoriert)")
        return

    log(f"[EDIT] Kanal-Post {server_id} wurde bearbeitet - ersetze Bluesky-Post(s).")

    if DRY_RUN:
        log(f"   [DRY_RUN] Würde {len(entry['uris'])} alte(n) Bluesky-Post(s) löschen "
            f"und neu posten: {new_text[:100]!r}...")
        return

    ensure_bsky()
    deleted = 0
    # Replies zuerst löschen (umgekehrte Reihenfolge), zum Schluss den Hauptpost.
    for uri in reversed(entry["uris"]):
        try:
            bsky_client.delete_post(uri)
            deleted += 1
        except Exception as del_err:
            log(f"   ⚠️ Konnte {uri} nicht löschen: {del_err}")
    log(f"   ✓ {deleted} alte(n) Bluesky-Post(s) gelöscht.")

    uris = await process_newsletter_message(client, event.Message, server_id)
    if uris:
        posts_map[str(server_id)] = {"uris": uris, "hash": text_hash(new_text)}
        save_posts_map(posts_map)


# 5. Hauptablauf
client = NewAClient(SESSION_DB)

# connect() kehrt sofort zurück - Login/Pairing laufen asynchron. Wer zu früh
# Newsletter-Methoden aufruft, provoziert einen Segfault im Go-Layer (nil Client).
# Deshalb: auf das Connected-Event warten, bevor irgendetwas anderes passiert.
wa_connected = asyncio.Event()


@client.event(ConnectedEv)
async def on_connected(_, __):
    wa_connected.set()


@client.event(PairStatusEv)
async def on_pair_status(_, __):
    log("✓ Gerät erfolgreich mit dem WhatsApp-Konto gekoppelt.")


_qr_instructions_shown = False


@client.event.qr
async def on_qr(_, data_qr):
    """Eigener QR-Handler statt des neonize-Standards: Die Anleitung erscheint so
    nur, wenn wirklich eine Erst-Kopplung ansteht - nicht bei jedem normalen Start."""
    global _qr_instructions_shown
    if not _qr_instructions_shown:
        _qr_instructions_shown = True
        log("Erst-Kopplung nötig - diesen QR-Code mit dem Handy der Wegwerf-Nummer scannen:")
        log("(WhatsApp -> Einstellungen -> Verknüpfte Geräte -> Gerät hinzufügen;")
        log(" bei Scan-Problemen Terminal stark vergrößern und Bildschirm heller stellen.")
        log(" Alternative: Kopplung per Zahlencode via SKYRELAY_PAIR_PHONE=<Nummer>.)")
    else:
        log("(neuer QR-Code - der alte ist abgelaufen)")
    segno.make_qr(data_qr).terminal(compact=True)


# Kanal-Posts kommen als Live-Events herein. Regelmäßiges Polling über
# get_newsletter_messages ist im Dauerbetrieb TABU: Enthält der Kanal eine
# inhaltslose Nachricht, panict der Go-Layer von neonize beim Serialisieren
# ("required field ... Message not set") und reißt den ganzen Prozess mit.
# Auslöser sind UNSICHTBARE Meta-Nachrichten (im Kanal nicht sichtbar, belegen
# aber eine eigene ServerID) - z.B. die Bearbeitung oder Löschung eines Posts,
# evtl. auch Album-Gruppierungen. Was genau, ließ sich nicht abschließend klären:
# Die Nachricht ist mit neonize nicht inspizierbar, jeder Abruf stirbt an ihr.
# Auf dem Event-Weg passiert das nicht: whatsmeow filtert solche Nachrichten
# selbst aus ("doesn't have byte content").
channel_user = None                       # User-Teil der Kanal-JID, wird in main() gesetzt
incoming_events: asyncio.Queue = asyncio.Queue()


@client.event(MessageEv)
async def on_message(_, event):
    # Bewusst ALLE Newsletter-Events annehmen: Die Offline-Nachlieferung kommt
    # sofort beim Verbinden - also BEVOR main() die Kanal-JID aufgelöst hat.
    # Der Kanal-Filter passiert deshalb erst bei der Verarbeitung im Loop.
    if event.Info.MessageSource.Chat.Server == "newsletter":
        await incoming_events.put(event)


async def with_retries(description, coro_factory, max_attempts=6, wait_seconds=15):
    """Führt eine WhatsApp-Abfrage mit Wiederholungen aus. Nötig, weil whatsmeow
    v.a. direkt nach dem Erst-Pairing mehrfach neu verbindet (Code 515, History-Sync,
    Prekey-Upload) - Abfragen in dieser Phase scheitern oder liefern kaputte Antworten
    (z.B. protobuf 'Wire format was corrupt')."""
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            log(f"⚠️ {description} fehlgeschlagen (Versuch {attempt}/{max_attempts}): {e}")
            if attempt == max_attempts:
                raise
            log(f"   Warte {wait_seconds}s (Verbindung stabilisiert sich vermutlich noch)...")
            await asyncio.sleep(wait_seconds)


async def main():
    global match_hashtag, match_info, match_kickoff, channel_user

    fehlend = [name for name, wert in (
        ("[source] channel_invite_link", CHANNEL_INVITE_LINK),
        ("[bluesky] handle", BLUESKY_HANDLE),
        ("[team] openligadb_filter", OPENLIGADB_TEAM_FILTER),
    ) if not wert or "HIER-DEN" in wert or wert.startswith("dein-bot.")]
    if fehlend:
        log(f"Fehler: In {os.path.basename(CONFIG_FILE)} fehlen noch Angaben: {', '.join(fehlend)}")
        log("Den Kanal-Link bekommst du im Handy über: Kanal öffnen -> Kanalnamen")
        log("antippen -> Teilen -> Link kopieren.")
        sys.exit(1)

    # Nur-Profil-Modus "off": braucht weder Spieltag noch WhatsApp.
    if PROFILE_ONLY == "off":
        set_profile_status(False)
        return

    if MANUAL_HASHTAG:
        match_hashtag = MANUAL_HASHTAG
        log(f"Manueller Spiel-Hashtag gesetzt: #{match_hashtag}")

    # Spieltags-Check VOR dem WhatsApp-Connect - an spielfreien Tagen wird gar
    # keine Verbindung aufgebaut (minimiert Laufzeit und Auffälligkeit).
    # FORCE heißt "starte trotzdem", NICHT "ignoriere die Spieldaten": Findet
    # OpenLigaDB ein Spiel, werden Hashtag und Spieltagsinfo auch dann genutzt.
    try:
        match = get_todays_match()
    except Exception as e:
        log(f"Fehler beim OpenLigaDB-Abruf: {e}")
        if not FORCE_RUN:
            sys.exit(1)
        match = None

    if match:
        kickoff, desc, auto_hashtag, info = match
        if not match_hashtag:
            match_hashtag = auto_hashtag
        match_info = info
        match_kickoff = kickoff
        log(f"⚽ Heute ist Spieltag: {desc}, {info}, Anstoß {kickoff.strftime('%H:%M')} Uhr. "
            f"Spiel-Hashtag: #{match_hashtag}")
    elif FORCE_RUN:
        note = "" if match_hashtag else " (ohne Spiel-Hashtag)"
        log(f"SKYRELAY_FORCE=1 gesetzt - kein OpenLigaDB-Spiel für heute gefunden, "
            f"laufe trotzdem{note}.")
    else:
        log("Heute kein Spiel - Script beendet sich.")
        return

    # Nur-Profil-Modus "on": Statuszeile setzen und beenden, ohne WhatsApp.
    if PROFILE_ONLY == "on":
        set_profile_status(True)
        return

    if DRY_RUN:
        log("SKYRELAY_DRY_RUN=1 gesetzt - es wird NICHTS auf Bluesky gepostet.")

    log("Verbinde mit WhatsApp...")
    await client.connect()

    if PAIR_PHONE:
        # Kurz warten: Existiert schon eine gültige Session, ist kein Pairing nötig
        # (PairPhone würde bei bestehendem Login einen Fehler werfen).
        try:
            await asyncio.wait_for(wa_connected.wait(), timeout=10)
            log("ℹ️ Bereits gekoppelt - SKYRELAY_PAIR_PHONE wird ignoriert.")
        except asyncio.TimeoutError:
            try:
                code = await client.PairPhone(PAIR_PHONE, show_push_notification=True)
                log("=" * 50)
                log(f"KOPPLUNGSCODE: {code}")
                log("Im Handy eingeben: WhatsApp -> Einstellungen -> Verknüpfte Geräte")
                log('-> Gerät hinzufügen -> "Stattdessen mit Telefonnummer koppeln"')
                log("=" * 50)
            except Exception as pair_err:
                # Nicht fatal: Schlägt u.a. fehl, wenn parallel schon der QR-Code
                # gescannt wurde (Race zwischen beiden Pairing-Wegen) - dann steht
                # die Verbindung gleich trotzdem. Der Wait unten klärt das.
                log(f"⚠️ Kopplung per Code nicht möglich ({pair_err}) - "
                    f"falls der QR-Code gescannt wurde, geht es trotzdem weiter.")

    try:
        await asyncio.wait_for(wa_connected.wait(), timeout=300)
    except asyncio.TimeoutError:
        log("Fehler: Innerhalb von 5 Minuten keine eingeloggte WhatsApp-Verbindung.")
        log("Wurde der QR-Code gescannt? Bei einer kaputten Session hilft Löschen + Neu-Pairing:")
        log(f"  rm {SESSION_DB}")
        sys.exit(1)
    log("✓ WhatsApp verbunden und eingeloggt.")

    # Kurz durchatmen: direkt nach dem Login (v.a. nach Erst-Pairing) laufen noch
    # Reconnects und History-Sync - Abfragen wären jetzt unzuverlässig.
    await asyncio.sleep(5)

    log("Löse Kanal über Invite-Link auf...")
    metadata = await with_retries(
        "Kanal-Auflösung",
        lambda: client.get_newsletter_info_with_invite(CHANNEL_INVITE_LINK),
    )
    channel_jid = metadata.ID
    channel_user = channel_jid.User  # ab jetzt nimmt der Event-Handler Kanal-Posts an
    log(f"✓ Kanal gefunden: JID {channel_jid.User}@{channel_jid.Server}")

    try:
        await client.follow_newsletter(channel_jid)
        log("✓ Kanal ist abonniert.")
    except Exception as follow_err:
        # Schlägt u.a. fehl, wenn der Kanal bereits abonniert ist - unkritisch.
        log(f"ℹ️ follow_newsletter: {follow_err} (vermutlich bereits abonniert)")

    # Wasserzeichen früh laden - CATCHUP und der Lausch-Loop brauchen es beide.
    watermark = load_watermark() or 0
    if watermark:
        log(f"Wasserzeichen von heute geladen: MessageServerID {watermark}.")
    seen_ids = set()  # Fallback-Dedup über Message-IDs, falls Events ohne ServerID kommen
    posts_map = load_posts_map()  # ServerID -> Bluesky-URIs (für die Bearbeitungs-Logik)

    # REPLAY: die letzten N Posts einmal durch die Pipeline (Testwerkzeug; ignoriert
    #         das Wasserzeichen, verändert es nicht, Script endet danach).
    # CATCHUP: verpasste Posts nachholen (respektiert + pflegt das Wasserzeichen)
    #          und danach normal weiterlauschen.
    # ACHTUNG bekannter neonize-Bug: Liegt unter den letzten N Nachrichten eine
    # inhaltslose Meta-Nachricht (unsichtbar im Kanal, z.B. Bearbeitung/Löschung),
    # stürzt der Go-Layer hart ab (panic). Deshalb wird exakt N abgerufen - und
    # der Live-Betrieb nutzt diesen Abruf gar nicht, sondern lauscht auf Events.
    if REPLAY_COUNT > 0 or CATCHUP_COUNT > 0:
        mode = "REPLAY" if REPLAY_COUNT > 0 else "CATCHUP"
        count = REPLAY_COUNT or CATCHUP_COUNT
        log(f"{mode}-Modus: Verarbeite die letzten {count} Kanal-Post(s)...")
        log(f"(Stürzt das Script hier hart ab ('panic ... Message not set'), liegt eine "
            f"unsichtbare Meta-Nachricht im Fenster - mit kleinerem N erneut versuchen.)")
        messages = await with_retries(
            f"{mode}-Abruf",
            lambda: client.get_newsletter_messages(
                channel_jid, count, MessageServerID(0)
            ),
        )
        for nm in sorted(messages, key=lambda nm: nm.MessageServerID)[-count:]:
            if mode == "CATCHUP" and nm.MessageServerID <= watermark:
                log(f"[CATCHUP] ServerID {nm.MessageServerID} bereits verarbeitet - übersprungen.")
                continue
            log(f"[{mode}] Kanal-Nachricht mit ServerID {nm.MessageServerID}:")
            try:
                uris = await process_newsletter_message(client, nm.Message, nm.MessageServerID)
                if mode == "CATCHUP" and uris:
                    posts_map[str(nm.MessageServerID)] = {
                        "uris": uris,
                        "hash": text_hash(extract_text(unwrap_message(nm.Message))),
                    }
                    save_posts_map(posts_map)
            except Exception as post_err:
                log(f"   ⚠️ Fehler beim Verarbeiten: {post_err}")
                traceback.print_exc()
            if mode == "CATCHUP":
                watermark = nm.MessageServerID
                save_watermark(watermark)
            await asyncio.sleep(PAUSE_BETWEEN_POSTS_SECONDS)
        if mode == "REPLAY":
            log("REPLAY abgeschlossen - Script beendet sich.")
            return
        log("CATCHUP abgeschlossen - lausche jetzt auf neue Posts.")

    # Live-Updates abonnieren: Kanal-Posts werden dann als Events gepusht.
    # Das Abo gilt nur wenige Minuten und wird im Loop regelmäßig erneuert.
    # (Gefolgte Kanäle pushen meist auch ohne Abo - das Abo ist Absicherung.)
    try:
        await client.newsletter_subscribe_live_updates(channel_jid)
        log("✓ Live-Updates abonniert.")
    except Exception as sub_err:
        log(f"⚠️ Live-Update-Abo fehlgeschlagen ({sub_err}) - weiter ohne, "
            f"gefolgte Kanäle pushen in der Regel trotzdem.")
    next_renew = time.monotonic() + SUBSCRIBE_RENEW_SECONDS

    # Events sind per Definition neu - eine Baseline-Abfrage ist nicht nötig.
    # WhatsApp liefert beim Verbinden auch aufgelaufene Posts nach; die
    # Datums-Prüfung unten verwirft dabei alles, was nicht von heute ist.
    day_end = datetime.now(LOCAL_TZ).replace(
        hour=DAY_END_HOUR, minute=DAY_END_MINUTE, second=0, microsecond=0
    )
    # Ab jetzt ist der Bot "im Dienst" -> Bio-Statuszeile umschalten.
    set_profile_status(True)

    log(f"Lausche auf neue Kanal-Posts bis {day_end.strftime('%H:%M')} Uhr...")

    while datetime.now(LOCAL_TZ) < day_end:
        if time.monotonic() >= next_renew:
            try:
                await client.newsletter_subscribe_live_updates(channel_jid)
            except Exception as sub_err:
                log(f"⚠️ Live-Update-Verlängerung fehlgeschlagen: {sub_err}")
            next_renew = time.monotonic() + SUBSCRIBE_RENEW_SECONDS

        try:
            event = await asyncio.wait_for(incoming_events.get(), timeout=15)
        except asyncio.TimeoutError:
            continue

        # Schutzmantel um die GESAMTE Event-Verarbeitung: Ein einzelnes kaputtes
        # Event darf den Lauscher nie beenden (Lehre vom 14.07.: Timestamp in ms
        # statt s -> ValueError -> Loop tot, ausgerechnet während des Spiels).
        try:
            chat = event.Info.MessageSource.Chat
            if chat.User != channel_user:
                log(f"(Event von fremdem Kanal {chat.User} - ignoriert)")
                continue

            server_id = event.Info.ServerID
            msg_id = event.Info.ID
            when = ""
            msg_time = None
            if event.Info.Timestamp:
                # Live-Events liefern den Timestamp in MILLIsekunden, andere Pfade
                # in Sekunden - anhand der Größenordnung normalisieren.
                ts = event.Info.Timestamp
                if ts > 1e14:    # Mikrosekunden
                    ts = ts / 1e6
                elif ts > 1e11:  # Millisekunden
                    ts = ts / 1e3
                try:
                    msg_time = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(LOCAL_TZ)
                    when = f" vom {msg_time.strftime('%d.%m. %H:%M')}"
                except (ValueError, OverflowError, OSError):
                    msg_time = None
            log(f"Kanal-Event empfangen: ServerID {server_id or '?'}{when} (ID {msg_id})")

            if msg_id in seen_ids:
                log("   (in diesem Lauf bereits verarbeitet - übersprungen)")
                continue
            if server_id and server_id <= watermark:
                # Bereits verarbeitete ServerID mit neuem Event = vermutlich eine
                # Bearbeitung im Kanal -> alte Bluesky-Posts ersetzen.
                await handle_edit(client, event, server_id, posts_map)
                seen_ids.add(msg_id)
                continue

            # Nachgelieferte Posts aus der Offline-Queue können älter sein - alles,
            # was nicht vom heutigen (Spiel-)Tag stammt, wird verworfen.
            if msg_time and msg_time.date() != datetime.now(LOCAL_TZ).date():
                log("   (nicht von heute - übersprungen)")
                if server_id:
                    watermark = server_id
                    save_watermark(watermark)
                continue

            log(f"[NEU] Kanal-Nachricht mit ServerID {server_id or '?'}:")
            try:
                uris = await process_newsletter_message(client, event.Message, server_id)
                if uris and server_id:
                    posts_map[str(server_id)] = {
                        "uris": uris,
                        "hash": text_hash(extract_text(unwrap_message(event.Message))),
                    }
                    save_posts_map(posts_map)
            except Exception as post_err:
                log(f"   ⚠️ Fehler beim Reposten: {post_err}")
                traceback.print_exc()
            # Dedup auch bei Fehler fortschreiben, sonst bleibt das Script an
            # derselben kaputten Nachricht hängen.
            seen_ids.add(msg_id)
            if server_id:
                watermark = server_id
                save_watermark(watermark)
            await asyncio.sleep(PAUSE_BETWEEN_POSTS_SECONDS)
        except Exception as event_err:
            log(f"⚠️ Fehler bei der Event-Verarbeitung ({event_err}) - Event übersprungen, lausche weiter.")
            traceback.print_exc()

    log("Tagesende erreicht - Ticker beendet sich. Bis zum nächsten Spieltag!")


async def run():
    """Wrapper um main(): trennt die WhatsApp-Verbindung am Ende IMMER sauber -
    sonst halten die Go-Threads den Prozess minutenlang am Leben (Executor-Hang)."""
    try:
        await main()
    finally:
        # Bio zurück auf "Bot ist aus" - auch bei Strg+C oder einem Fehler.
        # (Im Nur-Profil-Modus "on" natürlich nicht: da ist "an" ja das Ergebnis.)
        if profile_status_on and PROFILE_ONLY != "on":
            set_profile_status(False)
        try:
            await asyncio.wait_for(client.stop(), timeout=10)
            # Gnadenfrist: Der Go-Socket-Thread loggt seinen Verbindungsabbruch über
            # einen Callback nach Python. Beenden wir zu schnell, zeigt der Callback
            # ins Leere -> SIGSEGV beim Exit (kosmetisch, aber unschön; neonize-Race).
            await asyncio.sleep(2)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Abbruch per Strg+C - WhatsApp-Verbindung wurde getrennt.")
