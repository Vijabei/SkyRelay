# Beta: the Instagram feed with the fix from instaloader#2795

***English** · [Deutsch](BETA.de.md)*

This branch is `main` with a single difference: the Instagram feed does not take
instaloader from PyPI but from the still open pull request
[instaloader/instaloader#2795](https://github.com/instaloader/instaloader/pull/2795),
pinned to commit `3e4d5ac0ec545a771a3a4b0b82cb313f32acb29f` (6 November 2026), so
that nothing changes along with it unchecked.

## Why

Since 2 November 2026 Instagram answers logged-in sessions on `graphql/query`
with `400 Bad Request – feedback_required`, with instaloader 4.16 too, and with a
session that had worked until then
([instaloader/instaloader#2792](https://github.com/instaloader/instaloader/issues/2792)).
The account the feed reads with is not the cause. The fix sends the profile and
post queries to `api/graphql`, as the Instagram website itself does, and repairs
the login check. It only changes which Instagram queries are made; it talks to
no other servers.

Checked on the Pi on 8 November 2026: with the fix the feed finds the posts again.

## Switching

```bash
cd ~/SkyRelay
git fetch origin
git checkout beta
./update.sh
```

`update.sh` installs the fix. pip alone would not, because both versions call
themselves "4.16"; that is why `tools/pakete_angleichen.py` checks where each
package came from after the installation.

Then check before cron asks again:

1. The feed names its instaloader at startup. The log has to say:
   `Using instaloader 4.16 from https://github.com/ppdms/instaloader @ 3e4d5ac0ec.`
2. Lift a running pause: `rm skyrelay_feed_pause.txt`
3. Dry run (publishes nothing):

```bash
set -a && . ~/.skyrelay.env && set +a
SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-feed.py
```

If it finds the posts, the next cron run takes over by itself. If
`feedback_required` comes back, the feed starts a new pause; then the fix does not
help in your case, and it is back to `main`.

Posts that appeared during the block are only caught up on as far as they are
among the newest `[feed] posts_to_check`.

## Back to `main`

```bash
git checkout main
./update.sh
```

`update.sh` puts instaloader back to the version from PyPI by itself.

## When to go back

As soon as instaloader publishes a newer version, the heartbeat reports it. `main`
then raises the pin in `requirements.txt`, and going back is the usual way. This
branch is deleted after that.
