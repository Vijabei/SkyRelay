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
# und Hashtags verwendet werden. Bewusst nur Einträge, die belegt sind - für
# alle übrigen Mannschaften schlägt der Assistent eine Ableitung aus dem Namen
# vor, die erkennbar ein Vorschlag ist. Stand: Saison 2026/27.
# Ergänzungen sind willkommen: Team-Nummer über die OpenLigaDB-Adresse
# getavailableteams/<liga>/<saison> ermitteln.
BEKANNTE_KUERZEL = {
    # 1. Bundesliga
    6: "B04", 7: "BVB", 9: "S04", 16: "VFB", 31: "SCP", 40: "FCB", 65: "KOE",
    80: "FCU", 81: "M05", 87: "BMG", 91: "SGE", 95: "FCA", 100: "HSV",
    112: "SCF", 134: "SVW", 175: "TSG", 198: "SVE", 1635: "RBL",
    # 2. Bundesliga
    36: "OSN", 54: "BSC", 55: "H96", 74: "EBS", 76: "FCK", 78: "FCM",
    79: "FCN", 83: "DSC", 93: "FCE", 98: "STP", 104: "KSV", 105: "KSC",
    115: "SGF", 118: "SVD", 129: "BOC", 131: "WOB", 177: "SGD", 199: "FCH",
    # 3. Liga (nur belegte Kürzel)
    23: "AAC", 102: "HRO", 107: "MSV", 109: "RWE", 114: "SCV", 171: "FCI",
    174: "SVWW", 185: "F95", 188: "PRM", 417: "FCS", 553: "SVW",
}
# Hinweis: SVW tragen sowohl Werder Bremen (1. Liga) als auch Waldhof Mannheim
# (3. Liga). Innerhalb einer Liga stört das nicht; treffen beide im Pokal
# aufeinander, entstünde "#SVWSVW" - dann eines der beiden hier anpassen.


# --------------------------------------------------------------- Darstellung
def titel(text):
    print(f"\n\033[1m{text}\033[0m")
    print("─" * len(text))


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
    """Ersetzt einen Wert und lässt alle Kommentare unangetastet."""
    aktuell = None
    for i, zeile in enumerate(zeilen):
        if zeile.startswith("["):
            aktuell = zeile.strip().strip("[]")
        elif aktuell == abschnitt and re.match(rf"\s*{re.escape(schluessel)}\s*=", zeile):
            zeilen[i] = f"{schluessel} = {wert}\n"
            return True
    return False


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
    was = auswahl(
        [("beide", "Beide"),
         ("matchday", "Nur Spieltags-Ticker (WhatsApp-Kanal)"),
         ("feed", "Nur Instagram-Spiegelung")],
        "Auswahl", 0,
    )
    macht_matchday = was in ("beide", "matchday")
    macht_feed = was in ("beide", "feed")

    # ------------------------------------------------------------ Einsatzzweck
    zweck = "sport_plan"
    if macht_matchday:
        titel("Wofür wird der Kanal-Ticker eingesetzt?")
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

    # ------------------------------------------------------------ Bluesky
    titel("Bluesky-Konto")
    hinweis("Das Konto, auf dem der Bot veröffentlicht - ohne führendes @.")
    handle = frage("Handle", bisher("bluesky", "handle") or "mein-bot.bsky.social",
                   pflicht=True, pruefung=pruefe_handle)
    setze_wert(zeilen, "bluesky", "handle", handle)

    # ------------------------------------------------------------ Matchday
    if macht_matchday:
        titel("WhatsApp-Kanal")
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

        titel("Beiträge")
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
        titel("Profil-Statuszeile")
        hinweis("Die erste Zeile der Bluesky-Biografie kann anzeigen, ob der Bot")
        hinweis("gerade läuft - und beim Beenden wieder zurückgestellt werden.")
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
        titel("Instagram-Spiegelung")
        profil = frage("Instagram-Profil, das gespiegelt wird (ohne @)",
                       bisher("feed", "instagram_profile"), pflicht=True)
        setze_wert(zeilen, "feed", "instagram_profile", profil)

        hinweis("Zweitkonto für den Abruf. Sitzung anlegen mit: venv/bin/instaloader -l <name>")
        zweitkonto = frage("Instagram-Zweitkonto", bisher("feed", "instagram_session_user"),
                           pflicht=True)
        setze_wert(zeilen, "feed", "instagram_session_user", zweitkonto)

        eigenes_konto = bisher("feed", "bluesky_handle")
        if ja_nein("Für Instagram ein anderes Bluesky-Konto verwenden?", bool(eigenes_konto)):
            feed_handle = frage("Handle für die Instagram-Spiegelung",
                                eigenes_konto or handle, pflicht=True, pruefung=pruefe_handle)
            setze_wert(zeilen, "feed", "bluesky_handle", feed_handle)
            hinweis("Dessen App-Passwort gehört in SKYRELAY_FEED_APP_PASSWORD.")
        else:
            setze_wert(zeilen, "feed", "bluesky_handle", "")

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

    teste_bluesky(handle)

    print("\n\033[1mFertig.\033[0m Nächste Schritte:\n")
    if macht_matchday:
        print("  Erste WhatsApp-Kopplung (interaktiv im Terminal):")
        print("    SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py")
    if macht_feed:
        print("  Instagram-Sitzung anlegen (einmalig):")
        print(f"    venv/bin/instaloader -l {zweitkonto}")
    print("\n  Danach den Dauerbetrieb per cron einrichten - siehe README.md.\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAbgebrochen - es wurde nichts geschrieben.\n")
        sys.exit(1)
