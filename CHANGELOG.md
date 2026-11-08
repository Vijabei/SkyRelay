# Changelog

***English** · [Deutsch](CHANGELOG.de.md)*

What has changed in SkyRelay, newest first. The numbers refer to the pull
requests on GitHub.

## 8 November 2026

### Fixed

- **Posts over 300 graphemes.** Splitting a long text could swing between one post and two. A text that fits as the first of two posts but not as the only one (the hashtags go on that one) ended up as a single post measured with the budget of the two-post thread. Bluesky refused it, on 30 October an Instagram post of 313 graphemes. This affected ticker and feed.
  - The last part is now also measured as the post it really becomes.
  - At the end every part is checked once more against its actual post. If something does not fit, the text is split more cautiously.
- **Feed with more media than text.** When every video gets a post of its own, the thread is longer than the text. The caption is now measured for that thread (`split_body(..., min_total=...)`); before, the counter could push a post over the limit.

### New

- **`tools/pruefe_aufteilung.py`** checks the splitting with the borderline case of 30 October and random texts across different `[layout]` settings: every post within the limits, no text lost. Exit status 0 as long as nothing is found. With the old code: 332 findings, with the new one: none.

## 7–8 November 2026

### Fixed

- **WhatsApp lets the ticker in again** ([#31](https://github.com/Vijabei/SkyRelay/pull/31)). Since 7 November WhatsApp refused the connection with `Client outdated (405)`. The ticker now uses `neonize==0.5.2` with the whatsmeow of 21.09.2026 instead of 0.4.3.post0 with the whatsmeow of 09.07. The interfaces the ticker calls are unchanged. The session's database schema has changed, so back up `skyrelay_session.sqlite3` before switching.
- **The ticker starts again without a fixture list** ([#36](https://github.com/Vijabei/SkyRelay/pull/36)). With an empty `[team] openligadb_filter`, which the template offers for sports OpenLigaDB does not carry, the ticker ended with "still missing". The search term is no longer required.
- **Without a fixture list the bio stayed on "Bot ist an"** ([#36](https://github.com/Vijabei/SkyRelay/pull/36)). `get_next_matches()` queried OpenLigaDB with an empty search term, failed, and the status line was not put back. Without a fixture list (`WITHOUT_SCHEDULE`) OpenLigaDB is no longer asked at all, and `line_off_no_match` applies.

### New: the heartbeat (`skyrelay-heartbeat.py`)

Introduced in [#32](https://github.com/Vijabei/SkyRelay/pull/32), extended in [#33](https://github.com/Vijabei/SkyRelay/pull/33) to [#35](https://github.com/Vijabei/SkyRelay/pull/35).

#### Status files

New module `skyrelay_health.py`. Ticker and feed write their state to `skyrelay_health.json` and `skyrelay_feed_health.json`:

- for each check (`whatsapp`, `instagram`, `bluesky`, `openligadb`, `posting`) the result with the kind of error, since when it has been so, and when it last worked;
- while the ticker listens, a sign of life, written at most once a minute.

Files are written atomically through a `.tmp` file. A failure to write never stops a bot. If a second, living process of the same bot is already running, a further run leaves the file alone.

#### The ticker listens for refusals from WhatsApp

It registers the neonize events `ClientOutdatedEv`, `ConnectFailureEv` (evaluating `ConnectFailureReason`), `LoggedOutEv`, `TemporaryBanEv`, `StreamReplacedEv`, `KeepAliveTimeoutEv`, `KeepAliveRestoredEv` and `DisconnectedEv`. The events are looked up by name, so a different neonize version that lacks one of them does not fail.

- On a final refusal (outdated client, logged out, banned, pushed out) it writes what to do into the log straight away and ends. Until now it waited 5 minutes for a timeout.
- If the refusal only comes while it is listening, the loop ends as well.

#### Daily WhatsApp probe

`skyrelay-matchday.py --probe` connects once, resolves the channel and leaves again. Nothing is posted, the bio is left alone.

- With `[heartbeat] enabled` and `whatsapp_probe`, the probe runs by itself on days without a match, as part of the daily cron run.
- If there is no pairing, the probe does not wait for a QR scan but reports it.

#### Session lock

An `flock` lock on `skyrelay_session.sqlite3.lock` keeps two processes from being connected with the same WhatsApp session at the same time. The probe skips itself while a ticker runs. A ticker waits at most 120 s for a running probe and then ends with a hint.

#### The heartbeat itself

Runs from cron every 15 minutes. It evaluates the status files and reports changes only: a new problem, a reminder every `reminder_hours`, the all-clear.

| What | Condition |
|---|---|
| ticker crashed | process gone |
| ticker hangs | no sign of life for `2 × video_job_timeout + 5 min` |
| WhatsApp connection disrupted | for longer than `ticker_stale_minutes` |
| WhatsApp refuses | outdated client, logged out, banned, not paired, channel not found |
| OpenLigaDB unreachable | the fetch failed |
| Instagram blocks | block by Instagram, with the end of the pause |
| feed without success | for `feed_stale_hours` |
| Bluesky login / post | failed |
| cron not running | ticker for 36 h, feed for `feed_stale_hours` without a start |
| disk space | below `disk_min_mb` |
| ticker ends with an error | safety net when none of the checks above applies |

A single failed post is reported once, without reminders and without an all-clear.

#### Channels

- **Bluesky direct message** from the bot account (`bluesky_sender`, the ticker account by default) to `bluesky_recipient`.
  - Long messages are split into parts under 1000 characters.
  - After a failure it tries again only after 1 h, then at growing intervals up to 6 h. That spares the bot account's login limit.
  - If the recipient is the sending account itself, this is reported clearly ([#35](https://github.com/Vijabei/SkyRelay/pull/35)).
- **Outside watcher** through `ping_url` ([#34](https://github.com/Vijabei/SkyRelay/pull/34)). The service is recognised by the URL:
  - healthchecks.io: `POST <url>` or `<url>/fail`, with the description of the problem as the body;
  - Uptime Kuma: `/api/push/` with `status` and `msg`.
  - `ping_mode` is `problems` (failure ping while a problem lasts) or `alive` (signs of life only).
  - The old names `kuma_push_url` and `kuma_mode` are still read and reported as out of use by the configuration check.

#### New versions on PyPI

Once a day the heartbeat checks `neonize` and `instaloader`. For neonize, what counts is the bundled whatsmeow, not the version number ([#33](https://github.com/Vijabei/SkyRelay/pull/33)):

- It downloads each new version once (about 20 MB) and reads the whatsmeow version from the Go build info of the library. The result is cached per version.
- Notices, and the message on a 405, only name a new neonize as a way out when its whatsmeow really is newer.
- Finding: neonize 0.6.1 to 0.10.0 carry the same whatsmeow as 0.5.2 and do not help against another 405.

#### Manual calls and language

- `--status` shows what it would report right now and sends nothing.
- `--test` sends a test message through every channel set up.
- Also `--check-config` and `--show-config`.
- The messages follow `[general] language` (de/en). The log `skyrelay-heartbeat.log` is in English.

### Changed

- **Configuration:** new section `[heartbeat]`, switched off by default, plus `[files] health` and `[feed] health`. `./update.sh` adds the keys together with their explanations. Whoever switches nothing on notices no difference.
- **Setup assistant:** new section "7 · Heartbeat" with a test button; "Check and top up" is now item 8. A switch whose key is missing shows the program's default instead of always "on".
- **Feed:** writes Instagram blocks (with the end of the pause), session errors, the Bluesky login and posting errors into its status file.
- **Documentation:** both READMEs with a section on the heartbeat, both cheat sheets with `--probe`, the new files and remedies for trouble. `requirements.txt` records the neonize findings. The English translation is complete (403/403).
- **`.gitignore`:** status files, the heartbeat's state and the session lock.

### Notes for updating

1. Run `./update.sh`.
2. Switch the heartbeat on under `./config.sh` → "7 · Heartbeat".
3. For direct messages the sending bot account needs an app password with "Allow access to your direct messages", either in place of its existing password or as `BLUESKY_HEARTBEAT_APP_PASSWORD`. It goes into `~/.skyrelay.env`. Follow the bot with your own account.
4. Add the cron entry:

   ```cron
   */15 * * * * set -a && . $HOME/.skyrelay.env && set +a && /path/to/SkyRelay/venv/bin/python3 /path/to/SkyRelay/skyrelay-heartbeat.py >/dev/null 2>&1
   ```

5. The ticker still has to start daily, or the daily probe is missing.

### Not checked in the test environment

The WhatsApp events, the probe and the pings were only tested against simulated counterparts, because the test environment could not reach WhatsApp, bsky.social or hc-ping.com. The Bluesky direct message and the ping to healthchecks.io have since been checked with `--test` on the Pi.
