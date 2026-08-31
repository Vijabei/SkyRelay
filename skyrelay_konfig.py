"""
SkyRelay - Konfiguration prüfen, anzeigen und nachziehen.

Bewusst ohne Fremdpakete: Der Einrichtungsassistent soll diese Werkzeuge auch
dann nutzen können, wenn atproto und Pillow noch nicht installiert sind.

Welche Werte die Programme lesen, wird nicht von Hand gepflegt, sondern aus den
Quelltexten abgeleitet. Eine gepflegte Liste wäre nach dem ersten Umbau falsch -
und genau solche Abweichungen sollen diese Werkzeuge ja finden.
"""

import ast
import configparser
import os

# Abschnitte, deren Schlüssel frei wählbar sind und deshalb in keinem Quelltext
# vorkommen: [team_codes] enthält OpenLigaDB-Team-Nummern.
FREIE_ABSCHNITTE = {"team_codes"}

QUELLTEXTE = {
    "skyrelay-matchday.py": "Ticker",
    "skyrelay-feed.py": "Feed",
    "skyrelay-setup.py": "Assistent",
    "skyrelay-testlauf.py": "Testlauf",
    "skyrelay_common.py": "gemeinsames Modul",
}

# cfg("abschnitt", "schluessel", vorgabe) in den Bots,
# lies_wert(zeilen, "abschnitt", "schluessel") im Assistenten.
LESEFUNKTIONEN = {"cfg": 0, "cfg_int": 0, "cfg_bool": 0}
ZEILENFUNKTIONEN = {"lies_wert": 1, "setze_wert": 1}

OHNE_VORGABE = object()  # Vorgabe ist kein Literal (oder es gibt keine)


def konfig_pfad(basis_dir):
    """Pfad der eigenen Konfiguration - im Programmverzeichnis oder dort, wohin
    SKYRELAY_CONFIG zeigt (so lassen sich mehrere Vereine parallel betreiben)."""
    return os.environ.get("SKYRELAY_CONFIG") or os.path.join(basis_dir, "skyrelay.conf")


# ------------------------------------------------------- Zugriffe im Quelltext
def _literal(knoten):
    """Wert eines Literals; OHNE_VORGABE, wenn es keines ist."""
    try:
        return ast.literal_eval(knoten)
    except Exception:
        return OHNE_VORGABE


def zugriffe(basis_dir):
    """Ermittelt aus den Quelltexten, welche Werte gelesen werden.

    Liefert {(abschnitt, schluessel): {"programme": Menge, "vorgabe": Wert}}.
    Der Quelltext wird geparst, nicht durchsucht - so zählt ein Beispielaufruf
    in einem Kommentar oder einer Zeichenkette nicht als echter Zugriff."""
    gefunden = {}
    for datei, name in QUELLTEXTE.items():
        pfad = os.path.join(basis_dir, datei)
        try:
            with open(pfad, encoding="utf-8") as quelle:
                baum = ast.parse(quelle.read(), pfad)
        except (OSError, SyntaxError):
            continue

        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call):
                continue
            funktion = getattr(knoten.func, "id", None)
            if funktion in LESEFUNKTIONEN:
                versatz = LESEFUNKTIONEN[funktion]
            elif funktion in ZEILENFUNKTIONEN:
                versatz = ZEILENFUNKTIONEN[funktion]
            else:
                continue
            if len(knoten.args) < versatz + 2:
                continue

            abschnitt = _literal(knoten.args[versatz])
            schluessel = _literal(knoten.args[versatz + 1])
            if not isinstance(abschnitt, str) or not isinstance(schluessel, str):
                continue

            eintrag = gefunden.setdefault((abschnitt, schluessel),
                                          {"programme": set(), "vorgabe": OHNE_VORGABE})
            eintrag["programme"].add(name)
            if funktion in LESEFUNKTIONEN and len(knoten.args) > versatz + 2:
                vorgabe = _literal(knoten.args[versatz + 2])
                if vorgabe is not OHNE_VORGABE and eintrag["vorgabe"] is OHNE_VORGABE:
                    eintrag["vorgabe"] = vorgabe
    return gefunden


# --------------------------------------------------------------- Vorlage lesen
def lade_vorlage(basis_dir):
    """Liest skyrelay.conf.example in Dateireihenfolge.

    Liefert eine Liste von Einträgen (abschnitt, schluessel, wert, kommentar) -
    kommentar sind die Zeilen, die unmittelbar über dem Schlüssel stehen. Sie
    wandern beim Nachziehen mit, denn ohne sie wäre ein neuer Schlüssel in der
    eigenen Datei ein Rätsel."""
    pfad = os.path.join(basis_dir, "skyrelay.conf.example")
    try:
        with open(pfad, encoding="utf-8") as datei:
            zeilen = datei.readlines()
    except OSError:
        return []

    eintraege = []
    abschnitt = None
    kommentar = []
    for zeile in zeilen:
        blank = zeile.strip()
        if not blank:
            kommentar = []
        elif blank.startswith("#"):
            kommentar.append(zeile)
        elif blank.startswith("[") and blank.endswith("]"):
            abschnitt = blank.strip("[]")
            kommentar = []
        elif "=" in blank and abschnitt is not None:
            schluessel, _, wert = blank.partition("=")
            eintraege.append((abschnitt, schluessel.strip(), wert.strip(), kommentar))
            kommentar = []
    return eintraege


def _schluesselpaare(text):
    """Alle (Abschnitt, Schlüssel) einer Konfiguration."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return {(abschnitt, schluessel)
            for abschnitt in parser.sections()
            for schluessel in parser[abschnitt]}


def _lies_eigene(basis_dir, konfig_text=None):
    """(Text, Parser) der eigenen Konfiguration - oder (None, Fehlermeldung)."""
    if konfig_text is None:
        try:
            with open(konfig_pfad(basis_dir), encoding="utf-8") as datei:
                konfig_text = datei.read()
        except OSError as fehler:
            return None, f"Konfiguration nicht lesbar: {fehler}"
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(konfig_text)
    except configparser.Error as fehler:
        return None, f"Konfiguration ist fehlerhaft: {fehler}"
    return konfig_text, parser


# -------------------------------------------------------------------- Prüfung
def sammle_konfig_befunde(basis_dir, konfig_text=None):
    """Vergleicht die eigene Konfiguration mit dem, was die Programme lesen, und
    mit der Vorlage. Liefert Paare (schwere, text); schwere ist "problem" oder
    "hinweis". Ohne konfig_text wird die Datei auf der Platte gelesen - der
    Assistent reicht stattdessen seinen noch ungespeicherten Stand herein."""
    text, parser = _lies_eigene(basis_dir, konfig_text)
    if text is None:
        return [("problem", parser)]

    eigene = _schluesselpaare(text)
    gelesen = zugriffe(basis_dir)
    vorlage = {(a, s) for a, s, _w, _k in lade_vorlage(basis_dir)}

    befunde = []
    if not vorlage:
        befunde.append(("hinweis", "skyrelay.conf.example fehlt oder ist "
                                   "unlesbar - der Abgleich mit der Vorlage "
                                   "entfällt."))

    # 1. Steht etwas in der eigenen Datei, das niemand liest? Genau so blieb
    #    [post] prefix im Feed monatelang wirkungslos.
    for abschnitt, schluessel in sorted(eigene):
        if abschnitt in FREIE_ABSCHNITTE:
            continue
        if (abschnitt, schluessel) not in gelesen:
            befunde.append(("problem",
                            f"[{abschnitt}] {schluessel} wird von keinem "
                            f"Programm gelesen - Tippfehler oder veraltet?"))

    # 2. Fehlt etwas, das gelesen wird? Dann greift still die Vorgabe.
    # 3. Fehlt etwas in der Vorlage? Dann erfährt niemand davon.
    for (abschnitt, schluessel), eintrag in sorted(gelesen.items()):
        wer = ", ".join(sorted(eintrag["programme"]))
        if (abschnitt, schluessel) not in eigene:
            befunde.append(("hinweis",
                            f"[{abschnitt}] {schluessel} fehlt - es greift die "
                            f"Vorgabe im Programm ({wer})"))
        if vorlage and (abschnitt, schluessel) not in vorlage:
            befunde.append(("problem",
                            f"[{abschnitt}] {schluessel} fehlt in "
                            f"skyrelay.conf.example ({wer})"))
    return befunde


def pruefe_konfiguration(basis_dir):
    """Druckt den Bericht und liefert den Rückgabewert für die Kommandozeile:
    0 = keine Probleme, 1 = mindestens eines. Verbindet sich mit nichts und
    verändert nichts."""
    print(f"Konfiguration: {konfig_pfad(basis_dir)}")
    befunde = sammle_konfig_befunde(basis_dir)
    probleme = [text for schwere, text in befunde if schwere == "problem"]
    hinweise = [text for schwere, text in befunde if schwere == "hinweis"]

    for titel, eintraege, zeichen in (("Probleme", probleme, "✗"),
                                      ("Hinweise", hinweise, "ℹ")):
        if eintraege:
            print(f"\n{titel}:")
            for eintrag in eintraege:
                print(f"  {zeichen} {eintrag}")

    if not befunde:
        print("\n✓ Keine Auffälligkeiten.")
    else:
        print(f"\n{len(probleme)} Problem(e), {len(hinweise)} Hinweis(e).")
        if hinweise:
            print("Fehlende Schlüssel lassen sich mit ihren Erklärungen "
                  "nachtragen:\n    venv/bin/python skyrelay-setup.py --nachziehen")
    return 1 if probleme else 0


# ------------------------------------------------------------ Was ist aktiv?
def _kurz(wert, breite=46):
    """Einzeilige, gekürzte Darstellung eines Wertes."""
    text = "" if wert is None else str(wert)
    text = text.replace("\n", " ")
    return text if len(text) <= breite else text[:breite - 1] + "…"


def zeige_konfiguration(basis_dir):
    """Listet jeden Wert, den die Programme lesen, mit seiner Herkunft auf:
    aus der eigenen Datei oder aus der Vorgabe im Programm.

    Ohne das bleibt undurchsichtig, was tatsächlich gilt: Ein fehlender
    Schlüssel fällt nicht auf, weil an seiner Stelle stillschweigend die Vorgabe
    greift."""
    pfad = konfig_pfad(basis_dir)
    print(f"Konfiguration: {pfad}\n")

    text, parser = _lies_eigene(basis_dir)
    if text is None:
        print(parser)
        return 1

    gelesen = zugriffe(basis_dir)
    vorlage = lade_vorlage(basis_dir)

    # Reihenfolge der Vorlage übernehmen, damit die Ausgabe der Datei ähnelt.
    reihenfolge = [(a, s) for a, s, _w, _k in vorlage if (a, s) in gelesen]
    reihenfolge += [paar for paar in sorted(gelesen) if paar not in reihenfolge]

    aus_datei = aus_vorgabe = 0
    letzter = None
    # Die Herkunft steht VOR dem Wert: Emoji sind im Terminal doppelt breit,
    # eine Spalte hinter dem Wert wuerde deshalb verrutschen.
    breite = max((len(s) for _a, s in reihenfolge), default=20)
    for abschnitt, schluessel in reihenfolge:
        if abschnitt != letzter:
            print(f"[{abschnitt}]")
            letzter = abschnitt
        if parser.has_option(abschnitt, schluessel):
            wert = parser.get(abschnitt, schluessel)
            herkunft = "(Datei)"
            aus_datei += 1
        else:
            vorgabe = gelesen[(abschnitt, schluessel)]["vorgabe"]
            wert = "" if vorgabe is OHNE_VORGABE else vorgabe
            herkunft = "(Vorgabe)" if vorgabe is not OHNE_VORGABE else "(Vorgabe?)"
            aus_vorgabe += 1
        anzeige = _kurz(wert, 60) if str(wert) else "– leer –"
        print(f"  {schluessel:<{breite}}  {herkunft:<11}  {anzeige}")

    for abschnitt in sorted(FREIE_ABSCHNITTE):
        if parser.has_section(abschnitt):
            print(f"[{abschnitt}]")
            print(f"  – freie Tabelle mit {len(parser[abschnitt])} Einträgen –")

    unbekannt = [(a, s) for a in parser.sections() if a not in FREIE_ABSCHNITTE
                 for s in parser[a] if (a, s) not in gelesen]
    if unbekannt:
        print("\nSteht in der Datei, wird aber von keinem Programm gelesen:")
        for abschnitt, schluessel in unbekannt:
            print(f"  ✗ [{abschnitt}] {schluessel}")

    print(f"\n{aus_datei} Wert(e) aus der Datei, {aus_vorgabe} aus den Vorgaben "
          f"der Programme.")
    if aus_vorgabe:
        print("Achtung: Eine auskommentierte Zeile zählt als fehlend - dann gilt\n"
              "die Vorgabe, die Einstellung ist also NICHT abgeschaltet. Wer etwas\n"
              "abschalten will, lässt den Wert leer (z.B. \"source_label =\").")
        print("Die Vorgaben lassen sich mit ihren Erklärungen nachtragen:\n"
              "    venv/bin/python skyrelay-setup.py --nachziehen")
    return 0


# ----------------------------------------------------------------- Nachziehen
def _abschnitts_grenzen(zeilen):
    """{abschnitt: (start, ende)} - ende ist der Index hinter der letzten Zeile."""
    grenzen = {}
    aktuell = None
    start = 0
    for i, zeile in enumerate(zeilen):
        if zeile.strip().startswith("[") and zeile.strip().endswith("]"):
            if aktuell is not None:
                grenzen[aktuell] = (start, i)
            aktuell = zeile.strip().strip("[]")
            start = i
    if aktuell is not None:
        grenzen[aktuell] = (start, len(zeilen))
    return grenzen


def nachziehen(zeilen, basis_dir):
    """Ergänzt in zeilen alle Schlüssel, die die Vorlage kennt und die eigene
    Konfiguration nicht - jeweils mit den erklärenden Kommentaren darüber.

    Vorhandene Werte, Reihenfolge und Kommentare bleiben unangetastet; es wird
    ausschließlich ergänzt. Liefert die Liste der ergänzten (abschnitt,
    schluessel, wert)."""
    vorhanden = _schluesselpaare("".join(zeilen))
    fehlend = [(a, s, w, k) for a, s, w, k in lade_vorlage(basis_dir)
               if (a, s) not in vorhanden and a not in FREIE_ABSCHNITTE]
    if not fehlend:
        return []

    # Nach Abschnitt bündeln, Reihenfolge der Vorlage beibehalten.
    je_abschnitt = {}
    for abschnitt, schluessel, wert, kommentar in fehlend:
        je_abschnitt.setdefault(abschnitt, []).append((schluessel, wert, kommentar))

    grenzen = _abschnitts_grenzen(zeilen)
    # Von hinten einfügen, damit die zuvor ermittelten Grenzen gültig bleiben.
    for abschnitt in sorted(je_abschnitt, key=lambda a: grenzen.get(a, (len(zeilen),))[0],
                            reverse=True):
        # Kommentare, die im Abschnitt bereits stehen, nicht ein zweites Mal
        # einfügen: Wer nur die Wertzeile gelöscht hat, bekäme sie sonst
        # doppelt erklärt.
        start_alt, ende_alt = grenzen.get(abschnitt, (0, 0))
        bekannt = {z.strip() for z in zeilen[start_alt:ende_alt]
                   if z.strip().startswith("#")}
        neu = []
        for schluessel, wert, kommentar in je_abschnitt[abschnitt]:
            neu.extend(z for z in kommentar if z.strip() not in bekannt)
            neu.append(f"{schluessel} = {wert}\n")

        if abschnitt not in grenzen:
            if zeilen and zeilen[-1].strip():
                zeilen.append("\n")
            zeilen.append(f"[{abschnitt}]\n")
            zeilen.extend(neu)
            continue

        start, ende = grenzen[abschnitt]
        einfuegen = ende
        while einfuegen > start + 1 and not zeilen[einfuegen - 1].strip():
            einfuegen -= 1
        zeilen[einfuegen:einfuegen] = neu

    return [(a, s, w) for a, s, w, _k in fehlend]


def nachziehen_datei(basis_dir, bestaetigen=None):
    """Zieht die Konfigurationsdatei nach und legt vorher eine Sicherung an.

    bestaetigen bekommt die Liste der Ergänzungen und entscheidet, ob
    geschrieben wird. Ohne Rückfrage-Funktion wird geschrieben."""
    pfad = konfig_pfad(basis_dir)
    try:
        with open(pfad, encoding="utf-8") as datei:
            zeilen = datei.readlines()
    except OSError as fehler:
        return None, f"Konfiguration nicht lesbar: {fehler}"

    probe = list(zeilen)
    ergaenzt = nachziehen(probe, basis_dir)
    if not ergaenzt:
        return [], None
    if bestaetigen is not None and not bestaetigen(ergaenzt):
        return None, "abgebrochen"

    try:
        with open(pfad + ".bak", "w", encoding="utf-8") as sicherung:
            sicherung.writelines(zeilen)
        with open(pfad, "w", encoding="utf-8") as datei:
            datei.writelines(probe)
    except OSError as fehler:
        return None, f"Schreiben fehlgeschlagen: {fehler}"
    return ergaenzt, None
