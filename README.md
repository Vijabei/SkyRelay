# SkyRelay

Zwei kleine Bots, die Inhalte automatisch nach **Bluesky** weiterleiten — entstanden für
ein Fanprojekt, das Vereinsnachrichten dort verfügbar machen wollte, wo sie sonst fehlen.
Sport ist dabei kein Muss: Der Ticker lässt sich ebenso für Veranstaltungen, Vereine
oder andere Kanäle einrichten, dann ohne Spielplan-Anbindung.

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

### Welche Quelle passt zu welcher Betriebsart?

Die Quelle ist derzeit fest mit dem jeweiligen Programm verbunden. Ob eine
Kombination überhaupt sinnvoll ist, entscheidet eine einzige Frage: **Meldet die
Quelle neue Beiträge von sich aus (Push), oder muss man sie regelmäßig abfragen?**

| Quelle | Ticker (ereignisgetrieben, Zeitfenster) | Feed (Dauerbetrieb, Abfrage) |
|---|---|---|
| **WhatsApp-Kanal** | ✅ umgesetzt — Beiträge erscheinen binnen Sekunden | ⚙️ möglich: Ticker ohne Spielplan mit weitem Zeitfenster (siehe unten) |
| **Instagram** | ❌ nicht sinnvoll — Instagram kennt kein Push; für Ticker-Tempo müsste man im Minutentakt abfragen, was zuverlässig zur Kontosperre führt | ✅ umgesetzt |
| **weitere Quellen** | nur mit Push-Unterstützung | wenn regelmäßiges Abfragen erlaubt ist |

**WhatsApp im Dauerbetrieb** braucht keinen Umbau: In der Konfiguration den
Spielplan leer lassen (`[team] openligadb_filter =`), `day_end` auf `23:59`
setzen und den Cron-Eintrag täglich statt nur an Spieltagen starten. Einzige
Einschränkung: Über Mitternacht entsteht eine kurze Lücke, weil sich das
Programm beendet und nach dem Neustart nur Beiträge des laufenden Tages annimmt.

**Neue Quellen** sind willkommen, aber vor dem Programmieren lohnt die Prüfung:
Gibt es eine brauchbare Bibliothek? Erlaubt der Dienst automatisierten Zugriff?
Und vor allem — kann er Ereignisse melden, oder verträgt er zumindest häufiges
Abfragen ohne Sperre? Erst wenn das geklärt ist, lohnt sich die Trennung von
Quelle und Ablaufsteuerung. Sprich uns über ein Issue an.

### Was `skyrelay-matchday.py` besonders macht

* **Spieltagserkennung** über [OpenLigaDB](https://www.openligadb.de): An spielfreien
  Tagen beendet sich das Programm sofort und baut gar keine Verbindung auf.
  Nicht auf Fußball beschränkt — gut gepflegt sind dort Fußball (1.–3. Liga,
  DFB-Pokal, Frauen-Bundesliga) und Eishockey (DEL, DEL2). Für Sportarten ohne
  Daten (etwa Basketball oder der Handball-Ligabetrieb) lässt sich die Erkennung
  abschalten; der Ticker läuft dann an jedem Tag, an dem er gestartet wird.
* **Spiel-Hashtag** wird automatisch erzeugt (`#KSCDSC` heim/auswärts korrekt herum),
  inklusive DFB-Pokal-Runden, und an jeden Beitrag gehängt.
* **Mehrere Mannschaften eines Vereins** lassen sich abdecken, sofern sie bei
  OpenLigaDB dieselbe Team-Nummer tragen — dann genügt es, ihre Liga in
  `league_prefixes` zu ergänzen. Spielen an einem Tag beide, kann eine
  Kanal-Nachricht keiner Partie zugeordnet werden: die Beiträge bekommen dann
  statt der Spiel-Hashtags den `overlap_hashtag`, während die Profil-Statuszeile
  beide Partien nennt.
* **Profil-Statuszeile:** Die erste Zeile der Bluesky-Biografie schaltet zwischen
  „Bot ist an – 1. Spieltag …" und „Bot ist aus – nächstes Spiel …" um.
* **Bearbeitungen** im Kanal werden erkannt: Der alte Bluesky-Beitrag wird gelöscht
  und durch die korrigierte Fassung ersetzt.
* Überträgt Text, Bilder, Videos und erzeugt Link-Vorschaukarten.
* **Sprachnachrichten und Sticker** aus dem WhatsApp-Kanal werden übernommen.
  Bluesky kennt beides nicht: Aus einer Sprachnachricht wird ein Video mit
  animierter Wellenform (der Ton bleibt erhalten), aus einem Sticker ein Bild.
* **Videos werden nachgereicht:** Scheitert der Upload zur Video-API von
  Bluesky, geht der Beitrag sofort mit dem Vorschaubild raus — beim Ticker
  zählt die Zeit. Das Video bleibt liegen und wird in späteren Läufen erneut
  versucht; klappt es, hängt der Bot es als Antwort an den Beitrag. So wird
  aus einer vorübergehenden Störung bei Bluesky kein dauerhaft bebilderter
  Beitrag. Einstellungen dazu: `video_retry_*` in `skyrelay.conf`.

## Projektstatus

Beide Programme laufen produktiv und sind vollständig über `skyrelay.conf`
konfigurierbar — Verein, Kanäle, Konten, Texte und Dateinamen stecken alle dort,
im Code steht nichts Vereinsspezifisches mehr.

---

## Voraussetzungen

* **Linux, 64 Bit** (`x86_64` oder `aarch64`). Ein Raspberry Pi 3B+ genügt, aber das
  System muss 64-Bit sein — für 32-Bit (`armv7l`) gibt es keine passenden Pakete.
* **Python 3.10** oder neuer, dazu `python3-venv` und `tzdata`.
* **`ffmpeg`** (`sudo apt install ffmpeg`) — nur für Sprachnachrichten aus dem
  WhatsApp-Kanal. Fehlt es, werden Sprachnachrichten übersprungen, alles andere
  läuft unverändert weiter.
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

Am einfachsten mit dem Einrichtungsassistenten:

```bash
venv/bin/python skyrelay-setup.py
```

Er öffnet ein **Menü im Stil von `raspi-config`** — mit Pfeiltasten bedienbar, auch
über SSH. Aus dem Hauptmenü lassen sich die Bereiche einzeln ansteuern, statt sich
durch alle Fragen am Stück zu arbeiten:

```
  1  Spieltags-Ticker    WhatsApp-Kanal → Bluesky
  2  Instagram-Feed      Instagram → Bluesky
  3  Kürzel für Hashtags
  4  Beiträge und Profil
  5  Aufbau der Beiträge (Kopfzeile, Quelle, Hashtags)
  6  Zeitfenster
  7  Anmeldung bei Bluesky prüfen
  8  Konfiguration prüfen
  9  Konfiguration nachziehen
  0  Speichern und beenden
```

Das ist auch der bequeme Weg für spätere Änderungen: Ein einzelnes Kürzel
korrigieren oder das Zeitfenster verschieben dauert damit Sekunden. Oben zeigt das
Menü an, welche Pflichtangaben noch fehlen.

Fehlt `whiptail` auf dem System, fällt der Assistent automatisch auf eine
zeilenweise Abfrage zurück; erzwingen lässt sie sich mit `SKYRELAY_SETUP_TEXT=1`.

### Was gilt eigentlich gerade?

Eine Konfiguration wird über die Jahre undurchsichtig: Fehlt ein Schlüssel, gilt
still die Vorgabe aus dem Programm — man sieht der Datei nicht an, was tatsächlich
wirkt. Deshalb gibt jeder Bot darüber Auskunft:

```bash
venv/bin/python skyrelay-feed.py --show-config
```

Das listet jeden Wert, den die Programme lesen, mit seiner Herkunft:

```
[post]
  prefix                        (Datei)      ⚽ [Inoffizieller Bot]
  source_label                  (Datei)      WhatsApp-Kanal des Vereins
[feed]
  source_label                  (Vorgabe)    Beitrag auf Instagram
```

**Auskommentieren schaltet nichts ab.** Eine Zeile mit `#` davor zählt als
fehlend, und dann greift die Vorgabe aus dem Programm — die Einstellung wirkt
also weiter, nur eben mit einem anderen Wert. Wer etwas *weglassen* will, lässt
den Wert leer:

```ini
# keine Quellenangabe im Beitrag:
source_label =
# keine Kopfzeile:
prefix =
```

Dasselbe gilt für `standing_hashtag`. In der Übersicht oben ist der Unterschied
an der Herkunft ablesbar: `(Datei)` heißt „so steht es bei dir", `(Vorgabe)`
heißt „das Programm hat entschieden".

### Wo Kopfzeile, Quelle und Hashtags stehen

Bis dahin war das festgelegt: Kopfzeile und Quelle oben im ersten Beitrag, die
Hashtags unten im letzten. Der Abschnitt `[layout]` macht daraus eine
Entscheidung — eine Zeile je Baustein:

```ini
[layout]
#                  Beiträge ; Stelle ; Reihenfolge
prefix           = first ; top    ; 1
source           = first ; top    ; 2
match_hashtag    = last  ; bottom ; 1
standing_hashtag = last  ; bottom ; 2
```

| Spalte | Werte | Bedeutung |
|---|---|---|
| Beiträge | `first`, `last`, `all`, `none` | an welchen Beiträgen des Threads |
| Stelle | `top`, `bottom` | über oder unter dem Text |
| Reihenfolge | Zahl | wer zuerst kommt, wenn zwei an derselben Stelle landen |

Die Werte oben sind die Vorgaben und erzeugen genau das Aussehen, das SkyRelay
immer hatte. Einen Baustein ganz weglassen geht auf zwei Wegen: `none`
eintragen, oder seinen Inhalt leer lassen (`source_label =`).

Am bequemsten ist der Assistent unter Punkt 5 — er zeigt die Matrix und auf
Wunsch eine **Vorschau des fertigen Beitrags**, bevor irgendetwas gepostet wird:

```
── Beitrag 1 von 2 ──────────────────────────────
⚽ [Inoffizieller Bot]
🔗 Quelle: WhatsApp-Kanal des Vereins

Beispieltext, Teil 1. (1/2)

── Beitrag 2 von 2 ──────────────────────────────
Beispieltext, Teil 2. (2/2)

#DSCWOB #arminia
```

### Konfiguration prüfen

Ein falsch geschriebener Schlüssel wirkt einfach nicht — ohne Fehlermeldung, weil
das Programm dann still seine Vorgabe nimmt. Deshalb prüfen beide Bots die
Konfiguration auf Zuruf:

```bash
venv/bin/python skyrelay-feed.py --check-config
```

Gemeldet wird, was kein Programm liest (Tippfehler oder veraltet), was fehlt und
deshalb auf die Vorgabe zurückfällt, und was in `skyrelay.conf.example`
undokumentiert geblieben ist. Der Aufruf verbindet sich mit nichts, verändert
nichts und postet nichts; der Rückgabewert ist 0, solange es keine Probleme gibt.
Denselben Bericht zeigt der Assistent unter Punkt 7 — dort auch für Änderungen,
die noch nicht gespeichert sind.

### Fehlende Schlüssel nachziehen

Nach einem Update kennt die Vorlage Schlüssel, die in der eigenen Datei fehlen.
Sie lassen sich mitsamt ihren Erklärungen ergänzen — vorhandene Werte, Reihenfolge
und Kommentare bleiben unangetastet, es wird ausschließlich hinzugefügt:

```bash
venv/bin/python skyrelay-setup.py --add-missing
```

Der Aufruf zeigt zuerst, was ergänzt würde, und fragt nach. Vorher entsteht eine
Sicherung als `skyrelay.conf.bak`. Im Menü steht dasselbe unter Punkt 8.

Beim ersten Einrichten führt der Assistent durch dieselben Punkte wie zuvor:

| Zweck | Ablauf |
|---|---|
| **Sport mit Spielplan** | Liga und Verein werden **live bei OpenLigaDB gesucht** (kein Nachschlagen von Team-Nummern), die **Kürzeltabelle für die Hashtags wird vorbefüllt** — für die Fußball-Ligen 1–3 mit den gebräuchlichen Kürzeln, sonst mit klar als Vorschlag markierten Ableitungen (`?`). Neben Fußball stehen die weiteren gepflegten Ligen zur Wahl, etwa die DEL. |
| **Sport ohne Spielplan** | Für Sportarten, die OpenLigaDB nicht führt (Basketball, Handball-Liga): keine Spieltags-Erkennung, wechselnder Hashtag über `SKYRELAY_HASHTAG`. |
| **Anderer Zweck** | Für Vereine, Veranstaltungen, Projekte: wie oben, zusätzlich mit **neutral formulierten Vorgabetexten** statt Fußballsprache. |

In allen Fällen werden Beitragstexte, Profil-Statuszeile und Zeitfenster abgefragt,
auf Wunsch die Bluesky-Anmeldung geprüft und daraus die fertige `skyrelay.conf`
geschrieben. Einmal gepflegte Kürzel bleiben dabei erhalten — auch nach einem
Ligawechsel, damit Pokalgegner aus anderen Ligen weiterhin korrekt benannt werden. Ein erneuter Aufruf dient zum Ändern: Vorhandene Werte
werden als Vorgabe angeboten, und von der alten Datei wird eine Sicherung angelegt.

Wer lieber von Hand arbeitet, kopiert die kommentierte Vorlage:

```bash
cp skyrelay.conf.example skyrelay.conf
```

Für den Spieltags-Ticker sind mindestens `[bluesky] handle` und
`[source] channel_invite_link` nötig; für einen anderen Verein zusätzlich
`[team]` (OpenLigaDB-Suchbegriff und Team-Nummer) sowie die Kürzel unter
`[team_codes]`. Für die Instagram-Spiegelung genügt der Abschnitt `[feed]`
(Profil, Zweitkonto und optional ein eigenes Bluesky-Konto). Alle Abschnitte sind
in der Vorlage kommentiert, eine Übersicht steht im
[CheatSheet](CHEATSHEET-matchday.md).

Die eigene `skyrelay.conf` steht in `.gitignore` und gehört nicht ins Repository.
Mehrere Vereine parallel betreibst du über `SKYRELAY_CONFIG=/pfad/zur/datei.conf`.

Wer von einer älteren Fassung umsteigt, muss **nichts neu koppeln**: vorhandene
`dsc_ticker_*`-Dateien werden beim ersten Start automatisch übernommen.

Das **App-Passwort gehört niemals in eine Datei**, sondern in eine Umgebungsvariable —
je eine pro Bot, damit die Zuordnung eindeutig bleibt:

```bash
export BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"; export BLUESKY_FEED_APP_PASSWORD="yyyy-yyyy-yyyy-yyyy"
```

Nutzen beide Bots dasselbe Konto, steht in beiden Variablen einfach dasselbe Passwort.

⚠️ **cron liest weder `~/.bashrc` noch `~/.profile`.** Für den automatischen Betrieb
gehören die Variablen **oben in die crontab** (`crontab -e`) — dort ohne
Anführungszeichen, sonst würden sie Teil des Werts:

```cron
BLUESKY_TICKER_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
BLUESKY_FEED_APP_PASSWORD=yyyy-yyyy-yyyy-yyyy
```

## Erste Kopplung mit WhatsApp

Einmalig und **interaktiv im Terminal** (nicht per cron):

```bash
SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
```

Es erscheint ein QR-Code, den du im Handy unter *WhatsApp → Einstellungen →
Verknüpfte Geräte → Gerät hinzufügen* scannst. Praxistipp: Bildschirm hell stellen und
das Terminal stark vergrößern, sonst findet die Kamera zu wenig Kontrast. Alternativ
per Zahlencode koppeln — dazu `SKYRELAY_PAIR_PHONE=49xxxxxxxxx` ergänzen.

Ein Login über `web.whatsapp.com` im Browser hilft **nicht**: Das Programm ist ein
eigenes verknüpftes Gerät mit eigener Sitzung. Diese liegt danach in
`*_session.sqlite3` und wird bei allen weiteren Läufen wiederverwendet.

## Betrieb

Im Regelbetrieb genügt ein täglicher cron-Eintrag — ob heute überhaupt gespielt wird,
entscheidet das Programm selbst:

```cron
0 6 * * * BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
```

Zwei häufige Stolpersteine: Pfade sind **groß-/kleinschreibungsabhängig**, und eine
Ausgabeumleitung (`>> skyrelay.log`) ist **nicht** nötig — das Programm schreibt sein
Protokoll selbst, sonst steht jede Zeile doppelt darin.

Läuft der Bot gerade?

```bash
pgrep -af skyrelay-matchday
```

Alle weiteren Betriebsarten (Testläufe, Nachholen verpasster Beiträge, Einzeltests)
stehen in **[CHEATSHEET-matchday.md](CHEATSHEET-matchday.md)**.

### Instagram-Spiegelung (`skyrelay-feed.py`)

Einmalig die Instagram-Sitzung des Zweitkontos anlegen:

```bash
venv/bin/instaloader -l dein_zweitkonto
```

Danach regelmäßig per cron aufrufen — das Programm überträgt, was seit dem
letzten Lauf neu ist:

```cron
*/15 * * * * BLUESKY_FEED_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /pfad/zu/SkyRelay/venv/bin/python3 /pfad/zu/SkyRelay/skyrelay-feed.py >/dev/null 2>&1
```

Nutzt du für Instagram ein **anderes** Bluesky-Konto als für den Ticker, trägst du
es unter `[feed] bluesky_handle` ein und gibst die App-Passwörter getrennt an
(siehe unten).

---

## Dokumentation

| Datei | Inhalt |
|---|---|
| `README.md` | dieses Dokument |
| `skyrelay-setup.py` | Einrichtungsassistent (erzeugt und ändert `skyrelay.conf`) |
| `skyrelay.conf.example` | kommentierte Vorlage aller Einstellungen |
| `skyrelay_common.py` | gemeinsame Bausteine beider Programme (Protokoll, Konfiguration, Bilder, Video-Upload) |
| `skyrelay-testlauf.py` | spielt echte Kanal-Nachrichten durch die Ticker-Pipeline, ohne zu veröffentlichen |
| `CHEATSHEET-matchday.md` | alle Umgebungsvariablen, Betriebsarten, Dateien, Fehlerbehebung |
| `ISSUE-DRAFT-neonize-newsletter-panic.md` | vorbereiteter Fehlerbericht an **neonize**: Absturz beim Verlaufsabruf |
| `ISSUE-DRAFT-whatsmeow-newsletter-audio-hmac.md` | vorbereiteter Fehlerbericht an **whatsmeow**: Sprachnachrichten nicht ladbar |

## Bekannte Einschränkungen

* **`neonize` ist auf `0.4.3.post0` festgelegt** und wurde am 08.08.2026
  vollständig geprüft (siehe [UPGRADE-TEST.md](UPGRADE-TEST.md)). Vor einem
  Wechsel die Sitzungsdatei sichern — das Datenbankschema ändert sich.
* **Strg+C kann gelegentlich vom Go-Anteil abgefangen werden** (ab 0.4.x): Dann
  endet das Programm mit `Quit`, ohne aufzuräumen, und die Profil-Statuszeile
  bleibt auf „Bot ist an" stehen. Beobachtet wurde das einmal; im Regelfall läuft
  das Aufräumen normal. Zurücksetzen notfalls mit `SKYRELAY_PROFILE=off`. Im
  cron-Betrieb ohne Bedeutung — dort endet der Ticker regulär zum Tagesende.
* **Absturz bei inhaltslosen Kanalbeiträgen:** Löscht der Kanal einen Beitrag, bleibt
  eine leere Nachricht zurück, an der der zugrundeliegende Go-Programmteil abstürzt.
  Betrifft **nur** die Nachhol-Betriebsarten, nicht den Dauerbetrieb — dieser lauscht
  auf Ereignisse und ist davon nicht betroffen.
  Wie sehr dich das trifft, hängt vom Kanal ab: Liegt so eine Nachricht weit
  hinten, genügt ein kleinerer Wert. Liegt sie **direkt hinter dem neuesten
  Beitrag**, ist `SKYRELAY_REPLAY` unbrauchbar — dann stürzt schon der Abruf von
  zwei Nachrichten ab und nur `1` läuft durch. (Im hier gespiegelten Kanal war
  das am 19.08.2026 der Fall.) Zum Prüfen an echtem Material dient dann
  `skyrelay-testlauf.py`; es blättert mit einem ausdrücklichen Startpunkt
  rückwärts an der kaputten Stelle vorbei:

  ```
  venv/bin/python3 skyrelay-testlauf.py --latest
  venv/bin/python3 skyrelay-testlauf.py --before <ServerID> --type audio,sticker
  ```
* **`instaloader` ist auf 4.15.2 festgelegt.** Version 4.15.3 stellte die
  Profilabfrage auf einen Endpunkt um, den Instagram seit Anfang August 2026
  drosselt — schon die erste Anfrage endet mit „429 Too Many Requests"
  ([instaloader#2726](https://github.com/instaloader/instaloader/issues/2726),
  offen). Erst nach dessen Lösung aktualisieren.
* **Umfragen** werden übersprungen: Bluesky kennt dieses Format nicht.
* **Sprachnachrichten brauchen einen Umweg beim Download.** Kanalmedien liegen
  unverschlüsselt hinter `directPath` — Bilder und Videos bringen deshalb gar
  keinen `mediaKey` mit. Sprachnachrichten schleppen einen mit, woraufhin
  whatsmeow zu entschlüsseln versucht und mit `invalid media hmac` scheitert.
  SkyRelay lädt bei genau diesem Fehler ein zweites Mal ohne den Schlüssel.
  Nachgemessen am 19.08.2026: regulär lud **keine** von 5 Sprachnachrichten,
  ohne Schlüssel **alle fünf** byte-genau.
* **Animierte Sticker** verlieren ihre Bewegung — übertragen wird das erste
  Einzelbild, weil Bluesky keine Animationen abspielt.
* **Sprachnachrichten** erscheinen als Video und haben damit keine sinnvolle
  Bildbeschreibung. Wer auf Barrierefreiheit Wert legt, sollte das bedenken.
* **Wellenform-Farbe:** `waveform_color` in `[audio]` nimmt ausschließlich
  ffmpeg-*Farbnamen* (`White`, `DodgerBlue`, …). Hex-Angaben wie `0x38BDF8`
  verwirft ffmpeg stillschweigend und zeichnet grün; SkyRelay warnt dann im
  Protokoll.
* **Videos** bis rund 100 MB; größere werden nicht übertragen (Bluesky-Grenze).
* **Nachgereichte Videos** landen als Antwort unter dem Beitrag, nicht im
  Beitrag selbst — Bluesky kennt kein nachträgliches Ändern von Beiträgen.
  Wird der Beitrag zwischenzeitlich gelöscht (etwa durch eine Bearbeitung im
  WhatsApp-Kanal), scheitert das Nachreichen und der Vorgang wird nach
  `video_retry_max_attempts` Versuchen verworfen.
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

## Mitmachen

Verbesserungen sind willkommen — vor allem **Vereinskürzel**, die noch fehlen oder
unüblich sind. Dafür muss man nicht programmieren können: Ein Issue mit Team-Nummer
und Kürzel genügt. Wie das geht, steht in [CONTRIBUTING.md](CONTRIBUTING.md).

## Lizenz

[PolyForm Noncommercial License 1.0.0](LICENSE) — nichtkommerzielle Nutzung ist
frei, einschließlich Fanprojekten, gemeinnütziger Organisationen und
Bildungseinrichtungen. Der Copyright-Hinweis muss dabei erhalten bleiben.

**Kommerzielle Nutzung** — etwa durch Vereine als Wirtschaftsunternehmen,
Medienhäuser oder werbefinanzierte Angebote — erfordert eine gesonderte Lizenz.
Anfragen bitte über ein Issue in diesem Repository.

Hinweis: Das ist eine „source available"-Lizenz, keine Open-Source-Lizenz im Sinne
der OSI — die Einschränkung auf nichtkommerzielle Nutzung ist damit unvereinbar.
