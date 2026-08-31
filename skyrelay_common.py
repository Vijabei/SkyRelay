"""
SkyRelay - shared building blocks

Everything skyrelay-matchday.py and skyrelay-feed.py both need: logging,
configuration, image preparation and the video upload to Bluesky. That way each
of those jobs has exactly one place.

Imported by both programs; not meant to be run on its own.
"""

import atexit
import configparser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

import requests
from PIL import Image
from atproto import models
from atproto_client.models.blob_ref import BlobRef

# The configuration tools live in their own module, deliberately without third
# party imports - that way the setup assistant can use them before atproto and
# Pillow are installed.
from skyrelay_config import config_path


# ------------------------------------------------------------------- logging
def log(*args, **kwargs):
    """print with a leading timestamp - for logs one can follow later (from
    cron, for instance, where the start time would otherwise be a mystery)."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]", *args, **kwargs, flush=True)


def rotate_log(path, max_bytes, backups):
    """Rotates the log file at startup once it has grown too large:
    file.log -> file.log.1 -> ... -> file.log.N (the oldest one is dropped)."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return
        oldest = f"{path}.{backups}"
        if os.path.exists(oldest):
            os.remove(oldest)
        for i in range(backups - 1, 0, -1):
            source = f"{path}.{i}"
            if os.path.exists(source):
                os.replace(source, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
    except Exception as error:
        print(f"⚠️ Log rotation failed: {error}", flush=True)


def start_file_logging(path, max_bytes=2_000_000, backups=5):
    """Writes ALL output to a file as well - including that of neonize's Go
    part, which does not pass through Python and which an ordinary Python log
    would therefore miss. To catch it, stdout and stderr are redirected into a
    pipe; a background thread writes every line into the file AND to the real
    console (like "tee"). If that fails, the program carries on normally - just
    without a log file."""
    try:
        rotate_log(path, max_bytes, backups)
        logfile = open(path, "ab", buffering=0)
        read_fd, write_fd = os.pipe()
        console_fd = os.dup(1)  # a copy of the real console, before redirecting
        os.dup2(write_fd, 1)
        os.dup2(write_fd, 2)
        os.close(write_fd)

        def distribute():
            with os.fdopen(read_fd, "rb", 0) as pipe:
                for line in iter(pipe.readline, b""):
                    for target in (logfile.write, lambda b: os.write(console_fd, b)):
                        try:
                            target(line)
                        except Exception:
                            pass

        writer = threading.Thread(target=distribute, name="log-tee", daemon=True)
        writer.start()
        # Without a terminal stdout would be block buffered - line buffering
        # makes "tail -f" follow along immediately.
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)

        def on_exit():
            """Give the writer thread time to drain the pipe on the way out.
            Without this, output is lost whenever the program ends shortly after
            starting - with a message about the configuration, say, or with "no
            match today". Which is precisely the output one needs then."""
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            time.sleep(0.2)

        atexit.register(on_exit)
        return True
    except Exception as error:
        print(f"⚠️ Could not start file logging: {error}", flush=True)
        return False


# ------------------------------------------------------------- configuration
def load_config(base_dir):
    """Loads skyrelay.conf (or the path from SKYRELAY_CONFIG) and returns
    (cfg, cfg_int, cfg_bool, path). If the file is missing or unreadable, the
    program ends with a message one can act on."""
    path = config_path(base_dir)

    if not os.path.exists(path):
        print(f"Error: configuration file not found: {path}\n"
              f"The easiest way to create one is the assistant:\n"
              f"    venv/bin/python skyrelay-setup.py\n"
              f"or by hand:  cp skyrelay.conf.example skyrelay.conf",
              file=sys.stderr)
        sys.exit(1)

    # interpolation=None: otherwise configparser reads percent signs in texts.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as config:
            parser.read_file(config)
    except Exception as error:
        print(f"Error reading {path}: {error}", file=sys.stderr)
        sys.exit(1)

    def cfg(section, key, default=None):
        """A value from the configuration; the default applies if it is absent."""
        return parser.get(section, key, fallback=default)

    def cfg_int(section, key, default):
        try:
            return int(str(parser.get(section, key, fallback=default)).strip())
        except (ValueError, TypeError):
            return default

    def cfg_bool(section, key, default):
        return parser.getboolean(section, key, fallback=default)

    return cfg, cfg_int, cfg_bool, path


# ----------------------------------------------------------- building a post
def build_source_line(tb, prefix, label, url):
    """Writes the header line and the source of a main post into the
    TextBuilder - one place for both programs.

    Either part can be switched off on its own by leaving its value EMPTY, the
    way [post] standing_hashtag has always worked here. Note the difference to
    commenting the line out: for configparser a missing key does not mean "off",
    it means "the program's default applies". What is in effect right now is
    what --show-config reports.

    The feed used to have these lines hard-wired. That is why [post] prefix had
    no effect there and the bare URL showed up as the link text (#2)."""
    wrote_something = bool(prefix)
    if prefix:
        tb.text(prefix)
    if label and url:
        if prefix:
            tb.text("\n")
        tb.text("🔗 Quelle: ")
        tb.link(label, url)
        wrote_something = True
    if wrote_something:
        tb.text("\n\n")


def show_preview(builders):
    """Shows in a dry run how the posts would look on Bluesky - line by line,
    indented, with the character count per post.

    Without this a dry run leaves only a summary ("2 posts, 1 video"), and
    whether header, source and hashtags sit in the right place is visible only
    on the finished post - which is too late."""
    for number, tb in enumerate(builders, start=1):
        text = tb.build_text()
        log(f"   ┌─ post {number}/{len(builders)} ({len(text)} characters)")
        for line in text.split("\n"):
            log(f"   │ {line}")
        log("   └─")


# --------------------------------------------------------------------- login
def get_app_password(*names):
    """Returns (password, variable name) from the first environment variable
    that is set. The order goes from the specific name to the general one:
    whoever runs ticker and feed on separate accounts sets the matching
    variable, whoever uses a single account gets by with
    BLUESKY_APP_PASSWORD."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value, name
    return None, names[0]


def log_in_to_bluesky(client, handle, password, password_variable):
    """Logs in and, if that fails, explains what it can be. A bare 401 helps
    nobody - the usual cause is a password that does not belong to the account,
    because ticker and feed use separate ones."""
    if not password:
        log(f"Error: {password_variable} is not set.")
        log(f'Set it with:  export {password_variable}="xxxx-xxxx-xxxx-xxxx"')
        raise SystemExit(1)
    try:
        client.login(handle, password)
    except Exception as error:
        log(f"✗ Login to Bluesky as @{handle} failed: {error}")
        if "RateLimitExceeded" in str(error):
            log("   The login limit is used up: Bluesky allows 10 logins per")
            log("   day and account. Only waiting helps - further attempts do")
            log("   not extend the block, but they achieve nothing either.")
            match = re.search(r"['\"]ratelimit-reset['\"]:\s*['\"](\d+)['\"]", str(error))
            if match:
                free_at = datetime.fromtimestamp(int(match.group(1)))
                log(f"   Possible again from: {free_at.strftime('%d.%m.%Y %H:%M')} (local time)")
        elif "Invalid identifier or password" in str(error):
            log(f"   Does the app password in {password_variable} really belong")
            log(f"   to this very account? If ticker and feed run on separate")
            log(f"   accounts, they need separate passwords as well:")
            log(f"     ticker: BLUESKY_TICKER_APP_PASSWORD")
            log(f"     feed:   BLUESKY_FEED_APP_PASSWORD")
            log("   Careful: only 10 login attempts per day - do not just retry.")
        raise


# --------------------------------------------------------------------- media
def compress_image_for_bluesky(source, max_dim=2000, max_bytes=1_500_000,
                               start_quality=85):
    """Shrinks an image until it fits under Bluesky's size limit.
    Takes image data (bytes) or a file path."""
    image = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray))
                       else source)
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    if max(image.size) > max_dim:
        image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    quality = start_quality
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    while buffer.tell() > max_bytes and quality > 50:
        buffer = io.BytesIO()
        quality -= 10
        image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def resolve_pds_did_web(actor_did):
    """Works out the DID of an account's actual server (PDS) - needed as the
    'aud' of the service token for the video upload."""
    if actor_did.startswith("did:plc:"):
        response = requests.get(f"https://plc.directory/{actor_did}", timeout=15)
    elif actor_did.startswith("did:web:"):
        host = actor_did.split(":", 2)[2]
        response = requests.get(f"https://{host}/.well-known/did.json", timeout=15)
    else:
        raise ValueError(f"Unknown DID format: {actor_did}")

    response.raise_for_status()
    document = response.json()

    for service in document.get("service", []):
        if service.get("id") == "#atproto_pds":
            endpoint = service["serviceEndpoint"]
            host = endpoint.split("://", 1)[-1].rstrip("/")
            return f"did:web:{host}"

    raise ValueError(f"Server address not found in the DID document: {document}")


_pds_aud = None  # worked out once, reused afterwards


def upload_video_to_bluesky(client, video, filename,
                            max_bytes=100_000_000, timeout_seconds=600,
                            attempts=3):
    """Uploads a video to Bluesky and returns the finished embed. `video` is
    either image data (bytes) or a file path. Bluesky does not take videos as a
    plain attachment: first they are uploaded, then the server processes them,
    and only afterwards is the embed ready. On failure an exception is raised -
    the caller decides on the fallback (a thumbnail, for instance)."""
    global _pds_aud

    if not isinstance(video, (bytes, bytearray)):
        with open(video, "rb") as source:
            video = source.read()

    if len(video) > max_bytes:
        raise RuntimeError(
            f"The video is {len(video)} bytes and exceeds Bluesky's limit of "
            f"{max_bytes} bytes - the upload is not even attempted."
        )

    if _pds_aud is None:
        try:
            _pds_aud = resolve_pds_did_web(client.me.did)
            log(f"   ✓ Server address resolved: {_pds_aud}")
        except Exception as error:
            log(f"   ⚠️ Could not resolve the server address: {error}")
            _pds_aud = "did:web:bsky.social"

    service_auth = client.com.atproto.server.get_service_auth({
        "aud": _pds_aud,
        "lxm": "com.atproto.repo.uploadBlob",
        "exp": int(time.time()) + 60 * 15,
    })
    token = service_auth.token

    upload_url = "https://video.bsky.app/xrpc/app.bsky.video.uploadVideo"
    params = {"did": client.me.did, "name": filename}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(len(video)),
    }

    log(f"   Sending video data to: {upload_url} ({len(video)} bytes)")

    response = None
    job_id = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.post(upload_url, params=params, headers=headers,
                                     data=video, timeout=180)

            if response.status_code == 409:
                # Already uploaded and fully processed in an earlier run - then
                # carry on with the job that already exists.
                conflict = response.json()
                if conflict.get("error") == "already_exists" and conflict.get("jobId"):
                    job_id = conflict["jobId"]
                    log(f"   ℹ️ The video was already processed, using job: {job_id}")
                    break

            response.raise_for_status()
            break
        except requests.exceptions.HTTPError as error:
            excerpt = response.text[:500] if response is not None else "(no response)"
            log(f"   ⚠️ Upload attempt {attempt}/{attempts} failed: {error}")
            log(f"      Server response: {excerpt}")
            if attempt == attempts:
                raise
            wait = 10 * attempt
            log(f"      Waiting {wait}s before the next attempt...")
            time.sleep(wait)

    if job_id is None:
        data = response.json()
        job_id = data.get("jobStatus", data).get("jobId")
        log(f"   ✓ Video transferred, job: {job_id}")

    log("   Waiting for Bluesky to process it...")

    status_url = "https://video.bsky.app/xrpc/app.bsky.video.getJobStatus"
    deadline = time.time() + timeout_seconds
    blob_data = None
    while True:
        if time.time() > deadline:
            raise RuntimeError(
                f"Processing did not finish within {timeout_seconds}s "
                f"(job {job_id})."
            )
        status = requests.get(status_url, params={"jobId": job_id},
                              headers={"Authorization": f"Bearer {token}"}, timeout=30)
        status.raise_for_status()
        data = status.json()
        job = data.get("jobStatus", data)
        state = job.get("state")

        if state == "JOB_STATE_COMPLETED":
            blob_data = job.get("blob")
            log("   ✓ Processing finished.")
            break
        if state == "JOB_STATE_FAILED":
            raise RuntimeError("Bluesky reports an error while processing the video: "
                               + str(job.get("error", "unknown")))
        log(f"   ⏳ State: {state}... (waiting 5 seconds)")
        time.sleep(5)

    return models.AppBskyEmbedVideo.Main(
        video=models.get_or_create(blob_data, model=BlobRef)
    )


# ------------------------------------------------- handing in videos later on
#
# If a video upload fails - because Bluesky's video API is playing up, say -
# the post still goes out immediately with its thumbnail: on a live ticker,
# time is what counts. The video data stays here, and a later run attaches the
# video as a reply to that post. That way a temporary glitch does not leave a
# permanently picture-only post behind.
#
# Each pending job keeps two files in the retry folder:
#   <id>.mp4    the video data
#   <id>.json   where the reply goes - written only once the post exists and
#               its URI is known
# An .mp4 without its .json comes from an interrupted run: without a target
# there is nothing to hand in, so it is dropped on the next pass.

RETRY_VIDEO = ".mp4"
RETRY_INFO = ".json"


def _retry_paths(folder, job_id):
    base = os.path.join(folder, job_id)
    return base + RETRY_VIDEO, base + RETRY_INFO


def _remove_retry_files(*paths):
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            log(f"   ⚠️ Could not remove {os.path.basename(path)}: {error}")


def stash_video(folder, job_id, video):
    """Puts the video data aside for a later attempt. `video` is bytes or a file
    path. Called right when the upload fails - the post does not exist yet at
    that point, which is why the target follows with stash_retry_target()."""
    try:
        os.makedirs(folder, exist_ok=True)
        video_path, _ = _retry_paths(folder, job_id)
        if not isinstance(video, (bytes, bytearray)):
            with open(video, "rb") as source:
                video = source.read()
        with open(video_path, "wb") as target:
            target.write(video)
        return True
    except OSError as error:
        log(f"   ⚠️ Could not stash the video for a later attempt: {error}")
        return False


def stash_retry_target(folder, job_id, did, root_uri, root_cid,
                       parent_uri, parent_cid, filename, alt_text=""):
    """Records which post the video will later be attached to. Only this makes
    the pending job valid."""
    video_path, info_path = _retry_paths(folder, job_id)
    if not os.path.exists(video_path):
        return False
    try:
        with open(info_path, "w", encoding="utf-8") as target:
            json.dump({
                "did": did,
                "root_uri": root_uri,
                "root_cid": root_cid,
                "parent_uri": parent_uri,
                "parent_cid": parent_cid,
                "filename": filename,
                "alt_text": alt_text,
                "attempts": 0,
                "created": datetime.now().isoformat(timespec="seconds"),
            }, target, ensure_ascii=False, indent=2)
        log(f"   📌 Video noted for a later attempt ({job_id}).")
        return True
    except OSError as error:
        log(f"   ⚠️ Could not note the retry target: {error}")
        _remove_retry_files(video_path)
        return False


def _send_retry_reply(client, info, embed, reply_text):
    """Attaches the handed-in video as a reply to the original post."""
    alt = info.get("alt_text")
    if alt:
        embed.alt = alt[:1000]
    record = models.AppBskyFeedPost.Record(
        text=reply_text,
        embed=embed,
        reply=models.AppBskyFeedPost.ReplyRef(
            parent=models.ComAtprotoRepoStrongRef.Main(uri=info["parent_uri"],
                                                      cid=info["parent_cid"]),
            root=models.ComAtprotoRepoStrongRef.Main(uri=info["root_uri"],
                                                     cid=info["root_cid"]),
        ),
        created_at=client.get_current_time_iso(),
    )
    client.com.atproto.repo.create_record(
        models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection=models.ids.AppBskyFeedPost,
            record=record,
        )
    )


def post_stashed_videos(client, folder, reply_text, max_attempts=8,
                        max_bytes=100_000_000, timeout_seconds=600):
    """Retries video uploads that failed earlier and attaches each successful
    one as a reply to its post. Belongs at the start of every run, right after
    the login. Returns the number of jobs completed."""
    if not os.path.isdir(folder):
        return 0

    done = 0
    for name in sorted(os.listdir(folder)):
        if not name.endswith(RETRY_VIDEO):
            continue

        job_id = name[:-len(RETRY_VIDEO)]
        video_path, info_path = _retry_paths(folder, job_id)

        if not os.path.exists(info_path):
            log(f"   Dropping a video without a target: {job_id}")
            _remove_retry_files(video_path)
            continue

        try:
            with open(info_path, encoding="utf-8") as source:
                info = json.load(source)
        except (OSError, ValueError) as error:
            log(f"   ⚠️ Retry note {job_id} is unreadable, dropping it: {error}")
            _remove_retry_files(video_path, info_path)
            continue

        # Both bots may share one folder - leave other accounts' jobs alone.
        if info.get("did") and info["did"] != client.me.did:
            continue

        # Notes written before the sources were translated carry German keys.
        # They may still be sitting on a running installation, so read both.
        attempt = int(info.get("attempts", info.get("versuche", 0))) + 1
        filename = info.get("filename") or info.get("dateiname") or name

        log(f"🎥 Handing in a video ({job_id}, attempt {attempt}/{max_attempts})...")
        try:
            embed = upload_video_to_bluesky(client, video_path, filename,
                                            max_bytes, timeout_seconds,
                                            attempts=1)
            _send_retry_reply(client, info, embed, reply_text)
            log(f"   ✓ Video handed in ({job_id}).")
            _remove_retry_files(video_path, info_path)
            done += 1
        except Exception as error:
            log(f"   ⚠️ Handing in failed ({job_id}): {error}")
            if attempt >= max_attempts:
                log(f"   Giving up after {attempt} attempts - dropping {job_id}.")
                _remove_retry_files(video_path, info_path)
                continue
            info["attempts"] = attempt
            info.pop("versuche", None)
            try:
                with open(info_path, "w", encoding="utf-8") as target:
                    json.dump(info, target, ensure_ascii=False, indent=2)
            except OSError as write_error:
                log(f"   ⚠️ Could not save the attempt counter: {write_error}")

    return done


# ------------------------------------------------- voice messages and stickers
#
# Bluesky knows neither an audio format nor animations. Both can be translated
# into something Bluesky does show:
#   voice message -> video with an animated waveform (the sound is kept)
#   sticker       -> a single image (the first frame of an animated one)

def _have_ffmpeg():
    return shutil.which("ffmpeg") is not None


def audio_to_video(audio_data, size="720x720", wave_color="White",
                   background="0x0b1220", framerate=25, timeout_seconds=120):
    """Turns a voice message into a video with an animated waveform and an
    unchanged audio track. Returns the mp4 data.

    CAREFUL with `wave_color`: ffmpeg's showwaves filter takes COLOUR NAMES
    only ("White", "DodgerBlue", "Cyan", ...). Hex values such as 0x38BDF8 or
    #38BDF8 are discarded silently and it draws green instead - without any
    error message. `background` is different, that one takes hex as well.
    """
    if not _have_ffmpeg():
        raise RuntimeError("ffmpeg is not installed - voice messages cannot be "
                           "converted without it.")

    if wave_color.startswith(("0x", "#")):
        log(f"   ⚠️ The wave colour {wave_color!r} is a hex value - ffmpeg "
            f"ignores those and draws green. Please use a colour name.")

    try:
        width, height = (int(part) for part in size.lower().split("x"))
    except ValueError:
        raise RuntimeError(f"Invalid size for the audio video: {size!r} "
                           f"(expected something like 720x720).")
    wave_height = max(2, height // 2)

    filter_chain = (
        f"color=c={background}:s={width}x{height}:r={framerate}[bg];"
        f"[0:a]showwaves=s={width}x{wave_height}:mode=cline:"
        f"colors={wave_color}:scale=sqrt:r={framerate}[w];"
        f"[bg][w]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[v]"
    )

    with tempfile.TemporaryDirectory(prefix="skyrelay-audio-") as folder:
        source = os.path.join(folder, "voice")
        target = os.path.join(folder, "voice.mp4")
        with open(source, "wb") as f:
            f.write(audio_data)

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", source, "-filter_complex", filter_chain,
             "-map", "[v]", "-map", "0:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
             target, "-loglevel", "error"],
            capture_output=True, text=True, timeout=timeout_seconds)

        if result.returncode != 0 or not os.path.exists(target):
            message = (result.stderr or "").strip()[:300] or "no output"
            raise RuntimeError(f"ffmpeg could not produce a video: {message}")

        with open(target, "rb") as f:
            return f.read()


def video_still(video_data, at_second=1.0, timeout_seconds=60):
    """Pulls a single frame out of a video - as a stand-in image should the
    video upload fail. Returns JPEG data or None."""
    if not _have_ffmpeg():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="skyrelay-frame-") as folder:
            source = os.path.join(folder, "video.mp4")
            target = os.path.join(folder, "frame.jpg")
            with open(source, "wb") as f:
                f.write(video_data)
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(at_second), "-i", source,
                 "-frames:v", "1", target, "-loglevel", "error"],
                capture_output=True, text=True, timeout=timeout_seconds)
            if result.returncode != 0 or not os.path.exists(target):
                return None
            with open(target, "rb") as f:
                return f.read()
    except Exception as error:
        log(f"   ⚠️ Could not produce a still frame: {error}")
        return None


def sticker_to_image(data, background="white", max_dim=2000, max_bytes=1_500_000):
    """Turns a WhatsApp sticker into an image for Bluesky. Stickers are WebP,
    usually with a transparent background and sometimes animated. The first
    frame is taken, and the transparency is laid onto a solid background -
    otherwise the motif would sit on black, because dropping the alpha channel
    leaves nothing else behind."""
    image = Image.open(io.BytesIO(data) if isinstance(data, (bytes, bytearray))
                       else data)

    # Animated stickers: the first frame. On still ones seek(0) does nothing.
    try:
        image.seek(0)
    except EOFError:
        pass

    image = image.convert("RGBA")
    ground = Image.new("RGB", image.size, background)
    ground.paste(image, mask=image.split()[3])

    buffer = io.BytesIO()
    ground.save(buffer, format="PNG")
    return compress_image_for_bluesky(buffer.getvalue(), max_dim, max_bytes)
