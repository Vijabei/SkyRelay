"""
SkyRelay - Feed: spiegelt ein Instagram-Profil nach Bluesky.

Im Gegensatz zum Spieltags-Ticker läuft dieses Programm im Dauerbetrieb: Es
prüft bei jedem Aufruf die letzten Beiträge des Profils und überträgt alles,
was noch nicht übernommen wurde. Gedacht für einen regelmäßigen Start per cron.

Alle Einstellungen stehen im Abschnitt [feed] von "skyrelay.conf"
(Vorlage: skyrelay.conf.example). Ein abweichender Pfad lässt sich über die
Umgebungsvariable SKYRELAY_CONFIG angeben.

Zugangsdaten kommen ausschließlich aus Umgebungsvariablen:
    BLUESKY_APP_PASSWORD        App-Passwort des Bluesky-Kontos
    SKYRELAY_FEED_APP_PASSWORD  nur nötig, wenn [feed] bluesky_handle ein
                                anderes Konto als [bluesky] handle verwendet

Die Instagram-Sitzung wird einmalig außerhalb dieses Programms angelegt:
    venv/bin/instaloader -l <zweitkonto>

Beispiel für cron (alle 15 Minuten):
    */15 * * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-feed.py >/dev/null 2>&1
"""

import configparser
import os
import re
import sys
import glob
import shutil
import threading
import instaloader
from atproto import Client, models, client_utils
from PIL import Image
import io
import textwrap
import time
import requests
import traceback
from atproto_client.models.blob_ref import BlobRef


def log(*args, **kwargs):
    """print mit vorangestelltem Zeitstempel - für nachvollziehbare Logs (z.B. aus cron)."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]", *args, **kwargs, flush=True)


# Der frühere web_profile_info-Monkey-Patch für den GraphQL-400-Fehler ist obsolet:
# instaloader >= 4.15.1 (PR #2652, released 21.03.2026) holt Profil-Metadaten über neue
# GraphQL-Endpoints - der alte web_profile_info-Endpoint liefert inzwischen selbst Fehler
# (429 / useragent mismatch) und darf nicht mehr verwendet werden.
_instaloader_version = tuple(int(p) for p in instaloader.__version__.split(".")[:3] if p.isdigit())
if _instaloader_version < (4, 15, 1):
    log(f"⚠️ instaloader {instaloader.__version__} ist veraltet und wird an Instagrams "
        f"GraphQL-Änderungen scheitern. Bitte aktualisieren: pip install -U instaloader")

# =============================== KONFIGURATION ===============================
# TODO(Auslagerung): Konfigurations- und Protokoll-Bausteine sind mit
# skyrelay-matchday.py nahezu identisch. Sobald beide Programme gemeinsame
# Bausteine teilen, gehören sie in ein eigenes Modul - die Doppelung hier ist
# bewusst und vorübergehend.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("SKYRELAY_CONFIG") or os.path.join(BASE_DIR, "skyrelay.conf")

if not os.path.exists(CONFIG_FILE):
    print(f"Fehler: Konfigurationsdatei nicht gefunden: {CONFIG_FILE}\n"
          f"Vorlage kopieren und anpassen:\n"
          f"    cp skyrelay.conf.example skyrelay.conf",
          file=sys.stderr)
    sys.exit(1)

_cfg = configparser.ConfigParser(interpolation=None)
try:
    with open(CONFIG_FILE, encoding="utf-8") as _f:
        _cfg.read_file(_f)
except Exception as _e:
    print(f"Fehler beim Lesen von {CONFIG_FILE}: {_e}", file=sys.stderr)
    sys.exit(1)


def cfg(section, key, default=None):
    return _cfg.get(section, key, fallback=default)


def cfg_int(section, key, default):
    try:
        return int(str(_cfg.get(section, key, fallback=default)).strip())
    except (ValueError, TypeError):
        return default


def cfg_bool(section, key, default):
    return _cfg.getboolean(section, key, fallback=default)


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
STANDING_HASHTAG = cfg("post", "standing_hashtag", "").strip().lstrip("#")

MAX_VIDEO_BYTES = cfg_int("limits", "max_video_bytes", 100_000_000)
VIDEO_JOB_TIMEOUT_SECONDS = cfg_int("limits", "video_job_timeout_seconds", 600)

LOG_TO_FILE = cfg_bool("logging", "to_file", True)
LOG_MAX_BYTES = cfg_int("logging", "max_bytes", 2_000_000)
LOG_BACKUP_COUNT = cfg_int("logging", "backup_count", 5)

STATE_FILE = os.path.join(BASE_DIR, cfg("feed", "state", "skyrelay_feed_posted.txt"))
LOG_FILE = os.path.join(BASE_DIR, cfg("feed", "log", "skyrelay-feed.log"))
TMP_DIR = os.path.join(BASE_DIR, "tmp")


def rotate_log(path, max_bytes, backups):
    """Rotiert die Protokolldatei beim Start, wenn sie zu groß geworden ist."""
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
        print(f"⚠️ Protokoll-Rotation fehlgeschlagen: {e}", flush=True)


def start_file_logging(path):
    """Schreibt alle Ausgaben zusätzlich in eine Datei und weiterhin auf die
    Konsole (wie "tee"), unabhängig davon, wie das Programm gestartet wurde."""
    try:
        rotate_log(path, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
        logfile = open(path, "ab", buffering=0)
        read_fd, write_fd = os.pipe()
        console_fd = os.dup(1)
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
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except Exception as e:
        print(f"⚠️ Datei-Protokoll konnte nicht gestartet werden: {e}", flush=True)


if LOG_TO_FILE:
    start_file_logging(LOG_FILE)
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
# Eigenes Konto für den Feed -> eigenes Passwort möglich.
BLUESKY_APP_PASSWORD = (os.environ.get("SKYRELAY_FEED_APP_PASSWORD")
                        or os.environ.get("BLUESKY_APP_PASSWORD"))
if not BLUESKY_APP_PASSWORD:
    log("Fehler: Umgebungsvariable BLUESKY_APP_PASSWORD ist nicht gesetzt.")
    log('Setzen z.B. mit:  export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log("(dauerhaft in ~/.bashrc oder in der crontab-Zeile vor dem python-Aufruf)")
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


def compress_image_for_bluesky(img_path, max_dim=2000, max_bytes=1_500_000, start_quality=85):
    """Komprimiert ein Bild so, dass es unter Blueskys Blob-Größenlimit passt."""
    img = Image.open(img_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    quality = start_quality
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=quality)

    while buf.tell() > max_bytes and quality > 50:
        buf = io.BytesIO()
        quality -= 10
        img.save(buf, format='JPEG', quality=quality)

    return buf.getvalue()


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


def upload_video_to_bluesky(client, pds_aud, video_path):
    """Lädt eine einzelne Video-Datei zu Bluesky hoch und liefert das fertige Embed-Objekt zurück
    (ohne Cover-Thumbnail - das Video steht für sich). Wirft eine Exception bei Fehlschlag -
    der Aufrufer kümmert sich um den Bild-Fallback."""
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise RuntimeError(
            f"Video ist {len(video_bytes)} Bytes groß und überschreitet das Bluesky-Limit "
            f"von {MAX_VIDEO_BYTES} Bytes - Upload wird gar nicht erst versucht."
        )

    service_auth = client.com.atproto.server.get_service_auth({
        'aud': pds_aud,
        'lxm': 'com.atproto.repo.uploadBlob',
        'exp': int(time.time()) + 60 * 15
    })
    token = service_auth.token

    upload_url = "https://video.bsky.app/xrpc/app.bsky.video.uploadVideo"
    upload_params = {"did": client.me.did, "name": os.path.basename(video_path)}
    upload_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(len(video_bytes)),
    }

    log(f"Sende Videodaten an: {upload_url} ({len(video_bytes)} Bytes)")

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
                # Video wurde in einem früheren Lauf bereits hochgeladen und fertig verarbeitet.
                # Die bestehende Job-ID steckt direkt in der Konflikt-Antwort - die nutzen wir weiter,
                # statt das komplette Video nochmal hochzuladen.
                conflict_data = upload_response.json()
                if conflict_data.get("error") == "already_exists" and conflict_data.get("jobId"):
                    job_id = conflict_data["jobId"]
                    log(f"ℹ️ Video wurde bereits verarbeitet, verwende bestehende Job-ID: {job_id}")
                    break

            upload_response.raise_for_status()
            break
        except requests.exceptions.HTTPError as http_err:
            body_preview = upload_response.text[:500] if upload_response is not None else "(keine Antwort)"
            log(f"⚠️ Upload-Versuch {attempt}/{max_attempts} fehlgeschlagen: {http_err}")
            log(f"   Server-Antwort: {body_preview}")
            if attempt == max_attempts:
                raise
            wait_seconds = 10 * attempt
            log(f"   Warte {wait_seconds}s vor dem nächsten Versuch...")
            time.sleep(wait_seconds)

    if job_id is None:
        status_data = upload_response.json()
        job_status_obj = status_data.get("jobStatus", status_data)
        job_id = job_status_obj.get("jobId")
        log(f"✓ Video erfolgreich übertragen! Job-ID erhalten: {job_id}")

    log("Warte auf Server-Verarbeitung...")

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
            log("✓ Video-Verarbeitung auf dem Bluesky-Server abgeschlossen!")
            break
        elif job_state == "JOB_STATE_FAILED":
            error_msg = current_job_obj.get("error", "Unbekannter Rendering-Fehler")
            raise RuntimeError(f"Bluesky-Server meldet Fehler beim Video-Rendering: {error_msg}")
        else:
            log(f"⏳ Status: {job_state}... (Warte 5 Sekunden)")
            time.sleep(5)

    video_blob = models.get_or_create(video_blob_dict, model=BlobRef)
    return models.AppBskyEmbedVideo.Main(video=video_blob)

client = None
pds_aud = None
new_posts_count = 0

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

        # 8. Bluesky-Verbindung herstellen (lazy) + PDS-DID auflösen
        if client is None:
            log("Verbinde mit Bluesky...")
            client = Client()
            client.login(BLUESKY_HANDLE, BLUESKY_APP_PASSWORD)

        if pds_aud is None:
            log("Löse eigene PDS-DID auf (für Video-Upload-Auth nötig)...")
            try:
                pds_aud = resolve_pds_did_web(client.me.did)
                log(f"✓ PDS-DID ermittelt: {pds_aud}")
            except Exception as pds_err:
                log(f"⚠️ Konnte PDS-DID nicht auflösen: {pds_err}")
                pds_aud = "did:web:bsky.social"

        # 9. Video-Uploads zur Bluesky-Video-API (einzeln oder mehrfach bei Multi-Video-Sidecar)
        # Priorität: Video. Backup bei Fehlschlag: das jeweilige Cover-Bild.
        video_embeds = []

        videos_to_process = []
        if (is_multi_video_post or is_mixed_post) and video_pairs:
            videos_to_process = video_pairs
        elif is_video_post and mp4_files:
            videos_to_process = [(mp4_files[0], jpg_files[0] if jpg_files else None)]

        for idx, (v_path, v_thumb) in enumerate(videos_to_process, start=1):
            log(f"Starte Video-Upload {idx}/{len(videos_to_process)}: {os.path.basename(v_path)}")
            try:
                embed = upload_video_to_bluesky(client, pds_aud, v_path)
                video_embeds.append(embed)
                log(f"✓ Video {idx}/{len(videos_to_process)} erfolgreich eingebettet.")
            except Exception as video_err:
                log(f"⚠️ Fehler beim Video-Upload {idx}/{len(videos_to_process)}: {video_err}")
                traceback.print_exc()
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
            is_last = (i == total_posts - 1)

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

            tb = client_utils.TextBuilder()
            if is_first:
                tb.text("⚽ [Inoffizieller Bot]\n🔗 Quelle: ")
                tb.link(insta_url, insta_url)
                tb.text(f"\n\n{current_text}")
                if total_posts > 1:
                    tb.text(f" (1/{total_posts})")
            else:
                tb.text(f"{current_text} ({i+1}/{total_posts})")

            if is_last and STANDING_HASHTAG:
                tb.text("\n\n")
                tb.tag(f"#{STANDING_HASHTAG}", STANDING_HASHTAG)

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
