# Changelog

*[English](CHANGELOG.md) · **Deutsch***

Was sich an SkyRelay geändert hat, das Neueste oben. Die Nummern verweisen auf
die Pull Requests auf GitHub.

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
