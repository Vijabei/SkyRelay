"""
SkyRelay - Feed: spiegelt ein Instagram-Profil nach Bluesky.

Im Gegensatz zum Spieltags-Ticker läuft dieses Programm im Dauerbetrieb: Es
prüft bei jedem Aufruf die letzten Beiträge des Profils und überträgt alles,
was noch nicht übernommen wurde. Gedacht für einen regelmäßigen Start per cron.

Alle Einstellungen stehen im Abschnitt [feed] von "skyrelay.conf"
(Vorlage: skyrelay.conf.example). Ein abweichender Pfad lässt sich über die
Umgebungsvariable SKYRELAY_CONFIG angeben.

Zugangsdaten kommen ausschließlich aus Umgebungsvariablen:
    BLUESKY_FEED_APP_PASSWORD   App-Passwort des Feed-Kontos
    BLUESKY_APP_PASSWORD        Ersatz, wenn Ticker und Feed dasselbe Konto
                                verwenden

Die Instagram-Sitzung wird einmalig außerhalb dieses Programms angelegt:
    venv/bin/instaloader -l <zweitkonto>

Beispiel für cron (alle 15 Minuten):
    */15 * * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-feed.py >/dev/null 2>&1
"""

import os
import re
import sys
import glob
import shutil
import instaloader
from atproto import Client, models, client_utils
import textwrap
import time
import requests
import traceback

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
    build_source_line,
    show_preview,
)
from skyrelay_config import check_config, show_config

# Auskunft über die Konfiguration, noch bevor irgendetwas anderes anläuft:
# Beide Aufrufe verbinden sich mit nichts und schreiben nichts.
#   --check-config  meldet, was nicht zusammenpasst
#   --show-config   zeigt, welcher Wert gerade gilt und woher er stammt
if "--check-config" in sys.argv:
    sys.exit(check_config(os.path.dirname(os.path.abspath(__file__))))
if "--show-config" in sys.argv:
    sys.exit(show_config(os.path.dirname(os.path.abspath(__file__))))


# Instagram wechselt regelmäßig die Endpunkte, über die Profildaten abrufbar sind -
# entsprechend eng ist das Zeitfenster brauchbarer instaloader-Versionen:
#   < 4.15.1  scheitert an Instagrams GraphQL-Umstellung
#   4.15.2    funktioniert (in requirements.txt festgelegt)
#   >= 4.15.3 nutzt wieder "web_profile_info"; Instagram drosselt diesen Endpunkt
#             seit August 2026, schon die erste Anfrage endet mit 429
#             (instaloader#2726, noch offen)
_instaloader_version = tuple(int(p) for p in instaloader.__version__.split(".")[:3] if p.isdigit())
if _instaloader_version < (4, 15, 1):
    log(f"⚠️ instaloader {instaloader.__version__} ist zu alt für Instagrams "
        f"aktuelle Endpunkte. Empfohlen: pip install 'instaloader==4.15.2'")
elif _instaloader_version >= (4, 15, 3):
    log(f"⚠️ instaloader {instaloader.__version__} nutzt den von Instagram "
        f"gedrosselten Endpunkt 'web_profile_info' - rechne mit HTTP 429.")
    log(f"   Empfohlen, bis instaloader#2726 gelöst ist: "
        f"pip install 'instaloader==4.15.2'")

# =============================== KONFIGURATION ===============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
cfg, cfg_int, cfg_bool, CONFIG_FILE = load_config(BASE_DIR)

INSTA_USER = cfg("feed", "instagram_profile", "")
INSTA_BOT_USER = cfg("feed", "instagram_session_user", "")
# Eigenes Konto für den Feed? Sonst das allgemeine aus [bluesky].
BLUESKY_HANDLE = cfg("feed", "bluesky_handle", "") or cfg("bluesky", "handle", "")

POSTS_TO_CHECK = cfg_int("feed", "posts_to_check", 10)
PAUSE_BETWEEN_POSTS_SECONDS = cfg_int("feed", "pause_between_posts_seconds", 8)
VIDEO_PLACEHOLDER = cfg("feed", "video_placeholder", "🎥 Neues Video/Reel")
MIXED_PLACEHOLDER = cfg("feed", "mixed_placeholder", "📸🎥 Neuer Beitrag")
IMAGE_PLACEHOLDER = cfg("feed", "image_placeholder", "📸 Neues Bild")
ALT_TEXT_FALLBACK = cfg("feed", "alt_text_fallback", "News")

POST_PREFIX = cfg("post", "prefix", "⚽ [Inoffizieller Bot]")
# Eigene Beschriftung für den Quell-Link: [post] source_label beschreibt den
# WhatsApp-Kanal des Tickers und passt nicht auf einen Instagram-Beitrag.
FEED_SOURCE_LABEL = cfg("feed", "source_label", "Beitrag auf Instagram")
STANDING_HASHTAG = cfg("post", "standing_hashtag", "").strip().lstrip("#")

MAX_VIDEO_BYTES = cfg_int("limits", "max_video_bytes", 100_000_000)
VIDEO_JOB_TIMEOUT_SECONDS = cfg_int("limits", "video_job_timeout_seconds", 600)
VIDEO_RETRY_MAX_ATTEMPTS = cfg_int("limits", "video_retry_max_attempts", 8)
VIDEO_RETRY_TEXT = cfg("post", "video_retry_text",
                      "🎥 Nachgereicht: das Video zum Beitrag oben.")

LOG_TO_FILE = cfg_bool("logging", "to_file", True)
LOG_MAX_BYTES = cfg_int("logging", "max_bytes", 2_000_000)
LOG_BACKUP_COUNT = cfg_int("logging", "backup_count", 5)

STATE_FILE = os.path.join(BASE_DIR, cfg("feed", "state", "skyrelay_feed_posted.txt"))
LOG_FILE = os.path.join(BASE_DIR, cfg("feed", "log", "skyrelay-feed.log"))
TMP_DIR = os.path.join(BASE_DIR, "tmp")
VIDEO_RETRY_DIR = os.path.join(BASE_DIR,
                               cfg("files", "video_retry_dir", "skyrelay_nachreichen"))


if LOG_TO_FILE:
    start_file_logging(LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
    log(f"--- Feed-Start (Protokoll: {LOG_FILE}) ---")

# Altbestand aus früheren Fassungen übernehmen, damit nichts doppelt gepostet wird.
_alt_state = os.path.join(BASE_DIR, "posted_shortcodes.txt")
if os.path.exists(_alt_state) and not os.path.exists(STATE_FILE):
    try:
        os.replace(_alt_state, STATE_FILE)
        log(f"Übernommen: posted_shortcodes.txt -> {os.path.basename(STATE_FILE)}")
    except Exception as _e:
        log(f"⚠️ Konnte posted_shortcodes.txt nicht übernehmen: {_e}")

# App-Passwort NICHT in der Konfiguration speichern - kommt aus der Umgebung.
# Eigenes Konto für den Feed -> eigenes Passwort möglich. BLUESKY_APP_PASSWORD
# greift, wenn Ticker und Feed dasselbe Konto verwenden.
BLUESKY_APP_PASSWORD, PASSWORT_VARIABLE = get_app_password(
    "BLUESKY_FEED_APP_PASSWORD",
    "SKYRELAY_FEED_APP_PASSWORD",  # früherer Name, weiterhin akzeptiert
    "BLUESKY_APP_PASSWORD")

# SKYRELAY_DRY_RUN=1 -> Instagram abfragen und Medien laden, aber nichts
# veröffentlichen und die Merkliste nicht fortschreiben. Zum gefahrlosen Prüfen
# der Einrichtung auf beliebigen Rechnern.
DRY_RUN = os.environ.get("SKYRELAY_DRY_RUN") == "1"
if DRY_RUN:
    log("SKYRELAY_DRY_RUN=1 gesetzt - es wird NICHTS auf Bluesky veröffentlicht.")

if not BLUESKY_APP_PASSWORD and not DRY_RUN:
    log(f"Fehler: Kein App-Passwort für das Feed-Konto @{BLUESKY_HANDLE} gesetzt.")
    log('Nutzt der Feed ein EIGENES Konto (anders als der Ticker):')
    log('    export BLUESKY_FEED_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log('Nutzen Ticker und Feed dasselbe Konto, genügt:')
    log('    export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log("Achtung: cron liest ~/.bashrc nicht - dort die Variablen oben in die")
    log("crontab schreiben (ohne Anführungszeichen).")
    sys.exit(1)

_fehlend = [n for n, w in (("[feed] instagram_profile", INSTA_USER),
                           ("[feed] instagram_session_user", INSTA_BOT_USER),
                           ("bluesky_handle", BLUESKY_HANDLE))
            if not w or w.startswith("dein")]
if _fehlend:
    log(f"Fehler: In {os.path.basename(CONFIG_FILE)} fehlen noch Angaben: {', '.join(_fehlend)}")
    sys.exit(1)

os.makedirs(TMP_DIR, exist_ok=True)

# 1. Bereits gepostete Shortcodes laden
posted_shortcodes = set()
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        posted_shortcodes = set(f.read().splitlines())

# 2. Instaloader initialisieren + Session laden
L = instaloader.Instaloader(
    dirname_pattern=os.path.join(TMP_DIR, "{target}"),
    download_videos=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

log(f"Lade bestehende Instagram-Session für {INSTA_BOT_USER}...")
try:
    L.load_session_from_file(INSTA_BOT_USER)
    log("Session erfolgreich geladen!")
except Exception as e:
    log(f"Fehler beim Laden der Session-Datei: {e}")
    sys.exit(1)

# 3. Instagram-Profil & letzte Posts abrufen
log(f"Rufe Profil @{INSTA_USER} direkt von Instagram ab...")
try:
    profile = instaloader.Profile.from_username(L.context, INSTA_USER)
    posts_iterator = profile.get_posts()

    latest_posts = []
    for _ in range(POSTS_TO_CHECK):
        try:
            latest_posts.append(next(posts_iterator))
        except StopIteration:
            break

    latest_posts.reverse()

except Exception as e:
    log(f"Instagram-Abfrage fehlgeschlagen: {e}")
    sys.exit(1)

# 4. Hilfsfunktionen (PDS-DID-Auflösung, Bild-Kompression, Text-Splitting, Video-Upload)
def split_caption(caption, first_limit, follow_limit):
    """Zerlegt die Caption in Chunks. textwrap kann mit break_long_words=False Chunks
    liefern, die länger als die Zielbreite sind (z.B. lange URLs/Hashtag-Ketten) -
    deshalb wird hier zusätzlich hart auf das Limit gekürzt."""
    wrapped = textwrap.wrap(caption, width=first_limit, break_long_words=False, replace_whitespace=False)
    if not wrapped:
        return []

    raw_chunks = [wrapped[0]]
    remaining_text = caption[len(wrapped[0]):].strip()
    if remaining_text:
        raw_chunks.extend(textwrap.wrap(remaining_text, width=follow_limit, break_long_words=False, replace_whitespace=False))

    chunks = []
    for chunk in raw_chunks:
        limit = first_limit if not chunks else follow_limit
        while len(chunk) > limit:
            chunks.append(chunk[:limit])
            chunk = chunk[limit:].lstrip()
            limit = follow_limit
        if chunk:
            chunks.append(chunk)
    return chunks


def natural_sort_key(path):
    """Sortierschlüssel, der Zahlen in Dateinamen numerisch behandelt - damit sortiert
    instaloaders ..._2.jpg korrekt vor ..._10.jpg (alphabetisch käme _10 zuerst)."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", os.path.basename(path))]


def build_alt_text(caption, suffix=""):
    """Erzeugt einen Alt-Text aus der Caption (Barrierefreiheit) statt eines generischen Platzhalters."""
    base = caption.strip() if caption else ALT_TEXT_FALLBACK
    if len(base) > 200:
        base = base[:200].rstrip() + "…"
    return base + suffix


def baue_beitragstext(inhalt, index, gesamt, quell_url):
    """Baut den Text eines einzelnen Beitrags im Thread - Kopfbereich im ersten,
    Dauer-Hashtag im letzten, Zähler dazwischen.

    Bewusst eine einzige Stelle: Der Trockenlauf zeigt damit genau das, was der
    echte Lauf senden würde, statt einer nachgebauten Näherung."""
    tb = client_utils.TextBuilder()
    if index == 0:
        build_source_line(tb, POST_PREFIX, FEED_SOURCE_LABEL, quell_url)
        tb.text(inhalt)
        if gesamt > 1:
            tb.text(f" (1/{gesamt})")
    else:
        tb.text(f"{inhalt} ({index + 1}/{gesamt})")
    if index == gesamt - 1 and STANDING_HASHTAG:
        tb.text("\n\n")
        tb.tag(f"#{STANDING_HASHTAG}", STANDING_HASHTAG)
    return tb


client = None
new_posts_count = 0
nachreichen_erledigt = DRY_RUN  # im Trockenlauf geht nichts nach außen


def melde_an():
    """Meldet bei Bluesky an und holt beim ersten Mal alle Videos nach, deren
    Upload in früheren Läufen scheiterte. Die Anmeldung erfolgt bewusst spät und
    nur einmal je Lauf: Bluesky begrenzt die Anmeldungen pro Konto und Tag."""
    global client, nachreichen_erledigt

    # Wichtig: `client` erst NACH erfolgreicher Anmeldung setzen. Sonst bliebe
    # nach einem gescheiterten Login ein unangemeldetes Objekt zurück, die
    # Bedingung wäre nie wieder wahr, und alle folgenden Beiträge liefen ohne
    # Anmeldung ins Leere ("AuthMissing").
    if client is None:
        log("Verbinde mit Bluesky...")
        verbindung = Client()
        try:
            log_in_to_bluesky(verbindung, BLUESKY_HANDLE, BLUESKY_APP_PASSWORD,
                                 PASSWORT_VARIABLE)
        except Exception:
            # Ohne Anmeldung lässt sich nichts veröffentlichen, und weitere
            # Versuche würden nur das Anmeldelimit aufbrauchen (Bluesky erlaubt
            # 10 Anmeldungen pro Tag und Konto).
            log("Ohne Anmeldung kann nichts veröffentlicht werden - Lauf wird beendet.")
            log("Die noch nicht übernommenen Beiträge bleiben offen und werden")
            log("beim nächsten Lauf erneut versucht.")
            sys.exit(1)
        client = verbindung

    if not nachreichen_erledigt:
        nachreichen_erledigt = True
        post_stashed_videos(client, VIDEO_RETRY_DIR, VIDEO_RETRY_TEXT,
                           VIDEO_RETRY_MAX_ATTEMPTS, MAX_VIDEO_BYTES,
                           VIDEO_JOB_TIMEOUT_SECONDS)
    return client


# Warten Videos aus früheren Läufen, lohnt die Anmeldung auch ohne neuen Beitrag.
if not DRY_RUN and os.path.isdir(VIDEO_RETRY_DIR) and any(
        name.endswith(".json") for name in os.listdir(VIDEO_RETRY_DIR)):
    melde_an()

for post in latest_posts:
    if post.shortcode in posted_shortcodes:
        continue

    new_posts_count += 1
    log(f"\n[NEUER POST] Verarbeite Beitrag: {post.shortcode}")

    try:
        # 5. Medien-Download & Video-/Sidecar-Erkennung
        L.download_post(post, target=INSTA_USER)

        jpg_files = sorted(glob.glob(os.path.join(TMP_DIR, INSTA_USER, "*.jpg")), key=natural_sort_key)
        mp4_files = sorted(glob.glob(os.path.join(TMP_DIR, INSTA_USER, "*.mp4")), key=natural_sort_key)
        log(f"[DEBUG] typename={post.typename}, is_video={post.is_video}, jpgs={len(jpg_files)}, mp4s={len(mp4_files)}")

        is_video_post = post.is_video       # True nur bei einem einzelnen Reel/Video-Post
        is_multi_video_post = False         # True bei einem Karussell, dessen Elemente ALLE Videos sind
        is_mixed_post = False               # True bei einem Karussell aus Bildern UND Videos
        video_pairs = []                    # [(mp4_path, thumb_jpg_path), ...] für Karussell-Videos
        mixed_image_files = []              # jpg-Pfade der echten Bild-Elemente eines gemischten Karussells

        if is_video_post and not mp4_files:
            # Instaloaders eigener Video-Download liefert hier aus unbekanntem Grund
            # keine .mp4, obwohl post.video_url eine gültige URL zurückgibt.
            # Workaround: Video manuell direkt über diese URL herunterladen.
            log("⚠️ Instaloader hat keine .mp4 heruntergeladen. Versuche manuellen Direkt-Download über post.video_url...")
            try:
                video_url_direct = post.video_url
                log(f"[DEBUG] post.video_url = {video_url_direct}")

                video_target_path = os.path.join(TMP_DIR, INSTA_USER, f"{post.shortcode}.mp4")
                video_response = requests.get(video_url_direct, stream=True, timeout=60)
                video_response.raise_for_status()
                with open(video_target_path, "wb") as vf:
                    for chunk in video_response.iter_content(chunk_size=1024 * 1024):
                        vf.write(chunk)

                mp4_files = [video_target_path]
                log(f"✓ Video manuell heruntergeladen: {os.path.basename(video_target_path)}")
            except Exception as vid_dl_err:
                log(f"[DEBUG] Manueller Video-Download fehlgeschlagen: {vid_dl_err}")
                traceback.print_exc()
                log("   -> Video-Upload wird in Block 9 übersprungen, Cover-Bild-Fallback greift.")
        elif mp4_files:
            log(f"Video-Datei gefunden: {os.path.basename(mp4_files[0])}")

        if post.typename == "GraphSidecar":
            try:
                sidecar_nodes = list(post.get_sidecar_nodes())
                video_indices = [idx for idx, n in enumerate(sidecar_nodes) if n.is_video]
                log(f"[DEBUG] Sidecar mit {len(sidecar_nodes)} Elementen, davon {len(video_indices)} als Video markiert.")

                if video_indices:
                    # Annahme in beiden Fällen: jpg_files liegen in derselben Reihenfolge vor
                    # wie sidecar_nodes (Instaloader benennt sie fortlaufend _1, _2, ...).
                    if len(video_indices) == len(sidecar_nodes):
                        # Alle Elemente sind Videos -> als Multi-Video-Post behandeln.
                        is_multi_video_post = True
                        log(f"Multi-Video-Post erkannt: {len(sidecar_nodes)} Videos. Lade einzeln herunter...")
                    else:
                        # Gemischtes Karussell: Bilder kommen in den Hauptpost,
                        # danach folgt je ein Video als eigener Antwort-Post im Thread.
                        is_mixed_post = True
                        mixed_image_files = [jpg_files[idx] for idx, n in enumerate(sidecar_nodes)
                                             if not n.is_video and idx < len(jpg_files)]
                        log(f"Gemischtes Karussell erkannt: {len(mixed_image_files)} Bild(er) + "
                            f"{len(video_indices)} Video(s). Bilder in den Hauptpost, Videos je einzeln als Antwort.")

                    for idx, node in enumerate(sidecar_nodes, start=1):
                        if not node.is_video:
                            continue
                        try:
                            video_target_path = os.path.join(TMP_DIR, INSTA_USER, f"{post.shortcode}_{idx}.mp4")
                            vresp = requests.get(node.video_url, stream=True, timeout=60)
                            vresp.raise_for_status()
                            with open(video_target_path, "wb") as vf:
                                for chunk in vresp.iter_content(chunk_size=1024 * 1024):
                                    vf.write(chunk)
                            thumb_path = jpg_files[idx - 1] if idx - 1 < len(jpg_files) else None
                            video_pairs.append((video_target_path, thumb_path))
                            log(f"✓ Video {len(video_pairs)}/{len(video_indices)} heruntergeladen.")
                        except Exception as sc_vid_err:
                            log(f"⚠️ Sidecar-Video (Element {idx}) konnte nicht heruntergeladen werden: {sc_vid_err}")
            except Exception as sidecar_err:
                log(f"[DEBUG] Sidecar-Analyse fehlgeschlagen: {sidecar_err}")

        # 6. Text-Splitting & Instagram-URL
        caption = post.caption if post.caption else ""
        # Erster Post trägt zusätzlich Präfix + Quell-Link (~75 Zeichen) und muss unter
        # Blueskys 300-Zeichen-Limit bleiben -> bewusst kleiner als die Folge-Chunks.
        first_chunk_length = 180
        follow_chunk_length = 240

        text_chunks = split_caption(caption, first_chunk_length, follow_chunk_length)
        if not text_chunks:
            if is_video_post or is_multi_video_post:
                text_chunks = [VIDEO_PLACEHOLDER]
            elif is_mixed_post:
                text_chunks = [MIXED_PLACEHOLDER]
            else:
                text_chunks = [IMAGE_PLACEHOLDER]

        alt_text = build_alt_text(caption)

        if is_video_post:
            insta_url = f"https://www.instagram.com/reel/{post.shortcode}/"
        else:
            insta_url = f"https://www.instagram.com/p/{post.shortcode}/"

        # 7. Bilder vorbereiten (für reine Bild-Posts alle jpgs; bei gemischten Karussells nur
        # die echten Bild-Elemente - die jpgs der Video-Elemente sind nur deren Cover und dienen
        # in Block 9 als Fallback; bei reinen Video-Posts gar keine)
        bluesky_images = []

        if is_mixed_post:
            image_source_files = mixed_image_files
        elif not is_video_post and not is_multi_video_post:
            image_source_files = jpg_files
        else:
            image_source_files = []

        if image_source_files:
            log("Bereite Bilder für Bluesky vor (inkl. Komprimierung)...")
            for img_path in image_source_files:
                try:
                    bluesky_images.append(compress_image_for_bluesky(img_path))
                except Exception as e:
                    log(f"Fehler beim Verarbeiten von {img_path}: {e}")

        image_chunks = [bluesky_images[i:i + 4] for i in range(0, len(bluesky_images), 4)] if bluesky_images else []

        # 7b. Trockenlauf: Alles bis hierher ist gelaufen (Abruf, Medien-Download,
        # Textaufbereitung) - ab hier ginge es nach außen. Also nur berichten,
        # was veröffentlicht würde, und weder anmelden noch die Merkliste
        # fortschreiben. So bleibt der Beitrag für den echten Lauf erhalten.
        if DRY_RUN:
            medien = []
            if bluesky_images:
                medien.append(f"{len(bluesky_images)} Bild(er)")
            # Dieselbe Bedingung wie beim echten Lauf weiter unten - vorher fehlte
            # hier das gemischte Karussell, sodass dessen Videos unterschlagen wurden.
            anzahl_videos = (len(video_pairs)
                             if (is_multi_video_post or is_mixed_post) and video_pairs
                             else 1 if (is_video_post and mp4_files) else 0)
            if anzahl_videos:
                medien.append(f"{anzahl_videos} Video(s)")
            # So viele Beiträge, wie der echte Lauf anlegen würde: Text-Abschnitte
            # gegen Medien-Plätze (Bildgruppen im Hauptpost, je ein Video danach).
            vorschau_gesamt = max(len(text_chunks), len(image_chunks) + anzahl_videos)
            log(f"   [DRY_RUN] Würde auf @{BLUESKY_HANDLE} posten: "
                f"{vorschau_gesamt} Beitrag/Beiträge"
                + (f", {', '.join(medien)}" if medien else ", ohne Medien"))
            show_preview([
                baue_beitragstext(text_chunks[i] if i < len(text_chunks)
                                  else "Weitere Inhalte...",
                                  i, vorschau_gesamt, insta_url)
                for i in range(vorschau_gesamt)])
            continue

        # 8. Bluesky-Verbindung herstellen (erst wenn wirklich gebraucht).
        # Die Server-Adresse für den Video-Upload ermittelt das gemeinsame Modul.
        melde_an()

        # 9. Video-Uploads zur Bluesky-Video-API (einzeln oder mehrfach bei Multi-Video-Sidecar)
        # Priorität: Video. Backup bei Fehlschlag: das jeweilige Cover-Bild.
        video_embeds = []
        nachreich_slots = {}  # Index in video_embeds -> offener Video-Vorgang

        videos_to_process = []
        if (is_multi_video_post or is_mixed_post) and video_pairs:
            videos_to_process = video_pairs
        elif is_video_post and mp4_files:
            videos_to_process = [(mp4_files[0], jpg_files[0] if jpg_files else None)]

        for idx, (v_path, v_thumb) in enumerate(videos_to_process, start=1):
            log(f"Starte Video-Upload {idx}/{len(videos_to_process)}: {os.path.basename(v_path)}")
            try:
                embed = upload_video_to_bluesky(client, v_path, os.path.basename(v_path),
                                                MAX_VIDEO_BYTES, VIDEO_JOB_TIMEOUT_SECONDS)
                video_embeds.append(embed)
                log(f"✓ Video {idx}/{len(videos_to_process)} erfolgreich eingebettet.")
            except Exception as video_err:
                log(f"⚠️ Fehler beim Video-Upload {idx}/{len(videos_to_process)}: {video_err}")
                traceback.print_exc()

                # Beitrag geht gleich mit dem Cover-Bild raus, das Video bleibt
                # für einen späteren Lauf liegen (siehe post_stashed_videos).
                kennung = f"{post.shortcode}_{idx}"
                if stash_video(VIDEO_RETRY_DIR, kennung, v_path):
                    nachreich_slots[len(video_embeds)] = (
                        kennung, os.path.basename(v_path),
                        build_alt_text(caption, " (Video)"))

                if v_thumb:
                    try:
                        log(f"   Nutze Cover-Bild als Fallback für Video {idx}.")
                        fallback_bytes = compress_image_for_bluesky(v_thumb)
                        fb_upload = client.upload_blob(fallback_bytes)
                        video_embeds.append(models.AppBskyEmbedImages.Main(
                            images=[models.AppBskyEmbedImages.Image(
                                alt=build_alt_text(caption, " (Video-Vorschau)"),
                                image=fb_upload.blob
                            )]
                        ))
                    except Exception as fb_err:
                        log(f"   ⚠️ Fallback-Bild fehlgeschlagen: {fb_err}")
                        video_embeds.append(None)
                else:
                    log(f"   Kein Cover-Bild für Video {idx} vorhanden, poste ohne Medien-Embed.")
                    video_embeds.append(None)

        # 10. Bluesky-Thread erstellen (Haupt- und Folge-Posts)
        # Medien-Reihenfolge im Thread: Bild-Gruppen zuerst (Hauptpost), danach je ein Video
        # pro Folge-Post. Bei reinen Bild- bzw. Video-Posts ist eine der Listen leer,
        # das Verhalten bleibt dort also unverändert.
        media_slots = [("images", chunk) for chunk in image_chunks]
        media_slots += [("video", v) for v in video_embeds]

        total_posts = max(len(text_chunks), len(media_slots))
        root_ref = None
        parent_ref = None

        for i in range(total_posts):
            is_first = (i == 0)

            current_text = text_chunks[i] if i < len(text_chunks) else "Weitere Inhalte..."

            embed = None
            if i < len(media_slots):
                slot_kind, slot_payload = media_slots[i]
                if slot_kind == "video" and slot_payload is not None:
                    embed = slot_payload
                    log(f"✓ Binde Video-Embed {i+1}/{total_posts} ein.")
                elif slot_kind == "images":
                    uploaded_images = []
                    for img_data in slot_payload:
                        upload = client.upload_blob(img_data)
                        uploaded_images.append(models.AppBskyEmbedImages.Image(alt=alt_text, image=upload.blob))
                    if uploaded_images:
                        embed = models.AppBskyEmbedImages.Main(images=uploaded_images)
                        log(f"✓ Binde Bild-Embed ({len(uploaded_images)} Bild(er)) {i+1}/{total_posts} ein.")

            tb = baue_beitragstext(current_text, i, total_posts, insta_url)

            if is_first:
                log(f"Sende Hauptpost für {post.shortcode}...")
                root_post = client.send_post(text=tb, embed=embed)
                root_ref = models.ComAtprotoRepoStrongRef.Main(cid=root_post.cid, uri=root_post.uri)
                parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=root_post.cid, uri=root_post.uri)

                # Shortcode sofort nach dem Hauptpost loggen - schlägt ein Folge-Post fehl,
                # würde der Beitrag sonst beim nächsten Lauf komplett dupliziert.
                with open(STATE_FILE, "a", encoding="utf-8") as f:
                    f.write(post.shortcode + "\n")
                posted_shortcodes.add(post.shortcode)
            else:
                log(f"Sende Thread-Post {i+1}/{total_posts}...")
                reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref, root=root_ref)
                reply_record = models.AppBskyFeedPost.Record(
                    text=tb.build_text(),
                    facets=tb.build_facets(),
                    embed=embed,
                    reply=reply_ref,
                    created_at=client.get_current_time_iso()
                )
                current_reply = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=client.me.did,
                        collection=models.ids.AppBskyFeedPost,
                        record=reply_record
                    )
                )
                parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=current_reply.cid, uri=current_reply.uri)

            # parent_ref zeigt jetzt auf den eben erzeugten Beitrag. Steckt in
            # diesem Slot ein offenes Video, ist das sein Antwortziel.
            if i < len(media_slots) and media_slots[i][0] == "video":
                offen = nachreich_slots.get(i - len(image_chunks))
                if offen:
                    kennung, dateiname, video_alt = offen
                    stash_retry_target(VIDEO_RETRY_DIR, kennung, client.me.did,
                                         root_ref.uri, root_ref.cid,
                                         parent_ref.uri, parent_ref.cid,
                                         dateiname, video_alt)

        log(f"Post {post.shortcode} erfolgreich auf Bluesky veröffentlicht!")

    except Exception as e:
        log(f"Fehler bei Post {post.shortcode}: {e}")
        traceback.print_exc()

    finally:
        # 11. Abschluss: Aufräumen (läuft auch bei Fehlern, räumt auch Unterordner weg)
        shutil.rmtree(os.path.join(TMP_DIR, INSTA_USER), ignore_errors=True)

    # Kurze Pause, um Bluesky-Rate-Limits und auffällige Zugriffsmuster zu vermeiden
    time.sleep(PAUSE_BETWEEN_POSTS_SECONDS)

# 12. Zusammenfassung
if new_posts_count == 0:
    log(f"Keine neuen Posts gefunden - die letzten {len(latest_posts)} Beiträge sind bereits auf Bluesky.")
else:
    log(f"Lauf abgeschlossen: {new_posts_count} neue(r) Beitrag/Beiträge verarbeitet.")
