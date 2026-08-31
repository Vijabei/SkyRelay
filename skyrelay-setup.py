"""
SkyRelay - Einrichtungsassistent

Fragt die nötigen Angaben ab und schreibt daraus eine fertige "skyrelay.conf".
Den Verein sucht er dabei direkt bei OpenLigaDB und füllt die Kürzeltabelle
für die Hashtags automatisch vor - das ist die mühsamste Handarbeit.

Aufruf:
    venv/bin/python skyrelay-setup.py

Ein zweiter Aufruf lässt sich auch zum Ändern nutzen: Vorhandene Werte werden
als Vorgabe angeboten, [Enter] übernimmt sie. Passwörter landen NIE in der
Konfiguration - sie gehören in die Umgebungsvariable BLUESKY_APP_PASSWORD.
"""

import os
import re
import sys
import unicodedata

import skyrelay_tui as tui
import skyrelay_konfig as konfig

try:
    import requests
except ImportError:
    print("Fehler: Das Paket 'requests' fehlt. Zuerst ./install.sh ausführen.", file=sys.stderr)
    sys.exit(1)

# Auf Konsolen ohne UTF-8 (etwa der Windows-Eingabeaufforderung) soll der
# Assistent nicht am ersten Sonderzeichen abbrechen.
for _strom in (sys.stdout, sys.stderr):
    try:
        _strom.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VORLAGE = os.path.join(BASE_DIR, "skyrelay.conf.example")
ZIEL = os.environ.get("SKYRELAY_CONFIG") or os.path.join(BASE_DIR, "skyrelay.conf")

# Häufig gebrauchte Ligen zuerst - alles Weitere über "andere Liga suchen".
# (leagueShortcut, Saison-Startjahr, Beschriftung)
EMPFOHLENE_LIGEN = [
    ("bl1", "2026", "Fußball · 1. Bundesliga"),
    ("bl2", "2026", "Fußball · 2. Bundesliga"),
    ("bl3", "2026", "Fußball · 3. Liga"),
    ("dfb", "2026", "Fußball · DFB-Pokal"),
    ("fbl1", "2025", "Frauenfußball · 1. Bundesliga"),
    ("del", "2026", "Eishockey · DEL"),
    ("del2", "2026", "Eishockey · DEL2"),
]

# Gebräuchliche Kürzel je OpenLigaDB-Team-Nummer, wie sie in Ergebnisdiensten
# und Fernsehgrafiken verwendet werden. Es gibt dafür keinen offiziellen
# Standard - und OpenLigaDB liefert selbst keine Kürzel (nur den Kurznamen,
# also "Bielefeld" statt "DSC"). Deshalb diese Tabelle.
# Für Mannschaften ohne Eintrag schlägt der Assistent eine Ableitung aus dem
# Namen vor, die im Dialog erkennbar als Vorschlag markiert ist ("?").
#
# Sortiert nach Vereinsnamen, nicht nach Liga: So bleiben Einträge bei Auf-
# und Abstieg an derselben Stelle stehen.
# Ergänzen: Team-Nummer über getavailableteams/<liga>/<saison> ermitteln.
# Stand: Saison 2026/27.
BEKANNTE_KUERZEL = {
    199: "FCH",       # 1. FC Heidenheim 1846
    76: "FCK",        # 1. FC Kaiserslautern
    65: "KOE",        # 1. FC Köln
    78: "FCM",        # 1. FC Magdeburg
    79: "FCN",        # 1. FC Nürnberg
    417: "FCS",       # 1. FC Saarbrücken
    80: "FCU",        # 1. FC Union Berlin
    81: "M05",        # 1. FSV Mainz 05
    23: "AAC",        # Alemannia Aachen
    6: "B04",         # Bayer 04 Leverkusen
    7: "BVB",         # Borussia Dortmund
    1714: "BVB2",     # Borussia Dortmund II
    87: "BMG",        # Borussia Mönchengladbach
    83: "DSC",        # DSC Arminia Bielefeld
    177: "SGD",       # Dynamo Dresden
    74: "EBS",        # Eintracht Braunschweig
    91: "SGE",        # Eintracht Frankfurt
    93: "FCE",        # Energie Cottbus
    95: "FCA",        # FC Augsburg
    40: "FCB",        # FC Bayern München
    171: "FCI",       # FC Ingolstadt 04
    9: "S04",         # FC Schalke 04
    98: "STP",        # FC St. Pauli
    185: "F95",       # Fortuna Düsseldorf
    100: "HSV",       # Hamburger SV
    55: "H96",        # Hannover 96
    102: "HRO",       # Hansa Rostock
    54: "BSC",        # Hertha BSC
    104: "KIE",       # Holstein Kiel
    181: "REG",       # Jahn Regensburg
    105: "KSC",       # Karlsruher SC
    107: "MSV",       # MSV Duisburg
    188: "PRM",       # Preußen Münster
    1635: "RBL",      # RB Leipzig
    109: "RWE",       # Rot-Weiss Essen
    112: "SCF",       # SC Freiburg
    31: "SCP",        # SC Paderborn 07
    114: "SCV",       # SC Verl
    115: "SGF",       # SpVgg Greuther Fürth
    116: "UHA",       # SpVgg Unterhaching
    564: "ULM",       # SSV Ulm 1846
    198: "ELV",       # SV 07 Elversberg
    118: "D98",       # SV Darmstadt 98
    119: "SVS",       # SV Sandhausen
    553: "SVW",       # SV Waldhof Mannheim
    174: "SVWW",      # SV Wehen Wiesbaden
    134: "SVW",       # SV Werder Bremen
    175: "TSG",       # TSG Hoffenheim
    2396: "TSG2",     # TSG 1899 Hoffenheim II
    16: "VFB",        # VfB Stuttgart
    184: "VFB2",      # VfB Stuttgart II
    129: "BOC",       # VfL Bochum
    36: "OSN",        # VfL Osnabrück
    131: "WOB",       # VfL Wolfsburg
    2199: "VIK",      # Viktoria Köln
    398: "FWK",       # Würzburger Kickers
}
# Hinweis: SVW tragen sowohl Werder Bremen (1. Liga) als auch Waldhof Mannheim
# (3. Liga). Innerhalb einer Liga stört das nicht; treffen beide im Pokal
# aufeinander, entstünde "#SVWSVW" - dann eines der beiden hier anpassen.


# --------------------------------------------------------------- Darstellung
def titel(text):
    print(f"\n\033[1m{text}\033[0m")
    print("─" * len(text))


def programm_banner(teil, name, fluss, zusatz=""):
    """Deutlich sichtbare Trennung zwischen den beiden Programmen - sonst ist beim
    Einrichten nicht klar, für welchen Bot eine Angabe gerade gilt."""
    breite = 74
    print("\n\033[1m" + "═" * breite)
    print(f"  {teil}{name}")
    print(f"  {fluss}" + (f"   ({zusatz})" if zusatz else ""))
    print("═" * breite + "\033[0m")


def hinweis(text):
    print(f"  \033[2m{text}\033[0m")


def frage(text, vorgabe="", pflicht=False, pruefung=None):
    """Fragt einen Wert ab. [Enter] übernimmt die Vorgabe."""
    while True:
        anzeige = f" [{vorgabe}]" if vorgabe else ""
        try:
            eingabe = input(f"  {text}{anzeige}: ").strip()
        except EOFError:
            raise KeyboardInterrupt
        wert = eingabe or vorgabe
        if pflicht and not wert:
            print("    Bitte etwas eingeben.")
            continue
        if wert and pruefung:
            fehler = pruefung(wert)
            if fehler:
                print(f"    {fehler}")
                continue
        return wert


def ja_nein(text, vorgabe=True):
    standard = "J/n" if vorgabe else "j/N"
    while True:
        try:
            eingabe = input(f"  {text} [{standard}]: ").strip().lower()
        except EOFError:
            raise KeyboardInterrupt
        if not eingabe:
            return vorgabe
        if eingabe in ("j", "ja", "y", "yes"):
            return True
        if eingabe in ("n", "nein", "no"):
            return False


def auswahl(eintraege, text, vorgabe_index=None):
    """Zeigt eine nummerierte Liste und liefert den gewählten Eintrag."""
    for nummer, (_, beschriftung) in enumerate(eintraege, 1):
        markierung = " ←" if vorgabe_index == nummer - 1 else ""
        print(f"    {nummer:2}) {beschriftung}{markierung}")
    vorgabe = str(vorgabe_index + 1) if vorgabe_index is not None else ""
    while True:
        eingabe = frage(text, vorgabe, pflicht=True)
        if eingabe.isdigit() and 1 <= int(eingabe) <= len(eintraege):
            return eintraege[int(eingabe) - 1][0]
        print(f"    Bitte eine Zahl zwischen 1 und {len(eintraege)} eingeben.")


# ------------------------------------------------------------ Konfiguration
def lies_wert(zeilen, abschnitt, schluessel):
    """Liest einen Wert aus den Zeilen einer Konfigurationsdatei."""
    aktuell = None
    for zeile in zeilen:
        if zeile.startswith("["):
            aktuell = zeile.strip().strip("[]")
        elif aktuell == abschnitt and re.match(rf"\s*{re.escape(schluessel)}\s*=", zeile):
            return zeile.split("=", 1)[1].strip()
    return ""


def setze_wert(zeilen, abschnitt, schluessel, wert):
    """Setzt einen Wert und lässt alle Kommentare unangetastet.

    Fehlt der Schlüssel, wird er am Ende seines Abschnitts ergänzt; fehlt auch
    der Abschnitt, entsteht er am Dateiende. Früher geschah in diesen Fällen
    schlicht nichts: Wer eine Konfiguration aus einer älteren Fassung
    weiterbenutzte, gab im Assistenten Werte ein, die stillschweigend verfielen -
    das Menü meldete "gespeichert", geändert hatte sich nichts."""
    aktuell = None
    abschnitt_start = None
    abschnitt_ende = None
    for i, zeile in enumerate(zeilen):
        if zeile.startswith("["):
            if aktuell == abschnitt and abschnitt_ende is None:
                abschnitt_ende = i
            aktuell = zeile.strip().strip("[]")
            if aktuell == abschnitt:
                abschnitt_start = i
        elif aktuell == abschnitt and re.match(rf"\s*{re.escape(schluessel)}\s*=", zeile):
            zeilen[i] = f"{schluessel} = {wert}\n"
            return True

    if abschnitt_start is None:
        if zeilen and zeilen[-1].strip():
            zeilen.append("\n")
        zeilen.extend([f"[{abschnitt}]\n", f"{schluessel} = {wert}\n"])
        return True

    # Hinter die letzte inhaltliche Zeile des Abschnitts, noch vor die
    # Leerzeilen zum nächsten Abschnitt.
    einfuegen = abschnitt_ende if abschnitt_ende is not None else len(zeilen)
    while einfuegen > abschnitt_start + 1 and not zeilen[einfuegen - 1].strip():
        einfuegen -= 1
    zeilen.insert(einfuegen, f"{schluessel} = {wert}\n")
    return True


def setze_team_codes(zeilen, codes):
    """Ersetzt den Inhalt von [team_codes] durch die neue Tabelle."""
    start = ende = None
    for i, zeile in enumerate(zeilen):
        if zeile.strip() == "[team_codes]":
            start = i
        elif start is not None and zeile.startswith("[") and i > start:
            ende = i
            break
    if start is None:
        return False
    ende = ende if ende is not None else len(zeilen)

    kopf = [z for z in zeilen[start:ende] if z.startswith("#") or z.strip() == "[team_codes]"]
    neu = kopf + [f"{team_id} = {code}\n" for team_id, code in sorted(codes.items())] + ["\n"]
    zeilen[start:ende] = neu
    return True


# ------------------------------------------------------------------ Fachlich
def kuerzel_vorschlag(team):
    """Liefert das gebräuchliche Kürzel, sonst eine Ableitung aus dem Namen.
    Der zweite Rückgabewert sagt, ob es sich um ein belegtes Kürzel handelt."""
    bekannt = BEKANNTE_KUERZEL.get(team.get("teamId"))
    if bekannt:
        return bekannt, True

    name = (team.get("shortName") or team.get("teamName") or "").strip()
    # Manche Ligen (z.B. die DEL) führen im Kurznamen bereits das offizielle
    # Kürzel - dann unverändert übernehmen statt es zu beschneiden.
    if 2 <= len(name) <= 5 and name.isupper() and name.isalpha():
        return name, True

    normal = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    buchstaben = re.sub(r"[^A-Za-z]", "", normal)
    return (buchstaben[:3] or "XXX").upper(), False


def hole_teams(liga, saison):
    antwort = requests.get(
        f"https://api.openligadb.de/getavailableteams/{liga}/{saison}", timeout=20
    )
    antwort.raise_for_status()
    return antwort.json()


def hole_aktuelle_ligen():
    """Alle Ligen mit laufender oder kommender Saison, nach Sportart gruppiert."""
    antwort = requests.get("https://api.openligadb.de/getavailableleagues", timeout=30)
    antwort.raise_for_status()
    aktuell = [l for l in antwort.json() if str(l.get("leagueSeason", "")) in ("2025", "2026")]
    return sorted(
        aktuell,
        key=lambda l: ((l.get("sport") or {}).get("sportName", ""), l.get("leagueName", "")),
    )


def pruefe_kanal_link(wert):
    if "whatsapp.com/channel/" not in wert:
        return "Das sieht nicht nach einem Kanal-Link aus (erwartet: https://whatsapp.com/channel/…)."
    if "HIER-DEN" in wert:
        return "Das ist noch der Platzhalter."
    return None


def pruefe_handle(wert):
    if wert.startswith("@"):
        return "Bitte ohne führendes @ angeben."
    if "." not in wert:
        return "Erwartet wird ein vollständiges Handle, z.B. mein-bot.bsky.social"
    return None


def teste_bluesky(handle, passwort_variable="BLUESKY_APP_PASSWORD"):
    """Prüft die Anmeldung, ohne etwas zu veröffentlichen."""
    passwort = os.environ.get(passwort_variable)
    if not passwort:
        hinweis(f"{passwort_variable} ist nicht gesetzt - Anmeldung wird nicht geprüft.")
        hinweis(f'Später setzen mit:  export {passwort_variable}="xxxx-xxxx-xxxx-xxxx"')
        return
    if not ja_nein(f"Anmeldung bei Bluesky als {handle} jetzt prüfen?", True):
        return
    try:
        from atproto import Client
        Client().login(handle, passwort)
        print(f"  ✓ Anmeldung erfolgreich: {handle}")
    except ImportError:
        hinweis("Paket 'atproto' fehlt - Prüfung übersprungen (./install.sh ausführen).")
    except Exception as fehler:
        print(f"  ✗ Anmeldung fehlgeschlagen: {fehler}")
        hinweis("Handle und App-Passwort prüfen. Die Einrichtung läuft trotzdem weiter.")


# --------------------------------------------------------------------- Ablauf
def main():
    print("\n\033[1mSkyRelay - Einrichtung\033[0m")
    print("Mit [Enter] wird jeweils der Wert in eckigen Klammern übernommen.")
    print("Abbruch jederzeit mit Strg+C - dann wird nichts geschrieben.")

    if not os.path.exists(VORLAGE):
        print(f"\nFehler: Vorlage fehlt: {VORLAGE}", file=sys.stderr)
        sys.exit(1)

    with open(VORLAGE, encoding="utf-8") as datei:
        zeilen = datei.readlines()

    # Vorhandene Konfiguration als Vorgabe verwenden
    alt = []
    if os.path.exists(ZIEL):
        with open(ZIEL, encoding="utf-8") as datei:
            alt = datei.readlines()
        print(f"\nVorhandene Konfiguration gefunden: {os.path.basename(ZIEL)}")
        hinweis("Die bisherigen Werte stehen als Vorgabe bereit.")

    def bisher(abschnitt, schluessel):
        return lies_wert(alt, abschnitt, schluessel) if alt else ""

    # ------------------------------------------------------ Welche Programme?
    titel("Welche Programme möchtest du einrichten?")
    hinweis("SkyRelay besteht aus zwei getrennten Bots, die auch getrennte")
    hinweis("Bluesky-Konten benutzen können:")
    print("     · Spieltags-Ticker: WhatsApp-Kanal → Bluesky, nur an Spieltagen")
    print("     · Instagram-Feed:   Instagram → Bluesky, im Dauerbetrieb")
    was = auswahl(
        [("beide", "Beide"),
         ("matchday", "Nur den Spieltags-Ticker (WhatsApp-Kanal)"),
         ("feed", "Nur die Instagram-Spiegelung")],
        "Auswahl", 0,
    )
    macht_matchday = was in ("beide", "matchday")
    macht_feed = was in ("beide", "feed")
    teile = "1 von 2: " if was == "beide" else ""
    ticker_handle = feed_handle = ""

    if macht_matchday:
        programm_banner(teile, "SPIELTAGS-TICKER",
                        "WhatsApp-Kanal  ──►  Bluesky", "läuft nur an Spieltagen")

    # ------------------------------------------------------------ Einsatzzweck
    zweck = "sport_plan"
    if macht_matchday:
        titel("Wofür wird der Ticker eingesetzt?")
        hinweis("Davon hängt ab, ob Spieltage automatisch erkannt werden können")
        hinweis("und wie die vorgeschlagenen Texte formuliert sind.")
        zweck = auswahl(
            [("sport_plan", "Sport mit Spielplan bei OpenLigaDB (Fußball, Eishockey …)"),
             ("sport_ohne", "Sport ohne Spielplan-Daten (z.B. Basketball, Handball-Liga)"),
             ("individuell", "anderer Zweck (Verein, Veranstaltung, Projekt …)")],
            "Auswahl", 0,
        )
    neutral = zweck == "individuell"
    # Wortwahl der Vorgaben an den Zweck anpassen
    W = {
        "ereignis": "Ereignis" if neutral else "Spiel",
        "prefix": "📡 [Inoffizieller Bot]" if neutral else "⚽ [Inoffizieller Bot]",
        "quelle": "WhatsApp-Kanal" if neutral else "WhatsApp-Kanal des Vereins",
        "an": ("🟢 Bot ist an {hashtag}" if neutral
               else "🟢 Bot ist an - {info} {hashtag} ⚫⚪🔵"),
        "aus": ("🔴 Bot ist aus" if neutral
                else "🔴 Bot ist aus - nächstes Spiel {hashtag} ⚫⚪🔵"),
        "aus_leer": "🔴 Bot ist aus" if neutral else "🔴 Bot ist aus ⚫⚪🔵",
        "fallback": "aktiv" if neutral else "Testspiel",
    }

    # ------------------------------------------------------------ Matchday
    if macht_matchday:
        titel("Bluesky-Konto FÜR DEN TICKER")
        hinweis("Auf dieses Konto werden die Beiträge aus dem WhatsApp-Kanal")
        hinweis("veröffentlicht - ohne führendes @.")
        ticker_handle = frage("Handle", bisher("bluesky", "handle") or "mein-ticker.bsky.social",
                              pflicht=True, pruefung=pruefe_handle)
        setze_wert(zeilen, "bluesky", "handle", ticker_handle)

        titel("WhatsApp-Kanal (Quelle des Tickers)")
        hinweis("Im Handy: Kanal öffnen → Kanalnamen antippen → Teilen → Link kopieren.")
        link = frage("Einladungslink", bisher("source", "channel_invite_link"),
                     pflicht=True, pruefung=pruefe_kanal_link)
        setze_wert(zeilen, "source", "channel_invite_link", link)

        liga = saison = None
        wahl = "ohne"
        if zweck == "sport_plan":
            titel("Liga und Verein")
            hinweis("Daraus erkennt der Ticker, an welchen Tagen er überhaupt laufen muss,")
            hinweis("und bildet den Spiel-Hashtag. Grundlage ist OpenLigaDB.")
            wahl = auswahl(
                [(k, b) for k, _, b in EMPFOHLENE_LIGEN]
                + [("suchen", "andere Liga aus OpenLigaDB wählen …"),
                   ("ohne", "doch kein Spielplan – Ticker läuft an jedem Starttag")],
                "Liga", 1,
            )

        if wahl == "ohne":
            titel("Ohne Spielplan")
            hinweis("Es findet keine automatische Prüfung statt, ob heute etwas ansteht:")
            hinweis("Der Ticker läuft an jedem Tag, an dem er gestartet wird.")
            hinweis(f"Ein wechselnder {W['ereignis']}-Hashtag lässt sich beim Start über")
            hinweis("SKYRELAY_HASHTAG mitgeben. Den cron-Eintrag also nur für die Tage")
            hinweis("einrichten, an denen etwas läuft - oder von Hand starten.")
            setze_wert(zeilen, "team", "openligadb_filter", "")
            setze_wert(zeilen, "team", "openligadb_team_id", "0")
        elif wahl == "suchen":
            try:
                ligen = hole_aktuelle_ligen()
            except Exception as fehler:
                print(f"  ✗ Abruf fehlgeschlagen: {fehler}")
                ligen = []
            if ligen:
                hinweis(f"{len(ligen)} Ligen mit laufender Saison:")
                gewaehlte = auswahl(
                    [(l, f'{(l.get("sport") or {}).get("sportName", "?")} · '
                         f'{l["leagueName"]} ({l["leagueShortcut"]}/{l["leagueSeason"]})')
                     for l in ligen],
                    "Liga",
                )
                liga, saison = gewaehlte["leagueShortcut"], str(gewaehlte["leagueSeason"])
            else:
                liga = frage("Ligakürzel", "bl2", pflicht=True)
                saison = frage("Saison (Startjahr)", "2026", pflicht=True)
        else:
            liga = wahl
            saison = next(s for k, s, _ in EMPFOHLENE_LIGEN if k == wahl)
            saison = frage("Saison (Startjahr)", saison, pflicht=True)

        teams = []
        if liga:
            try:
                teams = hole_teams(liga, saison)
            except Exception as fehler:
                print(f"  ✗ Abruf fehlgeschlagen: {fehler}")

        if teams:
            print(f"\n  {len(teams)} Mannschaften in {liga}/{saison}:")
            sortiert = sorted(teams, key=lambda t: t.get("shortName") or "")
            alt_id = bisher("team", "openligadb_team_id")
            vorgabe = next((i for i, t in enumerate(sortiert) if str(t["teamId"]) == alt_id), None)
            gewaehlt = auswahl(
                [(t, f'{t.get("shortName") or t["teamName"]}  ({t["teamName"]})') for t in sortiert],
                "Dein Verein", vorgabe,
            )
            setze_wert(zeilen, "team", "openligadb_team_id", gewaehlt["teamId"])
            such = (gewaehlt.get("shortName") or gewaehlt["teamName"]).split()[-1].lower()
            such = frage("Suchbegriff für OpenLigaDB", bisher("team", "openligadb_filter") or such,
                         pflicht=True)
            setze_wert(zeilen, "team", "openligadb_filter", such)

            # ------------------------------------------------ Kürzeltabelle
            titel("Kürzel für die Hashtags")
            hinweis("Aus Heim- und Auswärtskürzel entsteht der Spiel-Hashtag, z.B. #KSCDSC.")
            codes, belegt = {}, {}
            for team in teams:
                codes[team["teamId"]], belegt[team["teamId"]] = kuerzel_vorschlag(team)
            # Bereits gepflegte Kürzel haben immer Vorrang - und bleiben auch dann
            # erhalten, wenn die Mannschaft nicht in der gewählten Liga spielt
            # (Ligawechsel, Pokalgegner aus anderen Ligen).
            for zeile in alt:
                if re.match(r"^\d+\s*=", zeile):
                    team_id, code = zeile.split("=", 1)
                    codes[int(team_id.strip())] = code.strip()
                    belegt[int(team_id.strip())] = True

            anzahl_abgeleitet = sum(1 for t in teams if not belegt[t["teamId"]])
            if anzahl_abgeleitet:
                hinweis(f"{len(teams) - anzahl_abgeleitet} Kürzel sind hinterlegt, "
                        f"{anzahl_abgeleitet} aus dem Namen abgeleitet (mit ? markiert).")
                hinweis("Abgeleitete entsprechen oft nicht dem üblichen Kürzel - bitte prüfen.")
            else:
                hinweis("Für alle Mannschaften dieser Liga sind Kürzel hinterlegt.")

            eigenes = codes.get(gewaehlt["teamId"], "XXX")
            print(f"\n  Kürzel deines Vereins ({gewaehlt['teamName']}): {eigenes}")
            hinweis("Es steht in jedem Spiel-Hashtag - bitte genau prüfen.")
            codes[gewaehlt["teamId"]] = frage("Kürzel", eigenes, pflicht=True).upper()

            print("\n  Übrige Mannschaften (? = abgeleitet, ungeprüft):")
            for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                if team["teamId"] != gewaehlt["teamId"]:
                    marke = " " if belegt[team["teamId"]] else "?"
                    print(f"   {marke} {codes[team['teamId']]:5} {team['teamName']}")
            if ja_nein("\n  Diese Kürzel einzeln anpassen?", bool(anzahl_abgeleitet)):
                for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                    if team["teamId"] == gewaehlt["teamId"]:
                        continue
                    codes[team["teamId"]] = frage(
                        team["teamName"], codes[team["teamId"]], pflicht=True).upper()
            else:
                hinweis("Später jederzeit im Abschnitt [team_codes] änderbar.")
            setze_team_codes(zeilen, codes)

        titel("Beiträge des Tickers")
        hinweis("Es gibt zwei Sorten Hashtags:")
        hinweis(f"  · Dauer-Hashtag - steht unter JEDEM Beitrag (z.B. der Vereinsname)")
        hinweis(f"  · {W['ereignis']}-Hashtag - wechselt je Termin"
                + (" und wird aus dem Spielplan gebildet" if wahl != "ohne"
                   else ", kommt aus SKYRELAY_HASHTAG"))
        marke = frage("Dauer-Hashtag (ohne #, leer = keiner)",
                      bisher("post", "standing_hashtag"))
        setze_wert(zeilen, "post", "standing_hashtag", marke)
        setze_wert(zeilen, "post", "prefix", bisher("post", "prefix") or W["prefix"])
        beschriftung = frage("Beschriftung des Quell-Links",
                             bisher("post", "source_label") or W["quelle"])
        setze_wert(zeilen, "post", "source_label", beschriftung)

        # ------------------------------------------------------ Profil & Zeit
        titel("Profil-Statuszeile des Ticker-Kontos")
        hinweis(f"Die erste Zeile der Biografie von @{ticker_handle} kann anzeigen,")
        hinweis("ob der Bot gerade läuft - beim Beenden wird sie zurückgestellt.")
        vorher_an = bisher("profile", "enabled")
        if ja_nein("Statuszeile verwenden?", vorher_an.lower() != "false" if vorher_an else True):
            setze_wert(zeilen, "profile", "enabled", "true")
            hinweis("Platzhalter: {hashtag}" + (", {info} (z.B. '1. Spieltag'), {date}, {time}"
                                                if wahl != "ohne" else ""))
            zeile_an = frage("Text während des Betriebs",
                             bisher("profile", "line_on") or W["an"], pflicht=True)
            zeile_aus = frage("Text nach dem Beenden",
                              bisher("profile", "line_off") or W["aus"], pflicht=True)
            setze_wert(zeilen, "profile", "line_on", zeile_an)
            setze_wert(zeilen, "profile", "line_off", zeile_aus)
            setze_wert(zeilen, "profile", "line_off_no_match",
                       bisher("profile", "line_off_no_match") or W["aus_leer"])
            hinweis("Erkannt wird eine vorhandene Statuszeile am Text 'Bot ist'.")
            hinweis("Wer andere Formulierungen nutzt, passt [profile] marker an.")
        else:
            setze_wert(zeilen, "profile", "enabled", "false")
        setze_wert(zeilen, "profile", "fallback_match_info",
                   bisher("profile", "fallback_match_info") or W["fallback"])

        titel("Zeitfenster")
        hinweis("Bis zu dieser Uhrzeit lauscht der Ticker, danach beendet er sich selbst.")
        setze_wert(zeilen, "schedule", "day_end",
                   frage("Betriebsende (HH:MM)", bisher("schedule", "day_end") or "23:59",
                         pflicht=True))

    # ---------------------------------------------------------------- Feed
    if macht_feed:
        programm_banner("2 von 2: " if was == "beide" else "", "INSTAGRAM-SPIEGELUNG",
                        "Instagram  ──►  Bluesky", "läuft im Dauerbetrieb")

        titel("Instagram-Profil (Quelle des Feeds)")
        profil = frage("Profil, das gespiegelt wird (ohne @)",
                       bisher("feed", "instagram_profile"), pflicht=True)
        setze_wert(zeilen, "feed", "instagram_profile", profil)

        hinweis("Zweitkonto für den Abruf - NICHT das gespiegelte Profil.")
        hinweis("Sitzung anlegen mit: venv/bin/instaloader -l <name>")
        zweitkonto = frage("Instagram-Zweitkonto", bisher("feed", "instagram_session_user"),
                           pflicht=True)
        setze_wert(zeilen, "feed", "instagram_session_user", zweitkonto)

        titel("Bluesky-Konto FÜR DEN FEED")
        eigenes_konto = bisher("feed", "bluesky_handle")
        if macht_matchday:
            hinweis(f"Der Ticker veröffentlicht auf @{ticker_handle}.")
            eigenes = ja_nein("Soll die Instagram-Spiegelung ein ANDERES Konto verwenden?",
                              bool(eigenes_konto))
        else:
            eigenes = True
        if eigenes:
            hinweis("Auf dieses Konto werden die Instagram-Beiträge veröffentlicht.")
            feed_handle = frage("Handle", eigenes_konto or bisher("bluesky", "handle")
                                or "mein-feed.bsky.social", pflicht=True, pruefung=pruefe_handle)
            if macht_matchday:
                setze_wert(zeilen, "feed", "bluesky_handle", feed_handle)
                hinweis("Dessen App-Passwort gehört in SKYRELAY_FEED_APP_PASSWORD,")
                hinweis("nicht in BLUESKY_APP_PASSWORD (das gilt für den Ticker).")
            else:
                # Ohne Ticker ist das allgemeine Konto zugleich das des Feeds.
                setze_wert(zeilen, "bluesky", "handle", feed_handle)
                setze_wert(zeilen, "feed", "bluesky_handle", "")
        else:
            feed_handle = ticker_handle
            setze_wert(zeilen, "feed", "bluesky_handle", "")

    # ------------------------------------------------------- Zusammenfassung
    titel("Zusammenfassung")
    hinweis("Bitte prüfen, ob Quellen und Konten richtig zugeordnet sind:")
    if macht_matchday:
        print(f"  Spieltags-Ticker:  WhatsApp-Kanal  ──►  @{ticker_handle}")
    if macht_feed:
        print(f"  Instagram-Feed:    @{profil}  ──►  @{feed_handle}")
    if macht_matchday and macht_feed and ticker_handle == feed_handle:
        hinweis("Beide veröffentlichen auf demselben Konto - das ist zulässig,")
        hinweis("führt aber dazu, dass Ticker und Feed sich vermischen.")
    if not ja_nein("\n  Stimmt das so?", True):
        print("\nAbgebrochen - es wurde nichts geändert. Starte den Assistenten erneut.")
        return

    # ------------------------------------------------------------- Schreiben
    titel("Speichern")
    print(f"  Ziel: {ZIEL}")
    if os.path.exists(ZIEL):
        if not ja_nein("Vorhandene Konfiguration überschreiben? (Sicherung wird angelegt)", True):
            print("\nAbgebrochen - es wurde nichts geändert.")
            return
        sicherung = ZIEL + ".bak"
        try:
            with open(sicherung, "w", encoding="utf-8") as datei:
                datei.writelines(alt)
            print(f"  ✓ Sicherung: {os.path.basename(sicherung)}")
        except Exception as fehler:
            print(f"  ⚠️ Sicherung fehlgeschlagen: {fehler}")

    with open(ZIEL, "w", encoding="utf-8") as datei:
        datei.writelines(zeilen)
    print(f"  ✓ Geschrieben: {os.path.basename(ZIEL)}")

    # Jedes Konto mit der Passwort-Variablen prüfen, die im Betrieb auch gilt.
    if macht_matchday:
        teste_bluesky(ticker_handle, "BLUESKY_APP_PASSWORD")
    if macht_feed:
        if not macht_matchday:
            teste_bluesky(feed_handle, "BLUESKY_APP_PASSWORD")
        elif feed_handle != ticker_handle:
            teste_bluesky(feed_handle, "SKYRELAY_FEED_APP_PASSWORD")

    print("\n\033[1mFertig.\033[0m Nächste Schritte:\n")
    if macht_matchday:
        print(f"  SPIELTAGS-TICKER (postet auf @{ticker_handle})")
        print("    Erste WhatsApp-Kopplung, interaktiv im Terminal:")
        print("      SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py")
    if macht_feed:
        print(f"\n  INSTAGRAM-FEED (postet auf @{feed_handle})")
        print("    Instagram-Sitzung einmalig anlegen:")
        print(f"      venv/bin/instaloader -l {zweitkonto}")
    print("\n  Danach den Dauerbetrieb per cron einrichten - siehe README.md.\n")


# ============================== Menüoberfläche ===============================
# Fühlt sich an wie raspi-config: ein Hauptmenü, aus dem gezielt einzelne
# Bereiche geändert werden können - statt fünfzehn Fragen am Stück.

def _zeige(zeilen, abschnitt, schluessel, ersatz="– nicht gesetzt –", kurz=40):
    wert = lies_wert(zeilen, abschnitt, schluessel)
    if not wert or wert.startswith("dein") or "HIER-DEN" in wert:
        return ersatz
    return wert if len(wert) <= kurz else wert[:kurz - 1] + "…"


def m_ticker(zeilen):
    """Alles zum Spieltags-Ticker."""
    while True:
        kanal = lies_wert(zeilen, "source", "channel_invite_link")
        kanal_text = "– nicht gesetzt –" if not kanal or "HIER-DEN" in kanal else "gesetzt"
        liga = lies_wert(zeilen, "team", "openligadb_filter") or "ohne Spielplan"
        wahl = tui.menue(
            "Spieltags-Ticker", "WhatsApp-Kanal → Bluesky, läuft nur an Spieltagen.",
            [("konto", f"Bluesky-Konto ......  {_zeige(zeilen, 'bluesky', 'handle')}"),
             ("kanal", f"WhatsApp-Kanal ....  {kanal_text}"),
             ("liga", f"Liga und Verein ...  {liga}")])
        if wahl is None:
            return
        if wahl == "konto":
            wert = tui.frage("Bluesky-Konto für den TICKER",
                             "Konto, auf dem die Kanalbeiträge veröffentlicht werden.\n"
                             "Ohne führendes @, z.B. mein-ticker.bsky.social",
                             lies_wert(zeilen, "bluesky", "handle"))
            if wert:
                fehler = pruefe_handle(wert)
                if fehler:
                    tui.meldung("Ungültig", fehler)
                else:
                    setze_wert(zeilen, "bluesky", "handle", wert)
        elif wahl == "kanal":
            wert = tui.frage("WhatsApp-Kanal",
                             "Einladungslink des Kanals.\n\n"
                             "Im Handy: Kanal öffnen → Kanalnamen antippen →\n"
                             "Teilen → Link kopieren.",
                             lies_wert(zeilen, "source", "channel_invite_link"))
            if wert:
                fehler = pruefe_kanal_link(wert)
                if fehler:
                    tui.meldung("Ungültig", fehler)
                else:
                    setze_wert(zeilen, "source", "channel_invite_link", wert)
        elif wahl == "liga":
            m_liga(zeilen)


def m_liga(zeilen):
    """Liga und Verein wählen - oder den Spielplan ganz abschalten."""
    eintraege = [(k, b) for k, _, b in EMPFOHLENE_LIGEN]
    eintraege += [("suchen", "andere Liga aus OpenLigaDB …"),
                  ("ohne", "kein Spielplan (läuft an jedem Starttag)")]
    wahl = tui.menue("Liga", "Grundlage für Spieltags-Erkennung und Hashtag.", eintraege)
    if wahl is None:
        return

    if wahl == "ohne":
        setze_wert(zeilen, "team", "openligadb_filter", "")
        setze_wert(zeilen, "team", "openligadb_team_id", "0")
        tui.meldung("Ohne Spielplan",
                    "Die Spieltags-Erkennung ist abgeschaltet.\n\n"
                    "Der Ticker läuft an jedem Tag, an dem er gestartet wird.\n"
                    "Einen wechselnden Hashtag gibst du beim Start über\n"
                    "SKYRELAY_HASHTAG mit. Den cron-Eintrag also nur für die\n"
                    "Tage einrichten, an denen etwas läuft.")
        return

    if wahl == "suchen":
        tui.fortschritt("Ligen werden von OpenLigaDB geladen …")
        try:
            ligen = hole_aktuelle_ligen()
        except Exception as fehler:
            tui.meldung("Abruf fehlgeschlagen", str(fehler))
            return
        gewaehlt = tui.liste_waehlen(
            "Liga wählen", f"{len(ligen)} Ligen mit laufender Saison:",
            [(f'{l["leagueShortcut"]}|{l["leagueSeason"]}',
              f'{(l.get("sport") or {}).get("sportName", "?")} · {l["leagueName"]}')
             for l in ligen])
        if gewaehlt is None:
            return
        liga, saison = gewaehlt.split("|")
    else:
        liga = wahl
        saison = next(s for k, s, _ in EMPFOHLENE_LIGEN if k == wahl)
        eingabe = tui.frage("Saison", "Startjahr der Saison:", saison)
        if eingabe is None:
            return
        saison = eingabe

    tui.fortschritt(f"Mannschaften aus {liga}/{saison} werden geladen …")
    try:
        teams = hole_teams(liga, saison)
    except Exception as fehler:
        tui.meldung("Abruf fehlgeschlagen", str(fehler))
        return
    if not teams:
        tui.meldung("Nichts gefunden", f"Zu {liga}/{saison} liefert OpenLigaDB keine Mannschaften.")
        return

    sortiert = sorted(teams, key=lambda t: t.get("shortName") or "")
    gewaehlt = tui.liste_waehlen(
        "Verein wählen", "Für welchen Verein läuft der Ticker?",
        [(t["teamId"], f'{t.get("shortName") or t["teamName"]}  ({t["teamName"]})')
         for t in sortiert],
        lies_wert(zeilen, "team", "openligadb_team_id"))
    if gewaehlt is None:
        return

    verein = next(t for t in teams if str(t["teamId"]) == str(gewaehlt))
    setze_wert(zeilen, "team", "openligadb_team_id", verein["teamId"])
    such = (verein.get("shortName") or verein["teamName"]).split()[-1].lower()
    eingabe = tui.frage("Suchbegriff",
                        "Begriff, mit dem OpenLigaDB nach dem Verein sucht:", such)
    setze_wert(zeilen, "team", "openligadb_filter", eingabe or such)

    # Kürzel der Liga ergänzen, vorhandene behalten
    vorhanden = {int(z.split("=")[0].strip()) for z in zeilen if re.match(r"^\d+\s*=", z)}
    neu = 0
    codes = {int(z.split("=")[0].strip()): z.split("=", 1)[1].strip()
             for z in zeilen if re.match(r"^\d+\s*=", z)}
    for team in teams:
        if team["teamId"] not in vorhanden:
            codes[team["teamId"]] = kuerzel_vorschlag(team)[0]
            neu += 1
    setze_team_codes(zeilen, codes)
    tui.meldung("Verein gesetzt",
                f'{verein["teamName"]}\n\n'
                f"Kürzeltabelle: {neu} Mannschaft(en) ergänzt, "
                f"{len(vorhanden)} bereits vorhanden.\n\n"
                f"Unter „Kürzel für Hashtags“ kannst du sie prüfen –\n"
                f"abgeleitete Vorschläge sind mit ? markiert.")


def m_kuerzel(zeilen):
    """Kürzeltabelle einzeln bearbeiten."""
    while True:
        codes = {int(z.split("=")[0].strip()): z.split("=", 1)[1].strip()
                 for z in zeilen if re.match(r"^\d+\s*=", z)}
        if not codes:
            tui.meldung("Noch keine Kürzel",
                        "Wähle zuerst unter „Spieltags-Ticker“ eine Liga und einen Verein.")
            return
        eigenes = lies_wert(zeilen, "team", "openligadb_team_id")
        eintraege = []
        for team_id, code in sorted(codes.items(), key=lambda x: x[1]):
            marke = " ←  eigener Verein" if str(team_id) == eigenes else ""
            eintraege.append((team_id, f"{code:6} (Nr. {team_id}){marke}"))
        wahl = tui.liste_waehlen("Kürzel für Hashtags",
                                 "Aus Heim + Auswärts entsteht der Hashtag, z.B. #KSCDSC.\n"
                                 "Eintrag wählen zum Ändern.", eintraege)
        if wahl is None:
            return
        neu = tui.frage("Kürzel ändern", f"Kürzel für Team-Nummer {wahl}:", codes[int(wahl)])
        if neu:
            codes[int(wahl)] = neu.strip().upper()
            setze_team_codes(zeilen, codes)


def m_feed(zeilen):
    """Alles zur Instagram-Spiegelung."""
    while True:
        konto = lies_wert(zeilen, "feed", "bluesky_handle") or \
            (lies_wert(zeilen, "bluesky", "handle") + "  (wie Ticker)")
        wahl = tui.menue(
            "Instagram-Feed", "Instagram → Bluesky, läuft im Dauerbetrieb.",
            [("profil", f"Instagram-Profil ..  {_zeige(zeilen, 'feed', 'instagram_profile')}"),
             ("konto2", f"Zweitkonto (Abruf)   {_zeige(zeilen, 'feed', 'instagram_session_user')}"),
             ("bsky", f"Bluesky-Konto .....  {konto[:40]}")])
        if wahl is None:
            return
        if wahl == "profil":
            wert = tui.frage("Instagram-Profil",
                             "Profil, das gespiegelt wird (ohne @):",
                             lies_wert(zeilen, "feed", "instagram_profile"))
            if wert:
                setze_wert(zeilen, "feed", "instagram_profile", wert.lstrip("@"))
        elif wahl == "konto2":
            wert = tui.frage("Instagram-Zweitkonto",
                             "Konto, mit dem abgerufen wird – NICHT das gespiegelte Profil.\n\n"
                             "Sitzung anlegen mit:  venv/bin/instaloader -l <name>",
                             lies_wert(zeilen, "feed", "instagram_session_user"))
            if wert:
                setze_wert(zeilen, "feed", "instagram_session_user", wert.lstrip("@"))
        elif wahl == "bsky":
            eigenes = lies_wert(zeilen, "feed", "bluesky_handle")
            if tui.ja_nein("Bluesky-Konto für den Feed",
                           "Soll die Instagram-Spiegelung ein ANDERES Konto\n"
                           f"verwenden als der Ticker ({lies_wert(zeilen, 'bluesky', 'handle')})?",
                           bool(eigenes)):
                wert = tui.frage("Konto für den Feed", "Handle ohne @:",
                                 eigenes or lies_wert(zeilen, "bluesky", "handle"))
                if wert:
                    setze_wert(zeilen, "feed", "bluesky_handle", wert)
                    tui.meldung("Getrennte Konten",
                                "Beide Bots brauchen dann eigene App-Passwörter:\n\n"
                                "  Ticker: BLUESKY_TICKER_APP_PASSWORD\n"
                                "  Feed:   BLUESKY_FEED_APP_PASSWORD")
            else:
                setze_wert(zeilen, "feed", "bluesky_handle", "")


def m_texte(zeilen):
    """Beitragstexte und Profil-Statuszeile."""
    while True:
        an = lies_wert(zeilen, "profile", "enabled")
        wahl = tui.menue(
            "Beiträge und Profil", "Wie die Beiträge aussehen und was in der Bio steht.",
            [("tag", f"Dauer-Hashtag .....  {_zeige(zeilen, 'post', 'standing_hashtag', '– keiner –')}"),
             ("kopf", f"Kopfzeile .........  {_zeige(zeilen, 'post', 'prefix', kurz=30)}"),
             ("quelle", f"Quelle (Ticker) ...  {_zeige(zeilen, 'post', 'source_label', kurz=30)}"),
             ("quelle_feed", f"Quelle (Feed) .....  {_zeige(zeilen, 'feed', 'source_label', kurz=30)}"),
             ("profil", f"Statuszeile .......  {'ein' if an != 'false' else 'aus'}")])
        if wahl is None:
            return
        if wahl == "tag":
            wert = tui.frage("Dauer-Hashtag",
                             "Steht unter JEDEM Beitrag, ohne # (leer = keiner):",
                             lies_wert(zeilen, "post", "standing_hashtag"))
            if wert is not None:
                setze_wert(zeilen, "post", "standing_hashtag", wert.lstrip("#"))
        elif wahl == "kopf":
            wert = tui.frage("Kopfzeile", "Erste Zeile jedes Hauptbeitrags:",
                             lies_wert(zeilen, "post", "prefix"))
            if wert:
                setze_wert(zeilen, "post", "prefix", wert)
        elif wahl == "quelle":
            wert = tui.frage("Quell-Beschriftung (Ticker)",
                             "Text des Links zum WhatsApp-Kanal:",
                             lies_wert(zeilen, "post", "source_label"))
            if wert:
                setze_wert(zeilen, "post", "source_label", wert)
        elif wahl == "quelle_feed":
            wert = tui.frage("Quell-Beschriftung (Feed)",
                             "Text des Links zum Instagram-Beitrag:",
                             lies_wert(zeilen, "feed", "source_label"))
            if wert:
                setze_wert(zeilen, "feed", "source_label", wert)
        elif wahl == "profil":
            if tui.ja_nein("Profil-Statuszeile",
                           "Soll die erste Zeile der Bluesky-Biografie anzeigen,\n"
                           "ob der Bot gerade läuft?", an != "false"):
                setze_wert(zeilen, "profile", "enabled", "true")
                for schluessel, beschriftung in (("line_on", "Text während des Betriebs"),
                                                 ("line_off", "Text nach dem Beenden")):
                    wert = tui.frage(beschriftung,
                                     "Platzhalter: {info}, {hashtag}, {date}, {time}",
                                     lies_wert(zeilen, "profile", schluessel))
                    if wert:
                        setze_wert(zeilen, "profile", schluessel, wert)
            else:
                setze_wert(zeilen, "profile", "enabled", "false")


def m_zeiten(zeilen):
    """Zeitfenster und Zeitzone."""
    wahl = tui.menue("Zeitfenster", "Wann der Ticker arbeitet.",
                     [("ende", f"Betriebsende ......  {_zeige(zeilen, 'schedule', 'day_end')}"),
                      ("zone", f"Zeitzone ..........  {_zeige(zeilen, 'team', 'timezone')}")])
    if wahl == "ende":
        wert = tui.frage("Betriebsende",
                         "Bis zu dieser Uhrzeit lauscht der Ticker (HH:MM),\n"
                         "danach beendet er sich selbst:",
                         lies_wert(zeilen, "schedule", "day_end"))
        if wert:
            setze_wert(zeilen, "schedule", "day_end", wert)
    elif wahl == "zone":
        wert = tui.frage("Zeitzone", "z.B. Europe/Berlin:",
                         lies_wert(zeilen, "team", "timezone"))
        if wert:
            setze_wert(zeilen, "team", "timezone", wert)


def m_pruefen(zeilen):
    """Anmeldung bei Bluesky prüfen."""
    ticker = lies_wert(zeilen, "bluesky", "handle")
    feed = lies_wert(zeilen, "feed", "bluesky_handle")
    berichte = []
    for handle, variable in ((ticker, "BLUESKY_TICKER_APP_PASSWORD"),
                             (feed, "BLUESKY_FEED_APP_PASSWORD")):
        if not handle:
            continue
        passwort = os.environ.get(variable) or os.environ.get("BLUESKY_APP_PASSWORD")
        if not passwort:
            berichte.append(f"@{handle}\n   übersprungen – {variable} ist nicht gesetzt")
            continue
        try:
            from atproto import Client
            Client().login(handle, passwort)
            berichte.append(f"@{handle}\n   Anmeldung erfolgreich")
        except Exception as fehler:
            berichte.append(f"@{handle}\n   FEHLER: {str(fehler)[:60]}")
    tui.meldung("Anmeldung geprüft",
                "\n\n".join(berichte) or "Es ist noch kein Konto eingetragen.")


def m_nachziehen(zeilen):
    """Fehlende Schlüssel aus der Vorlage ergänzen - mit ihren Erklärungen.

    Arbeitet auf dem Stand im Assistenten, nicht auf der Datei: Gespeichert wird
    wie sonst auch erst über den Menüpunkt zum Speichern."""
    probe = list(zeilen)
    ergaenzt = konfig.nachziehen(probe, BASE_DIR)
    if not ergaenzt:
        tui.meldung("Nichts nachzuziehen",
                    "Alle Schlüssel der Vorlage stehen bereits in der "
                    "Konfiguration.")
        return

    liste = "\n".join(f"  [{a}] {s} = {w}" for a, s, w in ergaenzt[:18])
    if len(ergaenzt) > 18:
        liste += f"\n  … und {len(ergaenzt) - 18} weitere"
    if not tui.ja_nein("Konfiguration nachziehen",
                       f"{len(ergaenzt)} Schlüssel fehlen. Sie werden mit den "
                       f"Erklärungen aus der Vorlage ergänzt; vorhandene Werte "
                       f"bleiben unverändert.\n\n{liste}\n\nErgänzen?", True):
        return
    zeilen[:] = probe
    tui.meldung("Nachgezogen",
                f"{len(ergaenzt)} Schlüssel ergänzt.\n\n"
                f"Noch nicht gespeichert - das erledigt der Menüpunkt "
                f"\"Speichern und beenden\".")


def m_konfig_pruefen(zeilen):
    """Konfiguration gegen die Quelltexte und die Vorlage prüfen.
    Geprüft wird der aktuelle Stand im Assistenten, auch ungespeichert."""
    befunde = konfig.sammle_konfig_befunde(BASE_DIR, "".join(zeilen))
    probleme = [t for schwere, t in befunde if schwere == "problem"]
    hinweise = [t for schwere, t in befunde if schwere == "hinweis"]
    if not befunde:
        tui.meldung("Konfiguration geprüft", "Keine Auffälligkeiten.")
        return
    teile = []
    if probleme:
        teile.append("Probleme:\n" + "\n".join(f"  ✗ {t}" for t in probleme))
    if hinweise:
        teile.append("Hinweise (Vorgaben greifen):\n"
                     + "\n".join(f"  ℹ {t}" for t in hinweise))
    tui.meldung("Konfiguration geprüft", "\n\n".join(teile))


def menue_modus():
    """Hauptmenü - Einstiegspunkt der Oberfläche."""
    if not os.path.exists(VORLAGE):
        print(f"Fehler: Vorlage fehlt: {VORLAGE}", file=sys.stderr)
        sys.exit(1)

    quelle = ZIEL if os.path.exists(ZIEL) else VORLAGE
    with open(quelle, encoding="utf-8") as datei:
        zeilen = datei.readlines()
    gespeichert = list(zeilen)

    while True:
        offen = []
        if _zeige(zeilen, "bluesky", "handle") == "– nicht gesetzt –":
            offen.append("Bluesky-Konto")
        kanal = lies_wert(zeilen, "source", "channel_invite_link")
        if not kanal or "HIER-DEN" in kanal:
            offen.append("WhatsApp-Kanal")
        hinweis_text = ("Noch offen: " + ", ".join(offen)) if offen else \
            "Alle Pflichtangaben sind gesetzt."
        stern = " *" if zeilen != gespeichert else ""

        wahl = tui.menue(
            "SkyRelay einrichten",
            f"{hinweis_text}\n\nDatei: {os.path.basename(ZIEL)}{stern}",
            [("1", "Spieltags-Ticker    WhatsApp-Kanal → Bluesky"),
             ("2", "Instagram-Feed      Instagram → Bluesky"),
             ("3", "Kürzel für Hashtags"),
             ("4", "Beiträge und Profil"),
             ("5", "Zeitfenster"),
             ("6", "Anmeldung bei Bluesky prüfen"),
             ("7", "Konfiguration prüfen"),
             ("8", "Konfiguration nachziehen"),
             ("9", "Speichern und beenden")],
            abbruch_text="Beenden")

        if wahl == "1":
            m_ticker(zeilen)
        elif wahl == "2":
            m_feed(zeilen)
        elif wahl == "3":
            m_kuerzel(zeilen)
        elif wahl == "4":
            m_texte(zeilen)
        elif wahl == "5":
            m_zeiten(zeilen)
        elif wahl == "6":
            m_pruefen(zeilen)
        elif wahl == "7":
            m_konfig_pruefen(zeilen)
        elif wahl == "8":
            m_nachziehen(zeilen)
        elif wahl == "9":
            if speichern(zeilen, gespeichert):
                return
        else:  # Beenden oder Escape
            if zeilen == gespeichert:
                return
            if tui.ja_nein("Ungespeicherte Änderungen",
                           "Es gibt Änderungen, die noch nicht gespeichert sind.\n\n"
                           "Jetzt speichern?", True):
                if speichern(zeilen, gespeichert):
                    return
            else:
                return


def speichern(zeilen, gespeichert):
    """Schreibt die Konfiguration; legt vorher eine Sicherung an."""
    ticker = lies_wert(zeilen, "bluesky", "handle")
    feed = lies_wert(zeilen, "feed", "bluesky_handle") or ticker
    profil = lies_wert(zeilen, "feed", "instagram_profile")
    uebersicht = [f"Ticker:  WhatsApp-Kanal  →  @{ticker}" if ticker else "Ticker:  – kein Konto –"]
    if profil:
        uebersicht.append(f"Feed:    @{profil}  →  @{feed}")
    if not tui.ja_nein("Speichern",
                       "\n".join(uebersicht) + f"\n\nNach {os.path.basename(ZIEL)} schreiben?"):
        return False

    if os.path.exists(ZIEL):
        try:
            with open(ZIEL + ".bak", "w", encoding="utf-8") as datei:
                datei.writelines(gespeichert)
        except Exception as fehler:
            tui.meldung("Sicherung fehlgeschlagen", str(fehler))
    with open(ZIEL, "w", encoding="utf-8") as datei:
        datei.writelines(zeilen)

    schritte = ["Gespeichert: " + os.path.basename(ZIEL), ""]
    if lies_wert(zeilen, "source", "channel_invite_link"):
        schritte += ["Erste WhatsApp-Kopplung (interaktiv):",
                     "  SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 \\",
                     "     venv/bin/python skyrelay-matchday.py", ""]
    if profil:
        schritte += ["Instagram-Sitzung anlegen (einmalig):",
                     f"  venv/bin/instaloader -l {lies_wert(zeilen, 'feed', 'instagram_session_user')}", ""]
    schritte.append("Danach cron einrichten – siehe README.md")
    tui.meldung("Fertig", "\n".join(schritte))
    return True


def nachziehen_ohne_menue():
    """--nachziehen: Fehlende Schlüssel direkt in der Datei ergänzen.

    Für alle, die den Assistenten gar nicht brauchen - etwa nach einem Update
    auf einem Server, der ohne whiptail auskommt."""
    def bestaetigen(ergaenzt):
        print(f"{len(ergaenzt)} Schlüssel fehlen und werden mit ihren "
              f"Erklärungen ergänzt:")
        for abschnitt, schluessel, wert in ergaenzt:
            print(f"  [{abschnitt}] {schluessel} = {wert}")
        antwort = input("\nErgänzen? [j/N] ").strip().lower()
        return antwort in ("j", "ja", "y", "yes")

    ergaenzt, fehler = konfig.nachziehen_datei(BASE_DIR, bestaetigen)
    if fehler == "abgebrochen":
        print("Abgebrochen - die Datei bleibt unverändert.")
        return 1
    if fehler:
        print(f"Fehler: {fehler}", file=sys.stderr)
        return 1
    if not ergaenzt:
        print("Alle Schlüssel der Vorlage stehen bereits in der Konfiguration.")
        return 0
    print(f"\n{len(ergaenzt)} Schlüssel ergänzt. "
          f"Sicherung: {os.path.basename(konfig.konfig_pfad(BASE_DIR))}.bak")
    return 0


if __name__ == "__main__":
    if "--nachziehen" in sys.argv:
        sys.exit(nachziehen_ohne_menue())
    try:
        # Menüoberfläche, wenn whiptail vorhanden ist und ein Terminal dranhängt.
        # SKYRELAY_SETUP_TEXT=1 erzwingt die zeilenweise Abfrage.
        if (tui.verfuegbar() and sys.stdin.isatty()
                and os.environ.get("SKYRELAY_SETUP_TEXT") != "1"):
            menue_modus()
        else:
            main()
    except KeyboardInterrupt:
        print("\n\nAbgebrochen - es wurde nichts geschrieben.\n")
        sys.exit(1)
