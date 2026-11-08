# Beta: Instagram-Feed mit dem Fix aus instaloader#2795

*Deutsch · [English](#english)*

Dieser Branch ist `main` plus eine einzige Abweichung: Der Instagram-Feed nutzt
instaloader nicht von PyPI, sondern den Stand aus dem noch offenen Pull Request
[instaloader/instaloader#2795](https://github.com/instaloader/instaloader/pull/2795).
Fest auf den Commit `3e4d5ac0ec545a771a3a4b0b82cb313f32acb29f` (06.11.2026), damit
sich nichts ungeprüft mitändert.

## Warum

Seit dem 02.11.2026 antwortet Instagram eingeloggten Sitzungen auf `graphql/query`
mit `400 Bad Request – feedback_required`, auch mit instaloader 4.16 und einer
Sitzung, die bis dahin lief
([instaloader/instaloader#2792](https://github.com/instaloader/instaloader/issues/2792)).
Das liegt nicht am Abrufkonto. Der Fix stellt die Abfragen von Profil und Beiträgen
auf `api/graphql` um, wie es die Instagram-Website selbst tut, und repariert die
Login-Prüfung. Er ändert ausschließlich, welche Instagram-Abfragen gestellt werden;
fremde Server spricht er nicht an.

Getestet hier: Der Feed läuft mit dem Fix bis zur Instagram-Abfrage durch.
Instagram selbst war aus der Testumgebung nicht erreichbar. Ob der Abruf klappt,
zeigt erst der Trockenlauf auf dem Pi (Schritt 3).

## Umsteigen

```bash
cd ~/SkyRelay
git fetch origin
git checkout beta
./update.sh
```

`update.sh` installiert den Fix. pip allein täte das nicht, weil sich beide
Fassungen „4.16“ nennen; deshalb gleicht `tools/pakete_angleichen.py` nach der
Installation die Herkunft ab.

Dann prüfen, bevor cron wieder anfragt:

1. Der Feed nennt beim Start die Herkunft. Im Trockenlauf muss stehen:
   `Using instaloader 4.16 from https://github.com/ppdms/instaloader @ 3e4d5ac0ec.`
2. Die Pause aufheben: `rm skyrelay_feed_pause.txt`
3. Trockenlauf (veröffentlicht nichts):

```bash
set -a && . ~/.skyrelay.env && set +a
SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-feed.py
```

Findet er die Beiträge, übernimmt der nächste cron-Lauf von selbst. Kommt wieder
`feedback_required`, legt der Feed eine neue Pause ein; dann hilft der Fix bei
dir nicht, und es geht zurück auf `main`.

## Zurück auf `main`

```bash
git checkout main
./update.sh
```

Seit `tools/pakete_angleichen.py` auch auf `main` liegt, setzt `update.sh`
instaloader dabei von selbst auf die Fassung von PyPI zurück. Fehlt das Werkzeug
dort noch, einmal von Hand:

```bash
venv/bin/pip install --force-reinstall --no-deps instaloader==4.16
```

## Wann zurück

Sobald instaloader eine neuere Fassung veröffentlicht, meldet der Wächter sie.
Dann hebt `main` die Festlegung in `requirements.txt` an, und der Weg zurück ist
der normale. Dieser Branch wird danach gelöscht.

---

## English

This branch is `main` with one difference: the Instagram feed uses instaloader
not from PyPI but from the still open pull request
[instaloader/instaloader#2795](https://github.com/instaloader/instaloader/pull/2795),
pinned to commit `3e4d5ac0ec545a771a3a4b0b82cb313f32acb29f` (6 November 2026).

**Why:** since 2 November 2026 Instagram answers logged-in sessions on
`graphql/query` with `400 Bad Request – feedback_required`, with instaloader 4.16
too ([instaloader/instaloader#2792](https://github.com/instaloader/instaloader/issues/2792)).
The fix sends the profile and post queries to `api/graphql`, as the Instagram
website does, and repairs the login check. It only changes which Instagram
queries are made.

**Switch:**

```bash
git fetch origin
git checkout beta
./update.sh
```

Then lift the pause (`rm skyrelay_feed_pause.txt`) and do a dry run with
`SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-feed.py`. The log has to say
`Using instaloader 4.16 from https://github.com/ppdms/instaloader @ 3e4d5ac0ec.`

**Back:**

```bash
git checkout main
./update.sh
```

If `main` does not have `tools/pakete_angleichen.py` yet, once by hand:
`venv/bin/pip install --force-reinstall --no-deps instaloader==4.16`

**When:** as soon as instaloader publishes a newer version, the heartbeat reports
it; `main` then raises its pin and this branch is deleted.
