# Erprobung: neonize 0.5.2 → 0.12.4

Der Zweig mit diesem Wechsel wird **vor dem Merge** geprüft. `main` bleibt
bis dahin auf 0.5.2 und ist die Fassung, die im Produktivbetrieb läuft.

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

## Testumgebung

Auf einer **eigenen Maschine mit derselben Architektur wie das Zielsystem**
(hier: EC2 mit `aarch64`, wie der Raspberry Pi). Wichtig, weil neonize
architekturspezifische Pakete ausliefert.

Die WhatsApp-Sitzung des Produktivsystems darf **nicht kopiert** werden — zwei
Clients mit derselben Sitzung streiten um die Verbindung. Stattdessen dort
einmalig neu koppeln; WhatsApp erlaubt bis zu vier verknüpfte Geräte je Nummer.

## Prüfpunkte

Alle Tests im Trockenlauf (`SKYRELAY_DRY_RUN=1`), es wird nichts veröffentlicht.

- [ ] **Installation**: `./update.sh` holt neonize 0.12.4 und protobuf ≥ 7.36,
      der Import gelingt
- [ ] **Kopplung**: Code oder QR-Code erscheint, Anmeldung funktioniert
- [ ] **Probe**: `venv/bin/python skyrelay-matchday.py --probe` ohne 405
- [ ] **Kanal-Auflösung**: `get_newsletter_info_with_invite` liefert die Kennung
- [ ] **Live-Ereignisse**: neue Kanalbeiträge kommen an und werden verarbeitet
- [ ] **Nachrichtenabruf**: `SKYRELAY_REPLAY=5` läuft durch. Hier stürzte bis
      0.4.3 alles ab; leere Einträge erscheinen jetzt als
      `the message is completely empty … skipped`
- [ ] **Sprachnachricht**: `venv/bin/python3 skyrelay-testlauf.py --type audio`
      lädt sie beim ersten Versuch, und es entsteht das Video mit Wellenform
- [ ] **Medien**: Bild, Video und Sticker aus dem Kanal
      (`skyrelay-testlauf.py --type image,video,sticker`)
- [ ] **Beenden**: Tagesende bzw. Strg+C ohne Absturz
- [ ] **Wächter**: `venv/bin/python skyrelay-heartbeat.py --status` zeigt
      neonize 0.12.4 mit whatsmeow vom 16.11.2026

Schlägt die Sprachnachricht fehl, fehlt der Umweg ohne `mediaKey` doch noch: dann
den Commit „Drop the second voice-message download …“ zurücknehmen und das
Ergebnis hier festhalten.

## Übernehmen

Laufen alle Punkte durch, wird der Zweig nach `main` übernommen, danach `beta`
auf `main` nachgezogen (der Branch unterscheidet sich weiter nur durch den
instaloader-Fix). Auf dem Produktivsystem, **nicht kurz vor einem Spieltag**:

```bash
cp ~/SkyRelay/skyrelay_session.sqlite3 ~/skyrelay_session_052.sqlite3.bak
```

```bash
cd ~/SkyRelay && git pull && ./update.sh
```

```bash
venv/bin/python skyrelay-matchday.py --probe
```

## Rückweg

Den vorigen Stand auschecken und `./update.sh` ausführen. Das neue Schema ist
laut whatsmeow mit Programmen ab Schema v8 lesbar, 0.5.2 (v15) sollte die
Sitzungsdatei also weiter öffnen. Geht das schief, die Sicherung zurückspielen;
im schlimmsten Fall hilft eine neue Kopplung — sie dauert zwei Minuten.

## Ergebnis

*Noch offen.*

---

## Frühere Wechsel

### 0.4.3.post0 → 0.5.2 (07./08.11.2026)

Nötig, weil WhatsApp 0.4.3.post0 ab dem 07.11.2026 mit `Client outdated (405)`
abwies. Abseits des Pi geprüft: `requirements.txt` installiert, die Importe
laden, und ein ungekoppelter Wegwerf-Client bekommt die 405 mit 0.4.3.post0,
mit 0.5.2 nicht. Danach auf dem Pi in Betrieb genommen
([#31](https://github.com/Vijabei/SkyRelay/pull/31)).

### 0.3.18.post0 → 0.4.3.post0 (08.08.2026)

Getestet auf EC2 (Debian, `aarch64`) gegen den echten Kanal im Trockenlauf.
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
