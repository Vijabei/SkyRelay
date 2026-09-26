# CheatSheet: skyrelay-matchday.py

*[English](CHEATSHEET-matchday.md) · **Deutsch***

Repostet den **Arminia-Bielefeld-WhatsApp-Kanal** nach Bluesky
(`dsc-spieltagticker.bsky.social`) — an Spieltagen automatisch von 6 bis
24 Uhr, gesteuert über Umgebungsvariablen.

---

## Schnellreferenz: Schalter und Umgebungsvariablen

| Variable | Werte | Funktion |
|---|---|---|
| `BLUESKY_TICKER_APP_PASSWORD` | `xxxx-xxxx-xxxx-xxxx` | **Pflicht** (außer im Trockenlauf). App-Passwort des **Ticker**-Kontos. Der Feed nutzt entsprechend `BLUESKY_FEED_APP_PASSWORD`. |
| `--show-config` | – | Zeigt jeden gelesenen Wert mit seiner Herkunft (Datei oder Vorgabe). Kein Netz, keine Änderung. |
| `--check-config` | – | Meldet Schlüssel, die niemand liest, die fehlen oder die in der Vorlage fehlen. |
| `SKYRELAY_DRY_RUN` | `1` | Nur protokollieren, **nichts** auf Bluesky posten. Zeigt jeden Beitrag so, wie er auf Bluesky stünde — samt Zeichenzahl. Zum gefahrlosen Prüfen. |
| `SKYRELAY_FORCE` | `1` | Läuft auch dann, wenn OpenLigaDB für heute **kein** Spiel kennt (Testspiele, manuelle Läufe). Findet OpenLigaDB doch eins, werden Hashtag und Spieltagsinfo trotzdem übernommen. |
| `SKYRELAY_HASHTAG` | z.B. `DSCGUE` | Spiel-Hashtag **von Hand** setzen (mit oder ohne `#`, Groß- und Kleinschreibung egal). Hat Vorrang vor dem erzeugten. Nötig bei Testspielen, die OpenLigaDB nicht kennt. |
| `SKYRELAY_REPLAY` | Zahl `N` | Testmodus: verarbeitet einmalig die **letzten N vorhandenen** Kanalbeiträge und **beendet sich**. Das Wasserzeichen bleibt unberührt, es gibt keine Duplikatsprüfung. |
| `SKYRELAY_CATCHUP` | Zahl `N` | Nachholmodus: verarbeitet die letzten N Beiträge (**überspringt** dank Wasserzeichen, was schon erledigt ist) und **lauscht danach normal weiter**. Für „zu spät gestartet". |
| `SKYRELAY_PAIR_PHONE` | `4915123456789` | Erste Kopplung per achtstelligem Zahlencode statt QR-Scan (Nummer international, ohne `+` oder führende 0). Nur beim allerersten Lauf nötig; wird ignoriert, sobald gekoppelt. |
| `SKYRELAY_PROFILE` | `on` / `off` | Setzt **nur** die Statuszeile der Bluesky-Biografie und beendet sich sofort (ohne WhatsApp). Zum Prüfen und Geradeziehen von Hand. |
| `SKYRELAY_CONFIG` | Pfad | Eine andere Konfigurationsdatei verwenden (Vorgabe: `skyrelay.conf` neben dem Programm). So laufen mehrere Vereine nebeneinander. |
| `SKYRELAY_LANG` | `de` / `en` | Sprache des Einrichtungsassistenten, sofern `[general] language` nichts anderes sagt. |

> Die alten `DSC_TICKER_*`-Namen funktionieren übergangsweise weiter, geben aber
> einen Hinweis aus. Bitte auf `SKYRELAY_*` umstellen.

---

## Die Betriebsarten

### 1. cron, der Normalfall (Pflichtspiele)

```cron
0 6 * * * BLUESKY_TICKER_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /home/geordi/SkyRelay/venv/bin/python3 /home/geordi/SkyRelay/skyrelay-matchday.py >/dev/null 2>&1
```

> ⚠️ **Pfade unterscheiden Groß- und Kleinschreibung.** Ein falscher Großbuchstabe
> im Pfad des Interpreters führt zu `/bin/sh: 1: …/bin/python3: not found` — die
> Meldung meint den Interpreter, nicht Python selbst.
>
> ⚠️ **Keine Umleitung `>> skyrelay.log 2>&1` mehr:** Das Programm schreibt sein
> Protokoll selbst (siehe unten). Bleibt die Umleitung stehen, steht jede Zeile
> **doppelt** in der Datei.

**Verhalten:**
- Startet täglich um 6:00 und fragt bei OpenLigaDB, ob Arminia **heute** spielt
  (Liga und Pokal, Fenster von ±1 Woche, Team-Nummer 83).
- **Kein Spiel** → sofort Schluss, WhatsApp wird gar nicht erst kontaktiert.
- **Spieltag** → lauscht bis **23:59** auf **Live-Ereignisse** des Kanals
  (Reposts erscheinen sofort, es wird nicht gepollt) und beendet sich dann selbst.
- Der Spiel-Hashtag (`#DSCWOB` zuhause, `#WOBDSC` auswärts) entsteht aus den
  OpenLigaDB-Daten (DFL-Kürzel, Heimteam zuerst) und hängt zusammen mit dem
  Dauer-Hashtag an jedem Beitrag.
- Repostet nur Beiträge von **heute**: Was WhatsApp beim Verbinden aus seiner
  Warteschlange nachliefert, wird mitgenommen, sofern es von heute ist — Älteres
  wird verworfen.
- **Statuszeile der Biografie:** Sobald er lauscht, steht in der ersten Zeile der
  Bluesky-Biografie „🟢 Bot ist an - 1. Spieltag #KSCDSC ⚫⚪🔵", beim Beenden
  (auch mit Strg+C oder nach einem Fehler) „🔴 Bot ist aus - nächstes Spiel
  #DSCFCE ⚫⚪🔵". Alle weiteren Zeilen der Biografie, Avatar, Banner und
  Anzeigename bleiben unangetastet.

### 2. Ein Lauf von Hand (Testspiele, „lass ihn ein paar Stunden laufen")

```bash
# Das Passwort aus einer Datei, wo es hingehört - je Zeile SCHLÜSSEL=wert.
# "set -a" exportiert alles, egal ob "export" davorsteht:
set -a && . ~/.skyrelay.env && set +a
SKYRELAY_FORCE=1 SKYRELAY_HASHTAG=DSCGUE venv/bin/python skyrelay-matchday.py
```

- **Beenden:** `Strg+C` (wird sauber abgefangen) — oder von selbst um 23:59.
- **Neustart am selben Tag:** unkritisch, keine Duplikate (Wasserzeichen).
- Ohne `SKYRELAY_HASHTAG` bekommen die Beiträge nur den Dauer-Hashtag — außer
  OpenLigaDB kennt für heute doch ein Spiel, dann werden Hashtag und
  Spieltagsinfo automatisch übernommen.
- Für lange Läufe über SSH (übersteht das Schließen der Sitzung):

```bash
nohup env SKYRELAY_FORCE=1 SKYRELAY_HASHTAG=DSCGUE venv/bin/python skyrelay-matchday.py >/dev/null 2>&1 &
tail -f skyrelay.log        # zuschauen (das Protokoll schreibt das Programm selbst)
pgrep -af skyrelay-matchday # läuft er? PID und Kommandozeile
kill <PID>                  # beenden (sauber, die Biografie geht auf „Bot ist aus")
```

### 3a. Nachholen (verpasste Beiträge, danach weiterlauschen)

```bash
SKYRELAY_CATCHUP=5 SKYRELAY_FORCE=1 SKYRELAY_HASHTAG=DSCGUE venv/bin/python skyrelay-matchday.py
```

- Holt die letzten `N` Beiträge nach, **überspringt** dabei alles, was laut
  Wasserzeichen schon erledigt ist (keine Duplikate), und geht dann nahtlos ins
  Lauschen über.
- Der richtige Modus, wenn das Programm am Spieltag zu spät gestartet wurde oder
  abgestürzt war.
- ⚠️ Das Nachholen hat **keinen Datumsfilter**. Es überträgt jeden Kanalbeitrag,
  der neuer ist als das Wasserzeichen — nach einer längeren Pause also unter
  Umständen eine ganze Woche. `N` so wählen, wie viel wirklich nachzuholen ist,
  und daran denken, dass das Wasserzeichen Tage alt sein kann.
- Derselbe neonize-Vorbehalt wie beim Wiederholen (siehe unten): eine
  inhaltslose Nachricht unter den letzten `N` → Absturz. Kleines `N` wählen.

### 3b. Wiederholen (einen einzelnen Beitrag prüfen)

```bash
# Den letzten Kanalbeitrag nur ins Protokoll (Trockenlauf):
SKYRELAY_REPLAY=1 SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py

# Den letzten Kanalbeitrag ECHT auf Bluesky (Prüfung über die ganze Strecke):
SKYRELAY_REPLAY=1 SKYRELAY_FORCE=1 venv/bin/python skyrelay-matchday.py

# Die letzten 3 Beiträge:
SKYRELAY_REPLAY=3 …
```

- Verarbeitet ältester → neuester über die ganze Kette (Text, Bilder,
  Linkkarte) und beendet sich dann sofort.
- Fasst das Wasserzeichen **nicht** an → der Automatikbetrieb bleibt unberührt.
- Vorsicht ohne Trockenlauf: Wiederholen prüft **nicht** auf Duplikate — zweimal
  ausgeführt heißt zweimal gepostet.
- ⚠️ Ein bekannter neonize-Fehler: Ist unter den **letzten N** Kanalnachrichten
  eine **inhaltslose Meta-Nachricht** (im Kanal unsichtbar, hat aber eine eigene
  ServerID — etwa eine Bearbeitung oder Löschung), stürzt das Programm hart ab
  (Go-`panic: required field … Message not set`). Solche Nachrichten lassen sich
  mit neonize nicht ansehen — bei einem Absturz einfach `N` schrittweise
  verkleinern, bis das Fenster vor der Meta-Nachricht endet. Betrifft nur
  Wiederholen und Nachholen; der Live-Betrieb ist immun.

---

## Die erste Kopplung (einmalig, interaktiv — nicht per cron)

```bash
SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
```

- Im Terminal erscheint ein **QR-Code aus Zeichen** (nur, wenn eine Kopplung
  wirklich ansteht). Scannen mit: WhatsApp → Einstellungen → **Verknüpfte
  Geräte** → Gerät hinzufügen.
- **Probleme beim Scannen?** Terminal stark vergrößern und den Bildschirm heller
  stellen (Kontrast). Der QR-Code wechselt etwa alle 30 s — immer den zuletzt
  angezeigten scannen.
- **Stattdessen der Zahlencode:** zusätzlich `SKYRELAY_PAIR_PHONE=49…` setzen →
  Code am Telefon eintippen („Stattdessen mit Telefonnummer koppeln").
- Eine Anmeldung über web.whatsapp.com im Browser hilft **nicht** — es zählt nur
  die Kopplung dieses Programms.
- Danach liegt die Sitzung in `skyrelay_session.sqlite3` und wird von selbst
  wiederverwendet.

---

## Dateien im Programmordner

| Datei | Wofür | Löschen erlaubt? |
|---|---|---|
| `skyrelay-matchday.py` | das Programm | — |
| `skyrelay_session.sqlite3` | die WhatsApp-Sitzung (die Kopplung) | Ja → erzwingt eine neue erste Kopplung |
| `skyrelay_state.txt` | das Wasserzeichen: `Datum;letzte ServerID` | Ja → der nächste Start nimmt eine frische Ausgangslage („ab jetzt") |
| `skyrelay_posts.json` | ServerID → Bluesky-Beiträge (nur heute, für Bearbeitungen) | Ja → Bearbeitungen können die alten Beiträge dann nicht mehr löschen, nur neu posten |
| `skyrelay.log` | das Protokoll — das Programm schreibt es **immer** selbst, egal wie es gestartet wurde (samt der Ausgaben der Go-Schicht). Die Konsole zeigt weiterhin alles. | Ja |
| `skyrelay.log.1` … `.5` | rotierte Protokolle (ab 2 MB wird beim Start rotiert, `.1` = neuestes) | Ja |
| `locales/*/LC_MESSAGES/*.mo` | übersetzte Kataloge | Ja → neu bauen mit `./tools/i18n.sh compile` |

---

## Einstellungen in `skyrelay.conf`

Angelegt wird sie aus der Vorlage — `cp skyrelay.conf.example skyrelay.conf` —
oder bequemer mit `./config.sh`. Sie steht in `.gitignore` und gehört **nicht**
ins Repository.

| Abschnitt / Schlüssel | Bedeutung |
|---|---|
| `[general] language` | Sprache des Einrichtungsassistenten (`de`, `en`, oder leer für das, was das System sagt). Das Protokoll bleibt englisch. |
| `[general] theme` | Farbton des Einrichtungsfensters (Vorgabe `flexoki`). Jedes Thema, das Textual kennt; ein unbekannter Name fällt einfach auf die Vorgabe zurück. |
| `[bluesky] handle` | Das Konto, auf dem gepostet wird. Das App-Passwort gehört **nicht** hierher, sondern in `BLUESKY_TICKER_APP_PASSWORD`. |
| `[source] channel_invite_link` | Der Einladungslink des WhatsApp-Kanals (Kanal → Teilen → Link kopieren) |
| `[team] openligadb_filter` / `openligadb_team_id` | Der Verein bei OpenLigaDB. Team-Nummer nachschlagen unter `https://api.openligadb.de/getavailableteams/bl2/2026`. **Leer beziehungsweise `0` heißt: keine Spieltags-Erkennung** — für Sportarten ohne OpenLigaDB-Daten; der Ticker läuft dann an jedem Tag, an dem er gestartet wird, mit dem Hashtag aus `SKYRELAY_HASHTAG`. |
| `[team] league_shortcuts` | Nur diese Ligen zählen — **exakte** Ligakürzel, z.B. `bl2, dfb, rlw-frauen`. OpenLigaDB liefert vereinzelt Fantasie-Ligen mit falschen Terminen (real gesehen: **„ESP8266"**) und führt dieselbe Partie in Varianten derselben Liga (`bl2h` neben `bl2`) an verschiedenen Tagen. Verworfene Ligen stehen im Protokoll. Der Vorgänger `league_prefixes` verglich Präfixe; er steht nicht mehr in der Vorlage, wird aber weiter gelesen und gilt, solange dieser Schlüssel leer ist. |
| `[team] timezone` | Die Zeitzone für Anstoß und Tagesende |
| `[team_codes]` | Das Kürzel je Team-Nummer für die Hashtag-Bildung. **Pokalgegner bei Bedarf nachtragen** — unbekannte Mannschaften bekommen ein Ersatzkürzel aus drei Buchstaben plus Warnung im Protokoll. |
| `[layout]` | Wo Kopfzeile, Quelle und Hashtags stehen — eine Zeile je Baustein, `Beiträge ; Stelle ; Reihenfolge`. Siehe README. |
| `[post] prefix` / `source_template` / `source_label` / `standing_hashtag` | Die Kopfzeile, wie die Quellzeile aussieht, die Beschriftung ihres Links und der Dauer-Hashtag unter jedem Beitrag |
| `[league_hashtags]` | Ein Dauer-Hashtag je Liga, als `<Kürzel> = <Hashtag>` — für Vereine, die ihre Mannschaften unterschiedlich kennzeichnen (`bl2 = arminia`, `rlw-frauen = arminiafrauen`). Welche Mannschaft spielt, sagt genau die Liga. Ohne passenden Eintrag gilt `[post] standing_hashtag`. Spielen mehrere Mannschaften an einem Tag, stehen alle zugehörigen Hashtags auf dem Beitrag — anders als der Spiel-Hashtag, der dann falsch wäre und `[post] overlap_hashtag` weicht. |
| `[post] bot_notice` / `bot_notice_marker` | Ob die Kopfzeile überhaupt erscheint: `always`, `never` oder `auto` (nur solange die Biografie es nicht selbst sagt) |
| `[post] image_placeholder` / `video_placeholder` / `audio_placeholder` / `sticker_placeholder` / `video_hint` | Texte für Beiträge, die Medien tragen, aber keinen eigenen Text — und für einen fehlgeschlagenen Video-Upload |
| `[post] media_prefix` / `[files] video_retry_dir` / `[post] video_retry_text` / `[limits] video_retry_max_attempts` / `video_retry_interval_seconds` | Ein Video, dessen Upload scheitert, wird beiseitegelegt und später als Antwort auf seinen Beitrag nachgereicht: unter welchem Namen es abgelegt wird, wo es wartet, was die Antwort sagt, wie oft es versucht wird und wie lange dazwischen |
| `[profile] enabled` / `marker` / `line_on` / `line_off` / `line_off_no_match` | Die Statuszeile der Biografie. Platzhalter: `{info}` („1. Spieltag" / „DFB-Pokal, 1. Runde" / `fallback_match_info`), `{hashtag}`, `{date}`, `{time}` |
| `[schedule] day_end` | Wann der Ticker sich selbst beendet (Vorgabe `23:59`) |
| `[schedule] subscribe_renew_seconds` | Wie oft das Live-Abo erneuert wird (es gilt nur wenige Minuten) |
| `[schedule] pause_between_posts_seconds` | Die Pause zwischen zwei Bluesky-Beiträgen |
| `[files] session` / `state` / `posts_map` / `log` | Dateinamen im Programmordner. Beim Umstieg von einer älteren Fassung werden vorhandene `dsc_ticker_*`-Dateien **automatisch übernommen** — eine neue Kopplung ist nicht nötig. |
| `[logging] to_file` / `max_bytes` / `backup_count` | Die Protokolldatei und ihre Rotation |
| `[limits] max_video_bytes` / `video_job_timeout_seconds` | Grenzwerte von Bluesky, normalerweise unverändert lassen |

---

## Wie ein Beitrag auf Bluesky aussieht

```
⚽ [Inoffizieller Bot]
🔗 Quelle: WhatsApp-Kanal der Arminia     ← „Quelle" ist der klickbare Link

<Kanaltext, bei Bedarf gekürzt> (1/3)

#DSCWOB #arminia                          ← nur am letzten Teil
```

- Zu lange Beiträge werden geteilt (300 Graphemes und 3000 Bytes); die folgenden
  Teile sind **Antworten** (ein Faden, der keine Zeitleisten flutet).
- URLs im Text sind klickbar; bei Links ohne andere Medien wird eine
  **Vorschaukarte** angehängt (etwa von YouTube).
- Bilder: bis zu 4 je Beitrag, automatisch komprimiert. **Videos werden
  hochgeladen** (Bluesky-Grenze etwa 100 MB, die Verarbeitung auf der Gegenseite
  kann einen Moment dauern); scheitert der Upload, tritt das WhatsApp-Vorschaubild
  an seine Stelle, dazu der Hinweis „🎥 (Video im Original-Kanal)".
- Reihenfolge der Einbettung je Beitrag: Video > Bilder > Linkkarte (Bluesky
  erlaubt nur eine).
- **Bearbeitungen im Kanal:** Wird ein bereits reposteter Kanalbeitrag geändert,
  löscht der Bot seine alten Bluesky-Beiträge dazu und postet die berichtigte
  Fassung neu (die Zuordnung steht in `skyrelay_posts.json` und gilt pro Tag).
  Unveränderte Wiederzustellungen erkennt er an einem Text-Hash und ignoriert
  sie. Bearbeitungen an Beiträgen **ohne** gespeicherte Zuordnung (alte oder
  fremde) werden vollständig ignoriert — alte Tickerbeiträge sind uninteressant.
- Wo das alles landet, entscheidet `[layout]`, und der Assistent zeigt davon eine
  Vorschau, bevor etwas veröffentlicht wird.

---

## Wenn etwas klemmt

| Symptom | Ursache / Abhilfe |
|---|---|
| `Wire format was corrupt` | Ein Fehler in neonize 0.4.0/0.4.1 (NUL-Bytes abgeschnitten, [#199](https://github.com/krypton-byte/neonize/issues/199)) — upstream in **0.4.2** behoben (PR #198). Wir fahren `0.4.3.post0`, vollständig geprüft am 08.08.2026. |
| `panic: required field neonize.NewsletterMessage.Message not set` | Ein Go-Absturz, wenn der Nachrichtenabruf eine unsichtbare **Meta-Nachricht** erwischt (ein bearbeiteter oder gelöschter Beitrag). Betrifft nur **Wiederholen und Nachholen** (kleineres `N` wählen); der Live-Betrieb lauscht seit 13.07.2026 auf Ereignisse und ist immun. |
| `VersionError: gencode … runtime …` | protobuf zu alt → `pip install -U protobuf` (**niemals** herunterstufen). |
| Ärger nach einem Wechsel der neonize-Fassung | `skyrelay_session.sqlite3` löschen und neu koppeln (Datenbankschema). |
| Hänger oder Fehler direkt nach der ersten Kopplung | Normal (der Server erzwingt eine neue Verbindung); das Programm wartet und versucht es selbst erneut. Einfach neu starten, falls es doch aufgibt. |
| `⚠️ No DFL code on file for "XY"` im Protokoll | Ein Pokalgegner oder sonst unbekannt → das richtige Kürzel unter `[team_codes]` nachtragen. |
| cron: `/bin/sh: 1: …/bin/python3: not found` | Ein Tippfehler im Pfad (meist die Groß- und Kleinschreibung). Gemeint ist der **Interpreter**, nicht Python. Prüfen mit `ls -l <Pfad>`. |
| Der Ticker startet an einem spielfreien Tag | Das sollte `league_shortcuts` verhindern. Im Protokoll nach „matches from other leagues ignored" schauen und danach, welche Liga als Spieltag erkannt wurde. |
| Die Statuszeile bleibt auf „Bot ist an" | Der Prozess wurde hart getötet (`kill -9`, Stromausfall) — dann läuft das `finally` nicht. Von Hand zurücksetzen: `SKYRELAY_PROFILE=off … venv/bin/python skyrelay-matchday.py`. |
| Das Programm „postet nichts" | Läuft es im richtigen Modus? Im Protokoll nachsehen: `REPLAY finished…` gegen `Listening for new channel posts…`. Umgebungsvariablen müssen **vor** dem python-Aufruf auf derselben Zeile stehen. |
| `Error sending close to websocket … EOF` am Ende | Kosmetik beim sauberen Trennen — ignorieren. |
| `SIGSEGV … signal arrived during cgo execution` **nach** „REPLAY finished" oder dem Tagesende | Ein Aufräumrennen in neonize: Der Go-Socket-Thread schreibt nach Python, während der Interpreter schon herunterfährt. Rein kosmetisch — die Arbeit war zu dem Zeitpunkt erledigt. Seit dem 13.07. wartet das Programm nach dem Trennen 2 s, um das zu vermeiden. |
| `Press Ctrl+C to exit` / `whatsmeow.Client INFO`-Zeilen | Kommen aus der Go-Schicht von neonize, lassen sich nicht abschalten und sind harmlos. |
| `failed to find libmagic` | Das Systempaket fehlt: `sudo apt install libmagic1`. Ohne das lässt sich neonize nicht einmal importieren. |

---

## Umgebung und Installation (zum Nachschlagen)

Ein Raspberry Pi mit **64-Bit-System** (aarch64). Installiert und aktualisiert
wird über die Skripte, nicht von Hand:

```bash
./install.sh     # einmalig
./update.sh      # später
```

Die Paketstände stehen in `requirements.txt` — `neonize` ist dort mit Absicht
festgelegt, siehe [UPGRADE-TEST.md](UPGRADE-TEST.md).
