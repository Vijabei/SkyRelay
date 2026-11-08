# Beta: Instagram-Feed mit dem Fix aus instaloader#2795

*[English](BETA.md) · **Deutsch***

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

Auf dem Pi geprüft am 08.11.2026: Mit dem Fix findet der Feed die Beiträge wieder.

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

1. Der Feed nennt beim Start die Herkunft. Im Protokoll muss stehen:
   `Using instaloader 4.16 from https://github.com/ppdms/instaloader @ 3e4d5ac0ec.`
2. Eine laufende Pause aufheben: `rm skyrelay_feed_pause.txt`
3. Trockenlauf (veröffentlicht nichts):

```bash
set -a && . ~/.skyrelay.env && set +a
SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-feed.py
```

Findet er die Beiträge, übernimmt der nächste cron-Lauf von selbst. Kommt wieder
`feedback_required`, legt der Feed eine neue Pause ein; dann hilft der Fix bei
dir nicht, und es geht zurück auf `main`.

Beiträge, die während der Sperre erschienen sind, holt der Feed nur nach, soweit
sie unter den neuesten `[feed] posts_to_check` liegen.

## Zurück auf `main`

```bash
git checkout main
./update.sh
```

`update.sh` setzt instaloader dabei von selbst auf die Fassung von PyPI zurück.

## Wann zurück

Sobald instaloader eine neuere Fassung veröffentlicht, meldet der Wächter sie.
Dann hebt `main` die Festlegung in `requirements.txt` an, und der Weg zurück ist
der normale. Dieser Branch wird danach gelöscht.
