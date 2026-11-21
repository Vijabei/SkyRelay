# Erprobung: neonize 0.5.2 → 0.12.4

Der Wechsel wurde **vor dem Merge** auf dem Pi geprüft, über den Branch
`develop`: damals `beta` (also `main` plus der instaloader-Fix, auf dem der Pi
läuft) plus dieser Wechsel. `main` und `beta` blieben bis dahin auf 0.5.2.

Seitdem ist `develop` der ständige Entwicklungszweig, und `beta` baut auf ihm
auf (siehe [CONTRIBUTING.md](CONTRIBUTING.md#branches)). Ein künftiger Wechsel
kommt über einen Pull Request nach `develop`, wird nach `beta` gemergt und auf
dem Pi erprobt, ohne dass der Pi den Branch wechseln muss.

## Warum überhaupt umsteigen?

Nicht wegen neuer Funktionen, sondern wegen **whatsmeow**: neonize bringt es
mit, und whatsmeow bestimmt, als welche Client-Version WhatsApp den Ticker
sieht. Alte Versionen weist WhatsApp nach einigen Monaten mit
`Client outdated (405)` ab, zuletzt 0.4.3.post0 am 07.11.2026. Der Wechsel
geschieht vorbeugend, damit die nächste 405 nicht an einem Spieltag kommt.

| | 0.5.2 (bisher) | 0.12.4 (neu) |
|---|---|---|
| whatsmeow | 21.09.2026 (`35ae40906e74`) | 16.11.2026 (`19d61352436f`), Stand von whatsmeow `main` am 21.11. |
| Client-Version | 2.3000.1047769893 | 2.3000.1054751086 |
| protobuf (Gencode) | 7.34.1 | **7.36.0** – ältere Laufzeit scheitert beim Import mit `VersionError` |
| neue Abhängigkeiten | – | `cryptography`, `pycryptodome` (Wheels für aarch64 vorhanden) |
| Schema der Sitzung | v15 | v16, „compatible with v8+“ (nur eine neue Spalte) |

0.11.0 bis 0.12.4 (alle vom 20.11.2026) bringen dieselbe whatsmeow mit.

## Was schon vorher erledigt war

Beide Fehler, für die SkyRelay eigene Umwege hatte, sind upstream behoben, und
zwar **schon in 0.5.2**:

* **Absturz bei inhaltslosen Kanalnachrichten** (`panic: required field
  neonize.NewsletterMessage.Message not set`): behoben in neonize#210/#211
  (26.08.2026). `ProtoReturnV3` stürzt nicht mehr ab, das Feld ist optional.
  Ein Abruf liefert solche Nachrichten jetzt als leere Einträge; der Ticker
  überspringt sie.
* **Sprachnachrichten mit `invalid media hmac`** (whatsmeow#1229, am 29.08.2026
  als erledigt geschlossen): `Download()` lässt den `mediaKey` jetzt weg, wenn
  `FileEncSHA256` fehlt – genau der Fall bei Kanalmedien. Die whatsmeow vom
  09.07. (in 0.4.3) hatte das noch nicht, die vom 21.09. (in 0.5.2) schon.
  Der zweite Download-Versuch ohne Schlüssel entfällt deshalb.

## Vorab geprüft (21.11.2026, ohne WhatsApp)

Auf x86_64; die Testumgebung erreichte WhatsApp nicht.

* Die whatsmeow-Fassung aus allen Wheels 0.5.2 bis 0.12.4 ausgelesen.
* Die Methoden, die der Ticker aufruft, haben in 0.12.4 dieselben Signaturen und
  bis auf `download_any(…, path=…)` (nutzen wir nicht) denselben Rumpf. Alle
  Ereignisse, die der Ticker registriert, gibt es weiterhin, ebenso
  `ConnectFailureReason` und `MessageServerID`.
* Im Go-Teil (`goneonize/main.go`) von 0.12.4 laufen `DownloadAny` und
  `GetNewsletterMessages` wie in 0.5.2 und ohne Absturz bei leeren Nachrichten.
* Mit 0.12.4: Import, `--check-config`, Aufbau des Clients, Registrierung aller
  Ereignisse, Anlegen einer Sitzungsdatei (Schema v16) und sauberes Ende bei
  SIGTERM. Mit protobuf 7.34.1 scheitert der Import wie erwartet.

## Umstieg auf dem Pi

Getestet wird mit der vorhandenen Sitzung, eine zweite Kopplung braucht es
nicht. Die Sitzungssperre verhindert, dass zwei Prozesse zugleich mit ihr
verbunden sind. Erst die Sitzung sichern, dann umsteigen:

```bash
cp ~/SkyRelay/skyrelay_session.sqlite3 ~/skyrelay_session_052.sqlite3.bak
```

```bash
cd ~/SkyRelay && git fetch && git checkout develop && ./update.sh
```

**Nicht kurz vor einem Spieltag:** Solange der Pi auf `develop` steht, läuft
auch der cron-Betrieb mit 0.12.4. An einem Spieltag wäre das schon der Ernstfall.

## Prüfpunkte

Die Tests im Trockenlauf (`SKYRELAY_DRY_RUN=1`, beim Ticker an spielfreien Tagen
mit `SKYRELAY_FORCE=1`), es wird nichts veröffentlicht.

- [x] **Installation**: `./update.sh` holt neonize 0.12.4 und protobuf ≥ 7.36,
      der Import gelingt
- [x] **Sitzung**: die bestehende Kopplung gilt weiter, kein QR-Code, kein Code
- [x] **Probe**: `venv/bin/python skyrelay-matchday.py --probe` ohne 405
- [x] **Kanal-Auflösung**: `get_newsletter_info_with_invite` liefert die Kennung
- [x] **Live-Ereignisse**: neue Kanalbeiträge kommen an und werden verarbeitet
- [x] **Nachrichtenabruf**: `SKYRELAY_REPLAY=5` läuft durch. Hier stürzte bis
      0.4.3 alles ab; leere Einträge erscheinen jetzt als
      `the message is completely empty … skipped`
- [x] **Sprachnachricht**: `venv/bin/python3 skyrelay-testlauf.py --type audio`
      lädt sie beim ersten Versuch, und es entsteht das Video mit Wellenform
- [x] **Medien**: Bild, Video und Sticker aus dem Kanal
      (`skyrelay-testlauf.py --type image,video,sticker`)
- [x] **Beenden**: Tagesende bzw. Strg+C ohne Absturz
- [x] **Wächter**: `venv/bin/python skyrelay-heartbeat.py --status` zeigt
      neonize 0.12.4 mit whatsmeow vom 16.11.2026

Schlägt die Sprachnachricht fehl, fehlt der Umweg ohne `mediaKey` doch noch: dann
den Commit „Drop the second voice-message download …“ zurücknehmen und das
Ergebnis hier festhalten.

## Übernehmen

Laufen alle Punkte durch:

1. PR [#43](https://github.com/Vijabei/SkyRelay/pull/43) nach `main` mergen.
2. `main` nach `beta` mergen (`beta` unterscheidet sich danach weiter nur durch
   den instaloader-Fix).
3. Den Pi zurück auf `beta`, jetzt mit 0.12.4:

   ```bash
   cd ~/SkyRelay && git fetch && git checkout beta && git pull && ./update.sh
   ```

4. ~~`develop` löschen.~~ Entfallen: `develop` ist seitdem der
   Entwicklungszweig.

## Rückweg

```bash
cd ~/SkyRelay && git checkout beta && ./update.sh
```

Damit kommt neonize 0.5.2 zurück. Das neue Schema ist laut whatsmeow mit
Programmen ab Schema v8 lesbar, 0.5.2 (v15) sollte die Sitzungsdatei also
weiter öffnen. Geht das schief, die Sicherung zurückspielen:

```bash
cp ~/skyrelay_session_052.sqlite3.bak ~/SkyRelay/skyrelay_session.sqlite3
```

Im schlimmsten Fall hilft eine neue Kopplung — sie dauert zwei Minuten.

## Ergebnis

**Getestet am 21.11.2026** auf dem Pi (`aarch64`) über `develop`, mit der
bestehenden Sitzung, gegen den echten Kanal im Trockenlauf.

| Prüfpunkt | Ergebnis |
|---|---|
| Installation | ✅ `./update.sh` grün, Import gelingt, die Probe meldet neonize 0.12.4 |
| Sitzung | ✅ die bestehende Kopplung gilt weiter, kein QR-Code; Schemawechsel auf v16 unauffällig |
| Probe | ✅ `Successfully authenticated`, keine 405, Kanal gefunden |
| Kanal-Auflösung | ✅ `120363246785630110@newsletter` |
| Nachrichtenabruf | ✅ `REPLAY=5` lieferte ServerID 8135–8139: Text, Bild, zwei Videos (2,5 MB und 25,5 MB), Linkkarte |
| Sprachnachricht | ✅ ServerID 8127 trägt einen `mediaKey` und lud trotzdem beim ersten Versuch, genau `fileLength` (17613 Bytes); Video mit Wellenform erzeugt |
| Abruf älterer Nachrichten | ✅ `skyrelay-testlauf.py` holte 30 Nachrichten (8139 bis 8110) in Blöcken zu 3 ohne Absturz |
| Sticker, Live-Ereignisse, Beenden, Wächter | ✅ laut Rückmeldung; die Ausgabe ist hier nicht festgehalten |

Beim Trennen erscheint jedes Mal
`Error sending close to websocket: … failed to read frame header: EOF`. Das ist
die bekannte, harmlose Warnung, die es schon mit 0.4.3 gab.

**Nicht gezielt geprüft:** eine leere Meta-Nachricht im Abruffenster. Unter
8135–8139 war keine, und ob unter den 30 Nachrichten des Testlaufs eine war,
zeigt die Ausgabe nicht. Der Fix steckt seit 0.5.2 im Go-Teil
(neonize#210/#211), und der Ticker überspringt solche Einträge.

### Bewertung

**Der Wechsel wird übernommen.** Der Umweg für Sprachnachrichten ist
überflüssig: whatsmeow übergeht den `mediaKey` jetzt selbst.

---

## Frühere Wechsel

### 0.4.3.post0 → 0.5.2 (07./08.11.2026)

Nötig, weil WhatsApp 0.4.3.post0 ab dem 07.11.2026 mit `Client outdated (405)`
abwies. Abseits des Pi geprüft: `requirements.txt` installiert, die Importe
laden, und ein ungekoppelter Wegwerf-Client bekommt die 405 mit 0.4.3.post0,
mit 0.5.2 nicht. Danach auf dem Pi in Betrieb genommen
([#31](https://github.com/Vijabei/SkyRelay/pull/31)).

### 0.3.18.post0 → 0.4.3.post0 (08.08.2026)

Getestet auf `aarch64` gegen den echten Kanal im Trockenlauf.
0.4.0/0.4.1 waren unbrauchbar gewesen („Wire format was corrupt“,
[neonize#199](https://github.com/krypton-byte/neonize/issues/199)), 0.4.2+
enthielt den Fix (PR #198).

| Prüfpunkt | Ergebnis |
|---|---|
| Installation | ✅ Paket lädt für aarch64, Import gelingt |
| Kopplung | ✅ QR-Code, Anmeldung, 515-Reconnect und History-Sync sauber durchlaufen |
| Kanal-Auflösung | ✅ ohne „Wire format was corrupt“ |
| Nachrichtenabruf | ✅ `REPLAY=1` liefert den Beitrag |
| Medien | ✅ Video mit 16,7 MB heruntergeladen |
| Beenden (regulär) | ✅ kein Absturz, nur die bekannte EOF-Warnung |
| Live-Ereignisse | ✅ Beitrag 6831 kam während des Lauschens an und wurde verarbeitet |
| Tagesende | ✅ Selbstbeendigung um 23:59 ausgelöst |
| Zeitzone | ✅ auch mit UTC-Systemzeit korrekt nach `[team] timezone` |

**Abweichung seitdem:** Strg+C wurde einmal vom Go-Anteil abgefangen. Das
Programm endet dann mit `Quit`, die Python-Aufräumarbeiten laufen nicht, und die
Profil-Statuszeile bleibt auf „Bot ist an“ stehen. Im cron-Betrieb ohne
Bedeutung; nach einem manuellen Abbruch zurücksetzen mit
`SKYRELAY_PROFILE=off venv/bin/python skyrelay-matchday.py`.
