# CheatSheet: dsc-spieltag-ticker.py

Repostet den **Arminia-Bielefeld-WhatsApp-Kanal** nach Bluesky (`dsc-spieltagticker.bsky.social`) —
an Spieltagen automatisch von 6 bis 24 Uhr, gesteuert über Umgebungsvariablen.

---

## Schnellreferenz: Umgebungsvariablen

| Variable | Werte | Funktion |
|---|---|---|
| `BLUESKY_APP_PASSWORD` | `xxxx-xxxx-xxxx-xxxx` | **Pflicht** (außer im Dry-Run). App-Passwort des Bluesky-Bot-Accounts. |
| `DSC_TICKER_DRY_RUN` | `1` | Nur loggen, **nichts** auf Bluesky posten. Für gefahrlose Tests. |
| `DSC_TICKER_FORCE` | `1` | Läuft auch dann, wenn OpenLigaDB für heute **kein** Spiel kennt (Testspiele, manuelle Läufe). Findet OpenLigaDB doch eins, werden Hashtag und Spieltagsinfo trotzdem übernommen. |
| `DSC_TICKER_HASHTAG` | z. B. `DSCGUE` | Spiel-Hashtag **manuell** setzen (mit/ohne `#`, Groß-/Kleinschreibung egal). Hat Vorrang vor dem automatisch generierten. Nötig bei Testspielen, die OpenLigaDB nicht kennt. |
| `DSC_TICKER_REPLAY` | Zahl `N` | Testmodus: verarbeitet einmalig die **letzten N vorhandenen** Kanal-Posts und **beendet sich**. Wasserzeichen bleibt unberührt, keine Duplikatsprüfung. |
| `DSC_TICKER_CATCHUP` | Zahl `N` | Nachhol-Modus: verarbeitet die letzten N Posts (**überspringt** bereits Verarbeitete dank Wasserzeichen) und **lauscht danach normal weiter**. Für „Script zu spät gestartet". |
| `DSC_TICKER_PAIR_PHONE` | `4915123456789` | Erst-Kopplung per 8-stelligem Zahlencode statt QR-Scan (Nummer international, ohne `+`/führende 0). Nur beim allerersten Lauf nötig; wird ignoriert, wenn schon gekoppelt. |
| `DSC_TICKER_PROFILE` | `on` / `off` | Setzt **nur** die Profil-Statuszeile der Bluesky-Bio und beendet sich sofort (ohne WhatsApp). Zum Testen und manuellen Nachkorrigieren. |

---

## Die Betriebsarten

### 1. Cron-Automatik (Normalbetrieb, Pflichtspiele)

```cron
0 6 * * * BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx" /home/geordi/trotzdemdabei/bin/python3 /home/geordi/trotzdemdabei/dsc-spieltag-ticker.py >/dev/null 2>&1
```

> ⚠️ **Pfade sind case-sensitive:** `trotzdemdabei` wird komplett kleingeschrieben.
> Ein `Trotzdemdabei` im Interpreter-Pfad führt zu `/bin/sh: 1: …/bin/python3: not found`
> — die Meldung meint den Interpreter, nicht Python selbst.
>
> ⚠️ **Keine `>> ticker.log 2>&1`-Umleitung mehr:** Das Script schreibt sein Log selbst
> (siehe unten). Bleibt die Umleitung stehen, steht jede Zeile **doppelt** in der Datei.

**Verhalten:**
- Startet täglich um 6:00 Uhr und prüft über OpenLigaDB, ob Arminia **heute** spielt (Liga & Pokal, ±1 Woche Abfragefenster, Team-ID 83).
- **Kein Spiel** → Sofort-Exit, WhatsApp wird gar nicht kontaktiert.
- **Spieltag** → lauscht auf **Live-Events** des Kanals bis **23:59 Uhr** (Reposts kommen sofort,
  kein Polling mehr), beendet sich dann selbst.
- Der Spiel-Hashtag (z. B. `#DSCWOB` heim, `#WOBDSC` auswärts) wird aus den OpenLigaDB-Daten generiert
  (DFL-Kürzel, Heimteam zuerst) und mit `#arminia` an jeden Post gehängt.
- Repostet nur Posts vom **heutigen Tag**: Was WhatsApp beim Verbinden an aufgelaufenen Posts
  nachliefert (Offline-Queue), wird mitgenommen, sofern es von heute ist — Älteres wird verworfen.
- **Bio-Statuszeile:** Zu Beginn des Lauschens wird die erste Zeile der Bluesky-Bio auf
  „🟢 Bot ist an - 1. Spieltag #KSCDSC ⚫⚪🔵" gesetzt, beim Beenden (auch per Strg+C oder
  nach einem Fehler) auf „🔴 Bot ist aus - nächstes Spiel #DSCFCE ⚫⚪🔵". Alle weiteren
  Bio-Zeilen, Avatar, Banner und Anzeigename bleiben unangetastet.

### 2. Manueller Lauf (Testspiele, „mal ein paar Stunden testen")

```bash
export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"
DSC_TICKER_FORCE=1 DSC_TICKER_HASHTAG=DSCGUE python dsc-spieltag-ticker.py
```

- **Beenden:** `Strg+C` (wird sauber abgefangen) — oder von selbst um 23:59 Uhr.
- **Neustart am selben Tag:** unkritisch, keine Duplikate (Wasserzeichen).
- Ohne `DSC_TICKER_HASHTAG` bekommen die Posts nur `#arminia` — außer OpenLigaDB kennt
  für heute doch ein Spiel, dann werden Hashtag und Spieltagsinfo automatisch übernommen.
- Für lange Läufe über SSH (überlebt das Schließen der Sitzung):

```bash
nohup env DSC_TICKER_FORCE=1 DSC_TICKER_HASHTAG=DSCGUE python dsc-spieltag-ticker.py >/dev/null 2>&1 &
tail -f ticker.log        # zuschauen (Log schreibt das Script selbst)
pgrep -af dsc-spieltag    # läuft er? PID + Kommandozeile
kill <PID>                # beenden (sauber, Bio geht auf "Bot ist aus")
```

### 3a. Catchup (verpasste Posts nachholen, dann weiterlauschen)

```bash
DSC_TICKER_CATCHUP=5 DSC_TICKER_FORCE=1 DSC_TICKER_HASHTAG=DSCGUE python dsc-spieltag-ticker.py
```

- Holt die letzten `N` Posts nach, **überspringt** dabei alles, was laut Wasserzeichen heute
  schon verarbeitet wurde (keine Duplikate!), und geht dann nahtlos in den Lausch-Betrieb über.
- Der richtige Modus, wenn das Script am Spieltag zu spät gestartet wurde oder abgestürzt war.
- Gleicher neonize-Bug-Vorbehalt wie beim Replay (siehe unten): inhaltslose Nachricht unter
  den letzten `N` → Absturz. Kleines `N` wählen.

### 3b. Replay (Einzelpost-Test)

```bash
# Letzten Kanal-Post nur ins Log (Trockenübung):
DSC_TICKER_REPLAY=1 DSC_TICKER_FORCE=1 DSC_TICKER_DRY_RUN=1 python dsc-spieltag-ticker.py

# Letzten Kanal-Post ECHT auf Bluesky posten (End-to-End-Test):
DSC_TICKER_REPLAY=1 DSC_TICKER_FORCE=1 python dsc-spieltag-ticker.py

# Die letzten 3 Posts:
DSC_TICKER_REPLAY=3 ...
```

- Verarbeitet älteste → neueste, komplette Pipeline (Text, Bilder, Link-Karte), dann Sofort-Exit.
- Fasst das Wasserzeichen **nicht** an → der Automatikbetrieb bleibt unbeeinflusst.
- Achtung ohne Dry-Run: Replay prüft **nicht** auf Duplikate — mehrfaches Ausführen postet mehrfach.
- ⚠️ Bekannter neonize-Bug: Ist unter den **letzten N** Kanal-Nachrichten eine **inhaltslose
  Meta-Nachricht** (im Kanal unsichtbar, hat aber eine eigene ServerID — z. B. Bearbeitung
  oder Löschung eines Posts), stürzt das Script hart ab (Go-`panic: required field …
  Message not set`). Solche Nachrichten sind mit neonize nicht inspizierbar — bei einem
  Absturz einfach `N` schrittweise verkleinern, bis das Fenster vor der Meta-Nachricht endet.
  Betrifft nur Replay/Catchup, der Live-Betrieb ist immun.

---

## Erst-Kopplung (einmalig, interaktiv — nicht per Cron!)

```bash
DSC_TICKER_FORCE=1 DSC_TICKER_DRY_RUN=1 python dsc-spieltag-ticker.py
```

- Es erscheint ein ASCII-**QR-Code im Terminal** (nur wenn wirklich eine Kopplung nötig ist).
  Scannen mit: WhatsApp → Einstellungen → **Verknüpfte Geräte** → Gerät hinzufügen.
- **Scan-Probleme?** Terminal stark vergrößern + Bildschirm heller stellen (Kontrast!).
  Der QR-Code rotiert alle ~30 s — immer den zuletzt angezeigten scannen.
- **Alternative Zahlencode:** zusätzlich `DSC_TICKER_PAIR_PHONE=49…` setzen → Code im Handy eintippen
  („Stattdessen mit Telefonnummer koppeln").
- Ein Login über web.whatsapp.com im Browser hilft **nicht** — es zählt nur die Kopplung dieses Scripts.
- Danach liegt die Session in `dsc_ticker_session.sqlite3` und wird automatisch wiederverwendet.

---

## Dateien im Scriptordner

| Datei | Zweck | Löschen erlaubt? |
|---|---|---|
| `dsc-spieltag-ticker.py` | das Script | — |
| `dsc_ticker_session.sqlite3` | WhatsApp-Session (Kopplung) | Ja → erzwingt neue Erst-Kopplung |
| `dsc_ticker_state.txt` | Wasserzeichen: `Datum;letzte ServerID` | Ja → nächster Start setzt frische Baseline („ab jetzt") |
| `dsc_ticker_posts.json` | Zuordnung ServerID → Bluesky-Posts (heutiger Tag, für Bearbeitungen) | Ja → Bearbeitungen können dann alte Posts nicht mehr löschen, nur neu posten |
| `ticker.log` | Log — schreibt das Script **immer selbst**, egal wie gestartet (inkl. der Ausgaben des Go-Layers). Konsole zeigt weiterhin alles. | Ja |
| `ticker.log.1` … `.5` | rotierte Logs (ab 2 MB wird beim Start rotiert, `.1` = neuestes) | Ja |

---

## Feste Einstellungen (Konstanten im Script-Kopf)

| Konstante | Wert | Bedeutung |
|---|---|---|
| `CHANNEL_INVITE_LINK` | `…/channel/0029VaR1aJm6RGJAiMy8w73L` | Arminia-Kanal (JID `120363246785630110@newsletter`) |
| `BLUESKY_HANDLE` | `dsc-spieltagticker.bsky.social` | Ziel-Account |
| `SUBSCRIBE_RENEW_SECONDS` | `240` | Erneuerungs-Rhythmus des Live-Update-Abos (gilt nur wenige Minuten) |
| `DAY_END_HOUR/MINUTE` | `23:59` | Selbst-Beenden am Spieltag |
| `TEAM_CODES` | Dict | DFL-Kürzel je OpenLigaDB-teamId (2. Liga 2026/27). **Pokalgegner ggf. nachtragen** — unbekannte Teams bekommen einen 3-Buchstaben-Fallback + Log-Warnung. |
| `LEAGUE_PREFIXES` | `("bl","dfb")` | Nur Spiele aus diesen OpenLigaDB-Ligen zählen. OpenLigaDB listet zu Arminia auch Fantasie-Ligen (gesehen: **„ESP8266"**, dasselbe Spiel mit falschem Datum!) — ohne Filter würde der Ticker an spielfreien Tagen anspringen. Verworfene Ligen stehen im Log. |
| `LOG_TO_FILE` / `LOG_FILE_NAME` | `True` / `ticker.log` | Datei-Logging (immer aktiv, unabhängig von der Startart). `LOG_MAX_BYTES` = 2 MB, `LOG_BACKUP_COUNT` = 5. |
| `PROFILE_LINE_ON` / `_OFF` | Templates | Text der Bio-Statuszeile. Platzhalter: `{info}` (= „1. Spieltag" / „DFB-Pokal, 1. Runde" / „Testspiel"), `{hashtag}`, `{date}`, `{time}`. `PROFILE_STATUS_ENABLED = False` schaltet die Funktion ganz ab. |

---

## Post-Format auf Bluesky

```
⚽ [Inoffizieller Bot]
🔗 Quelle: WhatsApp-Kanal der Arminia     ← klickbarer Link

<Kanal-Text, ggf. gekürzt> (1/3)

#DSCWOB #arminia                          ← nur am letzten Chunk
```

- Überlange Posts werden gechunkt (300-Zeichen-Limit); Folge-Chunks als **Replies** (Thread, flutet keine Timelines).
- URLs im Text sind klickbar; bei Links ohne andere Medien wird eine **Vorschaukarte** (z. B. YouTube) angehängt.
- Bilder: bis 4 pro Post, automatisch komprimiert. **Videos werden hochgeladen** (Bluesky-Limit ~100 MB,
  serverseitige Verarbeitung kann etwas dauern); scheitert der Upload, dient das WhatsApp-Vorschaubild
  als Bild-Fallback plus Texthinweis „🎥 (Video im Original-Kanal)".
- Embed-Priorität pro Post: Video > Bilder > Link-Karte (Bluesky erlaubt nur ein Embed).
- **Bearbeitungen im Kanal:** Wird ein bereits reposteter Kanal-Post editiert, löscht der Bot
  seine alten Bluesky-Posts dazu und postet die korrigierte Version neu (Zuordnung in
  `dsc_ticker_posts.json`, gilt pro Tag). Unveränderte Wiederzustellungen werden per
  Text-Hash erkannt und ignoriert. Bearbeitungen zu Posts **ohne** gespeicherte Zuordnung
  (alte/fremde Posts) werden komplett ignoriert — alte Tickerposts sind uninteressant.

---

## Troubleshooting

| Symptom | Ursache / Lösung |
|---|---|
| `Wire format was corrupt` | Bug in neonize 0.4.0/0.4.1 (NUL-Byte-Abschneidung, [#199](https://github.com/krypton-byte/neonize/issues/199)) — seit **0.4.2** upstream gefixt (PR #198). Wir bleiben vorerst auf `0.3.18.post0` (im Spielbetrieb bewährt); Upgrade auf ≥ 0.4.3 in einer ruhigen Woche mit DRY_RUN/REPLAY testen, vorher `dsc_ticker_session.sqlite3` sichern. |
| `panic: required field neonize.NewsletterMessage.Message not set` | Go-Absturz, wenn der Nachrichten-Abruf eine unsichtbare **Meta-Nachricht** erwischt (z. B. Post-Bearbeitung/-Löschung). Betrifft nur **Replay/Catchup** (kleineres `N` wählen); der Live-Betrieb lauscht seit 13.07.2026 auf Events und ist immun. |
| `VersionError: gencode … runtime …` | protobuf zu alt → `pip install -U protobuf` (**nie** downgraden). |
| Nach neonize-Versionswechsel Probleme | `dsc_ticker_session.sqlite3` löschen + neu koppeln (DB-Schema). |
| Hänger/Fehler direkt nach Erst-Kopplung | Normal (Server erzwingt Reconnect); Script wartet + wiederholt selbst. Einfach neu starten, falls es doch abbricht. |
| `⚠️ Kein DFL-Kürzel für "XY"` im Log | Pokal-/unbekannter Gegner → korrektes Kürzel in `TEAM_CODES` nachtragen. |
| Cron: `/bin/sh: 1: …/bin/python3: not found` | Pfad-Tippfehler (meist Groß-/Kleinschreibung, `Trotzdemdabei` statt `trotzdemdabei`). Gemeint ist der **Interpreter**, nicht Python. Prüfen mit `ls -l <pfad>`. |
| Ticker startet an einem spielfreien Tag | Sollte durch `LEAGUE_PREFIXES` verhindert sein. Log prüfen auf „Spiele aus unbekannten Ligen ignoriert" bzw. welche Liga als Spieltag erkannt wurde. |
| Bio-Statuszeile bleibt auf „Bot ist an" | Prozess wurde hart getötet (`kill -9`, Stromausfall) — dann läuft das `finally` nicht. Manuell zurücksetzen: `DSC_TICKER_PROFILE=off … python dsc-spieltag-ticker.py`. |
| Script „postet nichts" | Läuft es im richtigen Modus? Log prüfen: `REPLAY-Modus…` vs. `Starte Poll-Loop…`. Env-Variablen müssen **vor** dem python-Aufruf auf derselben Zeile stehen. |
| `Error sending close to websocket … EOF` am Ende | Kosmetik beim sauberen Trennen — ignorieren. |
| `SIGSEGV … signal arrived during cgo execution` **nach** „REPLAY abgeschlossen"/Tagesende | Aufräum-Race in neonize: Go-Socket-Thread loggt nach Python, während der Interpreter schon beendet. Rein kosmetisch — die Arbeit war zu dem Zeitpunkt komplett erledigt. Script wartet seit 13.07. 2 s nach dem Trennen, um das zu vermeiden. |
| `Press Ctrl+C to exit` / `whatsmeow.Client INFO`-Zeilen | Kommen aus dem Go-Layer von neonize, nicht abschaltbar, harmlos. |

---

## Umgebung / Installation (Referenz)
- Raspberry Pi, **64-bit-OS** (aarch64) erforderlich, venv: `/home/geordi/trotzdemdabei`

- Installation:
```bash
pip install "neonize==0.3.18.post0" "protobuf>=7.34.1" atproto pillow requests
```
