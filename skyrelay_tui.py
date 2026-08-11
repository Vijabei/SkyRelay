"""
SkyRelay - Menüoberfläche für den Einrichtungsassistenten

Dünne Hülle um `whiptail`, das auf Debian und Raspberry Pi OS vorinstalliert ist
und auch `raspi-config` antreibt. Dadurch fühlt sich die Einrichtung vertraut an,
läuft über SSH und erlaubt es, gezielt einzelne Punkte zu ändern - statt sich
durch fünfzehn Fragen am Stück zu arbeiten.

Fehlt whiptail, meldet `verfuegbar()` das, und der Assistent fällt auf die
zeilenweise Abfrage zurück.

whiptail gibt die Auswahl auf der Fehlerausgabe zurück und meldet über den
Rückgabewert, ob abgebrochen wurde (Abbrechen-Knopf oder Escape).
"""

import shutil
import subprocess

BREITE = 78
HOEHE = 20


def verfuegbar():
    """Ist eine Menüoberfläche nutzbar?"""
    return shutil.which("whiptail") is not None


def _ruf(argumente, eingabe=None):
    """Ruft whiptail auf. Liefert (abgebrochen, ausgabe)."""
    ergebnis = subprocess.run(
        ["whiptail", "--backtitle", "SkyRelay - Einrichtung"] + argumente,
        stderr=subprocess.PIPE, input=eingabe, text=True,
    )
    return ergebnis.returncode != 0, (ergebnis.stderr or "").strip()


def meldung(titel, text, hoehe=None):
    """Hinweis mit einem OK-Knopf."""
    zeilen = max(text.count("\n") + 7, 10)
    _ruf(["--title", titel, "--msgbox", text, str(hoehe or zeilen), str(BREITE)])


def frage(titel, text, vorgabe="", passwort=False):
    """Eingabefeld. Liefert None, wenn abgebrochen wurde."""
    art = "--passwordbox" if passwort else "--inputbox"
    zeilen = max(text.count("\n") + 9, 11)
    abbruch, wert = _ruf(["--title", titel, art, text, str(zeilen), str(BREITE), vorgabe])
    return None if abbruch else wert


def ja_nein(titel, text, vorgabe=True):
    """Ja/Nein-Abfrage. Liefert True/False."""
    zeilen = max(text.count("\n") + 8, 10)
    argumente = ["--title", titel]
    if not vorgabe:
        argumente.append("--defaultno")
    argumente += ["--yesno", text, str(zeilen), str(BREITE)]
    abbruch, _ = _ruf(argumente)
    return not abbruch


def menue(titel, text, eintraege, vorgabe=None, abbruch_text="Zurück"):
    """Auswahlmenü. `eintraege` ist eine Liste aus (Kennung, Beschriftung).
    Liefert die Kennung oder None bei Abbruch."""
    sichtbar = min(len(eintraege), HOEHE - 8)
    argumente = ["--title", titel, "--ok-button", "Auswählen",
                 "--cancel-button", abbruch_text]
    if vorgabe:
        argumente += ["--default-item", vorgabe]
    argumente += ["--menu", text, str(HOEHE), str(BREITE), str(sichtbar)]
    for kennung, beschriftung in eintraege:
        argumente += [str(kennung), beschriftung]
    abbruch, wert = _ruf(argumente)
    return None if abbruch else wert


def liste_waehlen(titel, text, eintraege, vorgabe=None):
    """Wie menue(), aber für lange Listen (Vereine, Ligen) - nutzt die volle
    Fensterhöhe und erlaubt das Springen per Anfangsbuchstabe."""
    sichtbar = min(len(eintraege), 14)
    argumente = ["--title", titel, "--ok-button", "Auswählen",
                 "--cancel-button", "Abbrechen"]
    if vorgabe:
        argumente += ["--default-item", str(vorgabe)]
    argumente += ["--menu", text, "22", str(BREITE), str(sichtbar)]
    for kennung, beschriftung in eintraege:
        argumente += [str(kennung), beschriftung[:BREITE - 20]]
    abbruch, wert = _ruf(argumente)
    return None if abbruch else wert


def fortschritt(text):
    """Kurzer Hinweis ohne Knopf - für Wartezeiten (z.B. Netzabfragen)."""
    subprocess.Popen(["whiptail", "--backtitle", "SkyRelay - Einrichtung",
                      "--infobox", text, "8", str(BREITE)])
