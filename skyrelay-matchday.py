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
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo

import requests
import segno
from atproto import Client, models, client_utils

from neonize.aioze.client import NewAClient
from neonize.aioze.events import ConnectedEv, MessageEv, PairStatusEv
from neonize.types import MessageServerID

from skyrelay_common import (
    log,
    lade_config,
    start_file_logging,
    melde_bei_bluesky_an,
    hole_app_passwort,
    compress_image_for_bluesky,
    upload_video_to_bluesky,
    merke_video_daten,
    merke_nachreich_ziel,
    reiche_videos_nach,
    audio_zu_video,
    video_standbild,
    sticker_zu_bild,
)

# httpx (der HTTP-Client der atproto-Bibliothek) protokolliert sonst jede Anfrage.
logging.getLogger("httpx").setLevel(logging.WARNING)


# =============================== KONFIGURATION ===============================
# Alle vereins- und kontospezifischen Werte stehen in "skyrelay.conf"
# (Vorlage: skyrelay.conf.example). Hier wird nur noch gelesen.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
cfg, cfg_int, cfg_bool, CONFIG_FILE = lade_config(BASE_DIR)
_cfg = configparser.ConfigParser(interpolation=None)
with open(CONFIG_FILE, encoding="utf-8") as _f:
    _cfg.read_file(_f)  # für den direkten Zugriff auf [team_codes]

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
AUDIO_PLACEHOLDER = cfg("post", "audio_placeholder",
                       "🔊 Neue Sprachnachricht im Kanal")
STICKER_PLACEHOLDER = cfg("post", "sticker_placeholder",
                         "✨ Neuer Sticker im Kanal")
# Sprachnachrichten werden als Video mit Wellenform übertragen (siehe [audio]).
AUDIO_SIZE = cfg("audio", "size", "720x720")
AUDIO_WAVE_COLOR = cfg("audio", "waveform_color", "White")
AUDIO_BG_COLOR = cfg("audio", "background_color", "0x0b1220")
AUDIO_FRAMERATE = cfg_int("audio", "framerate", 25)
STICKER_BACKGROUND = cfg("audio", "sticker_background", "white")
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
VIDEO_RETRY_MAX_ATTEMPTS = cfg_int("limits", "video_retry_max_attempts", 8)
VIDEO_RETRY_TEXT = cfg("post", "video_retry_text",
                      "🎥 Nachgereicht: das Video zum Beitrag oben.")
# Takt, in dem im Lausch-Betrieb offene Videos erneut versucht werden.
VIDEO_RETRY_INTERVAL_SECONDS = cfg_int("limits", "video_retry_interval_seconds", 600)

LOG_TO_FILE = cfg_bool("logging", "to_file", True)
LOG_MAX_BYTES = cfg_int("logging", "max_bytes", 2_000_000)
LOG_BACKUP_COUNT = cfg_int("logging", "backup_count", 5)

SESSION_DB = os.path.join(BASE_DIR, cfg("files", "session", "skyrelay_session.sqlite3"))
STATE_FILE = os.path.join(BASE_DIR, cfg("files", "state", "skyrelay_state.txt"))
POSTS_MAP_FILE = os.path.join(BASE_DIR, cfg("files", "posts_map", "skyrelay_posts.json"))
LOG_FILE = os.path.join(BASE_DIR, cfg("files", "log", "skyrelay.log"))
VIDEO_RETRY_DIR = os.path.join(BASE_DIR,
                               cfg("files", "video_retry_dir", "skyrelay_nachreichen"))


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


if LOG_TO_FILE:
    start_file_logging(LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
    log(f"--- Ticker-Start (Log: {LOG_FILE}) ---")

# Eigenes Passwort für den Ticker, sonst das gemeinsame.
BLUESKY_APP_PASSWORD, PASSWORT_VARIABLE = hole_app_passwort(
    "BLUESKY_TICKER_APP_PASSWORD", "BLUESKY_APP_PASSWORD")
if not BLUESKY_APP_PASSWORD and not DRY_RUN:
    log(f"Fehler: Kein App-Passwort für das Ticker-Konto @{BLUESKY_HANDLE} gesetzt.")
    log('Getrennte Konten für Ticker und Feed:')
    log('    export BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log('Ein gemeinsames Konto für beide:')
    log('    export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log("cron liest ~/.bashrc NICHT - dort die Variablen oben in die crontab")
    log("schreiben (ohne Anführungszeichen).")
    log("Nur lesen, ohne Bluesky:  SKYRELAY_DRY_RUN=1")
    sys.exit(1)
# ----------------------------------


# 1. Spieltags-Check über OpenLigaDB (kostenlos, ohne API-Key)
def team_code(team):
    """DFL-Kürzel zu einem OpenLigaDB-Team; Fallback für unbekannte (Pokal-)Gegner."""
    code = TEAM_CODES.get(team["teamId"])
    if code is None:
        # Nur Buchstaben behalten, dann erst großschreiben. isalpha() statt einer
        # Zeichenklasse, weil dort das ß fehlte und stillschweigend verschwand
        # ("Großaspach" -> "Groaspach"). Großschreiben VOR dem Kürzen, denn
        # "ß".upper() ergibt "SS" und hätte das Kürzel sonst vierstellig gemacht.
        name = team["shortName"] or team["teamName"]
        code = "".join(z for z in name if z.isalpha()).upper()[:3]
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
    heim, gast = team_code(match["team1"]), team_code(match["team2"])  # team1 = Heimteam
    if heim == gast:
        # Kommt vor, wenn zwei Vereine dasselbe Kürzel führen (z.B. tragen sowohl
        # Werder Bremen als auch Waldhof Mannheim "SVW"). Der Hashtag wäre unbrauchbar.
        log(f'⚠️ Beide Mannschaften führen das Kürzel "{heim}" - der Hashtag #{heim}{gast} '
            f'ergibt keinen Sinn.')
        log(f'   Bitte eines der Kürzel in [team_codes] anpassen: Team-Nummern '
            f'{match["team1"]["teamId"]} ({match["team1"]["teamName"]}) und '
            f'{match["team2"]["teamId"]} ({match["team2"]["teamName"]}).')
    return kickoff_local, desc, heim + gast, match_info_text(match)


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
_anmeldeversuche = 0  # begrenzt Wiederholungen, siehe ensure_bsky()
match_hashtag = None  # wird in main() aus den OpenLigaDB-Daten gesetzt (z.B. "DSCWOB")
match_info = None     # Kurzinfo zum heutigen Spiel, z.B. "1. Spieltag" (für die Profilzeile)
match_kickoff = None  # Anstoßzeit des heutigen Spiels (für die Profilzeile)


def ensure_bsky():
    """Stellt die Bluesky-Verbindung her (lazy, einmalig pro Lauf)."""
    global bsky_client
    global _anmeldeversuche
    if bsky_client is not None:
        return
    # Nach mehreren Fehlschlägen nicht weiter probieren: Bluesky erlaubt nur
    # 10 Anmeldungen pro Tag und Konto, und an einem Spieltag kämen sonst
    # dutzende Versuche zusammen - danach wäre auch ein korrigiertes Passwort
    # für den Rest des Tages gesperrt.
    if _anmeldeversuche >= 3:
        raise RuntimeError("Anmeldung bei Bluesky mehrfach fehlgeschlagen - "
                           "keine weiteren Versuche in diesem Lauf.")
    _anmeldeversuche += 1
    log("Verbinde mit Bluesky...")
    verbindung = Client()
    # Erst nach erfolgreicher Anmeldung übernehmen: Sonst bliebe ein
    # unangemeldetes Objekt zurück, und alle folgenden Beiträge liefen
    # ohne Anmeldung ins Leere ("AuthMissing").
    melde_bei_bluesky_an(verbindung, BLUESKY_HANDLE, BLUESKY_APP_PASSWORD,
                         PASSWORT_VARIABLE)
    bsky_client = verbindung


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

# ------------------------------------------------------------------------------


def post_to_bluesky(text, image_blobs, video_bytes=None, video_thumb=None,
                    media_name="video", platzhalter=None):
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
        preview = text_chunks[0][:120] if text_chunks else (platzhalter or "(nur Medien)")
        tags = " ".join(f"#{t}" for t in hashtags)
        card = f", Link-Karte für {card_url_match.group(0)}" if card_url_match else ""
        video = f", 1 Video ({len(video_bytes)} Bytes)" if video_bytes else ""
        log(f"   [DRY_RUN] Würde posten ({len(text_chunks)} Chunk(s), "
            f"{len(image_blobs)} Bild(er){video}, Tags: {tags}{card}): {preview}...")
        return []

    ensure_bsky()

    if not text_chunks:
        text_chunks = [platzhalter or
                       (VIDEO_PLACEHOLDER if video_bytes else IMAGE_PLACEHOLDER)]

    # Video-Upload zuerst versuchen; bei Fehlschlag Vorschaubild als Bild-Fallback
    # und das Video zum Nachreichen vormerken.
    video_embed = None
    offenes_video = False
    if video_bytes:
        try:
            video_embed = upload_video_to_bluesky(bsky_client, video_bytes,
                                                  f"{media_name}.mp4",
                                                  MAX_VIDEO_BYTES,
                                                  VIDEO_JOB_TIMEOUT_SECONDS)
        except Exception as video_err:
            log(f"   ⚠️ Video-Upload fehlgeschlagen: {video_err}")
            # Der Beitrag geht sofort mit dem Vorschaubild raus - beim Live-Ticker
            # zählt die Zeit. Das Video bleibt liegen und wird nachgereicht.
            offenes_video = merke_video_daten(VIDEO_RETRY_DIR, media_name, video_bytes)
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
            if offenes_video:
                merke_nachreich_ziel(VIDEO_RETRY_DIR, media_name, bsky_client.me.did,
                                     root_ref.uri, root_ref.cid,
                                     root_ref.uri, root_ref.cid,
                                     f"{media_name}.mp4", alt_text)
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
MEDIENFELDER = ("audioMessage", "videoMessage", "imageMessage",
                "stickerMessage", "documentMessage")


async def lade_kanal_medien(client, msg):
    """Lädt die Mediendatei einer Kanal-Nachricht herunter.

    Kanalmedien liegen unverschlüsselt hinter `directPath` - Bilder und Videos
    bringen deshalb gar keinen `mediaKey` mit und laden anstandslos.
    Sprachnachrichten schleppen aber einen `mediaKey` aus dem gewöhnlichen
    Chat-Ablauf mit. whatsmeow biegt daraufhin in den Entschlüsselungspfad ab
    und scheitert dort mit "invalid media hmac", obwohl die Datei abrufbar wäre.
    Nachgemessen am 19.08.2026: 0 von 5 Sprachnachrichten luden regulär, alle
    luden ohne den Schlüssel - byte-genau in der angekündigten Länge.

    Deshalb zuerst der reguläre Weg und nur bei einem hmac-Fehler ein zweiter
    Versuch ohne `mediaKey`. Liefert WhatsApp Kanal-Audio eines Tages doch
    verschlüsselt aus, greift weiterhin der reguläre Weg."""
    try:
        return await client.download_any(msg)
    except Exception as fehler:
        if "hmac" not in str(fehler).lower():
            raise
        log("   ℹ️ Download scheitert an der Prüfsumme - zweiter Versuch "
            "ohne mediaKey (Kanalmedien liegen unverschlüsselt).")
        ohne_schluessel = type(msg)()
        ohne_schluessel.CopyFrom(msg)
        for feld in MEDIENFELDER:
            if ohne_schluessel.HasField(feld):
                getattr(ohne_schluessel, feld).ClearField("mediaKey")
                break
        return await client.download_any(ohne_schluessel)


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
    platzhalter = None
    if msg.HasField("imageMessage"):
        try:
            raw = await lade_kanal_medien(client, msg)
            image_blobs.append(compress_image_for_bluesky(raw))
        except Exception as dl_err:
            # Kanal-Medien laufen teils über andere Endpunkte als normale Chats -
            # falls der Download scheitert, wird nur der Text gepostet.
            log(f"   ⚠️ Bild-Download fehlgeschlagen ({dl_err}) - poste nur Text.")
    elif msg.HasField("videoMessage"):
        video_thumb = msg.videoMessage.JPEGThumbnail or None
        try:
            log("   Lade Video aus dem Kanal herunter...")
            video_bytes = await lade_kanal_medien(client, msg)
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
    elif msg.HasField("audioMessage"):
        # Bluesky kennt kein Audio-Format. Aus der Sprachnachricht wird deshalb
        # ein Video mit animierter Wellenform - der Ton bleibt dabei erhalten.
        platzhalter = AUDIO_PLACEHOLDER
        sekunden = msg.audioMessage.seconds or 0
        try:
            log("   Lade Sprachnachricht aus dem Kanal herunter...")
            audio = await lade_kanal_medien(client, msg)
            log(f"   ✓ Sprachnachricht geladen ({len(audio)} Bytes, {sekunden}s).")
            log("   Erzeuge Video mit Wellenform...")
            video_bytes = audio_zu_video(audio, AUDIO_SIZE, AUDIO_WAVE_COLOR,
                                         AUDIO_BG_COLOR, AUDIO_FRAMERATE)
            video_thumb = video_standbild(video_bytes)
            log(f"   ✓ Video erzeugt ({len(video_bytes)} Bytes).")
        except Exception as audio_err:
            log(f"   ⚠️ Sprachnachricht nicht übertragbar: {audio_err}")
            if not text:
                log("   (ohne Text bleibt nichts zu posten - übersprungen)")
                return []
    elif msg.HasField("stickerMessage"):
        # Sticker sind WebP, oft transparent und manchmal animiert. Bluesky
        # spielt keine Animationen ab, also geht das erste Einzelbild raus.
        platzhalter = STICKER_PLACEHOLDER
        try:
            log("   Lade Sticker aus dem Kanal herunter...")
            roh = await lade_kanal_medien(client, msg)
            image_blobs.append(sticker_zu_bild(roh, STICKER_BACKGROUND))
            log(f"   ✓ Sticker umgewandelt ({len(roh)} Bytes Ausgangsmaterial).")
        except Exception as sticker_err:
            log(f"   ⚠️ Sticker nicht übertragbar: {sticker_err}")
            if not text:
                log("   (ohne Text bleibt nichts zu posten - übersprungen)")
                return []

    log(f"   Text ({len(text)} Zeichen): {text[:100]!r}...")
    return post_to_bluesky(text, image_blobs, video_bytes, video_thumb,
                           media_name=f"{MEDIA_PREFIX}_{server_id}",
                           platzhalter=platzhalter)


async def handle_edit(client, event, server_id, posts_map):
    """Behandelt ein Event zu einer bereits verarbeiteten ServerID: Bei einer
    echten Bearbeitung werden die alten Bluesky-Posts gelöscht und die neue
    Version gepostet; unveränderte Wiederzustellungen werden ignoriert."""
    msg = unwrap_message(event.Message)
    new_text = extract_text(msg)
    has_media = any(msg.HasField(feld) for feld in
                    ("imageMessage", "videoMessage", "audioMessage", "stickerMessage"))

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
    # Ohne konfigurierten Verein (Sportart nicht bei OpenLigaDB) entfällt die
    # Prüfung ganz - dann läuft der Ticker an jedem Tag, an dem er gestartet wird.
    ohne_spielplan = not OPENLIGADB_TEAM_FILTER or not OPENLIGADB_TEAM_ID
    match = None
    if ohne_spielplan:
        log("Kein Spielplan konfiguriert ([team] openligadb_filter leer) - "
            "Spieltags-Prüfung entfällt.")
    else:
        try:
            match = get_todays_match()
        except Exception as e:
            log(f"Fehler beim OpenLigaDB-Abruf: {e}")
            if not FORCE_RUN:
                sys.exit(1)

    if match:
        kickoff, desc, auto_hashtag, info = match
        if not match_hashtag:
            match_hashtag = auto_hashtag
        match_info = info
        match_kickoff = kickoff
        log(f"⚽ Heute ist Spieltag: {desc}, {info}, Anstoß {kickoff.strftime('%H:%M')} Uhr. "
            f"Spiel-Hashtag: #{match_hashtag}")
    elif FORCE_RUN or ohne_spielplan:
        note = "" if match_hashtag else " (ohne Spiel-Hashtag)"
        grund = ("ohne Spielplan-Prüfung" if ohne_spielplan
                 else "SKYRELAY_FORCE=1 gesetzt - kein OpenLigaDB-Spiel für heute gefunden")
        log(f"{grund}, laufe trotzdem{note}.")
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
                if not DRY_RUN:  # im Trockenlauf nichts als erledigt markieren
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
    next_nachreichen = time.monotonic() + VIDEO_RETRY_INTERVAL_SECONDS

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
            # Gerade nichts los - guter Moment, um offene Videos nachzureichen.
            # In einem Thread, damit eingehende Kanal-Posts weiter in die Queue
            # laufen, und nur im Leerlauf, damit nie ein Live-Post wartet.
            if (not DRY_RUN and time.monotonic() >= next_nachreichen
                    and os.path.isdir(VIDEO_RETRY_DIR)
                    and any(n.endswith(".json") for n in os.listdir(VIDEO_RETRY_DIR))):
                next_nachreichen = time.monotonic() + VIDEO_RETRY_INTERVAL_SECONDS
                try:
                    ensure_bsky()
                    await asyncio.to_thread(
                        reiche_videos_nach, bsky_client, VIDEO_RETRY_DIR,
                        VIDEO_RETRY_TEXT, VIDEO_RETRY_MAX_ATTEMPTS,
                        MAX_VIDEO_BYTES, VIDEO_JOB_TIMEOUT_SECONDS)
                except Exception as nach_err:
                    log(f"⚠️ Nachreichen offener Videos fehlgeschlagen: {nach_err}")
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
                    if not DRY_RUN:
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
            # Dedup auch bei Fehler fortschreiben, sonst bleibt das Programm an
            # derselben kaputten Nachricht hängen. Im Trockenlauf wird der Stand
            # NICHT gespeichert - sonst gälte ein nur getesteter Beitrag später
            # als erledigt und würde nie veröffentlicht.
            seen_ids.add(msg_id)
            if server_id:
                watermark = server_id
                if not DRY_RUN:
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
