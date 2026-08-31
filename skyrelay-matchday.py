"""
SkyRelay - matchday ticker: mirrors a WhatsApp channel to Bluesky, but only on
matchdays (checked against OpenLigaDB).

Everything specific to a club or an account lives in "skyrelay.conf" (template:
skyrelay.conf.example). A different path can be given through the environment
variable SKYRELAY_CONFIG, which is how several clubs run side by side. The
Bluesky app password comes from BLUESKY_APP_PASSWORD, never from a file.

How it works:
  1. At startup OpenLigaDB is asked whether our own team plays today. No match
     -> the program ends right away without opening a single connection (meant
     to be started daily from cron).
  2. On matchdays it connects to WhatsApp through neonize (whatsmeow) and
     listens for channel events until the configured end of day. New posts go
     to Bluesky immediately, as do posts delivered on connecting that are from
     TODAY - older ones are dropped.
     (Deliberately no polling during normal operation: get_newsletter_messages
     crashes in the Go layer as soon as a fetch catches an invisible meta
     message - an edit or a deletion, say. Only REPLAY and CATCHUP still use
     that call, where a crash is survivable.)
  3. The match hashtag (#DSCWOB at home, #WOBDSC away) is built from the
     OpenLigaDB data (codes from [team_codes], home team first) - or set by
     hand through SKYRELAY_HASHTAG.
  4. Duplicate posts after a restart on the same day are prevented by the
     channel's monotonically rising MessageServerID (kept in the file from
     [files] state).

Setting up (a 64 bit system is required - neonize ships no 32 bit packages):
    ./install.sh
    cp skyrelay.conf.example skyrelay.conf   # and adjust it
    # neonize is pinned in requirements.txt on purpose: 0.4.0 and 0.4.1
    # returned corrupted values ("Wire format was corrupt", issue #199). Fixed
    # in 0.4.2, but untested here - back up the session file before switching
    # and check with a dry run. The crash on deleted posts is still present in
    # 0.4.3 as well.

First pairing - has to run interactively in a terminal (not from cron; SSH is
fine):
    SKYRELAY_PAIR_PHONE="4915123456789" SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
    (the number in international form, without + and without a leading 0)
    -> A pairing code appears. On the phone: WhatsApp -> Settings -> Linked
       devices -> Link a device -> "Link with phone number instead" -> enter
       the code.
    Without SKYRELAY_PAIR_PHONE a QR code appears in the terminal instead. If
    scanning gives trouble: enlarge the window a lot and turn the screen
    brightness up, otherwise the camera lacks contrast.
    CAREFUL: signing in through web.whatsapp.com does NOT help - this program
    is a linked device of its own with a session of its own. It ends up in the
    file from [files] session; after that SKYRELAY_PAIR_PHONE is no longer
    needed.

Example for cron (a daily start at 6 in the morning, the program decides the
rest). Paths are case sensitive, and redirecting the output into the log file
is NOT necessary - the program writes it itself:
    0 6 * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /path/to/SkyRelay/venv/bin/python3 /path/to/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
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
    load_config,
    start_file_logging,
    log_in_to_bluesky,
    get_app_password,
    compress_image_for_bluesky,
    upload_video_to_bluesky,
    stash_video,
    stash_retry_target,
    post_stashed_videos,
    audio_to_video,
    video_still,
    sticker_to_image,
    build_source_line,
    show_preview,
)
from skyrelay_config import check_config, show_config

# Answers about the configuration, before anything else gets going:
# both calls connect to nothing and write nothing.
#   --check-config  reports what does not add up
#   --show-config   shows which value applies now and where it comes from
if "--check-config" in sys.argv:
    sys.exit(check_config(os.path.dirname(os.path.abspath(__file__))))
if "--show-config" in sys.argv:
    sys.exit(show_config(os.path.dirname(os.path.abspath(__file__))))

# httpx (the HTTP client of the atproto library) would log every request.
logging.getLogger("httpx").setLevel(logging.WARNING)


# ============================== CONFIGURATION ================================
# Every value specific to a club or an account lives in "skyrelay.conf"
# (template: skyrelay.conf.example). Here it is only ever read.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
cfg, cfg_int, cfg_bool, CONFIG_FILE = load_config(BASE_DIR)
_cfg = configparser.ConfigParser(interpolation=None)
with open(CONFIG_FILE, encoding="utf-8") as _f:
    _cfg.read_file(_f)  # for direct access to [team_codes]

BLUESKY_HANDLE = cfg("bluesky", "handle", "")
CHANNEL_INVITE_LINK = cfg("source", "channel_invite_link", "")

OPENLIGADB_TEAM_FILTER = cfg("team", "openligadb_filter", "")
OPENLIGADB_TEAM_ID = cfg_int("team", "openligadb_team_id", 0)
LEAGUE_PREFIXES = tuple(
    p.strip().lower() for p in cfg("team", "league_prefixes", "bl, dfb").split(",") if p.strip()
)
LOCAL_TZ = ZoneInfo(cfg("team", "timezone", "Europe/Berlin"))

# Codes used to build the hashtag, e.g. {83: "DSC"}
TEAM_CODES = {}
if _cfg.has_section("team_codes"):
    for _team_id, _code in _cfg.items("team_codes"):
        try:
            TEAM_CODES[int(_team_id)] = _code.strip().upper()
        except ValueError:
            print(f"⚠️ [team_codes] '{_team_id}' is not a team number - skipped",
                  file=sys.stderr)

POST_PREFIX = cfg("post", "prefix", "⚽ [Inoffizieller Bot]")
POST_SOURCE_LABEL = cfg("post", "source_label", "Original-Kanal")
STANDING_HASHTAG = cfg("post", "standing_hashtag", "").strip().lstrip("#")
# When several of the club's teams play on the same day, a channel message
# does not reveal which match it belongs to. Rather than labelling the posts
# wrongly, this hashtag takes the place of the match hashtags. Leave it empty
# to set none at all on such days.
OVERLAP_HASHTAG = cfg("post", "overlap_hashtag", "").strip().lstrip("#")
IMAGE_PLACEHOLDER = cfg("post", "image_placeholder", "📸 Neues Bild im Kanal")
VIDEO_PLACEHOLDER = cfg("post", "video_placeholder", "🎥 Neues Video im Kanal")
VIDEO_HINT = cfg("post", "video_hint", "🎥 (Video im Original-Kanal)")
AUDIO_PLACEHOLDER = cfg("post", "audio_placeholder",
                       "🔊 Neue Sprachnachricht im Kanal")
STICKER_PLACEHOLDER = cfg("post", "sticker_placeholder",
                         "✨ Neuer Sticker im Kanal")
# Voice messages are carried over as a video with a waveform (see [audio]).
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
# How often pending videos are retried while listening.
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
    """Reads SKYRELAY_<name>; for the time being the old DSC_TICKER_ names are
    still accepted, so existing calls and crontab lines keep working."""
    value = os.environ.get(f"SKYRELAY_{name}")
    if value is not None:
        return value
    old = os.environ.get(f"DSC_TICKER_{name}")
    if old is not None:
        print(f"Note: DSC_TICKER_{name} is deprecated - please use SKYRELAY_{name}.",
              file=sys.stderr)
        return old
    return default


# SKYRELAY_DRY_RUN=1 -> only log, post nothing on Bluesky.
DRY_RUN = env("DRY_RUN") == "1"
# SKYRELAY_FORCE=1 -> run even when OpenLigaDB knows no match today (friendlies).
FORCE_RUN = env("FORCE") == "1"
# SKYRELAY_PAIR_PHONE=<number> -> first pairing by numeric code instead of a
# QR scan (international, without "+", e.g. 4915123456789). First run only.
PAIR_PHONE = env("PAIR_PHONE")
# SKYRELAY_REPLAY=N -> test run: processes the last N existing channel posts
# once and then ends. The stored position stays untouched.
REPLAY_COUNT = int(env("REPLAY", "0") or 0)
# SKYRELAY_CATCHUP=N -> like REPLAY, but skips what has been processed
# already, advances the stored position and keeps listening afterwards.
CATCHUP_COUNT = int(env("CATCHUP", "0") or 0)
# SKYRELAY_HASHTAG=DSCGUE -> set the match hashtag by hand (with or without "#").
MANUAL_HASHTAG = (env("HASHTAG", "") or "").strip().lstrip("#").upper()
# SKYRELAY_PROFILE=on|off -> only set the profile line, then end.
PROFILE_ONLY = (env("PROFILE", "") or "").strip().lower()


def adopt_old_file(new_path, old_name):
    """Renames a file from an earlier version to its new name. Keeps the move to
    a configuration file from costing a fresh WhatsApp pairing or the position
    the ticker had already reached."""
    old_path = os.path.join(BASE_DIR, old_name)
    if os.path.exists(old_path) and not os.path.exists(new_path):
        try:
            os.replace(old_path, new_path)
            print(f"Carried over: {old_name} -> {os.path.basename(new_path)}")
        except Exception as error:
            print(f"⚠️ Could not carry over {old_name}: {error}", file=sys.stderr)


for _new_name, _old_name in ((SESSION_DB, "dsc_ticker_session.sqlite3"),
                   (STATE_FILE, "dsc_ticker_state.txt"),
                   (POSTS_MAP_FILE, "dsc_ticker_posts.json"),
                   (LOG_FILE, "ticker.log")):
    adopt_old_file(_new_name, _old_name)


if LOG_TO_FILE:
    start_file_logging(LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
    log(f"--- ticker start (log: {LOG_FILE}) ---")

# A password of its own for the ticker, otherwise the shared one.
BLUESKY_APP_PASSWORD, PASSWORD_VARIABLE = get_app_password(
    "BLUESKY_TICKER_APP_PASSWORD", "BLUESKY_APP_PASSWORD")
if not BLUESKY_APP_PASSWORD and not DRY_RUN:
    log(f"Error: no app password set for the ticker account @{BLUESKY_HANDLE}.")
    log('Separate accounts for ticker and feed:')
    log('    export BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log('One shared account for both:')
    log('    export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log("cron does NOT read ~/.bashrc - put the variables above into the")
    log("crontab itself (without quotation marks).")
    log("Read only, without Bluesky:  SKYRELAY_DRY_RUN=1")
    sys.exit(1)
# ----------------------------------


# 1. The matchday check through OpenLigaDB (free, no API key)
def team_code(team):
    """The DFL code for an OpenLigaDB team; a fallback for unknown cup opponents."""
    code = TEAM_CODES.get(team["teamId"])
    if code is None:
        # Keep the letters only, and upper-case afterwards. isalpha() rather
        # than a character class, because that one was missing ß and dropped
        # it silently ("Großaspach" -> "Groaspach"). Upper-casing comes
        # BEFORE the cut, since "ß".upper() is "SS" and would otherwise have
        # made the code four letters long.
        #
        # The short name is the first choice, but not always usable: "S04"
        # for Schalke shrinks to a single "S" once the digits are gone and
        # would give the hashtag #SDSC. So if fewer than three letters remain,
        # the full club name counts instead.
        def letters_only(text):
            return "".join(z for z in (text or "") if z.isalpha()).upper()

        code = letters_only(team["shortName"])
        if len(code) < 3:
            code = letters_only(team["teamName"]) or code
        code = code[:3]
        log(f'⚠️ No DFL code on file for "{team["teamName"]}" '
            f'(teamId {team["teamId"]}) - falling back to "{code}". '
            f'Please add it to [team_codes].')
    return code


def match_info_text(match):
    """A short note about the match for the profile line: "1. Spieltag" or
    "DFB-Pokal, 1. Runde"."""
    group = (match.get("group") or {}).get("groupName", "").strip()
    if "pokal" in match.get("leagueName", "").lower():
        return f"DFB-Pokal, {group}" if group else "DFB-Pokal"
    return group or match.get("leagueName", "")


def _last_updated(match):
    """When OpenLigaDB last touched this entry - it decides which of two
    duplicates counts. Without the field the entry counts as the oldest."""
    try:
        return datetime.fromisoformat(match.get("lastUpdateDateTime") or "")
    except (TypeError, ValueError):
        return datetime.min


def fetch_team_matches(weeks_back=1, weeks_forward=1):
    """Fetches our own team's matches from OpenLigaDB and returns them as a
    list of (kickoff_local, match) - sorted ascending, duplicates removed.

    Two kinds of rubbish are filtered out:
      * other teams (the API's team filter "bielefeld" is fuzzy) -> checked
        against teamId
      * made up or test leagues: OpenLigaDB really did list a league called
        "ESP8266" carrying the same match on the WRONG date. Without this
        filter the ticker would start up on a day with no match at all."""
    url = (f"https://api.openligadb.de/getmatchesbyteam/{OPENLIGADB_TEAM_FILTER}"
           f"/{weeks_back}/{weeks_forward}")
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()

    best = {}
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
        # The same match arrives twice when two leagues carry it (seen in the
        # wild: bl2 and bl2h). The kickoff time is NOT usable to tell them
        # apart - while the fixture is still open it holds a placeholder, and
        # the two entries then differ by hours. So deduplicate by date and
        # pairing and keep the entry updated last: it carries the more recent
        # fixture.
        # Without this the ticker would take a duplicate for a second match on
        # the same day and label the posts with the overlap hashtag.
        key = (kickoff_local.date(), match["team1"]["teamId"], match["team2"]["teamId"])
        existing = best.get(key)
        if existing is None or _last_updated(match) > _last_updated(existing[1]):
            best[key] = (kickoff_local, match)

    if skipped_leagues:
        log(f"(OpenLigaDB: Spiele aus unbekannten Ligen ignoriert: {', '.join(sorted(skipped_leagues))})")
    return sorted(best.values(), key=lambda item: item[0])


def describe_match(kickoff_local, match):
    """Builds (kickoff_local, description, hashtag, short info) from an
    OpenLigaDB match."""
    desc = f'{match["team1"]["teamName"]} - {match["team2"]["teamName"]} ({match["leagueName"]})'
    # team1 is the home side in the OpenLigaDB data.
    home_team, away_team = team_code(match["team1"]), team_code(match["team2"])
    if home_team == away_team:
        # Happens when two clubs share a code (both Werder Bremen and Waldhof
        # Mannheim carry "SVW", for instance). The hashtag would be useless.
        log(f'⚠️ Both teams carry the code "{home_team}" - the hashtag '
            f'#{home_team}{away_team} makes no sense.')
        log(f'   Please adjust one of them in [team_codes]: team numbers '
            f'{match["team1"]["teamId"]} ({match["team1"]["teamName"]}) and '
            f'{match["team2"]["teamId"]} ({match["team2"]["teamName"]}).')
    return kickoff_local, desc, home_team + away_team, match_info_text(match)


def get_todays_matches():
    """Returns every match of today as a list of (kickoff_local, description,
    hashtag, short info), sorted by kickoff - empty when nothing is on today.

    A list on purpose: when, say, the men's and the women's team play on the
    same day, both run through the same WhatsApp channel. The hashtag follows
    the pattern home+away, so DSCWOB at home and WOBDSC away."""
    today_local = datetime.now(LOCAL_TZ).date()
    return [describe_match(kickoff_local, match)
            for kickoff_local, match in fetch_team_matches(1, 1)
            if kickoff_local.date() == today_local]


def get_next_matches():
    """Returns every match of the next matchday (for the "bot is off" profile
    line), otherwise an empty list. It deliberately looks far ahead, so that
    winter and summer breaks are bridged too. If several matches fall on that
    day, all of them come back - the profile line then names each one."""
    now = datetime.now(LOCAL_TZ)
    upcoming = [(k, m) for k, m in fetch_team_matches(0, 12) if k > now]
    if not upcoming:
        return []
    first_day = upcoming[0][0].date()
    return [describe_match(k, m) for k, m in upcoming if k.date() == first_day]


# 2. The watermark (a channel's MessageServerID rises monotonically)
def load_watermark():
    """Reads the watermark, but only if it is from TODAY (a restart on the same
    day). On a new matchday a fresh baseline is taken instead."""
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
    """Loads the mapping of server ID -> posted Bluesky URIs and text hash (only
    for today). Needed to delete and replace the old Bluesky posts when a
    channel post is edited."""
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


# 3. Helpers for message content and Bluesky
def unwrap_message(msg):
    """Unwraps generic container messages - some posts sit inside wrappers
    such as ephemeralMessage until the actual content shows up.
    Edits arrive as protocolMessage.editedMessage carrying the NEW content."""
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
    """Pulls the text out of a WhatsApp E2E message (channel posts are usually
    conversation or extendedTextMessage; images carry their text as a
    caption)."""
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
    """Splits text into chunks below Bluesky's 300 character limit. The first
    chunk is smaller because it also carries the header and the source link
    (~60 characters); the last one still gets the hashtags. It prefers word
    and line boundaries and cuts hard only when it has to."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    limit = first_limit
    while len(text) > limit:
        cut = max(text.rfind(" ", 0, limit), text.rfind("\n", 0, limit))
        if cut < limit // 2:  # no usable boundary found -> cut hard
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
        limit = follow_limit
    if text:
        chunks.append(text)
    return chunks


URL_REGEX = re.compile(r"https?://[^\s<>()\[\]]+")


def add_text_with_links(tb, text):
    """Adds text to the TextBuilder and makes any URLs in it clickable
    (Bluesky needs facets for that - plain text is not linked automatically)."""
    pos = 0
    for m in URL_REGEX.finditer(text):
        if m.start() > pos:
            tb.text(text[pos:m.start()])
        tb.link(m.group(0), m.group(0))
        pos = m.end()
    if pos < len(text):
        tb.text(text[pos:])


def build_post_text(chunk, index, total, hashtags):
    """Builds the text of a single post in the thread - the header in the first
    one, the hashtags in the last, a counter in between.

    Deliberately one single place: it lets the dry run show exactly what the
    real run would send, instead of a rebuilt approximation."""
    tb = client_utils.TextBuilder()
    if index == 0:
        build_source_line(tb, POST_PREFIX, POST_SOURCE_LABEL, CHANNEL_INVITE_LINK)
    add_text_with_links(tb, chunk if total == 1 else f"{chunk} ({index + 1}/{total})")
    if index == total - 1:
        tb.text("\n\n")
        for nth, tag in enumerate(hashtags):
            if nth:
                tb.text(" ")
            tb.tag(f"#{tag}", tag)
    return tb


def fetch_og_data(url):
    """Fetches the OpenGraph data (title, description, preview image) of a page
    for the Bluesky link card. Returns (title, description, thumb_bytes|None)."""
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
    """Builds a Bluesky link preview card (app.bsky.embed.external). Bluesky
    does not generate previews server side - without this card a link stays
    bare."""
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
_login_attempts = 0  # begrenzt Wiederholungen, siehe ensure_bsky()
match_hashtag = None  # set in main() from the OpenLigaDB data (e.g. "DSCWOB")
match_hashtags_tag = []  # every match hashtag of the day - the profile line names them
match_info = None     # short note on today's match, e.g. "1. Spieltag" (profile line)
match_kickoff = None  # kickoff time of today's match (profile line)


def ensure_bsky():
    """Opens the Bluesky connection (lazily, once per run)."""
    global bsky_client
    global _login_attempts
    if bsky_client is not None:
        return
    # Do not keep trying after several failures: Bluesky allows only 10
    # logins per day and account, and on a matchday dozens of attempts would
    # pile up - after which even a corrected password would be locked out for
    # the rest of the day.
    if _login_attempts >= 3:
        raise RuntimeError("Logging in to Bluesky failed repeatedly - "
                           "no further attempts in this run.")
    _login_attempts += 1
    log("Connecting to Bluesky...")
    connection = Client()
    # Take it over only after a successful login: otherwise an
    # unauthenticated object would stay behind, and every following post
    # would go out unauthenticated ("AuthMissing").
    log_in_to_bluesky(connection, BLUESKY_HANDLE, BLUESKY_APP_PASSWORD,
                         PASSWORD_VARIABLE)
    bsky_client = connection


# --- Profil-Statuszeile -------------------------------------------------------
profile_status_on = False  # true once the bio says the bot is on


def set_profile_status(on):
    """Switches the FIRST line of the Bluesky bio between "bot is on" and "bot
    is off". Every other line of the bio, as well as avatar, banner and display
    name, is left untouched (the whole profile record is read and only the
    description changed). Errors are only logged - the ticker carries on either
    way."""
    global profile_status_on

    if not PROFILE_STATUS_ENABLED:
        return

    # Zeile zusammenbauen
    try:
        if on:
            # With several matches the profile line names all of them, even
            # though the posts themselves only carry the overlap hashtag.
            shown = match_hashtags_tag or ([match_hashtag] if match_hashtag else [])
            line = PROFILE_LINE_ON.format(
                info=match_info or FALLBACK_MATCH_INFO,
                hashtag=" ".join(f"#{h}" for h in shown),
                date=match_kickoff.strftime("%d.%m.") if match_kickoff else "",
                time=match_kickoff.strftime("%H:%M") if match_kickoff else "",
            )
        else:
            next_matches = get_next_matches()
            if next_matches:
                kickoff, _desc, _hashtag, info = next_matches[0]
                line = PROFILE_LINE_OFF.format(
                    info=" + ".join(dict.fromkeys(i for *_, i in next_matches)) or info,
                    hashtag=" ".join(f"#{h}" for *_, h, _ in next_matches),
                    date=kickoff.strftime("%d.%m."), time=kickoff.strftime("%H:%M"),
                )
            else:
                line = PROFILE_LINE_OFF_NO_MATCH
        # Empty placeholders would otherwise leave double spaces behind.
        line = " ".join(line.split())
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
                # Guards against overwriting a bio changed in parallel.
                swap_record=resp.cid,
            )
        )
        profile_status_on = on
        log(f"✓ Profil-Statuszeile gesetzt: {line}")
    except Exception as e:
        log(f"⚠️ Profil-Statuszeile konnte nicht aktualisiert werden: {e}")


# --- Video-Upload: portiert aus skyrelay-feed.py ---------------------------
# TODO(extract): resolve_pds_did_web, upload_video_to_bluesky, compressing
# images and the thread posting logic exist almost identically in the
# Instagram reposter. Once both programs share common building blocks, these
# functions belong in a shared module - the redundancy here is deliberate and
# temporary.

# ------------------------------------------------------------------------------


def post_to_bluesky(text, image_blobs, video_bytes=None, video_thumb=None,
                    media_name="video", placeholder=None):
    """Posts a channel message in the same shape as the Instagram reposter: a
    main post with the header and the source link, and follow-up chunks as
    replies when it runs long. URLs in the text become clickable. Embed order
    (one post = one embed): video > images > link preview card. If the video
    upload fails, the WhatsApp thumbnail steps in as an image. The last chunk
    gets the generated match hashtag and the standing hashtag. Returns the list
    of URIs created (the main post first) - stored for the edit logic, so posts
    can be deleted later."""
    hashtags = ([match_hashtag] if match_hashtag else []) + \
               ([STANDING_HASHTAG] if STANDING_HASHTAG else [])

    text_chunks = split_text(text)
    if not text_chunks and not image_blobs and not video_bytes:
        log("   (empty message, skipped)")
        return []

    card_url_match = URL_REGEX.search(text) if not image_blobs and not video_bytes else None

    # When the post is media only, a placeholder text takes the place of the
    # text. That has to happen before the dry run, so it builds its preview on
    # the same footing as the real run.
    if not text_chunks:
        text_chunks = [placeholder or
                       (VIDEO_PLACEHOLDER if video_bytes else IMAGE_PLACEHOLDER)]

    if DRY_RUN:
        card = (f", link card for {card_url_match.group(0)}"
                if card_url_match else "")
        video = f", 1 Video ({len(video_bytes)} Bytes)" if video_bytes else ""
        log(f"   [DRY_RUN] Würde posten ({len(text_chunks)} Beitrag/Beiträge, "
            f"{len(image_blobs)} Bild(er){video}{card}):")
        show_preview([build_post_text(chunk, i, len(text_chunks), hashtags)
                        for i, chunk in enumerate(text_chunks)])
        return []

    ensure_bsky()

    # Try the video upload first; on failure use the thumbnail as an image and
    # note the video down to be handed in later.
    video_embed = None
    video_pending = False
    if video_bytes:
        try:
            video_embed = upload_video_to_bluesky(bsky_client, video_bytes,
                                                  f"{media_name}.mp4",
                                                  MAX_VIDEO_BYTES,
                                                  VIDEO_JOB_TIMEOUT_SECONDS)
        except Exception as video_err:
            log(f"   ⚠️ Video-Upload fehlgeschlagen: {video_err}")
            # The post goes out with the thumbnail right away - on a live
            # ticker, time is what counts. The video stays behind and is
            # handed in later.
            video_pending = stash_video(VIDEO_RETRY_DIR, media_name, video_bytes)
            if video_thumb and not image_blobs:
                try:
                    image_blobs = [compress_image_for_bluesky(video_thumb)]
                    log("   Using the video thumbnail as a fallback.")
                except Exception as thumb_err:
                    log(f"   ⚠️ Auch Vorschaubild-Fallback fehlgeschlagen: {thumb_err}")

    # A link preview card only when no other media are present (one post = one embed).
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

        tb = build_post_text(chunk, i, total, hashtags)

        if is_first:
            root_post = bsky_client.send_post(text=tb, embed=embed)
            root_ref = models.ComAtprotoRepoStrongRef.Main(cid=root_post.cid, uri=root_post.uri)
            parent_ref = root_ref
            created_uris.append(root_post.uri)
            if video_pending:
                stash_retry_target(VIDEO_RETRY_DIR, media_name, bsky_client.me.did,
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

    log("   ✓ Published on Bluesky.")
    return created_uris


# 4. Kanal-Nachricht verarbeiten
MEDIA_FIELDS = ("audioMessage", "videoMessage", "imageMessage",
                "stickerMessage", "documentMessage")


async def download_channel_media(client, msg):
    """Downloads the media file of a channel message.

    Channel media sit unencrypted behind `directPath` - images and videos
    therefore carry no `mediaKey` at all and download without complaint.
    Voice messages, however, drag a `mediaKey` along from the ordinary chat
    flow. whatsmeow then turns into the decryption path and fails there with
    "invalid media hmac", even though the file could be fetched.
    Measured on 19.08.2026: 0 of 5 voice messages downloaded the regular way,
    all 5 downloaded without the key - byte for byte the announced length.

    So the regular way comes first, and only an hmac error triggers a second
    attempt without the `mediaKey`. Should WhatsApp one day serve channel
    audio encrypted after all, the regular way still applies."""
    try:
        return await client.download_any(msg)
    except Exception as error:
        if "hmac" not in str(error).lower():
            raise
        log("   ℹ️ The download fails on the checksum - trying again without "
            "the mediaKey (channel media are unencrypted).")
        without_key = type(msg)()
        without_key.CopyFrom(msg)
        for field in MEDIA_FIELDS:
            if without_key.HasField(field):
                getattr(without_key, field).ClearField("mediaKey")
                break
        return await client.download_any(without_key)


async def process_newsletter_message(client, raw_msg, server_id):
    """Handles a new channel message: extract the text, fetch media if any,
    repost it."""
    msg = unwrap_message(raw_msg)

    # Diagnosis: which fields does the message really carry? (Channel posts
    # can be shaped differently from ordinary chats - this shows at once what
    # arrived.)
    field_names = [fd.name for fd, _ in msg.ListFields()]
    log(f"   [DEBUG] message fields: {field_names or '(none)'}")

    text = extract_text(msg)
    if not text and not field_names:
        log("   [DEBUG] the message is completely empty "
            "(probably a deleted post or a reaction update).")
    elif not text:
        preview = str(msg)[:300].replace("\n", " | ")
        log(f"   [DEBUG] Kein Text extrahierbar, Roh-Vorschau: {preview}")

    image_blobs = []
    video_bytes = None
    video_thumb = None
    placeholder = None
    if msg.HasField("imageMessage"):
        try:
            raw = await download_channel_media(client, msg)
            image_blobs.append(compress_image_for_bluesky(raw))
        except Exception as dl_err:
            # Channel media partly run through different endpoints than
            # ordinary chats - if the download fails, only the text is posted.
            log(f"   ⚠️ Bild-Download fehlgeschlagen ({dl_err}) - poste nur Text.")
    elif msg.HasField("videoMessage"):
        video_thumb = msg.videoMessage.JPEGThumbnail or None
        try:
            log("   Downloading the video from the channel...")
            video_bytes = await download_channel_media(client, msg)
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
        # Bluesky knows no audio format. A voice message therefore becomes a
        # video with an animated waveform - the sound is kept.
        placeholder = AUDIO_PLACEHOLDER
        seconds = msg.audioMessage.seconds or 0
        try:
            log("   Downloading the voice message from the channel...")
            audio = await download_channel_media(client, msg)
            log(f"   ✓ Sprachnachricht geladen ({len(audio)} Bytes, {seconds}s).")
            log("   Building the video with a waveform...")
            video_bytes = audio_to_video(audio, AUDIO_SIZE, AUDIO_WAVE_COLOR,
                                         AUDIO_BG_COLOR, AUDIO_FRAMERATE)
            video_thumb = video_still(video_bytes)
            log(f"   ✓ Video erzeugt ({len(video_bytes)} Bytes).")
        except Exception as audio_err:
            log(f"   ⚠️ Sprachnachricht nicht übertragbar: {audio_err}")
            if not text:
                log("   (without text there is nothing to post - skipped)")
                return []
    elif msg.HasField("stickerMessage"):
        # Stickers are WebP, often transparent and sometimes animated. Bluesky
        # plays no animations, so the first frame goes out.
        placeholder = STICKER_PLACEHOLDER
        try:
            log("   Downloading the sticker from the channel...")
            raw_sticker = await download_channel_media(client, msg)
            image_blobs.append(sticker_to_image(raw_sticker, STICKER_BACKGROUND))
            log(f"   ✓ Sticker umgewandelt ({len(raw_sticker)} Bytes Ausgangsmaterial).")
        except Exception as sticker_err:
            log(f"   ⚠️ Sticker nicht übertragbar: {sticker_err}")
            if not text:
                log("   (without text there is nothing to post - skipped)")
                return []

    log(f"   Text ({len(text)} Zeichen): {text[:100]!r}...")
    return post_to_bluesky(text, image_blobs, video_bytes, video_thumb,
                           media_name=f"{MEDIA_PREFIX}_{server_id}",
                           placeholder=placeholder)


async def handle_edit(client, event, server_id, posts_map):
    """Handles an event for a server ID that was processed already: on a real
    edit the old Bluesky posts are deleted and the new version is posted;
    unchanged redeliveries are ignored."""
    msg = unwrap_message(event.Message)
    new_text = extract_text(msg)
    has_media = any(msg.HasField(field) for field in
                    ("imageMessage", "videoMessage", "audioMessage", "stickerMessage"))

    if not new_text and not has_media:
        log(f"   (ServerID {server_id}: Meta-Nachricht ohne Inhalt - übersprungen)")
        return

    entry = posts_map.get(str(server_id))
    if entry and entry.get("hash") == text_hash(new_text):
        log(f"   (ServerID {server_id}: unveränderte Wiederzustellung - übersprungen)")
        return

    if not entry:
        # An edit to a post this bot did not publish today (or cannot trace) -
        # old ticker posts are of no interest, so ignore it.
        log(f"   (Bearbeitung zu ServerID {server_id} ohne gespeicherte Bluesky-Posts - "
            f"alter/unbekannter Post, wird ignoriert)")
        return

    log(f"[EDIT] Kanal-Post {server_id} wurde bearbeitet - ersetze Bluesky-Post(s).")

    if DRY_RUN:
        log(f"   [DRY_RUN] would delete {len(entry['uris'])} old Bluesky post(s) "
            f"and post anew: {new_text[:100]!r}...")
        return

    ensure_bsky()
    deleted = 0
    # Delete the replies first (in reverse order), the main post last.
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

# connect() returns immediately - login and pairing run asynchronously. Acting
# Newsletter-Methoden aufruft, provoziert einen Segfault im Go-Layer (nil Client).
# So: wait for the connected event before anything else happens.
wa_connected = asyncio.Event()


@client.event(ConnectedEv)
async def on_connected(_, __):
    wa_connected.set()


@client.event(PairStatusEv)
async def on_pair_status(_, __):
    log("✓ The device is paired with the WhatsApp account.")


_qr_instructions_shown = False


@client.event.qr
async def on_qr(_, data_qr):
    """A QR handler of our own instead of neonize's default: this way the
    instructions appear only when a first pairing is really due - not on every
    ordinary start."""
    global _qr_instructions_shown
    if not _qr_instructions_shown:
        _qr_instructions_shown = True
        log("A first pairing is needed - scan this QR code with the phone that "
            "holds the number:")
        log("(WhatsApp -> Settings -> Linked devices -> Link a device;")
        log(" if scanning gives trouble, enlarge the terminal a lot and turn the "
            "screen brighter.")
        log(" Alternative: pairing by numeric code through SKYRELAY_PAIR_PHONE=<number>.)")
    else:
        log("(a new QR code - the old one expired)")
    segno.make_qr(data_qr).terminal(compact=True)


# Channel posts arrive as live events. Polling get_newsletter_messages regularly
# is TABOO during normal operation: if the channel holds a message without
# content, neonize's Go layer panics while serialising it ("required field ...
# Message not set") and takes the whole process with it. The trigger is an
# INVISIBLE meta message (not shown in the channel, but occupying a server ID
# of its own) - the edit or deletion of a post, say, possibly album grouping
# too. What exactly it is could not be settled: the message cannot be
# inspected with neonize, every fetch dies on it. On the event path this does
# not happen: whatsmeow filters such messages out
# selbst aus ("doesn't have byte content").
channel_user = None  # the user part of the channel JID, set in main()
incoming_events: asyncio.Queue = asyncio.Queue()


@client.event(MessageEv)
async def on_message(_, event):
    # Bewusst ALLE Newsletter-Events annehmen: Die Offline-Nachlieferung kommt
    # right on connecting - that is, BEFORE main() has resolved the channel
    # JID. The channel filter therefore happens later, during processing.
    if event.Info.MessageSource.Chat.Server == "newsletter":
        await incoming_events.put(event)


async def with_retries(description, coro_factory, max_attempts=6, wait_seconds=15):
    """Runs a WhatsApp query with retries. Needed because whatsmeow reconnects
    several times right after a first pairing in particular (code 515, history
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
    global match_hashtag, match_hashtags_tag, match_info, match_kickoff, channel_user

    missing = [name for name, value in (
        ("[source] channel_invite_link", CHANNEL_INVITE_LINK),
        ("[bluesky] handle", BLUESKY_HANDLE),
        ("[team] openligadb_filter", OPENLIGADB_TEAM_FILTER),
    ) if not value or "HIER-DEN" in value or value.startswith("dein-bot.")]
    if missing:
        log(f"Error: {os.path.basename(CONFIG_FILE)} is still missing: "
            f"{', '.join(missing)}")
        log("The channel link comes from the phone: open the channel -> tap the")
        log("channel name -> Share -> copy link.")
        sys.exit(1)

    # Profile-only mode "off": needs neither a matchday nor WhatsApp.
    if PROFILE_ONLY == "off":
        set_profile_status(False)
        return

    if MANUAL_HASHTAG:
        match_hashtag = MANUAL_HASHTAG
        log(f"Manueller Spiel-Hashtag gesetzt: #{match_hashtag}")

    # The matchday check comes BEFORE connecting to WhatsApp - on days without
    # a match no connection is opened at all (which keeps both the runtime and
    # the footprint small).
    # FORCE means "start anyway", NOT "ignore the fixture data": if
    # OpenLigaDB does find a match, hashtag and matchday info are used all the
    # same. Without a club configured (a sport OpenLigaDB does not carry) the
    # check is skipped entirely - the ticker then runs on any day it is
    # started.
    without_schedule = not OPENLIGADB_TEAM_FILTER or not OPENLIGADB_TEAM_ID
    todays_matches = []
    if without_schedule:
        log("No fixture list configured ([team] openligadb_filter is empty) - "
            "skipping the matchday check.")
    else:
        try:
            todays_matches = get_todays_matches()
        except Exception as e:
            log(f"Fehler beim OpenLigaDB-Abruf: {e}")
            if not FORCE_RUN:
                sys.exit(1)

    if todays_matches:
        match_hashtags_tag = [h for *_, h, _ in todays_matches]
        match_kickoff = todays_matches[0][0]
        match_info = " + ".join(dict.fromkeys(i for *_, i in todays_matches))
        for kickoff, desc, hashtag, info in todays_matches:
            log(f"⚽ Today is a matchday: {desc}, {info}, "
                f"kickoff {kickoff.strftime('%H:%M')}. Match hashtag: #{hashtag}")

        if len(todays_matches) > 1:
            # Several teams on one day sharing one channel: a message does not
            # reveal which match it belongs to. Better no match hashtag than
            # the wrong one.
            log(f"ℹ️ {len(todays_matches)} Spiele an einem Tag - die Beiträge bekommen "
                f"statt der Spiel-Hashtags "
                + (f"#{OVERLAP_HASHTAG}." if OVERLAP_HASHTAG else "gar keinen.")
                + " Die Profilzeile nennt beide Partien.")
            if not match_hashtag:
                match_hashtag = OVERLAP_HASHTAG or None
        elif not match_hashtag:
            match_hashtag = match_hashtags_tag[0]
    elif FORCE_RUN or without_schedule:
        note = "" if match_hashtag else " (without a match hashtag)"
        reason = ("no fixture check configured" if without_schedule
                  else "SKYRELAY_FORCE=1 is set - OpenLigaDB knows no match today")
        log(f"{reason}, running anyway{note}.")
    else:
        log("No match today - the program ends here.")
        return

    # Profile-only mode "on": set the status line and end, without WhatsApp.
    if PROFILE_ONLY == "on":
        set_profile_status(True)
        return

    if DRY_RUN:
        log("SKYRELAY_DRY_RUN=1 is set - NOTHING will be posted on Bluesky.")

    log("Connecting to WhatsApp...")
    await client.connect()

    if PAIR_PHONE:
        # Wait briefly: if a valid session already exists, no pairing is needed
        # (PairPhone would raise an error on an existing login).
        try:
            await asyncio.wait_for(wa_connected.wait(), timeout=10)
            log("ℹ️ Already paired - SKYRELAY_PAIR_PHONE is ignored.")
        except asyncio.TimeoutError:
            try:
                code = await client.PairPhone(PAIR_PHONE, show_push_notification=True)
                log("=" * 50)
                log(f"PAIRING CODE: {code}")
                log("Enter it on the phone: WhatsApp -> Settings -> Linked devices")
                log('-> Link a device -> "Link with phone number instead"')
                log("=" * 50)
            except Exception as pair_err:
                # Not fatal: this fails when the QR code has already been
                # scanned in parallel (a race between the two pairing paths) -
                # the connection then stands anyway. The wait below sorts it out.
                log(f"⚠️ Kopplung per Code nicht möglich ({pair_err}) - "
                    f"falls der QR-Code gescannt wurde, geht es trotzdem weiter.")

    try:
        await asyncio.wait_for(wa_connected.wait(), timeout=300)
    except asyncio.TimeoutError:
        log("Error: no logged-in WhatsApp connection within 5 minutes.")
        log("Was the QR code scanned? For a broken session, deleting it and pairing")
        log("again helps:")
        log(f"  rm {SESSION_DB}")
        sys.exit(1)
    log("✓ WhatsApp connected and logged in.")

    # Catch a breath: right after logging in (especially after a first
    # pairing) reconnects and a history sync are still running - queries
    # would be unreliable now.
    await asyncio.sleep(5)

    log("Resolving the channel from its invite link...")
    metadata = await with_retries(
        "resolving the channel",
        lambda: client.get_newsletter_info_with_invite(CHANNEL_INVITE_LINK),
    )
    channel_jid = metadata.ID
    # From here on the event handler accepts channel posts.
    channel_user = channel_jid.User
    log(f"✓ Channel found: JID {channel_jid.User}@{channel_jid.Server}")

    try:
        await client.follow_newsletter(channel_jid)
        log("✓ The channel is followed.")
    except Exception as follow_err:
        # Fails when the channel is followed already, among other things -
        # nothing to worry about.
        log(f"ℹ️ follow_newsletter: {follow_err} (probably followed already)")

    # Load the watermark early - CATCHUP and the listening loop both need it.
    watermark = load_watermark() or 0
    if watermark:
        log(f"Watermark from today loaded: MessageServerID {watermark}.")
    seen_ids = set()  # fallback dedup by message ID, for events without a server ID
    posts_map = load_posts_map()  # server ID -> Bluesky URIs (for the edit logic)

    # REPLAY:  put the last N posts through the pipeline once (a test tool; it
    #          ignores the watermark and does not change it, then ends).
    # CATCHUP: fetch missed posts (respecting and maintaining the watermark)
    #          and keep listening normally afterwards.
    # CAREFUL, known neonize bug: if one of the last N messages is a meta
    # message without content (invisible in the channel, an edit or a deletion
    # for instance), the Go layer panics hard. So exactly N are fetched - and
    # live operation does not use this call at all, it listens for events.
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
                if not DRY_RUN:  # a dry run marks nothing as done
                    save_watermark(watermark)
            await asyncio.sleep(PAUSE_BETWEEN_POSTS_SECONDS)
        if mode == "REPLAY":
            log("REPLAY finished - the program ends here.")
            return
        log("CATCHUP finished - now listening for new posts.")

    # Subscribe to live updates: channel posts are then pushed as events.
    # The subscription lasts a few minutes only and is renewed in the loop.
    # (Followed channels usually push without it - this is a safety net.)
    try:
        await client.newsletter_subscribe_live_updates(channel_jid)
        log("✓ Live-Updates abonniert.")
    except Exception as sub_err:
        log(f"⚠️ Live-Update-Abo fehlgeschlagen ({sub_err}) - weiter ohne, "
            f"gefolgte Kanäle pushen in der Regel trotzdem.")
    next_renew = time.monotonic() + SUBSCRIBE_RENEW_SECONDS
    next_retry_at = time.monotonic() + VIDEO_RETRY_INTERVAL_SECONDS

    # Events are new by definition - no baseline query is needed. On
    # connecting, WhatsApp also delivers posts that piled up; the date check
    # below drops everything that is not from today.
    day_end = datetime.now(LOCAL_TZ).replace(
        hour=DAY_END_HOUR, minute=DAY_END_MINUTE, second=0, microsecond=0
    )
    # From here the bot is "on duty" -> switch the bio status line.
    set_profile_status(True)

    log(f"Listening for new channel posts until {day_end.strftime('%H:%M')}...")

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
            # In a thread, so incoming channel posts keep flowing into the
            # queue, and only while idle, so a live post never has to wait.
            if (not DRY_RUN and time.monotonic() >= next_retry_at
                    and os.path.isdir(VIDEO_RETRY_DIR)
                    and any(n.endswith(".json") for n in os.listdir(VIDEO_RETRY_DIR))):
                next_retry_at = time.monotonic() + VIDEO_RETRY_INTERVAL_SECONDS
                try:
                    ensure_bsky()
                    await asyncio.to_thread(
                        post_stashed_videos, bsky_client, VIDEO_RETRY_DIR,
                        VIDEO_RETRY_TEXT, VIDEO_RETRY_MAX_ATTEMPTS,
                        MAX_VIDEO_BYTES, VIDEO_JOB_TIMEOUT_SECONDS)
                except Exception as nach_err:
                    log(f"⚠️ Nachreichen offener Videos fehlgeschlagen: {nach_err}")
            continue

        # A guard around the WHOLE event handling: a single broken event must
        # never end the listener (the lesson of 14.07.: a timestamp in ms
        # instead of s -> ValueError -> loop dead, during the match of all
        # times).
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
                # Live events deliver the timestamp in MILLIseconds, other
                # paths in seconds - normalise it by its magnitude.
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
            log(f"Channel event received: server ID {server_id or '?'}{when} "
                f"(ID {msg_id})")

            if msg_id in seen_ids:
                log("   (already processed in this run - skipped)")
                continue
            if server_id and server_id <= watermark:
                # An already processed server ID with a new event is most
                # likely an edit in the channel -> replace the old posts.
                await handle_edit(client, event, server_id, posts_map)
                seen_ids.add(msg_id)
                continue

            # Posts delivered late from the offline queue can be older -
            # everything not from today's match day is dropped.
            if msg_time and msg_time.date() != datetime.now(LOCAL_TZ).date():
                log("   (not from today - skipped)")
                if server_id:
                    watermark = server_id
                    if not DRY_RUN:
                        save_watermark(watermark)
                continue

            log(f"[NEW] channel message with server ID {server_id or '?'}:")
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
            # Advance the dedup even on an error, or the program would keep
            # hanging on the same broken message. In a dry run the position is
            # NOT saved - otherwise a merely tested post would later count as
            # done and never be published.
            seen_ids.add(msg_id)
            if server_id:
                watermark = server_id
                if not DRY_RUN:
                    save_watermark(watermark)
            await asyncio.sleep(PAUSE_BETWEEN_POSTS_SECONDS)
        except Exception as event_err:
            log(f"⚠️ Fehler bei der Event-Verarbeitung ({event_err}) - Event übersprungen, lausche weiter.")
            traceback.print_exc()

    log("End of day reached - the ticker stops. See you next matchday!")


async def run():
    """A wrapper around main(): it ALWAYS closes the WhatsApp connection cleanly
    at the end - otherwise the Go threads keep the process alive for minutes
    (an executor hang)."""
    try:
        await main()
    finally:
        # Put the bio back to "bot is off" - on Ctrl+C or an error as well.
        # (Not in profile-only mode "on", of course: there "on" is the point.)
        if profile_status_on and PROFILE_ONLY != "on":
            set_profile_status(False)
        try:
            await asyncio.wait_for(client.stop(), timeout=10)
            # A moment's grace: the Go socket thread logs its disconnect through
            # a callback into Python. Ending too quickly leaves that callback
            # pointing nowhere -> SIGSEGV on exit (cosmetic, but ugly; a
            # neonize race).
            await asyncio.sleep(2)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log("Interrupted with Ctrl+C - the WhatsApp connection was closed.")
