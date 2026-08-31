# Cheat sheet: skyrelay-matchday.py

***English** · [Deutsch](CHEATSHEET-matchday.de.md)*

Reposts the **Arminia Bielefeld WhatsApp channel** to Bluesky
(`dsc-spieltagticker.bsky.social`) — automatically on matchdays from 6 in the
morning until midnight, steered through environment variables.

---

## Quick reference: switches and environment variables

| Variable | Values | What it does |
|---|---|---|
| `BLUESKY_TICKER_APP_PASSWORD` | `xxxx-xxxx-xxxx-xxxx` | **Required** (except in a dry run). App password of the **ticker** account. The feed uses `BLUESKY_FEED_APP_PASSWORD` accordingly. |
| `--show-config` | – | Shows every value that is read, with where it comes from (file or default). No network, no changes. |
| `--check-config` | – | Reports keys nobody reads, keys that are missing, and keys missing from the template. |
| `SKYRELAY_DRY_RUN` | `1` | Only log, post **nothing** to Bluesky. Shows every post as it would stand on Bluesky — with its character count. For checking without consequences. |
| `SKYRELAY_FORCE` | `1` | Runs even when OpenLigaDB knows **no** match for today (friendlies, manual runs). If OpenLigaDB does find one, the hashtag and the matchday info are used all the same. |
| `SKYRELAY_HASHTAG` | e.g. `DSCGUE` | Set the match hashtag **by hand** (with or without `#`, case does not matter). Takes precedence over the generated one. Needed for friendlies OpenLigaDB does not know. |
| `SKYRELAY_REPLAY` | a number `N` | Test mode: processes the **last N existing** channel posts once and then **stops**. The watermark is left alone, and there is no duplicate check. |
| `SKYRELAY_CATCHUP` | a number `N` | Catch-up mode: processes the last N posts (**skipping** what has been done already, thanks to the watermark) and **keeps listening normally afterwards**. For "the program started too late". |
| `SKYRELAY_PAIR_PHONE` | `4915123456789` | First pairing by an eight digit code instead of a QR scan (the number international, without `+` or a leading 0). Only needed on the very first run; ignored once paired. |
| `SKYRELAY_PROFILE` | `on` / `off` | Sets **only** the profile status line of the Bluesky bio and stops at once (without WhatsApp). For testing and for putting it right by hand. |
| `SKYRELAY_CONFIG` | a path | Use a different configuration file (default: `skyrelay.conf` next to the program). This is how several clubs run side by side. |
| `SKYRELAY_LANG` | `de` / `en` | Language of the setup assistant, unless `[general] language` says otherwise. |

> The old `DSC_TICKER_*` names still work for the time being, but print a note.
> Please move to `SKYRELAY_*`.

---

## The modes

### 1. cron, the normal way (competitive matches)

```cron
0 6 * * * BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /home/geordi/SkyRelay/venv/bin/python3 /home/geordi/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
```

> ⚠️ **Paths are case sensitive.** A wrong capital in the interpreter path leads
> to `/bin/sh: 1: …/bin/python3: not found` — that message is about the
> interpreter, not about Python itself.
>
> ⚠️ **No `>> skyrelay.log 2>&1` redirect any more:** the program writes its own
> log (see below). Leave the redirect in and every line appears **twice**.

**What it does:**
- Starts daily at 6:00 and asks OpenLigaDB whether Arminia plays **today** (league
  and cup, a window of ±1 week, team number 83).
- **No match** → it stops at once, WhatsApp is never contacted.
- **A matchday** → it listens for **live events** from the channel until **23:59**
  (reposts appear immediately, no polling), then stops by itself.
- The match hashtag (`#DSCWOB` at home, `#WOBDSC` away) is generated from the
  OpenLigaDB data (DFL codes, home team first) and attached to every post
  together with `#arminia`.
- It reposts only posts from **today**: whatever WhatsApp delivers on connecting
  from its offline queue is taken along if it is from today — anything older is
  dropped.
- **The bio status line:** when listening begins, the first line of the Bluesky
  bio is set to "🟢 Bot ist an - 1. Spieltag #KSCDSC ⚫⚪🔵", and on stopping
  (including Ctrl+C or after an error) to "🔴 Bot ist aus - nächstes Spiel
  #DSCFCE ⚫⚪🔵". Every other line of the bio, the avatar, the banner and the
  display name are left untouched.

### 2. A manual run (friendlies, "let it run for a few hours")

```bash
export BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
SKYRELAY_FORCE=1 SKYRELAY_HASHTAG=DSCGUE python skyrelay-matchday.py
```

- **Stopping:** `Ctrl+C` (caught cleanly) — or by itself at 23:59.
- **Restarting on the same day:** harmless, no duplicates (the watermark).
- Without `SKYRELAY_HASHTAG` the posts only get `#arminia` — unless OpenLigaDB
  does know a match for today, in which case the hashtag and the matchday info
  are used automatically.
- For long runs over SSH (surviving the session being closed):

```bash
nohup env SKYRELAY_FORCE=1 SKYRELAY_HASHTAG=DSCGUE python skyrelay-matchday.py >/dev/null 2>&1 &
tail -f skyrelay.log        # watch (the program writes the log itself)
pgrep -af skyrelay-matchday # is it running? PID and command line
kill <PID>                  # stop it (cleanly, the bio goes to "Bot ist aus")
```

### 3a. Catch-up (fetch missed posts, then keep listening)

```bash
SKYRELAY_CATCHUP=5 SKYRELAY_FORCE=1 SKYRELAY_HASHTAG=DSCGUE python skyrelay-matchday.py
```

- Fetches the last `N` posts, **skipping** everything the watermark says was
  already done today (no duplicates), and then moves seamlessly into listening.
- The right mode when the program started too late on a matchday, or had crashed.
- The same neonize caveat as replay (see below): a message without content among
  the last `N` → a crash. Pick a small `N`.

### 3b. Replay (testing a single post)

```bash
# The last channel post to the log only (a dry run):
SKYRELAY_REPLAY=1 SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 python skyrelay-matchday.py

# The last channel post FOR REAL on Bluesky (an end to end test):
SKYRELAY_REPLAY=1 SKYRELAY_FORCE=1 python skyrelay-matchday.py

# The last 3 posts:
SKYRELAY_REPLAY=3 ...
```

- Processes oldest → newest through the whole pipeline (text, images, link card),
  then stops at once.
- Does **not** touch the watermark → automatic operation is unaffected.
- Careful without a dry run: replay does **not** check for duplicates — running it
  twice posts twice.
- ⚠️ A known neonize bug: if among the **last N** channel messages there is a
  **meta message without content** (invisible in the channel, but holding a
  server ID of its own — an edit or a deletion, say), the program crashes hard
  (a Go `panic: required field … Message not set`). Such messages cannot be
  inspected with neonize — on a crash simply reduce `N` step by step until the
  window ends before the meta message. This affects replay and catch-up only;
  live operation is immune.

---

## The first pairing (once, interactively — not from cron)

```bash
SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 python skyrelay-matchday.py
```

- An ASCII **QR code appears in the terminal** (only when a pairing is really
  due). Scan it with: WhatsApp → Settings → **Linked devices** → Link a device.
- **Trouble scanning?** Enlarge the terminal a lot and turn the screen brightness
  up (contrast). The QR code rotates every ~30 s — always scan the one shown
  last.
- **The numeric code instead:** also set `SKYRELAY_PAIR_PHONE=49…` → type the
  code on the phone ("Link with phone number instead").
- Signing in through web.whatsapp.com in a browser does **not** help — only this
  program's own pairing counts.
- Afterwards the session lives in `skyrelay_session.sqlite3` and is reused
  automatically.

---

## Files in the program folder

| File | What for | Safe to delete? |
|---|---|---|
| `skyrelay-matchday.py` | the program | — |
| `skyrelay_session.sqlite3` | the WhatsApp session (the pairing) | Yes → forces a fresh first pairing |
| `skyrelay_state.txt` | the watermark: `date;last server ID` | Yes → the next start takes a fresh baseline ("from now on") |
| `skyrelay_posts.json` | server ID → Bluesky posts (today only, for edits) | Yes → edits can then no longer delete the old posts, only post anew |
| `skyrelay.log` | the log — the program writes it **always**, however it was started (including the output of the Go layer). The console still shows everything. | Yes |
| `skyrelay.log.1` … `.5` | rotated logs (rotated at startup from 2 MB on, `.1` = newest) | Yes |
| `locales/*/LC_MESSAGES/*.mo` | compiled translations | Yes → rebuild with `./tools/i18n.sh compile` |

---

## Settings in `skyrelay.conf`

It is created from the template: `cp skyrelay.conf.example skyrelay.conf`, or by
`./config.sh`. It is in `.gitignore` and does **not** belong in the repository.

| Section / key | Meaning |
|---|---|
| `[general] language` | Language of the setup assistant (`de`, `en`, or empty for whatever the system says). The log stays English. |
| `[bluesky] handle` | The account that is posted to. The app password does **not** go here but into `BLUESKY_TICKER_APP_PASSWORD`. |
| `[source] channel_invite_link` | The WhatsApp channel's invite link (channel → Share → copy link) |
| `[team] openligadb_filter` / `openligadb_team_id` | The club at OpenLigaDB. Look the team number up at `https://api.openligadb.de/getavailableteams/bl2/2026`. **Empty, or `0`, means no matchday detection** — for sports without OpenLigaDB data; the ticker then runs on any day it is started, with the hashtag from `SKYRELAY_HASHTAG`. |
| `[team] league_shortcuts` | Only these leagues count — **exact** shortcuts, such as `bl2, dfb, rlw-frauen`. OpenLigaDB occasionally serves made up leagues with wrong dates (seen in the wild: **"ESP8266"**) and carries the same fixture in variants of the same league (`bl2h` beside `bl2`) on different days. Dropped leagues appear in the log. Its predecessor `league_prefixes` compared prefixes and still applies while this key is empty. |
| `[team] timezone` | The time zone for kickoff and the end of the day |
| `[team_codes]` | The code per team number for building the hashtag. **Add cup opponents as needed** — unknown teams get a three letter stand-in plus a warning in the log. |
| `[layout]` | Where header, source and hashtags go — one line per block, `posts ; spot ; order`. See the README. |
| `[post] prefix` / `source_template` / `source_label` / `standing_hashtag` | The header, how the source line looks, the label of its link, and the standing hashtag on every post |
| `[post] bot_notice` / `bot_notice_marker` | Whether the header appears at all: `always`, `never`, or `auto` (only while the bio does not mention it itself) |
| `[post] image_placeholder` / `video_placeholder` / `video_hint` | Texts for posts without text of their own, and for a failed video upload |
| `[profile] enabled` / `marker` / `line_on` / `line_off` / `line_off_no_match` | The bio status line. Placeholders: `{info}` ("1. Spieltag" / "DFB-Pokal, 1. Runde" / `fallback_match_info`), `{hashtag}`, `{date}`, `{time}` |
| `[schedule] day_end` | When the ticker stops by itself (`23:59` by default) |
| `[schedule] subscribe_renew_seconds` | How often the live subscription is renewed (it lasts only a few minutes) |
| `[files] session` / `state` / `posts_map` / `log` | File names in the program folder. When moving over from an older version, existing `dsc_ticker_*` files are **adopted automatically** — no fresh pairing needed. |
| `[logging] to_file` / `max_bytes` / `backup_count` | The log file and its rotation |
| `[limits] max_video_bytes` / `video_job_timeout_seconds` | Bluesky's limits; normally left alone |

---

## What a post looks like on Bluesky

```
⚽ [Inoffizieller Bot]
🔗 Quelle: WhatsApp-Kanal der Arminia     ← "Quelle" is the clickable link

<channel text, shortened if need be> (1/3)

#DSCWOB #arminia                          ← on the last chunk only
```

- Overlong posts are split (300 graphemes and 3000 bytes); the following chunks
  are **replies** (a thread, so it does not flood timelines).
- URLs in the text are clickable; for links without other media a **preview card**
  (YouTube, say) is attached.
- Images: up to 4 per post, compressed automatically. **Videos are uploaded**
  (Bluesky's limit is ~100 MB, and the server side processing can take a moment);
  if the upload fails, the WhatsApp thumbnail steps in as the image, plus the
  note "🎥 (Video im Original-Kanal)".
- Embed order per post: video > images > link card (Bluesky allows one embed).
- **Edits in the channel:** when a channel post that was already reposted is
  edited, the bot deletes its old Bluesky posts for it and posts the corrected
  version anew (the mapping is in `skyrelay_posts.json`, and holds per day).
  Unchanged redeliveries are recognised by a text hash and ignored. Edits to
  posts **without** a stored mapping (old or foreign posts) are ignored
  entirely — old ticker posts are of no interest.
- Where all of that goes is decided by `[layout]`, and the assistant shows a
  preview of it before anything is published.

---

## Troubleshooting

| Symptom | Cause / remedy |
|---|---|
| `Wire format was corrupt` | A bug in neonize 0.4.0/0.4.1 (NUL bytes truncated, [#199](https://github.com/krypton-byte/neonize/issues/199)) — fixed upstream in **0.4.2** (PR #198). We run `0.4.3.post0`, checked in full on 08.08.2026. |
| `panic: required field neonize.NewsletterMessage.Message not set` | A Go crash when fetching messages catches an invisible **meta message** (a post being edited or deleted). Affects **replay and catch-up** only (pick a smaller `N`); live operation has listened for events since 13.07.2026 and is immune. |
| `VersionError: gencode … runtime …` | protobuf too old → `pip install -U protobuf` (**never** downgrade). |
| Trouble after changing the neonize version | Delete `skyrelay_session.sqlite3` and pair again (the database schema). |
| Hangs or errors right after the first pairing | Normal (the server forces a reconnect); the program waits and retries by itself. Just start it again if it does give up. |
| `⚠️ No DFL code on file for "XY"` in the log | A cup or otherwise unknown opponent → add the correct code under `[team_codes]`. |
| cron: `/bin/sh: 1: …/bin/python3: not found` | A typo in the path (usually the capitalisation). It means the **interpreter**, not Python. Check with `ls -l <path>`. |
| The ticker starts on a day without a match | `league_shortcuts` should prevent that. Check the log for "matches from other leagues ignored", and which league was taken for a matchday. |
| The bio status line stays on "Bot ist an" | The process was killed hard (`kill -9`, a power cut) — then the `finally` never runs. Put it back by hand: `SKYRELAY_PROFILE=off … python skyrelay-matchday.py`. |
| The program "posts nothing" | Is it in the right mode? Check the log: `REPLAY finished…` versus `Listening for new channel posts…`. Environment variables have to stand **before** the python call on the same line. |
| `Error sending close to websocket … EOF` at the end | Cosmetic, from disconnecting cleanly — ignore it. |
| `SIGSEGV … signal arrived during cgo execution` **after** "REPLAY finished" or the end of the day | A cleanup race in neonize: the Go socket thread logs into Python while the interpreter is already shutting down. Purely cosmetic — the work was finished at that point. Since 13.07. the program waits 2 s after disconnecting to avoid it. |
| `Press Ctrl+C to exit` / `whatsmeow.Client INFO` lines | They come from neonize's Go layer, cannot be switched off, and are harmless. |
| `failed to find libmagic` | The system package is missing: `sudo apt install libmagic1`. Without it neonize will not import. |

---

## Environment and installation (for reference)

A Raspberry Pi with a **64 bit system** (aarch64). Installing and updating go
through the scripts, not by hand:

```bash
./install.sh     # once
./update.sh      # later
```

The package versions live in `requirements.txt` — `neonize` is pinned there on
purpose, see [UPGRADE-TEST.md](UPGRADE-TEST.md).
