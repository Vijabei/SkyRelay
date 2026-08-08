# SkyRelay

Zwei kleine Bots, die Inhalte automatisch nach **Bluesky** weiterleiten — gedacht für
Fanprojekte, die Vereinsnachrichten dort verfügbar machen wollen, wo sie sonst fehlen.

Das Ziel ist bewusst fest auf Bluesky gelegt. Die **Quelle** ist austauschbar: Aktuell
gibt es einen WhatsApp-Kanal- und einen Instagram-Anschluss.

> **Inoffiziell.** Dieses Projekt steht in keiner Verbindung zu Meta, Bluesky oder
> irgendeinem Verein. Es greift auf inoffizielle Schnittstellen zu — bitte lies den
> Abschnitt [Risiken & Rechtliches](#risiken--rechtliches), bevor du es einsetzt.

---

## Die zwei Programme

| Programm | Betriebsart | Quelle (aktuell) |
|---|---|---|
| **`skyrelay-matchday.py`** | Spieltagsgebunden: läuft nur an Spieltagen, lauscht auf Live-Ereignisse und postet sofort | WhatsApp-Kanal |
| **`skyrelay-feed.py`** | Dauerbetrieb: prüft regelmäßig auf neue Beiträge | Instagram-Profil |

Benannt sind sie nach der **Betriebsart**, nicht nach der Quelle — damit ein
Quellenwechsel später keine Umbenennung erzwingt.

### Was `skyrelay-matchday.py` besonders macht

* **Spieltagserkennung** über [OpenLigaDB](https://www.openligadb.de): An spielfreien
  Tagen beendet sich das Programm sofort und baut gar keine Verbindung auf.
* **Spiel-Hashtag** wird automatisch erzeugt (`#KSCDSC` heim/auswärts korrekt herum),
  inklusive DFB-Pokal-Runden, und an jeden Beitrag gehängt.
* **Profil-Statuszeile:** Die erste Zeile der Bluesky-Biografie schaltet zwischen
  „Bot ist an – 1. Spieltag …" und „Bot ist aus – nächstes Spiel …" um.
* **Bearbeitungen** im Kanal werden erkannt: Der alte Bluesky-Beitrag wird gelöscht
  und durch die korrigierte Fassung ersetzt.
* Überträgt Text, Bilder, Videos und erzeugt Link-Vorschaukarten.

## Projektstatus

Läuft produktiv, ist aber **noch nicht allgemein konfigurierbar**: Verein, Kanal und
Konto stehen derzeit als Konstanten im Kopf der Programmdatei, die Umgebungsvariablen
heißen noch `DSC_TICKER_*`. Eine Konfigurationsdatei und vereinsneutrale Bezeichnungen
sind der nächste geplante Schritt. Wer es heute für einen anderen Verein nutzen will,
muss den Dateikopf anpassen — machbar, aber kein Komfort.

---

## Voraussetzungen

* **Linux, 64 Bit** (`x86_64` oder `aarch64`). Ein Raspberry Pi 3B+ genügt, aber das
  System muss 64-Bit sein — für 32-Bit (`armv7l`) gibt es keine passenden Pakete.
* **Python 3.10** oder neuer, dazu `python3-venv` und `tzdata`.
* Ein **Bluesky-Konto** für den Bot samt App-Passwort
  (Einstellungen → Datenschutz und Sicherheit → App-Passwörter).
* Für `skyrelay-matchday.py`: eine **separate WhatsApp-Nummer**, deren Verlust
  verschmerzbar wäre (siehe Risiken). Das Konto muss den Kanal abonniert haben.
* Für `skyrelay-feed.py`: ein **Instagram-Zweitkonto** für den Abruf.

## Installation

```bash
git clone https://github.com/Vijabei/SkyRelay.git
cd SkyRelay
./install.sh
```

Das Skript prüft System, Architektur und Python-Version, legt ein virtuelles
Umfeld unter `venv/` an und installiert die Abhängigkeiten. Es ändert **nichts** am
System: Fehlende Systempakete werden nur gemeldet, nicht automatisch nachinstalliert.

## Konfiguration

Bis zur Konfigurationsdatei werden die Werte im Kopf von `skyrelay-matchday.py`
gesetzt — mindestens diese beiden:

```python
CHANNEL_INVITE_LINK = "https://whatsapp.com/channel/..."   # Kanal → Teilen → Link kopieren
BLUESKY_HANDLE      = "dein-bot.bsky.social"
```

Für einen anderen Verein zusätzlich anzupassen: `OPENLIGADB_TEAM_FILTER`,
`OPENLIGADB_TEAM_ID`, die Kürzeltabelle `TEAM_CODES` und die Vorlagen der
Profil-Statuszeile.

Das **App-Passwort gehört niemals in eine Datei**, sondern in eine Umgebungsvariable:

```bash
export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
```

## Erste Kopplung mit WhatsApp

Einmalig und **interaktiv im Terminal** (nicht per cron):

```bash
DSC_TICKER_FORCE=1 DSC_TICKER_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
```

Es erscheint ein QR-Code, den du im Handy unter *WhatsApp → Einstellungen →
Verknüpfte Geräte → Gerät hinzufügen* scannst. Praxistipp: Bildschirm hell stellen und
das Terminal stark vergrößern, sonst findet die Kamera zu wenig Kontrast. Alternativ
per Zahlencode koppeln — dazu `DSC_TICKER_PAIR_PHONE=49xxxxxxxxx` ergänzen.

Ein Login über `web.whatsapp.com` im Browser hilft **nicht**: Das Programm ist ein
eigenes verknüpftes Gerät mit eigener Sitzung. Diese liegt danach in
`*_session.sqlite3` und wird bei allen weiteren Läufen wiederverwendet.

## Betrieb

Im Regelbetrieb genügt ein täglicher cron-Eintrag — ob heute überhaupt gespielt wird,
entscheidet das Programm selbst:

```cron
0 6 * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
```

Zwei häufige Stolpersteine: Pfade sind **groß-/kleinschreibungsabhängig**, und eine
Ausgabeumleitung (`>> ticker.log`) ist **nicht** nötig — das Programm schreibt sein
Protokoll selbst, sonst steht jede Zeile doppelt darin.

Läuft der Bot gerade?

```bash
pgrep -af skyrelay-matchday
```

Alle weiteren Betriebsarten (Testläufe, Nachholen verpasster Beiträge, Einzeltests)
stehen in **[CHEATSHEET-matchday.md](CHEATSHEET-matchday.md)**.

---

## Dokumentation

| Datei | Inhalt |
|---|---|
| `README.md` | dieses Dokument |
| `CHEATSHEET-matchday.md` | alle Umgebungsvariablen, Betriebsarten, Dateien, Fehlerbehebung |
| `ISSUE-DRAFT-neonize-newsletter-panic.md` | vorbereiteter Fehlerbericht an das neonize-Projekt |

## Bekannte Einschränkungen

* **`neonize` ist auf `0.3.18.post0` festgelegt.** Diese Fassung ist im Spielbetrieb
  erprobt. Neuere sind ungetestet — vor einem Wechsel die Sitzungsdatei sichern und
  mit Trockenlauf testen.
* **Absturz bei gelöschten Kanalbeiträgen:** Löscht der Kanal einen Beitrag, bleibt
  eine leere Nachricht zurück, an der der zugrundeliegende Go-Programmteil abstürzt.
  Betrifft **nur** die Nachhol-Betriebsarten (kleineren Wert wählen), nicht den
  Dauerbetrieb — dieser lauscht auf Ereignisse und ist davon nicht betroffen.
* **Umfragen** werden übersprungen: Bluesky kennt dieses Format nicht.
* **Videos** bis rund 100 MB; größere werden nicht übertragen (Bluesky-Grenze).
* Mehrere Bilder in einem Beitrag: Bluesky nimmt höchstens vier.

## Risiken & Rechtliches

* **WhatsApp:** Der Zugriff erfolgt über einen inoffiziellen Client. Das verstößt
  gegen die Nutzungsbedingungen und kann zur **Sperrung der Nummer** führen. Nutze
  ausschließlich eine Nummer, deren Verlust dich nicht trifft — und niemals deine
  private. Dasselbe gilt sinngemäß für das Instagram-Zweitkonto.
* **Urheberrecht:** Du überträgst fremde Inhalte. Kläre für dich, ob du das darfst,
  und kennzeichne den Bot als inoffiziell (die Beiträge tragen dafür einen Hinweis
  samt Quellenangabe).
* **Keine Verbindung** zu Meta, Bluesky oder einem Verein. Marken und Namen werden
  ausschließlich beschreibend verwendet.
* **Ohne Gewähr:** Die Schnittstellen sind inoffiziell und können sich jederzeit
  ändern. Nutzung auf eigenes Risiko.

## Verwendete Projekte

SkyRelay ist im Wesentlichen Verdrahtung — die eigentliche Arbeit leisten diese
Projekte, denen der Dank gebührt:

| Projekt | Wofür | Lizenz |
|---|---|---|
| [neonize](https://github.com/krypton-byte/neonize) | Zugriff auf WhatsApp aus Python | Apache-2.0 |
| [whatsmeow](https://github.com/tulir/whatsmeow) | die WhatsApp-Umsetzung, auf der neonize aufbaut | MPL-2.0 |
| [atproto (Python SDK)](https://github.com/MarshalX/atproto) | Anbindung an Bluesky | MIT |
| [Instaloader](https://github.com/instaloader/instaloader) | Abruf von Instagram-Beiträgen | MIT |
| [Pillow](https://github.com/python-pillow/Pillow) | Bildbearbeitung und Komprimierung | HPND |
| [Requests](https://github.com/psf/requests) | HTTP-Aufrufe | Apache-2.0 |
| [Segno](https://github.com/heuer/segno) | QR-Code im Terminal für die Kopplung | BSD-3-Clause |
| [OpenLigaDB](https://www.openligadb.de) | freie Spielplandaten (Spieltag, Anstoß, Gegner) | Community-Projekt |

## Lizenz

Noch nicht festgelegt. Bis dahin gilt: **alle Rechte vorbehalten** — eine Nutzung,
Weitergabe oder Veränderung ist ohne Absprache nicht gestattet. Vorgesehen ist eine
Lizenz, die nichtkommerzielle Nutzung mit Namensnennung erlaubt; kommerzielle Nutzung
soll gesondert vereinbart werden.
