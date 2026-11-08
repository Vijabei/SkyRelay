"""Passt jeder aufgeteilte Beitrag in Blueskys Grenzen?

Bluesky nimmt hoechstens 300 Graphemes und 3000 Bytes je Beitrag. split_body
in skyrelay_layout.py teilt einen Text so auf, dass jeder fertige Beitrag -
samt Kopfzeile, Quelle, Hashtags und Zaehler - hineinpasst. Dieses Werkzeug
prueft das, statt es zu glauben:

  * der Fall vom 30.10.2026: ein Text, der als erster von zwei Beitraegen
    passt, als einziger aber nicht (dort stehen die Hashtags mit drin). Die
    alte Aufteilung pendelte zwischen einem und zwei Beitraegen und endete mit
    einem Beitrag von 313 Graphemes, den Bluesky ablehnte;
  * Zufallstexte mit Emoji, Flaggen, ZWJ-Folgen, Links und Umbruechen, ueber
    verschiedene [layout]-Einstellungen, mit und ohne Spiel-Hashtag, und mit
    Threads, die laenger sind als ihr Text (Videos im Feed: min_total).

Geprueft wird jeweils: jeder Beitrag in den Grenzen, kein Text verloren,
nichts dazuerfunden.

    tools/pruefe_aufteilung.py [Wurzelverzeichnis] [Anzahl Zufallstexte]

Rueckgabewert 0, solange nichts gefunden wird. Braucht nichts ausser der
Standardbibliothek (und nimmt regex, wenn es installiert ist).
"""
import pathlib
import random
import sys

WURZEL = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
ANZAHL = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
sys.path.insert(0, str(WURZEL))

import skyrelay_layout as L  # noqa: E402

URL = "https://www.instagram.com/p/DfFkvesivQm/"

# Die Bausteine, wie sie in der Vorlage stehen.
SCHREIBER = {
    "prefix": L.text_block("⚽ [Inoffizieller Bot]"),
    "source": L.source_block(L.DEFAULT_SOURCE_TEMPLATE, "Beitrag auf Instagram", URL),
    "match_hashtag": None,
    "standing_hashtag": L.tag_block("arminia"),
}
MIT_SPIEL = dict(SCHREIBER, match_hashtag=L.tag_block("DSCWOB"))


def layout(**abweichend):
    ergebnis = dict(L.DEFAULT_LAYOUT)
    for baustein, wert in abweichend.items():
        ergebnis[baustein] = tuple(wert)
    return ergebnis


LAYOUTS = {
    "Vorgabe": layout(),
    "Hashtag auf jedem": layout(standing_hashtag=("all", "bottom", 2)),
    "Hashtag oben im ersten": layout(standing_hashtag=("first", "top", 3)),
    "Kopfzeile auf jedem": layout(prefix=("all", "top", 1)),
    "Quelle unten im letzten": layout(source=("last", "bottom", 3)),
    "ohne Kopfzeile und Quelle": layout(prefix=("none", "top", 1),
                                        source=("none", "top", 2)),
}

STUECKE = ["Arminia", "Sieg", "⚫⚪🔵", "💪", "🙌🏼", "#DSC", "@arminiaofficial",
           "Spieltag", "Tor!", "\n", "\n\n", "🏟️", "👨‍👩‍👧", "🇩🇪", "Danke", "an",
           "alle", "Fans", "https://arminia.de/news/ein-langer-artikel", "…",
           "—", ".", "!", "Schüco-Arena", "Ä", "ß"]

befunde = []
faelle = 0


def pruefe(name, text, schreiber, aufbau, min_total=1):
    global faelle
    faelle += 1

    def budget(index, total):
        g, b = L.post_overhead(index, total, schreiber, aufbau)
        return L.GRAPHEME_LIMIT - g, L.BYTE_LIMIT - b

    teile = L.split_body(text, budget, min_total=min_total)
    gesamt = max(len(teile), min_total)
    for index, teil in enumerate(teile):
        fertig = L.build_post(
            L.PlainBuilder(), index, gesamt,
            lambda tb, t=teil, i=index: tb.text(t + L.counter_suffix(i, gesamt)),
            schreiber, aufbau).build_text()
        if not L.fits(fertig):
            befunde.append(f"{name}: Beitrag {index + 1}/{gesamt} hat "
                           f"{L.grapheme_len(fertig)} Graphemes / "
                           f"{L.utf8_len(fertig)} Bytes - Text {text[:60]!r}")
            return
    if "".join(text.split()) != "".join("".join(teile).split()):
        befunde.append(f"{name}: Text veraendert - {text[:60]!r}")


# 1. Der Fall vom 30.10.: genau zwischen den beiden Budgets.
for laenge in range(200, 300):
    text = ("Arminia " * 60)[:laenge]
    for aufbau_name, aufbau in LAYOUTS.items():
        pruefe(f"Grenzfall {laenge} ({aufbau_name})", text, SCHREIBER, aufbau)
        pruefe(f"Grenzfall {laenge} mit Spiel ({aufbau_name})", text, MIT_SPIEL, aufbau)
        pruefe(f"Grenzfall {laenge}, 2 Videos ({aufbau_name})", text, SCHREIBER,
               aufbau, min_total=2)

# 2. Zufallstexte.
zufall = random.Random(20261108)
for nummer in range(ANZAHL):
    text = " ".join(zufall.choice(STUECKE) for _ in range(zufall.randint(3, 260)))
    aufbau_name = zufall.choice(list(LAYOUTS))
    schreiber = zufall.choice([SCHREIBER, MIT_SPIEL])
    pruefe(f"Zufall {nummer} ({aufbau_name})", text, schreiber,
           LAYOUTS[aufbau_name], min_total=zufall.choice([1, 1, 1, 2, 3]))

for befund in befunde[:20]:
    print(f"✗ {befund}")
if len(befunde) > 20:
    print(f"… und {len(befunde) - 20} weitere")
print(f"{faelle} Fälle, {len(befunde)} Befund(e)  (Zählung: {L.GRAPHEME_SOURCE})")
sys.exit(1 if befunde else 0)
