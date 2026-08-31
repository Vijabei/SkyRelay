"""
SkyRelay - feed: mirrors an Instagram profile to Bluesky.

Unlike the matchday ticker this program runs all year round: on every call it
checks the profile's latest posts and transfers everything not carried over yet.
Meant to be started regularly from cron.

All settings live in the [feed] section of "skyrelay.conf" (template:
skyrelay.conf.example). A different path can be given through the environment
variable SKYRELAY_CONFIG.

Credentials come from environment variables only:
    BLUESKY_FEED_APP_PASSWORD   app password of the feed account
    BLUESKY_APP_PASSWORD        stands in when ticker and feed share one account

The Instagram session is created once, outside this program:
    venv/bin/instaloader -l <secondary account>

Example for cron (every 15 minutes):
    */15 * * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /path/to/SkyRelay/venv/bin/python3 /path/to/SkyRelay/skyrelay-feed.py >/dev/null 2>&1
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
    build_post,
    load_layout,
    text_block,
    source_block,
    DEFAULT_SOURCE_TEMPLATE,
    tag_block,
    show_preview,
)
from skyrelay_config import check_config, show_config

# Answers about the configuration, before anything else gets going: both calls
# connect to nothing and write nothing.
#   --check-config  reports what does not add up
#   --show-config   shows which value applies right now and where it comes from
if "--check-config" in sys.argv:
    sys.exit(check_config(os.path.dirname(os.path.abspath(__file__))))
if "--show-config" in sys.argv:
    sys.exit(show_config(os.path.dirname(os.path.abspath(__file__))))


# Instagram keeps changing the endpoints that serve profile data, so the window
# of usable instaloader versions is narrow:
#   < 4.15.1  breaks on Instagram's move to GraphQL
#   4.15.2    works (pinned in requirements.txt)
#   >= 4.15.3 goes back to "web_profile_info"; Instagram has been throttling
#             that endpoint since August 2026, the very first request ends in a
#             429 (instaloader#2726, still open)
_instaloader_version = tuple(int(p) for p in instaloader.__version__.split(".")[:3]
                             if p.isdigit())
if _instaloader_version < (4, 15, 1):
    log(f"⚠️ instaloader {instaloader.__version__} is too old for Instagram's "
        f"current endpoints. Recommended: pip install 'instaloader==4.15.2'")
elif _instaloader_version >= (4, 15, 3):
    log(f"⚠️ instaloader {instaloader.__version__} uses 'web_profile_info', the "
        f"endpoint Instagram throttles - expect HTTP 429.")
    log(f"   Recommended until instaloader#2726 is solved: "
        f"pip install 'instaloader==4.15.2'")

# ============================== CONFIGURATION ================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
cfg, cfg_int, cfg_bool, CONFIG_FILE = load_config(BASE_DIR)

INSTA_USER = cfg("feed", "instagram_profile", "")
INSTA_BOT_USER = cfg("feed", "instagram_session_user", "")
# A separate account for the feed? Otherwise the general one from [bluesky].
BLUESKY_HANDLE = cfg("feed", "bluesky_handle", "") or cfg("bluesky", "handle", "")

POSTS_TO_CHECK = cfg_int("feed", "posts_to_check", 10)
PAUSE_BETWEEN_POSTS_SECONDS = cfg_int("feed", "pause_between_posts_seconds", 8)
VIDEO_PLACEHOLDER = cfg("feed", "video_placeholder", "🎥 Neues Video/Reel")
MIXED_PLACEHOLDER = cfg("feed", "mixed_placeholder", "📸🎥 Neuer Beitrag")
IMAGE_PLACEHOLDER = cfg("feed", "image_placeholder", "📸 Neues Bild")
ALT_TEXT_FALLBACK = cfg("feed", "alt_text_fallback", "News")
# Stands in when a post carries more media than text - it ends up in the post
# itself, so it belongs in the configuration rather than in the code.
CONTINUATION_TEXT = cfg("feed", "continuation_text", "Weitere Inhalte...")

POST_PREFIX = cfg("post", "prefix", "⚽ [Inoffizieller Bot]")
# A label of its own for the source link: [post] source_label describes the
# ticker's WhatsApp channel and does not fit an Instagram post.
FEED_SOURCE_LABEL = cfg("feed", "source_label", "Beitrag auf Instagram")
# Shared with the ticker: the word "Quelle" is the same either way, only
# what it points at differs (#7).
SOURCE_TEMPLATE = cfg("post", "source_template", DEFAULT_SOURCE_TEMPLATE)
STANDING_HASHTAG = cfg("post", "standing_hashtag", "").strip().lstrip("#")

# Where header, source and the standing hashtag go (#6). The feed knows no
# match hashtag, so that block simply never has anything to show.
LAYOUT = load_layout(cfg, log)

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
# The German default stays: it is a folder name on disk, and renaming it would
# strand videos that a running installation still has waiting in there.
VIDEO_RETRY_DIR = os.path.join(BASE_DIR,
                               cfg("files", "video_retry_dir", "skyrelay_nachreichen"))


if LOG_TO_FILE:
    start_file_logging(LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
    log(f"--- feed start (log: {LOG_FILE}) ---")

# Carry over what earlier versions left behind, so nothing gets posted twice.
_old_state = os.path.join(BASE_DIR, "posted_shortcodes.txt")
if os.path.exists(_old_state) and not os.path.exists(STATE_FILE):
    try:
        os.replace(_old_state, STATE_FILE)
        log(f"Carried over: posted_shortcodes.txt -> {os.path.basename(STATE_FILE)}")
    except Exception as _error:
        log(f"⚠️ Could not carry over posted_shortcodes.txt: {_error}")

# The app password does NOT belong in the configuration - it comes from the
# environment. A separate account for the feed means a separate password;
# BLUESKY_APP_PASSWORD applies when ticker and feed share one account.
BLUESKY_APP_PASSWORD, PASSWORD_VARIABLE = get_app_password(
    "BLUESKY_FEED_APP_PASSWORD",
    "SKYRELAY_FEED_APP_PASSWORD",  # earlier name, still accepted
    "BLUESKY_APP_PASSWORD")

# SKYRELAY_DRY_RUN=1 -> query Instagram and download media, but publish nothing
# and do not advance the list of posts already carried over. For checking a
# setup on any machine without consequences.
DRY_RUN = os.environ.get("SKYRELAY_DRY_RUN") == "1"
if DRY_RUN:
    log("SKYRELAY_DRY_RUN=1 is set - NOTHING will be published on Bluesky.")

if not BLUESKY_APP_PASSWORD and not DRY_RUN:
    log(f"Error: no app password set for the feed account @{BLUESKY_HANDLE}.")
    log('If the feed uses an account of ITS OWN (different from the ticker):')
    log('    export BLUESKY_FEED_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log('If ticker and feed share one account, this is enough:')
    log('    export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"')
    log("Careful: cron does not read ~/.bashrc - put the variables above into")
    log("the crontab itself (without quotation marks).")
    sys.exit(1)

_missing = [name for name, value in (("[feed] instagram_profile", INSTA_USER),
                                     ("[feed] instagram_session_user", INSTA_BOT_USER),
                                     ("bluesky_handle", BLUESKY_HANDLE))
            if not value or value.startswith("dein")]
if _missing:
    log(f"Error: {os.path.basename(CONFIG_FILE)} is still missing: "
        f"{', '.join(_missing)}")
    sys.exit(1)

os.makedirs(TMP_DIR, exist_ok=True)

# 1. Load the shortcodes carried over already
posted_shortcodes = set()
if os.path.exists(STATE_FILE):
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        posted_shortcodes = set(f.read().splitlines())

# 2. Set up instaloader and load the session
L = instaloader.Instaloader(
    dirname_pattern=os.path.join(TMP_DIR, "{target}"),
    download_videos=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False
)

log(f"Loading the existing Instagram session for {INSTA_BOT_USER}...")
try:
    L.load_session_from_file(INSTA_BOT_USER)
    log("Session loaded.")
except Exception as error:
    log(f"Error while loading the session file: {error}")
    sys.exit(1)

# 3. Fetch the Instagram profile and its latest posts
log(f"Fetching the profile @{INSTA_USER} straight from Instagram...")
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

except Exception as error:
    log(f"The Instagram query failed: {error}")
    sys.exit(1)


# 4. Helpers
def split_caption(caption, first_limit, follow_limit):
    """Splits the caption into chunks. With break_long_words=False textwrap can
    return chunks longer than the target width (long URLs or hashtag chains, for
    instance) - which is why they are additionally cut hard to the limit."""
    wrapped = textwrap.wrap(caption, width=first_limit, break_long_words=False,
                            replace_whitespace=False)
    if not wrapped:
        return []

    raw_chunks = [wrapped[0]]
    remaining_text = caption[len(wrapped[0]):].strip()
    if remaining_text:
        raw_chunks.extend(textwrap.wrap(remaining_text, width=follow_limit,
                                        break_long_words=False,
                                        replace_whitespace=False))

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
    """A sort key that treats numbers in file names numerically - so that
    instaloader's ..._2.jpg sorts before ..._10.jpg (alphabetically _10 would
    come first)."""
    return [int(part) if part.isdigit() else part
            for part in re.split(r"(\d+)", os.path.basename(path))]


def build_alt_text(caption, suffix=""):
    """Builds an alt text from the caption (accessibility) rather than using a
    generic placeholder."""
    base = caption.strip() if caption else ALT_TEXT_FALLBACK
    if len(base) > 200:
        base = base[:200].rstrip() + "…"
    return base + suffix


def build_post_text(body, index, total, source_url):
    """Builds the text of a single post in the thread. Where header, source and
    the standing hashtag land is decided by [layout]; the body and its counter
    always sit in the middle.

    Deliberately one single place: it lets the dry run show exactly what the
    real run would send, instead of a rebuilt approximation."""
    text = body if total == 1 else f"{body} ({index + 1}/{total})"
    writers = {
        "prefix": text_block(POST_PREFIX),
        "source": source_block(SOURCE_TEMPLATE, FEED_SOURCE_LABEL, source_url),
        "match_hashtag": None,
        "standing_hashtag": tag_block(STANDING_HASHTAG),
    }
    return build_post(client_utils.TextBuilder(), index, total,
                      lambda tb: tb.text(text), writers, LAYOUT)


client = None
new_posts_count = 0
retries_done = DRY_RUN  # in a dry run nothing goes out at all


def ensure_login():
    """Logs in to Bluesky and, the first time, hands in every video whose upload
    failed in earlier runs. The login happens deliberately late and only once
    per run: Bluesky limits logins per account and day."""
    global client, retries_done

    # Important: assign `client` only AFTER a successful login. Otherwise a
    # failed login would leave an unauthenticated object behind, the condition
    # would never be true again, and every following post would go out
    # unauthenticated ("AuthMissing").
    if client is None:
        log("Connecting to Bluesky...")
        connection = Client()
        try:
            log_in_to_bluesky(connection, BLUESKY_HANDLE, BLUESKY_APP_PASSWORD,
                              PASSWORD_VARIABLE)
        except Exception:
            # Without a login nothing can be published, and further attempts
            # would only use up the login limit (Bluesky allows 10 logins per
            # day and account).
            log("Nothing can be published without a login - ending this run.")
            log("The posts not carried over yet stay open and will be tried")
            log("again on the next run.")
            sys.exit(1)
        client = connection

    if not retries_done:
        retries_done = True
        post_stashed_videos(client, VIDEO_RETRY_DIR, VIDEO_RETRY_TEXT,
                            VIDEO_RETRY_MAX_ATTEMPTS, MAX_VIDEO_BYTES,
                            VIDEO_JOB_TIMEOUT_SECONDS)
    return client


# If videos from earlier runs are waiting, logging in pays off even without a
# new post.
if not DRY_RUN and os.path.isdir(VIDEO_RETRY_DIR) and any(
        name.endswith(".json") for name in os.listdir(VIDEO_RETRY_DIR)):
    ensure_login()

for post in latest_posts:
    if post.shortcode in posted_shortcodes:
        continue

    new_posts_count += 1
    log(f"\n[NEW POST] Processing: {post.shortcode}")

    try:
        # 5. Download the media, work out video and sidecar shapes
        L.download_post(post, target=INSTA_USER)

        jpg_files = sorted(glob.glob(os.path.join(TMP_DIR, INSTA_USER, "*.jpg")),
                           key=natural_sort_key)
        mp4_files = sorted(glob.glob(os.path.join(TMP_DIR, INSTA_USER, "*.mp4")),
                           key=natural_sort_key)
        log(f"[DEBUG] typename={post.typename}, is_video={post.is_video}, "
            f"jpgs={len(jpg_files)}, mp4s={len(mp4_files)}")

        is_video_post = post.is_video   # true only for a single reel or video post
        is_multi_video_post = False     # true for a carousel where ALL items are videos
        is_mixed_post = False           # true for a carousel of images AND videos
        video_pairs = []                # [(mp4_path, thumb_jpg_path), ...] for carousels
        mixed_image_files = []          # jpg paths of the real image items of a mixed carousel

        if is_video_post and not mp4_files:
            # For reasons unknown instaloader's own video download returns no
            # .mp4 here, even though post.video_url gives a valid URL.
            # Workaround: fetch the video manually from that URL.
            log("⚠️ instaloader downloaded no .mp4. Trying a direct download "
                "via post.video_url...")
            try:
                video_url_direct = post.video_url
                log(f"[DEBUG] post.video_url = {video_url_direct}")

                video_target_path = os.path.join(TMP_DIR, INSTA_USER,
                                                 f"{post.shortcode}.mp4")
                video_response = requests.get(video_url_direct, stream=True, timeout=60)
                video_response.raise_for_status()
                with open(video_target_path, "wb") as vf:
                    for chunk in video_response.iter_content(chunk_size=1024 * 1024):
                        vf.write(chunk)

                mp4_files = [video_target_path]
                log(f"✓ Video downloaded manually: "
                    f"{os.path.basename(video_target_path)}")
            except Exception as video_download_error:
                log(f"[DEBUG] The manual video download failed: {video_download_error}")
                traceback.print_exc()
                log("   -> the upload is skipped in block 9, the cover image "
                    "steps in.")
        elif mp4_files:
            log(f"Video file found: {os.path.basename(mp4_files[0])}")

        if post.typename == "GraphSidecar":
            try:
                sidecar_nodes = list(post.get_sidecar_nodes())
                video_indices = [idx for idx, node in enumerate(sidecar_nodes)
                                 if node.is_video]
                log(f"[DEBUG] sidecar with {len(sidecar_nodes)} items, "
                    f"{len(video_indices)} of them marked as video.")

                if video_indices:
                    # Assumed in both cases: jpg_files come in the same order as
                    # sidecar_nodes (instaloader numbers them _1, _2, ...).
                    if len(video_indices) == len(sidecar_nodes):
                        # Every item is a video -> treat it as a multi video post.
                        is_multi_video_post = True
                        log(f"Multi video post: {len(sidecar_nodes)} videos. "
                            f"Downloading them one by one...")
                    else:
                        # Mixed carousel: the images go into the main post, then
                        # each video follows as its own reply in the thread.
                        is_mixed_post = True
                        mixed_image_files = [jpg_files[idx]
                                             for idx, node in enumerate(sidecar_nodes)
                                             if not node.is_video and idx < len(jpg_files)]
                        log(f"Mixed carousel: {len(mixed_image_files)} image(s) + "
                            f"{len(video_indices)} video(s). Images into the main "
                            f"post, videos one per reply.")

                    for idx, node in enumerate(sidecar_nodes, start=1):
                        if not node.is_video:
                            continue
                        try:
                            video_target_path = os.path.join(
                                TMP_DIR, INSTA_USER, f"{post.shortcode}_{idx}.mp4")
                            video_response = requests.get(node.video_url,
                                                          stream=True, timeout=60)
                            video_response.raise_for_status()
                            with open(video_target_path, "wb") as vf:
                                for chunk in video_response.iter_content(
                                        chunk_size=1024 * 1024):
                                    vf.write(chunk)
                            thumb_path = (jpg_files[idx - 1]
                                          if idx - 1 < len(jpg_files) else None)
                            video_pairs.append((video_target_path, thumb_path))
                            log(f"✓ Video {len(video_pairs)}/{len(video_indices)} "
                                f"downloaded.")
                        except Exception as sidecar_video_error:
                            log(f"⚠️ Could not download the sidecar video "
                                f"(item {idx}): {sidecar_video_error}")
            except Exception as sidecar_error:
                log(f"[DEBUG] Analysing the sidecar failed: {sidecar_error}")

        # 6. Split the text, build the Instagram URL
        caption = post.caption if post.caption else ""
        # The first post also carries the header and the source link (~75
        # characters) and has to stay under Bluesky's 300 character limit ->
        # deliberately smaller than the following chunks.
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

        # 7. Prepare the images (for pure image posts all jpgs; for mixed
        # carousels only the real image items - the jpgs of the video items are
        # merely their covers and serve as a fallback in block 9; for pure video
        # posts none at all)
        bluesky_images = []

        if is_mixed_post:
            image_source_files = mixed_image_files
        elif not is_video_post and not is_multi_video_post:
            image_source_files = jpg_files
        else:
            image_source_files = []

        if image_source_files:
            log("Preparing the images for Bluesky (including compression)...")
            for img_path in image_source_files:
                try:
                    bluesky_images.append(compress_image_for_bluesky(img_path))
                except Exception as error:
                    log(f"Error while processing {img_path}: {error}")

        image_chunks = ([bluesky_images[i:i + 4]
                         for i in range(0, len(bluesky_images), 4)]
                        if bluesky_images else [])

        # 7b. Dry run: everything up to here has happened (the query, the media
        # download, preparing the text) - from here on it would go outwards. So
        # only report what would be published, and neither log in nor advance
        # the list. That keeps the post around for the real run.
        if DRY_RUN:
            media = []
            if bluesky_images:
                media.append(f"{len(bluesky_images)} image(s)")
            # The same condition as the real run further down - this used to
            # miss the mixed carousel and swallowed its videos.
            video_count = (len(video_pairs)
                           if (is_multi_video_post or is_mixed_post) and video_pairs
                           else 1 if (is_video_post and mp4_files) else 0)
            if video_count:
                media.append(f"{video_count} video(s)")
            # As many posts as the real run would create: text chunks against
            # media slots (image groups in the main post, one video each after).
            preview_total = max(len(text_chunks), len(image_chunks) + video_count)
            log(f"   [DRY_RUN] Would post on @{BLUESKY_HANDLE}: "
                f"{preview_total} post(s)"
                + (f", {', '.join(media)}" if media else ", without media"))
            show_preview([
                build_post_text(text_chunks[i] if i < len(text_chunks)
                                else CONTINUATION_TEXT,
                                i, preview_total, insta_url)
                for i in range(preview_total)])
            continue

        # 8. Connect to Bluesky (only once it is really needed). The server
        # address for the video upload is worked out by the shared module.
        ensure_login()

        # 9. Video uploads to Bluesky's video API (one, or several for a
        # multi video sidecar). Video first; on failure its cover image.
        video_embeds = []
        retry_slots = {}  # index in video_embeds -> pending video job

        videos_to_process = []
        if (is_multi_video_post or is_mixed_post) and video_pairs:
            videos_to_process = video_pairs
        elif is_video_post and mp4_files:
            videos_to_process = [(mp4_files[0], jpg_files[0] if jpg_files else None)]

        for idx, (video_path, video_thumb) in enumerate(videos_to_process, start=1):
            log(f"Starting video upload {idx}/{len(videos_to_process)}: "
                f"{os.path.basename(video_path)}")
            try:
                embed = upload_video_to_bluesky(client, video_path,
                                                os.path.basename(video_path),
                                                MAX_VIDEO_BYTES,
                                                VIDEO_JOB_TIMEOUT_SECONDS)
                video_embeds.append(embed)
                log(f"✓ Video {idx}/{len(videos_to_process)} embedded.")
            except Exception as video_error:
                log(f"⚠️ Video upload {idx}/{len(videos_to_process)} failed: "
                    f"{video_error}")
                traceback.print_exc()

                # The post goes out right away with the cover image; the video
                # stays behind for a later run (see post_stashed_videos).
                job_id = f"{post.shortcode}_{idx}"
                if stash_video(VIDEO_RETRY_DIR, job_id, video_path):
                    retry_slots[len(video_embeds)] = (
                        job_id, os.path.basename(video_path),
                        build_alt_text(caption, " (Video)"))

                if video_thumb:
                    try:
                        log(f"   Using the cover image as a fallback for video {idx}.")
                        fallback_bytes = compress_image_for_bluesky(video_thumb)
                        fallback_upload = client.upload_blob(fallback_bytes)
                        video_embeds.append(models.AppBskyEmbedImages.Main(
                            images=[models.AppBskyEmbedImages.Image(
                                alt=build_alt_text(caption, " (Video-Vorschau)"),
                                image=fallback_upload.blob
                            )]
                        ))
                    except Exception as fallback_error:
                        log(f"   ⚠️ The fallback image failed too: {fallback_error}")
                        video_embeds.append(None)
                else:
                    log(f"   No cover image for video {idx}, posting without "
                        f"a media embed.")
                    video_embeds.append(None)

        # 10. Build the Bluesky thread (main post and replies)
        # Order of the media in the thread: image groups first (the main post),
        # then one video per reply. For pure image or pure video posts one of
        # the lists is empty, so the behaviour there is unchanged.
        media_slots = [("images", chunk) for chunk in image_chunks]
        media_slots += [("video", embed) for embed in video_embeds]

        total_posts = max(len(text_chunks), len(media_slots))
        root_ref = None
        parent_ref = None

        for i in range(total_posts):
            is_first = (i == 0)

            current_text = text_chunks[i] if i < len(text_chunks) else CONTINUATION_TEXT

            embed = None
            if i < len(media_slots):
                slot_kind, slot_payload = media_slots[i]
                if slot_kind == "video" and slot_payload is not None:
                    embed = slot_payload
                    log(f"✓ Adding the video embed {i + 1}/{total_posts}.")
                elif slot_kind == "images":
                    uploaded_images = []
                    for img_data in slot_payload:
                        upload = client.upload_blob(img_data)
                        uploaded_images.append(models.AppBskyEmbedImages.Image(
                            alt=alt_text, image=upload.blob))
                    if uploaded_images:
                        embed = models.AppBskyEmbedImages.Main(images=uploaded_images)
                        log(f"✓ Adding the image embed ({len(uploaded_images)} "
                            f"image(s)) {i + 1}/{total_posts}.")

            tb = build_post_text(current_text, i, total_posts, insta_url)

            if is_first:
                log(f"Sending the main post for {post.shortcode}...")
                root_post = client.send_post(text=tb, embed=embed)
                root_ref = models.ComAtprotoRepoStrongRef.Main(cid=root_post.cid,
                                                               uri=root_post.uri)
                parent_ref = models.ComAtprotoRepoStrongRef.Main(cid=root_post.cid,
                                                                 uri=root_post.uri)

                # Record the shortcode right after the main post - if a reply
                # fails, the whole post would otherwise be duplicated next run.
                with open(STATE_FILE, "a", encoding="utf-8") as f:
                    f.write(post.shortcode + "\n")
                posted_shortcodes.add(post.shortcode)
            else:
                log(f"Sending thread post {i + 1}/{total_posts}...")
                reply_ref = models.AppBskyFeedPost.ReplyRef(parent=parent_ref,
                                                            root=root_ref)
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
                parent_ref = models.ComAtprotoRepoStrongRef.Main(
                    cid=current_reply.cid, uri=current_reply.uri)

            # parent_ref now points at the post just created. If this slot holds
            # a pending video, that post is where its reply belongs.
            if i < len(media_slots) and media_slots[i][0] == "video":
                pending = retry_slots.get(i - len(image_chunks))
                if pending:
                    job_id, filename, video_alt = pending
                    stash_retry_target(VIDEO_RETRY_DIR, job_id, client.me.did,
                                       root_ref.uri, root_ref.cid,
                                       parent_ref.uri, parent_ref.cid,
                                       filename, video_alt)

        log(f"Post {post.shortcode} published on Bluesky.")

    except Exception as error:
        log(f"Error on post {post.shortcode}: {error}")
        traceback.print_exc()

    finally:
        # 11. Clean up (runs on errors as well, and clears subfolders too)
        shutil.rmtree(os.path.join(TMP_DIR, INSTA_USER), ignore_errors=True)

    # A short pause, to avoid Bluesky rate limits and conspicuous access patterns
    time.sleep(PAUSE_BETWEEN_POSTS_SECONDS)

# 12. Summary
if new_posts_count == 0:
    log(f"No new posts - the latest {len(latest_posts)} are already on Bluesky.")
else:
    log(f"Run finished: {new_posts_count} new post(s) processed.")
