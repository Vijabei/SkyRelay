# Changelog

*[English](CHANGELOG.md) · **Deutsch***

Was sich an SkyRelay geändert hat, das Neueste oben. Die Nummern verweisen auf
die Pull Requests auf GitHub.

## 21.11.2026

### Geändert

- **neonize 0.12.4 statt 0.5.2.** Es bringt whatsmeow vom 16.11.2026 mit (Client 2.3000.1054751086 statt 2.3000.1047769893). Das ist Vorsorge: WhatsApp weist alte Client-Versionen nach einigen Monaten ab, so mit 0.4.3.post0 am 07.11. Die Schnittstellen, die der Ticker aufruft, sind gleich geblieben. 0.11.0 bis 0.12.4 bringen alle dieselbe whatsmeow mit.
  - `protobuf>=7.36.0`: Die Wheels von 0.12.4 sind damit gebaut, mit älterem protobuf scheitert der Import mit einem `VersionError`.
  - Neue Abhängigkeiten sind `cryptography` und `pycryptodome`; pip installiert sie selbst.
  - Das Datenbankschema der Sitzung geht von v15 auf v16. Vor dem Wechsel `skyrelay_session.sqlite3` sichern. Laut whatsmeow kann 0.5.2 das neue Schema weiter lesen.
- **Wiederholen und Nachholen überspringen leere Nachrichten sofort.** Ein bearbeiteter oder gelöschter Kanalbeitrag hinterlässt eine unsichtbare Nachricht ohne Inhalt. Seit neonize 0.5.2 liefert ein Verlaufsabruf sie als leeren Eintrag, statt abzustürzen (neonize#210/#211). Der Ticker überspringt ihn jetzt, bevor er sich bei Bluesky anmeldet; `SKYRELAY_REPLAY=N` kann deshalb weniger als `N` Beiträge durchspielen.
- **`skyrelay-testlauf.py`** sucht standardmäßig ab der neuesten Nachricht; `--before` braucht es nicht mehr.
- **Das Protokoll des Tickers ist jetzt durchgehend englisch**, wie das von Feed und Wächter. Rund 45 Zeilen waren noch deutsch, etwa `Würde posten`, `Video geladen`, `Kanal-Nachricht mit ServerID` oder `Fehler beim Reposten`. Wer im Protokoll nach den alten Texten sucht, findet jetzt `Would post`, `Video downloaded`, `Channel message with server ID` und `Reposting failed`. Beiträge und Bio-Statuszeile bleiben, wie sie konfiguriert sind.
- **Die Roh-Vorschau im Protokoll nur noch für Unbekanntes.** Ein Bild, Video, Sticker oder eine Sprachnachricht ohne Bildtext schrieb die ganze Nachricht als Bytes ins Protokoll, mit Prüfsummen und `mediaKey`. Jetzt kommt die Vorschau nur noch, wenn eine Nachricht weder Text noch eine bekannte Medienart hat, etwa bei einer Umfrage.

### Entfernt

- **Der zweite Download von Sprachnachrichten ohne `mediaKey`.** whatsmeow wollte Sprachnachrichten aus Kanälen wegen ihres `mediaKey` entschlüsseln und scheiterte mit `invalid media hmac`; SkyRelay lud dann ein zweites Mal ohne den Schlüssel. Seit whatsmeow#1229 (in neonize 0.5.2 und später) übergeht whatsmeow so einen Schlüssel selbst. Scheitert eine Sprachnachricht doch mit `invalid media hmac`, bitte melden.
- Die Issue-Entwürfe `ISSUE-DRAFT-neonize-newsletter-panic.md` und `ISSUE-DRAFT-whatsmeow-newsletter-audio-hmac.md`. Beide sind eingereicht, beide Fehler upstream behoben: neonize#210/#211 und whatsmeow#1229.

### Doku

- READMEs und CheatSheets: keine Warnung mehr vor dem Absturz beim Wiederholen und Nachholen, Festlegung jetzt 0.12.4, und nach einem missglückten Wechsel zuerst die gesicherte Sitzungsdatei zurückspielen.
- `UPGRADE-TEST.md` ist jetzt die Prüfliste für diesen Wechsel. Das Ergebnis vom 08.08. bleibt darin erhalten.
- **Branches** (`CONTRIBUTING.md`): `develop` ist der Entwicklungszweig, Pull Requests gehen dorthin. `beta` ist jetzt `develop` plus der instaloader-Fix, und darauf läuft der Pi; was sich bewährt hat, geht von `develop` nach `main`.

### Hinweise zum Update

1. Vorher auf dem Pi über den kurzlebigen Branch `develop` (`beta` plus dieser Wechsel) erprobt, Prüfliste und Ergebnis in `UPGRADE-TEST.md`: am 21.11. alles bestanden, die Sprachnachricht eingeschlossen. Nicht kurz vor einem Spieltag aktualisieren.
2. Vor dem Umstieg: `cp ~/SkyRelay/skyrelay_session.sqlite3 ~/skyrelay_session_052.sqlite3.bak`
3. Nach dem Merge wird `beta` auf `main` nachgezogen, der Pi geht mit `git checkout beta && git pull && ./update.sh` zurück auf `beta`, und `develop` wird gelöscht.

## 09.11.2026

### Behoben

- **Falsche Meldungen während der Nachtruhe des Feeds** ([#41](https://github.com/Vijabei/SkyRelay/pull/41)). Läuft der Feed nur tagsüber (etwa `3 7-23 * * *`), meldete der Wächter um 2 Uhr „seit 3 Stunden nicht gelaufen“ und um 7 Uhr die Entwarnung. Die Ruhestunden zählen jetzt nicht mehr für `feed_stale_hours`. Sie stammen aus `[heartbeat] feed_hours` oder, wenn das leer ist, aus dem eigenen crontab-Eintrag für `skyrelay-feed.py`. Als Ruhe gilt nur eine Lücke von mindestens drei Stunden, damit ein Takt wie `*/2` keine erfindet. `--status` zeigt, was gilt. Ein echter Ausfall wird weiterhin gemeldet: Lief der Feed zuletzt um 23:03, kommt die Meldung gegen 9:06.

### Hinweise

- **Ruht auch der Wächter selbst nachts,** bleiben bei healthchecks.io die Pings aus, und von dort kommt die falsche Meldung. Das kann SkyRelay nicht abfangen: Den Check bei healthchecks.io auf den Zeitplan-Typ „Cron“ stellen, mit dem Ausdruck aus der crontab und der Zeitzone `Europe/Berlin`. Läuft der Wächter rund um die Uhr (`*/15 * * * *`), ist nichts zu tun.
- **Der Branch `beta` ist auf dem Stand von `main`** und unterscheidet sich weiter nur durch den instaloader-Fix (siehe 08.11.).

## 08.11.2026

### Behoben

- **Beiträge über 300 Zeichen** ([#39](https://github.com/Vijabei/SkyRelay/pull/39)). Die Aufteilung langer Texte konnte zwischen einem und zwei Beiträgen hin- und herpendeln. Ein Text, der als erster von zwei Beiträgen passt, als einziger aber nicht (dort stehen die Hashtags mit drin), wurde am Ende mit dem Budget des Zweier-Threads als einzelner Beitrag ausgegeben. Bluesky lehnte ihn ab, am 30.10. einen Instagram-Beitrag mit 313 Zeichen. Betraf Ticker und Feed.
  - Der letzte Teil wird jetzt auch als der Beitrag gemessen, der er wirklich wird.
  - Zum Schluss wird jeder Teil noch einmal gegen seinen tatsächlichen Beitrag geprüft. Passt etwas nicht, wird vorsichtiger geteilt.
- **Feed mit mehr Medien als Text** ([#39](https://github.com/Vijabei/SkyRelay/pull/39)). Bekommt jedes Video einen eigenen Beitrag, ist der Thread länger als der Text. Die Beschriftung wird jetzt für diesen Thread gemessen (`split_body(..., min_total=...)`); vorher konnte der Zähler einen Beitrag über die Grenze schieben.

### Neu

- **`tools/pruefe_aufteilung.py`** prüft die Aufteilung mit dem Grenzfall vom 30.10. und Zufallstexten über verschiedene `[layout]`-Einstellungen: jeder Beitrag in den Grenzen, kein Text verloren. Rückgabewert 0, solange nichts gefunden wird. Mit dem alten Code: 332 Befunde, mit dem neuen: keiner.
- **`tools/pakete_angleichen.py`** ([#40](https://github.com/Vijabei/SkyRelay/pull/40)), aufgerufen von `update.sh` nach `pip install -r requirements.txt`. pip vergleicht nur Versionsnummern; ein Stand direkt aus git, der sich wie die letzte Veröffentlichung nennt, käme so nie an und ginge nie wieder. Das Werkzeug liest, woher jedes installierte Paket stammt (`direct_url.json`), und installiert nur die neu, deren Quelle nicht zu `requirements.txt` passt. Mit reinen PyPI-Paketen ändert es nichts.
- **Der Feed nennt beim Start sein instaloader** ([#40](https://github.com/Vijabei/SkyRelay/pull/40)): `Using instaloader 4.16.` oder `… from https://github.com/ppdms/instaloader @ 3e4d5ac0ec.`

### Beta-Branch `beta`: Instagram-Feed mit dem Fix aus instaloader#2795

Seit dem 02.11.2026 antwortet Instagram eingeloggten Sitzungen auf `graphql/query` mit `feedback_required`, auch mit instaloader 4.16 ([instaloader/instaloader#2792](https://github.com/instaloader/instaloader/issues/2792)). Der Fix in [instaloader/instaloader#2795](https://github.com/instaloader/instaloader/pull/2795) stellt die Abfragen auf `api/graphql` um, ist aber noch nicht veröffentlicht.

- Der Branch `beta` ist `main` plus diesen Fix, fest auf den Commit `3e4d5ac0ec545a771a3a4b0b82cb313f32acb29f`.
- Umsteigen mit `git checkout beta && ./update.sh`, zurück mit `git checkout main && ./update.sh`. Anleitung in [BETA.de.md](https://github.com/Vijabei/SkyRelay/blob/beta/BETA.de.md) (englisch: [BETA.md](https://github.com/Vijabei/SkyRelay/blob/beta/BETA.md)).
- Auf dem Pi geprüft: Mit dem Fix findet der Feed die Beiträge wieder.
- Sobald instaloader eine neuere Fassung veröffentlicht, meldet der Wächter sie. Dann hebt `main` die Festlegung an, und der Branch wird gelöscht.

## 07.11.–08.11.2026

### Behoben

- **WhatsApp lässt den Ticker wieder ein** ([#31](https://github.com/Vijabei/SkyRelay/pull/31)). Seit dem 07.11. lehnte WhatsApp die Verbindung mit `Client outdated (405)` ab. Der Ticker nutzt jetzt `neonize==0.5.2` mit whatsmeow vom 21.09.2026 statt 0.4.3.post0 mit whatsmeow vom 09.07. Die Schnittstellen, die der Ticker aufruft, sind gleich geblieben. Das Datenbankschema der Sitzung hat sich geändert, deshalb vor dem Wechsel `skyrelay_session.sqlite3` sichern.
- **Ticker ohne Spielplan startet wieder** ([#36](https://github.com/Vijabei/SkyRelay/pull/36)). Mit leerem `[team] openligadb_filter`, laut Vorlage gedacht für Sportarten ohne Daten bei OpenLigaDB, brach der Ticker mit „still missing“ ab. Der Suchbegriff ist keine Pflichtangabe mehr.
- **Bio blieb ohne Spielplan auf „Bot ist an“** ([#36](https://github.com/Vijabei/SkyRelay/pull/36)). `get_next_matches()` fragte OpenLigaDB mit leerem Suchbegriff ab, scheiterte, und die Statuszeile wurde nicht zurückgesetzt. Ohne Spielplan (`WITHOUT_SCHEDULE`) wird OpenLigaDB jetzt gar nicht gefragt, und es gilt `line_off_no_match`.

### Neu: Wächter (`skyrelay-heartbeat.py`)

Eingeführt in [#32](https://github.com/Vijabei/SkyRelay/pull/32), ergänzt in [#33](https://github.com/Vijabei/SkyRelay/pull/33) bis [#35](https://github.com/Vijabei/SkyRelay/pull/35).

#### Statusdateien

Neues Modul `skyrelay_health.py`. Ticker und Feed schreiben ihren Zustand nach `skyrelay_health.json` bzw. `skyrelay_feed_health.json`:

- je Prüfpunkt (`whatsapp`, `instagram`, `bluesky`, `openligadb`, `posting`) das Ergebnis mit Fehlerart, seit wann es so ist und wann es zuletzt geklappt hat;
- beim lauschenden Ticker ein Lebenszeichen, höchstens einmal pro Minute geschrieben.

Geschrieben wird atomar über eine `.tmp`-Datei. Ein Fehler beim Schreiben bringt keinen Bot zum Stehen. Läuft schon ein zweiter, lebender Prozess desselben Bots, lässt ein weiterer Lauf die Datei in Ruhe.

#### Ticker hört auf Ablehnungen durch WhatsApp

Er registriert die neonize-Ereignisse `ClientOutdatedEv`, `ConnectFailureEv` (mit Auswertung von `ConnectFailureReason`), `LoggedOutEv`, `TemporaryBanEv`, `StreamReplacedEv`, `KeepAliveTimeoutEv`, `KeepAliveRestoredEv` und `DisconnectedEv`. Die Ereignisse werden über ihren Namen nachgeschlagen, damit eine andere neonize-Fassung ohne einzelne davon nicht scheitert.

- Bei einer endgültigen Ablehnung (veralteter Client, abgemeldet, gesperrt, verdrängt) schreibt er sofort ins Protokoll, was zu tun ist, und beendet sich. Bisher wartete er 5 Minuten bis zur Zeitüberschreitung.
- Kommt die Ablehnung erst während des Lauschens, endet die Schleife ebenfalls.

#### Tägliche WhatsApp-Probe

`skyrelay-matchday.py --probe` verbindet sich einmal, löst den Kanal auf und trennt wieder. Es wird nichts gepostet, die Bio bleibt unberührt.

- Mit `[heartbeat] enabled` und `whatsapp_probe` läuft die Probe an spielfreien Tagen automatisch im täglichen cron-Lauf.
- Fehlt eine Kopplung, wartet die Probe nicht auf einen QR-Scan, sondern meldet das.

#### Sitzungssperre

Eine `flock`-Sperre auf `skyrelay_session.sqlite3.lock` verhindert, dass zwei Prozesse gleichzeitig mit derselben WhatsApp-Sitzung verbunden sind. Die Probe überspringt sich, wenn ein Ticker läuft. Ein Ticker wartet höchstens 120 s auf eine laufende Probe und bricht danach mit Hinweis ab.

#### Der Wächter selbst

Läuft per cron alle 15 Minuten. Er wertet die Statusdateien aus und meldet nur Änderungen: neues Problem, Erinnerung alle `reminder_hours`, Entwarnung.

| Was | Bedingung |
|---|---|
| Ticker abgestürzt | Prozess weg |
| Ticker hängt | kein Lebenszeichen seit `2 × video_job_timeout + 5 min` |
| WhatsApp-Verbindung gestört | länger als `ticker_stale_minutes` |
| WhatsApp lehnt ab | veralteter Client, abgemeldet, gesperrt, nicht gekoppelt, Kanal nicht gefunden |
| OpenLigaDB nicht erreichbar | Abruf gescheitert |
| Instagram sperrt | Sperre durch Instagram, mit Ende der Pause |
| Feed ohne Erfolg | seit `feed_stale_hours` |
| Bluesky-Anmeldung / Beitrag | gescheitert |
| cron läuft nicht | Ticker 36 h bzw. Feed `feed_stale_hours` ohne Start |
| Speicher | unter `disk_min_mb` |
| Ticker endet mit Fehler | Auffangnetz, wenn keine der Prüfungen oben greift |

Ein einzelner gescheiterter Beitrag wird einmal gemeldet, ohne Erinnerung und ohne Entwarnung.

#### Kanäle

- **Bluesky-Direktnachricht** vom Bot-Konto (`bluesky_sender`, vorgegeben das Ticker-Konto) an `bluesky_recipient`.
  - Lange Meldungen werden in Teile unter 1000 Zeichen aufgeteilt.
  - Nach einem Fehlschlag wird erst nach 1 h wieder versucht, dann in wachsenden Abständen bis zu 6 h. So bleibt das Anmeldelimit des Bot-Kontos verschont.
  - Ist der Empfänger das sendende Konto selbst, wird das klar gemeldet ([#35](https://github.com/Vijabei/SkyRelay/pull/35)).
- **Wächter von außen** über `ping_url` ([#34](https://github.com/Vijabei/SkyRelay/pull/34)). Der Dienst wird an der Adresse erkannt:
  - healthchecks.io: `POST <url>` oder `<url>/fail`, die Beschreibung des Problems im Text;
  - Uptime Kuma: `/api/push/` mit `status` und `msg`.
  - `ping_mode` steht auf `problems` (Fehler-Ping, solange ein Problem besteht) oder `alive` (nur Lebenszeichen).
  - Die alten Namen `kuma_push_url` und `kuma_mode` werden weiter gelesen und von der Konfigurationsprüfung als veraltet gemeldet.

#### Neue Fassungen auf PyPI

Einmal täglich prüft der Wächter `neonize` und `instaloader`. Bei neonize zählt die mitgelieferte whatsmeow, nicht die Versionsnummer ([#33](https://github.com/Vijabei/SkyRelay/pull/33)):

- Er lädt jede neue Fassung einmal herunter (rund 20 MB) und liest die whatsmeow-Fassung aus den Go-Build-Infos der Bibliothek. Das Ergebnis wird je Fassung zwischengespeichert.
- Hinweise und die Meldung bei einer 405 nennen eine neue neonize nur dann als Ausweg, wenn ihre whatsmeow wirklich neuer ist.
- Befund: neonize 0.6.1 bis 0.10.0 bringen dieselbe whatsmeow wie 0.5.2 mit und helfen nicht gegen eine erneute 405.

#### Aufrufe von Hand und Sprache

- `--status` zeigt, was er gerade melden würde, und sendet nichts.
- `--test` schickt eine Testmeldung über alle eingerichteten Wege.
- Außerdem `--check-config` und `--show-config`.
- Die Meldungen folgen `[general] language` (de/en). Das Protokoll `skyrelay-heartbeat.log` ist englisch.

### Geändert

- **Konfiguration:** neuer Abschnitt `[heartbeat]`, standardmäßig ausgeschaltet, dazu `[files] health` und `[feed] health`. `./update.sh` trägt die Schlüssel samt Erklärungen nach. Wer nichts einschaltet, bemerkt keinen Unterschied.
- **Einrichtungsassistent:** neuer Bereich „7 · Wächter“ mit Testknopf, „Prüfen und nachziehen“ ist jetzt Punkt 8. Ein Schalter zeigt bei fehlendem Schlüssel die Vorgabe des Programms statt pauschal „an“.
- **Feed:** schreibt Instagram-Sperre (mit Ende der Pause), Sitzungsfehler, Bluesky-Anmeldung und Postingfehler in seine Statusdatei.
- **Doku:** beide READMEs mit Abschnitt „Wächter“, beide CheatSheets mit `--probe`, den neuen Dateien und Hilfen bei Störungen. `requirements.txt` hält die neonize-Befunde fest. Die englische Übersetzung ist vollständig (403/403).
- **`.gitignore`:** Statusdateien, Zustand des Wächters und Sitzungssperre.

### Hinweise zum Update

1. `./update.sh` ausführen.
2. Den Wächter unter `./config.sh` → „7 · Wächter“ einschalten.
3. Für Direktnachrichten braucht das Absender-Bot-Konto ein App-Passwort mit „Zugriff auf Direktnachrichten erlauben“, entweder anstelle seines bisherigen Passworts oder als `BLUESKY_HEARTBEAT_APP_PASSWORD`. Es gehört in `~/.skyrelay.env`. Mit dem eigenen Konto dem Bot folgen.
4. cron-Eintrag anlegen:

   ```cron
   */15 * * * * set -a && . $HOME/.skyrelay.env && set +a && /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-heartbeat.py >/dev/null 2>&1
   ```

5. Der Ticker muss weiterhin täglich starten, sonst fehlt die tägliche Probe.

### Nicht in der Testumgebung geprüft

Die WhatsApp-Ereignisse, die Probe und die Pings wurden nur gegen nachgestellte Gegenstellen getestet, weil die Testumgebung WhatsApp, bsky.social und hc-ping.com nicht erreichte. Direktnachricht über Bluesky und Ping an healthchecks.io sind inzwischen mit `--test` auf dem Pi geprüft.
