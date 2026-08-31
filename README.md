# SkyRelay

***English** · [Deutsch](README.de.md)*

Two small bots that forward content to **Bluesky** automatically — written for a
supporters' project that wanted club news available where it was otherwise
missing. Sport is not a requirement: the ticker works just as well for events,
clubs or other channels, then without the fixture lookup.

The destination is deliberately fixed to Bluesky. The **source** is
interchangeable: right now there is a WhatsApp channel connector and an
Instagram one.

> **Unofficial.** This project has no connection to Meta, Bluesky or any club. It
> uses unofficial interfaces — please read [Risks and legal
> matters](#risks-and-legal-matters) before running it.

---

## The two programs

| Program | How it runs | Source (today) |
|---|---|---|
| **`skyrelay-matchday.py`** | Tied to matchdays: runs only on matchdays, listens for live events and posts at once | WhatsApp channel |
| **`skyrelay-feed.py`** | All year round: checks for new posts at regular intervals | Instagram profile |

They are named after **how they run**, not after the source — so that changing
the source later does not force a rename.

### Which source fits which mode?

A source is currently tied to its program. Whether a combination makes sense at
all comes down to a single question: **does the source announce new posts by
itself (push), or does it have to be asked at intervals?**

| Source | Ticker (event driven, time window) | Feed (all year round, polling) |
|---|---|---|
| **WhatsApp channel** | ✅ built — posts appear within seconds | ⚙️ possible: ticker without fixtures and a wide time window (see below) |
| **Instagram** | ❌ not sensible — Instagram has no push; ticker speed would mean polling every minute, which reliably gets the account locked | ✅ built |
| **other sources** | only where push exists | where regular polling is allowed |

**WhatsApp all year round** needs no rebuilding: leave the fixture lookup empty
in the configuration (`[team] openligadb_filter =`), set `day_end` to `23:59`,
and let cron start it daily rather than on matchdays only. The one limitation:
a short gap appears around midnight, because the program stops and, after the
restart, accepts only posts from the day it is currently in.

**New sources** are welcome, but it pays to check before writing code: is there a
usable library? Does the service allow automated access? And above all — can it
announce events, or does it at least tolerate frequent polling without locking
the account? Only once that is settled is it worth separating source from
scheduling. Get in touch through an issue.

### What makes `skyrelay-matchday.py` particular

* **Matchday detection** through [OpenLigaDB](https://www.openligadb.de): on days
  without a match the program stops immediately and opens no connection at all.
  Not limited to football — what is well maintained there is football (divisions
  1–3, the DFB cup, the women's Bundesliga) and ice hockey (DEL, DEL2). For
  sports without data (basketball, or handball league play) the detection can be
  switched off; the ticker then runs on every day it is started.
* **The match hashtag** is generated automatically (`#KSCDSC`, home and away the
  right way round), cup rounds included, and attached to every post.
* **Several teams of one club** can be covered, as long as OpenLigaDB gives them
  the same team number — then it is enough to add their league shortcut to
  `league_shortcuts`. When both play on the same day, a channel message cannot be
  attributed to either match: the posts then carry `overlap_hashtag` instead of
  the match hashtags, while the profile status line names both fixtures.
* **Profile status line:** the first line of the Bluesky bio switches between
  "Bot ist an – 1. Spieltag …" and "Bot ist aus – nächstes Spiel …".
* **Edits** in the channel are noticed: the old Bluesky post is deleted and
  replaced by the corrected version.
* Carries over text, images and video, and builds link preview cards.
* **Voice messages and stickers** from the WhatsApp channel are carried over.
  Bluesky knows neither: a voice message becomes a video with an animated
  waveform (the sound is kept), a sticker becomes an image.
* **Videos are handed in later:** if the upload to Bluesky's video API fails, the
  post goes out immediately with its thumbnail — on a ticker, time is what
  counts. The video stays behind and is retried in later runs; when it succeeds,
  the bot attaches it as a reply to the post. That way a temporary glitch at
  Bluesky does not leave a permanently picture-only post behind. The settings for
  it are the `video_retry_*` keys in `skyrelay.conf`.

## Project status

Both programs run in production and are configurable in full through
`skyrelay.conf` — club, channels, accounts, texts and file names all live there,
and nothing club specific is left in the code.

---

## Requirements

* **Linux, 64 bit** (`x86_64` or `aarch64`). A Raspberry Pi 3B+ is enough, but the
  system has to be 64 bit — there are no packages for 32 bit (`armv7l`).
* **Python 3.10** or newer, plus `python3-venv` and `tzdata`.
* **`libmagic1`** (`sudo apt install libmagic1`) — neonize will not import without
  it.
* **`ffmpeg`** (`sudo apt install ffmpeg`) — only for voice messages from the
  WhatsApp channel. Without it voice messages are skipped and everything else
  carries on unchanged.
* A **Bluesky account** for the bot, with an app password
  (Settings → Privacy and Security → App Passwords).
* For `skyrelay-matchday.py`: a **separate WhatsApp number** whose loss you could
  live with (see the risks). That account has to follow the channel.
* For `skyrelay-feed.py`: a **secondary Instagram account** to fetch with.

## Installation

From nothing, in one line:

```bash
git clone https://github.com/Vijabei/SkyRelay.git && cd SkyRelay && ./install.sh
```

`install.sh` checks the system, the architecture and the Python version, fetches
the newest state, creates a virtual environment under `venv/`, installs the
dependencies — and then starts the setup. It changes **nothing** about the
system: missing system packages are only reported, never installed for you (no
`sudo`, no surprises on someone else's machine).

### The three scripts

| Script | What for |
|---|---|
| `./install.sh` | once: check the system, build the venv, fetch the dependencies |
| `./config.sh` | set up, and change things later — starts the assistant |
| `./update.sh` | fetch the new state, pull the dependencies along, top up the configuration |

`config.sh` knows two shortcuts:

```bash
./config.sh --check          # check the configuration, change nothing
./config.sh --add-missing    # add missing keys along with their explanations
```

### Updating

```bash
./update.sh
```

Fetches the new state (and stops rather than overwriting local changes), pulls
`requirements.txt` along and asks about the keys that have appeared since.
**Running services are left alone** — the next cron run picks the new state up on
its own. A ticker listening through a matchday keeps going with the old one until
it stops in the evening.

## Configuration

The easiest way is the setup assistant:

```bash
./config.sh
```

It opens a **menu in the style of `raspi-config`** — arrow keys, works over SSH.
From the main menu the areas can be reached one at a time, instead of working
through every question in a row:

```
  1  Matchday ticker     WhatsApp channel → Bluesky
  2  Instagram feed      Instagram → Bluesky
  3  Codes for hashtags
  4  Posts and profile
  5  How the posts are built (header, source, hashtags)
  6  Time window
  7  Check the login to Bluesky
  8  Check the configuration
  9  Top up the configuration
  s  Language / Sprache
  0  Save and quit
```

That is also the comfortable way to change things later: correcting a single code
or shifting the time window takes seconds. At the top the menu shows what
required entries are still missing.

If `whiptail` is missing, the assistant falls back to asking line by line;
`SKYRELAY_SETUP_TEXT=1` forces that mode.

### The language of the interface

```ini
[general]
language = en     # de or en; empty = whatever the system says
```

This affects **only the setup assistant**. The log and the console output of the
bots are always English — so that the same message reads the same wherever it
turns up, whoever reads it and wherever it gets pasted.

In the assistant the language sits under item `s`. What is offered is whatever a
catalogue has been built for; the catalogues are built by `./install.sh` and
`./update.sh` on their own. Without them the interface speaks German — the
language it is written in.

Contributing another language: see [CONTRIBUTING.md](CONTRIBUTING.md).

### What is actually in effect?

A configuration goes opaque over the years: a missing key means the program's
own default quietly applies — the file does not show you what is really at work.
So each bot answers that question:

```bash
venv/bin/python skyrelay-feed.py --show-config
```

It lists every value the programs read, together with where it comes from:

```
[post]
  prefix                        (file)       ⚽ [Inoffizieller Bot]
  source_label                  (file)       WhatsApp-Kanal des Vereins
[feed]
  source_label                  (default)    Beitrag auf Instagram
```

**Commenting a line out switches nothing off.** A line with a `#` in front counts
as missing, and then the program's default applies — the setting still works,
only with a different value. To *leave something out*, make the value empty:

```ini
# no source line in the post:
source_label =
# no header:
prefix =
```

The same goes for `standing_hashtag`. In the listing above the difference is
readable from the origin: `(file)` means "this is what you wrote", `(default)`
means "the program decided".

### How SkyRelay keeps to the character limit

Bluesky limits a post twice — the lexicon definition of `app.bsky.feed.post`
says it exactly:

```json
{ "type": "string", "maxLength": 3000, "maxGraphemes": 300 }
```

So **300 graphemes and 3000 bytes**. A grapheme is what a person sees as one
character: `👨‍👩‍👧‍👦` is seven code points and 25 bytes and still counts as
**one**. There is no API to ask — the counter in the Bluesky app runs in the
browser, and the server only says no once the post is finished.

SkyRelay therefore counts for itself, by the same rule (UAX #29), and works out
before splitting **what this very post actually carries**: the blocks the layout
puts on it, the blank lines between them, and the `(2/3)` counter. Since the
number of posts in turn decides the width of that counter, this is settled in
rounds until it comes to rest.

What that is worth shows in the same text with different source lines:

| Source line | Frame | Posts |
|---|---|---|
| `🔗 [Quelle]: {label}` with a label | 66 characters | 2 |
| `🔗 [Quelle]` | 50 characters | **1** |

Breaks are taken in this order of preference: paragraph, sentence, word, and only
then hard — **never** inside a URL (Bluesky does not shorten links, and half of
one is worthless) and never inside a grapheme cluster.

For the counting the package `regex` is recommended, which implements UAX #29 in
full. Without it SkyRelay carries on with a built-in approximation that covers
what actually turns up — umlauts, emoji with skin tones, ZWJ sequences, flags.
**A `git pull` without `pip install` therefore stays harmless**; the bot only
says at startup which way of counting it is using.

### Does the bot notice have to be in every post?

Where the Bluesky bio already says plainly that a bot is writing,
`⚽ [Inoffizieller Bot]` above every post is wasted space — about 22 characters
of 300:

```ini
[post]
bot_notice = auto
bot_notice_marker = Bot
```

| Value | Meaning |
|---|---|
| `always` | always (the default — what SkyRelay always did) |
| `never` | never |
| `auto` | only while the profile description does not carry the notice itself |

With `auto` the description is checked **once per run** and the outcome written
to the log. SkyRelay skips its own status line while doing so: that line reads
"🟢 **Bot** ist an", and a search for "Bot" would otherwise always find what the
bot itself wrote rather than what you typed into the bio.

If the profile cannot be read, the notice stays. The same goes for a dry run,
which deliberately does not log in — leaving out a disclaimer on a guess would be
the worse of the two mistakes.

### What the source line looks like

Which part of the line carries the link is decided by a template. What stands in
**[square brackets]** becomes the link; `{label}` is the label from
`[post] source_label` or `[feed] source_label`:

```ini
[post]
source_template = 🔗 [Quelle]: {label}
```

| Template | Result (bold = clickable) |
|---|---|
| `🔗 [Quelle]: {label}` | 🔗 **Quelle**: WhatsApp-Kanal des Vereins |
| `🔗 [Quelle]` | 🔗 **Quelle** |
| `🔗 Quelle: [{label}]` | 🔗 Quelle: **WhatsApp-Kanal des Vereins** |

The first line is the default. Up to version 1.3 the third one applied: the link
hung on the label, and the word in front of it was dead text. The second variant
saves about 28 characters — which tells against Bluesky's limit of 300.

Without a label the colon disappears with it. Without any square brackets the
whole line becomes the link — a source line that cannot be clicked would be
pointless.

### Where the header, the source and the hashtags go

Until now that was fixed: header and source at the top of the first post, the
hashtags at the bottom of the last. The `[layout]` section turns it into a
decision — one line per block:

```ini
[layout]
#                  posts ; spot ; order
prefix           = first ; top    ; 1
source           = first ; top    ; 2
match_hashtag    = last  ; bottom ; 1
standing_hashtag = last  ; bottom ; 2
```

| Column | Values | Meaning |
|---|---|---|
| posts | `first`, `last`, `all`, `none` | on which posts of the thread |
| spot | `top`, `bottom` | above or below the text |
| order | a number | which comes first when two land in the same spot |

The values above are the defaults and produce exactly the look SkyRelay always
had. Leaving a block out entirely can be done two ways: enter `none`, or make its
content empty (`source_label =`).

The most comfortable way is item 5 of the assistant — it shows the matrix and, on
request, a **preview of the finished post**, before anything is published:

```
── post 1 of 2 ──────────────────────────────
⚽ [Inoffizieller Bot]
🔗 Quelle: WhatsApp-Kanal des Vereins

Beispieltext, Teil 1. (1/2)

── post 2 of 2 ──────────────────────────────
Beispieltext, Teil 2. (2/2)

#DSCWOB #arminia
```

### Checking the configuration

A misspelt key simply has no effect — with no error message, because the program
quietly takes its default instead. So both bots check the configuration on
request:

```bash
venv/bin/python skyrelay-feed.py --check-config
```

It reports what no program reads (a typo, or something left over), what is
missing and therefore falls back to a default, and what has stayed undocumented
in `skyrelay.conf.example`. The call connects to nothing, changes nothing and
posts nothing; the exit status is 0 as long as there are no problems. The
assistant shows the same report under item 8 — there including changes that have
not been saved yet.

### Topping up missing keys

After an update the template knows keys that the own file does not. They can be
added along with their explanations — existing values, order and comments stay
untouched, this only ever adds:

```bash
./config.sh --add-missing
```

The call first shows what would be added and asks. A backup is written as
`skyrelay.conf.bak` beforehand. The same sits under item 9 of the menu.

For a first setup the assistant walks through the same points as before:

| Purpose | How it goes |
|---|---|
| **A sport with fixtures** | League and club are **looked up live at OpenLigaDB** (no hunting for team numbers), and the **table of codes for the hashtags is pre-filled** — for football divisions 1–3 with the codes in common use, otherwise with derivations clearly marked as proposals (`?`). Besides football the other maintained leagues can be picked, the DEL for instance. |
| **A sport without fixtures** | For sports OpenLigaDB does not carry (basketball, handball league play): no matchday detection, a changing hashtag through `SKYRELAY_HASHTAG`. |
| **Something else** | For clubs, events, projects: as above, and additionally with **neutrally worded default texts** instead of football language. |

In every case the post texts, the profile status line and the time window are
asked for, the Bluesky login is checked on request, and the finished
`skyrelay.conf` is written from all of it. Codes once maintained are kept — even
after a move between divisions, so that cup opponents from other leagues are
still named correctly. Calling it again is the way to change things: existing
values are offered as defaults, and a backup of the old file is written.

Anyone who would rather work by hand copies the commented template:

```bash
cp skyrelay.conf.example skyrelay.conf
```

For the matchday ticker at least `[bluesky] handle` and
`[source] channel_invite_link` are needed; for a different club also `[team]`
(the OpenLigaDB search term and team number) and the codes under `[team_codes]`.
For the Instagram mirror the `[feed]` section is enough (profile, secondary
account and optionally a Bluesky account of its own). Every section is commented
in the template, and an overview lives in the
[cheat sheet](CHEATSHEET-matchday.md).

Your own `skyrelay.conf` is in `.gitignore` and does not belong in the
repository. Several clubs run side by side through
`SKYRELAY_CONFIG=/path/to/file.conf`.

Anyone moving over from an older version needs **no fresh pairing**: existing
`dsc_ticker_*` files are adopted on the first start.

The **app password never belongs in a file**, but in an environment variable —
one per bot, so that the attribution stays unambiguous:

```bash
export BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"; export BLUESKY_FEED_APP_PASSWORD="yyyy-yyyy-yyyy-yyyy"
```

If both bots use the same account, both variables simply hold the same password.

⚠️ **cron reads neither `~/.bashrc` nor `~/.profile`.** For automatic operation
the variables belong **at the top of the crontab** (`crontab -e`) — there without
quotation marks, or they would become part of the value:

```cron
BLUESKY_TICKER_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
BLUESKY_FEED_APP_PASSWORD=yyyy-yyyy-yyyy-yyyy
```

## The first pairing with WhatsApp

Once, and **interactively in a terminal** (not from cron):

```bash
SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
```

A QR code appears, which you scan on the phone under *WhatsApp → Settings →
Linked devices → Link a device*. From experience: turn the screen brightness up
and enlarge the terminal a lot, or the camera finds too little contrast. As an
alternative, pair by numeric code — add `SKYRELAY_PAIR_PHONE=49xxxxxxxxx` for
that.

Signing in through `web.whatsapp.com` in a browser does **not** help: the program
is a linked device of its own with a session of its own. That session then lives
in `*_session.sqlite3` and is reused by every later run.

## Running it

In normal operation a daily cron entry is enough — whether there is a match today
is something the program decides for itself:

```cron
0 6 * * * BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /path/to/SkyRelay/venv/bin/python3 /path/to/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
```

Two common stumbling blocks: paths are **case sensitive**, and redirecting the
output (`>> skyrelay.log`) is **not** necessary — the program writes its own log,
and otherwise every line would appear in it twice.

Is the bot running?

```bash
pgrep -af skyrelay-matchday
```

Every other mode (test runs, catching up on missed posts, single tests) lives in
**[CHEATSHEET-matchday.md](CHEATSHEET-matchday.md)**.

### The Instagram mirror (`skyrelay-feed.py`)

Create the Instagram session of the secondary account once:

```bash
venv/bin/instaloader -l your_secondary_account
```

After that call it regularly from cron — the program carries over whatever is new
since the last run:

```cron
*/15 * * * * BLUESKY_FEED_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /path/to/SkyRelay/venv/bin/python3 /path/to/SkyRelay/skyrelay-feed.py >/dev/null 2>&1
```

If you use a **different** Bluesky account for Instagram than for the ticker,
enter it under `[feed] bluesky_handle` and give the app passwords separately
(see above).

---

## Documentation

| File | Contents |
|---|---|
| `README.md` | this document (German: `README.de.md`) |
| `skyrelay-setup.py` | the setup assistant (creates and changes `skyrelay.conf`) |
| `skyrelay.conf.example` | the commented template of every setting |
| `skyrelay_common.py` | shared building blocks of both programs (log, images, video upload) |
| `skyrelay_config.py` | checking, showing and topping up the configuration |
| `skyrelay_layout.py` | how posts are built, character counting, splitting |
| `skyrelay_i18n.py` | translations of the interface |
| `tools/i18n.sh` | creating, aligning and compiling the catalogues |
| `skyrelay-testlauf.py` | plays real channel messages through the ticker pipeline without publishing |
| `CHEATSHEET-matchday.md` | every environment variable, mode, file and remedy (German: `CHEATSHEET-matchday.de.md`) |
| `ISSUE-DRAFT-neonize-newsletter-panic.md` | a prepared bug report for **neonize**: the crash while fetching history |
| `ISSUE-DRAFT-whatsmeow-newsletter-audio-hmac.md` | a prepared bug report for **whatsmeow**: voice messages cannot be downloaded |

## Known limitations

* **`neonize` is pinned to `0.4.3.post0`** and was checked in full on 08.08.2026
  (see [UPGRADE-TEST.md](UPGRADE-TEST.md)). Back the session file up before
  switching — the database schema changes.
* **Ctrl+C can occasionally be caught by the Go part** (from 0.4.x on): the
  program then ends with `Quit` without cleaning up, and the profile status line
  is left saying "Bot ist an". This was seen once; normally the cleanup runs
  properly. Reset it with `SKYRELAY_PROFILE=off` if need be. Of no consequence
  under cron — there the ticker ends properly at the end of the day.
* **A crash on channel posts without content:** when the channel deletes a post,
  an empty message is left behind, and the underlying Go part crashes on it. This
  affects **only** the catch-up modes, not normal operation — that one listens
  for events and is not touched by it.
  How much it affects you depends on the channel: if such a message lies far
  back, a smaller value is enough. If it lies **directly behind the newest
  post**, `SKYRELAY_REPLAY` is unusable — fetching two messages already crashes
  and only `1` gets through. (In the channel mirrored here that was the case on
  19.08.2026.) To check against real material, `skyrelay-testlauf.py` pages
  backwards past the broken spot from an explicit starting point:

  ```
  venv/bin/python3 skyrelay-testlauf.py --latest
  venv/bin/python3 skyrelay-testlauf.py --before <serverID> --type audio,sticker
  ```
* **`instaloader` is pinned to 4.15.2.** Version 4.15.3 moved the profile lookup
  to an endpoint Instagram has been throttling since early August 2026 — the very
  first request ends in a "429 Too Many Requests"
  ([instaloader#2726](https://github.com/instaloader/instaloader/issues/2726),
  open). Only update once that is solved.
* **Polls** are skipped: Bluesky has no such format.
* **Voice messages need a detour to download.** Channel media sit unencrypted
  behind `directPath` — images and videos therefore carry no `mediaKey` at all.
  Voice messages drag one along, whereupon whatsmeow tries to decrypt and fails
  with `invalid media hmac`. On exactly that error SkyRelay downloads a second
  time without the key. Measured on 19.08.2026: **none** of 5 voice messages
  downloaded the regular way, **all five** did without the key, byte for byte.
* **Animated stickers** lose their movement — the first frame is carried over,
  because Bluesky plays no animations.
* **Voice messages** appear as video and therefore have no sensible image
  description. Anyone who cares about accessibility should bear that in mind.
* **The waveform colour:** `waveform_color` in `[audio]` takes ffmpeg *colour
  names* only (`White`, `DodgerBlue`, …). Hex values such as `0x38BDF8` are
  discarded silently by ffmpeg, which then draws green; SkyRelay warns about it
  in the log.
* **Videos** up to roughly 100 MB; larger ones are not carried over (Bluesky's
  limit).
* **Videos handed in later** end up as a reply below the post rather than in the
  post itself — Bluesky has no way of changing a post after the fact. If the post
  is deleted in the meantime (through an edit in the WhatsApp channel, say),
  handing in fails and the job is dropped after `video_retry_max_attempts`
  attempts.
* Several images in one post: Bluesky takes at most four.

## Risks and legal matters

* **WhatsApp:** access happens through an unofficial client. That breaches the
  terms of service and can get the **number banned**. Use only a number whose
  loss would not hurt you — and never your private one. The same applies to the
  secondary Instagram account.
* **Copyright:** you are carrying over someone else's content. Work out for
  yourself whether you may, and mark the bot as unofficial (the posts carry a
  notice and a source line for that).
* **No connection** to Meta, Bluesky or any club. Trademarks and names are used
  descriptively only.
* **No warranty:** the interfaces are unofficial and can change at any time. Use
  at your own risk.

## Projects used

SkyRelay is mostly wiring — the actual work is done by these projects, and the
thanks belong to them:

| Project | What for | Licence |
|---|---|---|
| [neonize](https://github.com/krypton-byte/neonize) | reaching WhatsApp from Python | Apache-2.0 |
| [whatsmeow](https://github.com/tulir/whatsmeow) | the WhatsApp implementation neonize builds on | MPL-2.0 |
| [atproto (Python SDK)](https://github.com/MarshalX/atproto) | talking to Bluesky | MIT |
| [Instaloader](https://github.com/instaloader/instaloader) | fetching Instagram posts | MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | image processing and compression | HPND |
| [Requests](https://github.com/psf/requests) | HTTP calls | Apache-2.0 |
| [Segno](https://github.com/heuer/segno) | the QR code in the terminal for pairing | BSD-3-Clause |
| [Babel](https://github.com/python-babel/babel) | building the translation catalogues | BSD-3-Clause |
| [regex](https://github.com/mrabarnett/mrab-regex) | counting graphemes to UAX #29 | Apache-2.0 |
| [OpenLigaDB](https://www.openligadb.de) | free fixture data (matchday, kickoff, opponent) | a community project |

## Contributing

Improvements are welcome — above all **club codes** that are missing or unusual.
That takes no programming: an issue with the team number and the code is enough.
How to go about it is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[PolyForm Noncommercial License 1.0.0](LICENSE) — non-commercial use is free,
including supporters' projects, non-profit organisations and educational
institutions. The copyright notice has to be kept.

**Commercial use** — by clubs as businesses, by media houses, or by
advertising-funded offerings — requires a separate licence. Please ask through an
issue in this repository.

A note: this is a "source available" licence, not an open source licence in the
OSI sense — the restriction to non-commercial use is incompatible with that.
