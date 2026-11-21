# Plan: SkyRelay einsprachig englisch

**Stand: zurückgestellt am 21.11.2026.** Erst wird der Stand aus
[#43](https://github.com/Vijabei/SkyRelay/pull/43) bis
[#45](https://github.com/Vijabei/SkyRelay/pull/45) auf dem Pi erprobt. Bevor es
losgeht, sind die Entscheidungen unten zu treffen.

## Ziel

Code, Kommentare, Protokolle, Skripte und Werkzeuge sind englisch. Mehrsprachig
bleibt nur, was ein Mensch im Einrichtungsassistenten und in den Meldungen des
Wächters liest – über die Übersetzung. Beitragstexte und Bio sind Inhalt und
kommen weiter aus der Konfiguration.

## Bestand (gezählt am 21.11.2026 auf `develop`)

| Bereich | Umfang | Deutsch darin |
|---|---|---|
| Shell-Skripte `install.sh`, `update.sh`, `config.sh`, `common.sh`, `tools/i18n.sh` | rund 600 Zeilen | rund 160 Zeilen Ausgabe und Kommentar |
| `tools/pakete_angleichen.py`, `tools/pruefe_aufrufe.py`, `tools/pruefe_aufteilung.py` | rund 300 Zeilen | Dateinamen, Ausgaben, Docstrings, Bezeichner (25 Funde von `check_language.py`) |
| `skyrelay-testlauf.py` | – | der Dateiname |
| Bezeichner im Assistenten | `skyrelay_setup_app.py`: rund 60 von 167 Namen (`Feld`, `Bereich`, `platzhalter`, `action_speichern` …) und 11 CSS-IDs (`#rahmen`, `#spalte` …); `skyrelay-setup.py`: eine Handvoll (`fenster_modus`, `naechste_schritte`) | nur im Code, nichts Sichtbares |
| Übersetzung | 437 Texte im Katalog | Quellsprache ist Deutsch: jeder Bedientext steht deutsch im Code |
| Tastenbeschriftungen im Fenster | 5 Stellen (`BINDINGS`) | immer deutsch, weil Textual sie beim Import liest |
| `skyrelay.conf.example` | 267 Kommentarzeilen | vollständig deutsch, ebenso die Erklärungen, die `--add-missing` einträgt |
| Doku | `CONTRIBUTING.md`, `UPGRADE-TEST.md` | nur deutsch; README, CheatSheets, Changelog und BETA sind zweisprachig |

Ticker, Feed, Wächter und die gemeinsamen Module sind schon englisch. Das hält
`tools/check_language.py` fest.

## Offene Entscheidungen

1. **Quellsprache der Übersetzung umdrehen?** Empfehlung: ja. Dann steht im
   Code nur Englisch, `de.po` übersetzt ins Deutsche, und die Regel B des
   Prüfers wird zu „Bedientext ist englisch und läuft durch `_()`“. Bei nein
   bleiben die Bedientexte die einzige deutsche Stelle im Code, und Phase 4
   entfällt.
2. **Sprache der Konfigurationsvorlage.** (a) nur englisch – einfach,
   einheitlich, wer lieber Deutsch liest, nimmt den Assistenten. (b) zwei
   Vorlagen, `--add-missing` wählt nach `language` – freundlicher, aber jede
   neue Einstellung muss in beide. Empfehlung: (a).
3. **Vorgaben für Beiträge** („Bot ist an“, „Neues Video im Kanal“): bleiben
   deutsch, sie sind Inhalt. Vorgaben je Sprache wären ein eigenes Vorhaben.
4. **Alte Dateinamen.** Empfehlung: nur `skyrelay-testlauf.py` bekommt für eine
   Fassung eine Weiterleitung, weil es von Hand aufgerufen wird; die Werkzeuge
   ruft nur `update.sh` auf.

## Phasen

Jede Phase ist ein eigener Pull Request nach `develop`, wird über `beta` auf dem
Pi erprobt und geht erst danach Richtung `main`. Jede Phase erweitert
`tools/check_language.py`, damit der erreichte Stand gesichert bleibt.

### Phase 1: Shell-Skripte englisch

- Ausgaben, Kommentare und Hilfetexte von `install.sh`, `update.sh`,
  `config.sh`, `common.sh` und `tools/i18n.sh` englisch.
- `update.sh` in eine `main()`-Funktion fassen, die am Ende aufgerufen wird:
  Dann hat bash das Skript vollständig gelesen, bevor `git pull` es ersetzt.
  Heute schützt nur, dass git Dateien neu anlegt statt sie zu überschreiben.
- `check_language.py` lernt Shell: Kommentare und Texte in `printf`, `echo`,
  `ok`, `warn`, `abort`.
- Test auf dem Pi: `./update.sh` mit und ohne Neues, `./config.sh --check`,
  `./config.sh --add-missing`; `install.sh` in einem frischen Verzeichnis.

### Phase 2: Werkzeuge und Dateinamen

- `tools/pakete_angleichen.py` → `tools/align_packages.py`,
  `tools/pruefe_aufrufe.py` → `tools/check_calls.py`,
  `tools/pruefe_aufteilung.py` → `tools/check_splitting.py`,
  `skyrelay-testlauf.py` → `skyrelay-testrun.py` (alter Name als Weiterleitung).
- Inhalt englisch, Bezeichner eingeschlossen (`WURZEL`, `Unterschrift`,
  `pruefe` …).
- `update.sh`, README, CheatSheets, `CONTRIBUTING.md`, `UPGRADE-TEST.md` nennen
  die neuen Namen.
- Übergang: Das alte `update.sh`, das gerade läuft, findet nach dem Pull
  `pakete_angleichen.py` nicht mehr und überspringt den Abgleich still. Harmlos,
  solange sich `requirements.txt` im selben Update nicht ändert – deshalb keine
  Paketänderung in diesem Pull Request.
- `check_language.py` prüft dann auch `tools/`.

### Phase 3: Bezeichner im Assistenten

- Klassen, Funktionen, Attribute, Aktionen (`action_speichern` → `action_save`)
  und CSS-IDs in `skyrelay_setup_app.py` und `skyrelay-setup.py` englisch. Ein
  reiner Umbau ohne sichtbare Änderung.
- Absicherung: ein Probelauf mit Textuals `run_test()`, der das Fenster ohne
  Terminal öffnet, durch alle Bereiche wechselt und die Tasten drückt. Er bleibt
  als `tools/check_window.py`.
- Vor Phase 4, damit das Umdrehen sauberen Code anfasst.

### Phase 4: Quellsprache umdrehen (nur bei Ja zu Entscheidung 1)

- Ein Skript nimmt die Paare aus `locales/en/LC_MESSAGES/skyrelay.po`, ersetzt
  im Code jeden deutschen Text durch seine englische Fassung, baut daraus
  `locales/de/LC_MESSAGES/skyrelay.po` (Englisch → Deutsch) und setzt
  `SOURCE_LANGUAGE = "en"`.
- Vorher prüfen, ob zwei deutsche Texte dieselbe englische Fassung haben; die
  bekommen einen Kontext (`pgettext`) oder eine eigene Formulierung.
- Betrifft auch die Meldungen des Wächters (Bluesky-Direktnachrichten, Pings).
- Die Tastenbeschriftungen werden zur Laufzeit gebaut, nachdem die Sprache
  feststeht.
- **Gleichheitsprobe:** vor und nach dem Umbau jeden Text in beiden Sprachen
  ausgeben – die Tabellen müssen Zeichen für Zeichen gleich sein. Dazu die
  Durchläufe des Assistenten auf Deutsch und Englisch und der Probelauf aus
  Phase 3.
- Risiko: Wer Deutsch nutzt, braucht ab jetzt die gebaute `de.mo`. Fehlt Babel
  oder scheitert das Bauen in `update.sh`, erscheint die Oberfläche englisch
  statt deutsch – heute ist es umgekehrt. `update.sh` meldet das bereits; Babel
  steht in `requirements.txt`.

### Phase 5: Konfigurationsvorlage (nach Entscheidung 2)

- Bei (a): die 267 Kommentarzeilen und die Erklärungen für `--add-missing`
  englisch, ebenso die Kommentare, die der Assistent in die `skyrelay.conf`
  schreibt (heute mit `# lang: de` markiert).
- Bestehende `skyrelay.conf`-Dateien bleiben, wie sie sind; neu eingetragene
  Schlüssel bekommen englische Erklärungen. Das gehört in den Changelog.

### Phase 6: Doku nachziehen

- `CONTRIBUTING.md` englisch, dazu `CONTRIBUTING.de.md` wie bei README und
  Changelog. Der Abschnitt zu Übersetzungen beschreibt die neue Quellsprache.
- `UPGRADE-TEST.md` englisch, der Teil vom August bleibt, wie er ist.

## Fertig, wenn

- `tools/check_language.py` über alle Python- und Shell-Dateien keinen Fund
  meldet;
- der Assistent auf Deutsch dieselben Texte zeigt wie vorher (Gleichheitsprobe)
  und auf Englisch kein Deutsch mehr außer den Vorgaben für Beiträge und Bio;
- `update.sh`, `install.sh` und `config.sh` auf dem Pi ohne Auffälligkeiten
  laufen.

## Reihenfolge und Aufwand

| Phase | Aufwand | Risiko | hängt ab von |
|---|---|---|---|
| 1 Shell | klein | gering | – |
| 2 Werkzeuge | klein | gering | – |
| 3 Bezeichner | mittel | gering (Probelauf) | – |
| 4 Quellsprache | groß | mittel (Gleichheitsprobe) | 3, Entscheidung 1 |
| 5 Vorlage | mittel | gering | Entscheidung 2 |
| 6 Doku | klein | gering | 2, 4 |

1 und 2 können sofort und auch zusammen laufen. Zwischen den Phasen am besten je
einen Spieltag, an dem der Pi den Stand im Ernstbetrieb fährt.
