# Mitmachen bei SkyRelay

Verbesserungen sind willkommen — besonders von Fans anderer Vereine, die das
Projekt bei sich einsetzen. Man muss dafür nicht programmieren können.

## Was am meisten hilft

### Vereinskürzel ergänzen oder korrigieren

Die Kürzel bilden den Spiel-Hashtag (`#KSCDSC`). Es gibt dafür **keinen
offiziellen Standard** und OpenLigaDB liefert sie nicht mit — deshalb pflegt
SkyRelay eine eigene Tabelle in `skyrelay-setup.py` (`BEKANNTE_KUERZEL`).
Fehlt dort ein Verein oder ist ein Kürzel unüblich, freuen wir uns über einen
Hinweis.

Dazu braucht es die **Team-Nummer** aus OpenLigaDB. So findest du sie:

```bash
curl -s "https://api.openligadb.de/getavailableteams/bl3/2026" | grep -B2 -i "verl"
```

(`bl1`, `bl2`, `bl3` für die Fußball-Ligen, `del` für Eishockey; die Zahl am
Ende ist das Startjahr der Saison.)

Dann entweder ein **Issue** aufmachen — „Team 114 (SC Verl) sollte SCV heißen"
genügt völlig — oder direkt einen Pull Request:

```python
    114: "SCV",       # SC Verl
```

Bitte **alphabetisch nach Vereinsnamen** einsortieren, nicht nach Liga. So
bleiben Einträge bei Auf- und Abstieg an derselben Stelle stehen.

Zwei Dinge zu beachten:

* Vereine mit **gleichem Kürzel** sind ein Problem, sobald sie aufeinander
  treffen (Werder Bremen und Waldhof Mannheim führen beide „SVW" — der Hashtag
  wäre `#SVWSVW`). Der Ticker warnt in dem Fall im Protokoll. Wenn du so eine
  Kollision entdeckst, gerne melden.
* **Zweitvertretungen** bekommen eine angehängte Ziffer (`VFB2`), weil
  Leerzeichen in Hashtags nicht funktionieren.

### Fehler melden

Hilfreich sind: was du getan hast, was passiert ist, und der passende Ausschnitt
aus der Protokolldatei.

> ⚠️ **Vorher schwärzen:** Protokolle enthalten Kanal-Kennungen (`…@newsletter`)
> und Beitragstexte. Poste **niemals** die Datei `*_session.sqlite3` oder deren
> Inhalt — sie entspricht dem Zugang zu deinem gekoppelten WhatsApp-Konto —
> und selbstverständlich keine App-Passwörter.

### Weitere Sportarten und Ligen

SkyRelay erkennt Spieltage über OpenLigaDB. Gepflegt sind dort vor allem Fußball
und Eishockey. Wenn du eine weitere aktuelle Liga findest, die in die Vorschlags-
liste des Assistenten gehört (`EMPFOHLENE_LIGEN`), sag Bescheid.

## Übersetzungen

Die Bedienoberfläche des Einrichtungsassistenten ist auf **Deutsch** geschrieben
— das ist die Ausgangssprache, alles andere ist eine Übersetzung davon. Englisch
liegt fertig vor; weitere Sprachen sind willkommen.

Nicht übersetzt werden Protokoll und Konsolenausgabe der beiden Bots. Die
bleiben immer englisch, damit dieselbe Meldung überall dieselbe ist — im
cron-Mail, im kopierten Ausschnitt, im Issue.

Eine neue Sprache anlegen:

```bash
./tools/i18n.sh add nl          # legt locales/nl/LC_MESSAGES/skyrelay.po an
# jetzt die .po-Datei bearbeiten - mit Poedit, mit Weblate oder von Hand
./tools/i18n.sh compile         # .po -> .mo, das braucht das Programm
./tools/i18n.sh status          # wie weit ist jede Sprache?
```

Die fertigen `.mo`-Dateien werden **gebaut, nicht eingecheckt**. Ins Repository
gehören nur die `.po`-Dateien und die Vorlage `locales/skyrelay.pot`.

Hat sich am Quelltext etwas geändert, holt

```bash
./tools/i18n.sh extract && ./tools/i18n.sh update
```

die neuen Texte in alle Kataloge. `update` markiert dabei ähnliche Treffer als
`fuzzy` — die will man durchsehen, bevor man sie freigibt, denn beim Übersetzen
werden sie sonst übersprungen.

Was in einem Wörterbuch steht und erst später übersetzt wird, muss mit `N_()`
markiert sein statt mit `_()`. Sonst findet Babel den Text im Quelltext nicht,
und die Oberfläche bleibt an dieser Stelle deutsch.

## Zum Code

* Kommentare erklären das **Warum**, nicht das Was — besonders bei den
  Eigenheiten von WhatsApp und Bluesky, von denen es reichlich gibt.
* Gemeinsam genutzte Funktionen gehören nach `skyrelay_common.py`.
* Vor einem Pull Request bitte prüfen, dass beide Programme starten:

```bash
venv/bin/python -m py_compile skyrelay-matchday.py skyrelay-feed.py skyrelay_common.py
```

* Änderungen am Ticker möglichst mit `SKYRELAY_DRY_RUN=1` testen, dann wird
  nichts veröffentlicht.

## Lizenz und Rechte an Beiträgen

Das Projekt steht unter der [PolyForm Noncommercial License 1.0.0](LICENSE):
nichtkommerzielle Nutzung frei, kommerzielle Nutzung auf Anfrage.

Damit dieses Modell tragfähig bleibt, gilt für eingereichte Beiträge:

> Mit dem Einreichen eines Beitrags (Pull Request, Patch oder Codeschnipsel in
> einem Issue) räumst du dem Projektinhaber das nicht-ausschließliche,
> unbefristete, unwiderrufliche und übertragbare Recht ein, deinen Beitrag zu
> nutzen, zu verändern und weiterzugeben — auch unter anderen Lizenzbedingungen,
> einschließlich kommerzieller Lizenzen. Deine eigenen Rechte am Beitrag behältst
> du; du kannst ihn also weiterhin frei verwenden.

Bitte reiche nur Code ein, den du selbst geschrieben hast oder weitergeben
darfst — insbesondere keine Ausschnitte aus fremden Projekten mit anderer
Lizenz.

## Kein Support-Versprechen

Das ist ein Freizeitprojekt. Issues werden gelesen, aber es gibt keine
garantierte Reaktionszeit — und ebenso wenig eine Zusage, dass jeder Vorschlag
übernommen wird.
