# Erprobung: neonize 0.3.18.post0 → 0.4.3.post0

Dieser Zweig dient allein dem Test. **`main` bleibt unverändert** und ist die
Fassung, die im Produktivbetrieb läuft — dort also **nicht** auf diesen Zweig
wechseln.

## Warum überhaupt umsteigen?

Nicht wegen neuer Funktionen, sondern wegen **whatsmeow**: Die eingebettete
WhatsApp-Umsetzung ist in 0.4.x deutlich neuer. WhatsApp ändert sein Protokoll
regelmäßig; irgendwann wird 0.3.18 daran scheitern, und das möchte man nicht an
einem Spieltag herausfinden. Zusätzlich enthält 0.4.2+ den Fix für „Wire format
was corrupt" (PR #198).

**Nicht** enthalten ist der Absturz bei gelöschten Kanalbeiträgen
(`required field NewsletterMessage.Message not set`) — der besteht in 0.4.3
unverändert und betrifft weiterhin nur REPLAY und CATCHUP.

## Vorgeschichte

| Version | Erfahrung |
|---|---|
| 0.3.18.post0 | seit Juli 2026 im Einsatz, mehrere Spieltage ohne Probleme |
| 0.4.0 / 0.4.1 | unbrauchbar: jeder Rückgabewert scheiterte mit „Wire format was corrupt" ([#199](https://github.com/krypton-byte/neonize/issues/199)); Ursache war ein am ersten NUL-Byte abgeschnittener Puffer |
| 0.4.2 / 0.4.3 | Fix dafür enthalten (PR #198), von uns bislang ungetestet |

## Testumgebung

Auf einer **eigenen Maschine mit derselben Architektur wie das Zielsystem**
(hier: EC2 mit `aarch64`, wie der Raspberry Pi). Wichtig, weil neonize
architekturspezifische Pakete ausliefert.

Die WhatsApp-Sitzung des Produktivsystems darf **nicht kopiert** werden — zwei
Clients mit derselben Sitzung streiten um die Verbindung. Stattdessen dort
einmalig neu koppeln; WhatsApp erlaubt bis zu vier verknüpfte Geräte je Nummer.

## Prüfpunkte

Alle Tests im Trockenlauf (`SKYRELAY_DRY_RUN=1`), es wird nichts veröffentlicht.

- [ ] **Installation**: Paket lädt für die Zielarchitektur, Import gelingt
- [ ] **Kopplung**: QR-Code erscheint, Anmeldung funktioniert
- [ ] **Kanal-Auflösung**: `get_newsletter_info_with_invite` liefert die Kanal-Kennung
      — hier scheiterte 0.4.1 mit „Wire format was corrupt"
- [ ] **Live-Ereignisse**: neue Kanalbeiträge kommen an und werden verarbeitet
- [ ] **Nachrichtenabruf**: `SKYRELAY_REPLAY=1` liefert die letzten Beiträge
- [ ] **Medien**: Bild- und Video-Download aus dem Kanal
- [ ] **Beenden**: kein Absturz beim Verbindungsabbau
- [ ] **Sitzungsdatei**: Wechsel der Version verändert das Datenbankschema —
      danach ein Rückschritt auf 0.3.18 nur mit neuer Kopplung

## Übernehmen oder verwerfen

Laufen alle Punkte durch, wird der Zweig nach `main` übernommen; auf dem
Produktivsystem dann **vor** dem Upgrade die Sitzungsdatei sichern:

```bash
cp ~/SkyRelay/skyrelay_session.sqlite3 ~/skyrelay_session.sqlite3.bak
```

Scheitert ein Punkt, bleibt `main` auf 0.3.18. Das Ergebnis gehört dann hier
dokumentiert, damit die Frage nicht in drei Monaten erneut aufkommt.

## Ergebnis

**Getestet am 08.08.2026** auf EC2 (Debian, `aarch64` — dieselbe Architektur wie
der Raspberry Pi), neonize **0.4.3.post0**, gegen den echten Kanal im
Trockenlauf.

| Prüfpunkt | Ergebnis |
|---|---|
| Installation | ✅ Paket lädt für aarch64, Import gelingt |
| Kopplung | ✅ QR-Code, Anmeldung, 515-Reconnect und History-Sync sauber durchlaufen |
| **Kanal-Auflösung** | ✅ **ohne „Wire format was corrupt"** — genau hier scheiterte 0.4.1 |
| **Nachrichtenabruf** | ✅ `REPLAY=1` liefert den Beitrag |
| Medien | ✅ Video mit 16,7 MB heruntergeladen |
| Beenden (regulär) | ✅ kein Absturz, nur die bekannte EOF-Warnung |
| **Live-Ereignisse** | ✅ Beitrag 6831 kam während des Lauschens an und wurde verarbeitet (inkl. erkannter Link-Vorschaukarte) |
| Tagesende | ✅ Selbstbeendigung um 23:59 ausgelöst — der Weg, den der Cron-Betrieb nimmt |
| Zeitzone | ✅ auch auf einer Maschine mit UTC-Systemzeit korrekt: Tagesende und Beitragszeiten richten sich nach `[team] timezone` |

### Abweichung gegenüber 0.3.18

**Strg+C wurde einmal vom Go-Anteil abgefangen.** Das Programm endet mit `Quit`, die
Python-Aufräumarbeiten laufen nicht mehr — die Profil-Statuszeile bleibt dann auf
„Bot ist an" stehen. `Press Ctrl+C to exit`, `SIGINT` und `signal.signal` finden
sich nicht im Python-Teil des Pakets, sondern ausschließlich in der
mitgelieferten Go-Bibliothek; deren Signal-Handler beendet den Prozess direkt.

Folgen:

* **Cron-Betrieb: nicht betroffen.** Der Ticker endet regulär zum konfigurierten
  Tagesende und stellt die Statuszeile dabei korrekt zurück.
* **Manuelle Läufe:** Nach einem Abbruch mit Strg+C die Zeile zurücksetzen mit
  `SKYRELAY_PROFILE=off venv/bin/python skyrelay-matchday.py`.

### Bewertung

**Alle Prüfpunkte bestanden — der Umstieg wird übernommen.** Die beiden Fehler,
die 0.4.0/0.4.1 unbrauchbar machten, sind behoben, und das neuere whatsmeow macht
den Ticker haltbarer gegenüber Änderungen am WhatsApp-Protokoll. Unverändert
bestehen bleibt der Absturz bei gelöschten Kanalbeiträgen in REPLAY und CATCHUP.

Vor dem Umstieg auf dem Produktivsystem die Sitzungsdatei sichern:

```bash
cp ~/SkyRelay/skyrelay_session.sqlite3 ~/skyrelay_session_0318.sqlite3.bak
```

Das ist die bestmögliche Rückfallebene, keine Garantie: Ein Rückschritt auf
0.3.18 erfordert zusätzlich das Herabstufen des Pakets, und ob die Sitzungsdatei
den Schemawechsel rückwärts übersteht, ist offen. Im Zweifel hilft eine neue
Kopplung — sie dauert zwei Minuten.

Ein Update **kurz vor einem Spieltag** ist die schlechtere Wahl: Bleibt Zeit für
Nachbesserungen, läuft am Spieltag eine Fassung, die sich schon eine Woche
bewährt hat.
