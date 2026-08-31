"""
SkyRelay - setup assistant

Asks for what is needed and writes a finished "skyrelay.conf" from it. It looks
the club up at OpenLigaDB directly and pre-fills the table of team codes for the
hashtags - which is the most tedious handwork.

Run it with:
    venv/bin/python skyrelay-setup.py

A second run doubles as the way to change things: existing values are offered as
defaults, [Enter] keeps them. Passwords NEVER end up in the configuration - they
belong in the environment variable BLUESKY_APP_PASSWORD.
"""

import os
import re
import sys
import unicodedata

import skyrelay_tui as tui
import skyrelay_config as config
import skyrelay_layout as layout

try:
    import requests
except ImportError:
    print("Error: the package 'requests' is missing. Run ./install.sh first.",
          file=sys.stderr)
    sys.exit(1)

# On consoles without UTF-8 (the Windows command prompt, say) the assistant
# should not give up at the first special character.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(BASE_DIR, "skyrelay.conf.example")
TARGET = os.environ.get("SKYRELAY_CONFIG") or os.path.join(BASE_DIR, "skyrelay.conf")

# The leagues asked for most often come first - everything else through
# "search for another league".
# (leagueShortcut, season start year, label)
SUGGESTED_LEAGUES = [
    ("bl1", "2026", "Fußball · 1. Bundesliga"),
    ("bl2", "2026", "Fußball · 2. Bundesliga"),
    ("bl3", "2026", "Fußball · 3. Liga"),
    ("dfb", "2026", "Fußball · DFB-Pokal"),
    ("fbl1", "2025", "Frauenfußball · 1. Bundesliga"),
    ("del", "2026", "Eishockey · DEL"),
    ("del2", "2026", "Eishockey · DEL2"),
]

# The codes in common use per OpenLigaDB team number, as they appear in
# results services and on television graphics. There is no official standard
# for them - and OpenLigaDB itself supplies none (only the short name, so
# "Bielefeld" rather than "DSC"). Hence this table.
# For teams without an entry the assistant proposes something derived from
# the name, marked in the dialog as a proposal ("?").
#
# Sorted by club name rather than by league: that way entries stay put when
# clubs are promoted or relegated.
# To add one: look the team number up via getavailableteams/<league>/<season>.
# As of season 2026/27.
KNOWN_TEAM_CODES = {
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
# Note: SVW is carried by both Werder Bremen (1st division) and Waldhof
# Mannheim (3rd division). Within one league that does no harm; should the
# two meet in the cup, "#SVWSVW" would come out - then change one of them.


# ------------------------------------------------------------- presentation
def heading(text):
    print(f"\n\033[1m{text}\033[0m")
    print("─" * len(text))


def program_banner(part, name, flow, extra=""):
    """A clearly visible divide between the two programs - without it, it is
    not obvious during setup which bot a given answer applies to."""
    width = 74
    print("\n\033[1m" + "═" * width)
    print(f"  {part}{name}")
    print(f"  {flow}" + (f"   ({extra})" if extra else ""))
    print("═" * width + "\033[0m")


def note(text):
    print(f"  \033[2m{text}\033[0m")


def ask(text, default="", required=False, validator=None):
    """Asks for a value. [Enter] keeps the default."""
    while True:
        shown = f" [{default}]" if default else ""
        try:
            entry = input(f"  {text}{shown}: ").strip()
        except EOFError:
            raise KeyboardInterrupt
        value = entry or default
        if required and not value:
            print("    Bitte etwas eingeben.")
            continue
        if value and validator:
            error = validator(value)
            if error:
                print(f"    {error}")
                continue
        return value


def confirm(text, default=True):
    hint = "J/n" if default else "j/N"
    while True:
        try:
            entry = input(f"  {text} [{hint}]: ").strip().lower()
        except EOFError:
            raise KeyboardInterrupt
        if not entry:
            return default
        if entry in ("j", "ja", "y", "yes"):
            return True
        if entry in ("n", "nein", "no"):
            return False


def choose(entries, text, default_index=None):
    """Shows a numbered list and returns the entry that was picked."""
    for number, (_, label) in enumerate(entries, 1):
        marker = " ←" if default_index == number - 1 else ""
        print(f"    {number:2}) {label}{marker}")
    default = str(default_index + 1) if default_index is not None else ""
    while True:
        entry = ask(text, default, required=True)
        if entry.isdigit() and 1 <= int(entry) <= len(entries):
            return entries[int(entry) - 1][0]
        print(f"    Bitte eine Zahl zwischen 1 und {len(entries)} eingeben.")


# ----------------------------------------------------------- configuration
def read_value(lines, section, key):
    """Reads a value from the lines of a configuration file."""
    current = None
    for line in lines:
        if line.startswith("["):
            current = line.strip().strip("[]")
        elif current == section and re.match(rf"\s*{re.escape(key)}\s*=", line):
            return line.split("=", 1)[1].strip()
    return ""


def set_value(lines, section, key, value):
    """Sets a value and leaves every comment untouched.

    If the key is missing it is appended at the end of its section; if the
    section is missing too, it is created at the end of the file. Previously
    nothing at all happened in those cases: anyone carrying a configuration
    over from an older version typed values into the assistant that quietly
    fell away - the menu said "saved" and nothing had changed."""
    current = None
    section_start = None
    section_end = None
    for i, line in enumerate(lines):
        if line.startswith("["):
            if current == section and section_end is None:
                section_end = i
            current = line.strip().strip("[]")
            if current == section:
                section_start = i
        elif current == section and re.match(rf"\s*{re.escape(key)}\s*=", line):
            lines[i] = f"{key} = {value}\n"
            return True

    if section_start is None:
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend([f"[{section}]\n", f"{key} = {value}\n"])
        return True

    # After the last line with content in the section, still before the blank
    # lines leading to the next one.
    insert_at = section_end if section_end is not None else len(lines)
    while insert_at > section_start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, f"{key} = {value}\n")
    return True


def set_team_codes(lines, codes):
    """Replaces the contents of [team_codes] with the new table."""
    start = end = None
    for i, line in enumerate(lines):
        if line.strip() == "[team_codes]":
            start = i
        elif start is not None and line.startswith("[") and i > start:
            end = i
            break
    if start is None:
        return False
    end = end if end is not None else len(lines)

    header = [z for z in lines[start:end] if z.startswith("#") or z.strip() == "[team_codes]"]
    new = header + [f"{team_id} = {code}\n" for team_id, code in sorted(codes.items())] + ["\n"]
    lines[start:end] = new
    return True


# ----------------------------------------------------------------- subject
def suggest_code(team):
    """Returns the code in common use, otherwise one derived from the name.
    The second return value says whether it is an established code."""
    known = KNOWN_TEAM_CODES.get(team.get("teamId"))
    if known:
        return known, True

    name = (team.get("shortName") or team.get("teamName") or "").strip()
    # Some leagues (the DEL, for instance) already carry the official code in
    # the short name - then take it as it is instead of trimming it.
    if 2 <= len(name) <= 5 and name.isupper() and name.isalpha():
        return name, True

    normalised = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    letters = re.sub(r"[^A-Za-z]", "", normalised)
    return (letters[:3] or "XXX").upper(), False


def fetch_teams(league, season):
    answer = requests.get(
        f"https://api.openligadb.de/getavailableteams/{league}/{season}", timeout=20
    )
    answer.raise_for_status()
    return answer.json()


def fetch_current_leagues():
    """Every league with a running or upcoming season, grouped by sport."""
    answer = requests.get("https://api.openligadb.de/getavailableleagues", timeout=30)
    answer.raise_for_status()
    current = [l for l in answer.json() if str(l.get("leagueSeason", "")) in ("2025", "2026")]
    return sorted(
        current,
        key=lambda l: ((l.get("sport") or {}).get("sportName", ""), l.get("leagueName", "")),
    )


def check_channel_link(value):
    if "whatsapp.com/channel/" not in value:
        return "Das sieht nicht nach einem Kanal-Link aus (erwartet: https://whatsapp.com/channel/…)."
    if "HIER-DEN" in value:
        return "Das ist noch der Platzhalter."
    return None


def check_handle(value):
    if value.startswith("@"):
        return "Bitte ohne führendes @ angeben."
    if "." not in value:
        return "Erwartet wird ein vollständiges Handle, z.B. mein-bot.bsky.social"
    return None


def test_bluesky(handle, password_variable="BLUESKY_APP_PASSWORD"):
    """Checks the login without publishing anything."""
    password = os.environ.get(password_variable)
    if not password:
        note(f"{password_variable} ist nicht gesetzt - Anmeldung wird nicht geprüft.")
        note(f'Später setzen mit:  export {password_variable}="xxxx-xxxx-xxxx-xxxx"')
        return
    if not confirm(f"Anmeldung bei Bluesky als {handle} jetzt prüfen?", True):
        return
    try:
        from atproto import Client
        Client().login(handle, password)
        print(f"  ✓ Anmeldung erfolgreich: {handle}")
    except ImportError:
        note("Paket 'atproto' fehlt - Prüfung übersprungen (./install.sh ausführen).")
    except Exception as error:
        print(f"  ✗ Anmeldung fehlgeschlagen: {error}")
        note("Handle und App-Passwort prüfen. Die Einrichtung läuft trotzdem weiter.")


# ------------------------------------------------------------------- flow
def main():
    print("\n\033[1mSkyRelay - Einrichtung\033[0m")
    print("Mit [Enter] wird jeweils der Wert in eckigen Klammern übernommen.")
    print("Abbruch jederzeit mit Strg+C - dann wird nichts geschrieben.")

    if not os.path.exists(TEMPLATE):
        print(f"\nError: the template is missing: {TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    with open(TEMPLATE, encoding="utf-8") as handle:
        lines = handle.readlines()

    # Use an existing configuration as the default
    old_lines = []
    if os.path.exists(TARGET):
        with open(TARGET, encoding="utf-8") as handle:
            old_lines = handle.readlines()
        print(f"\nVorhandene Konfiguration gefunden: {os.path.basename(TARGET)}")
        note("Die bisherigen Werte stehen als Vorgabe bereit.")

    def current_value(section, key):
        return read_value(old_lines, section, key) if old_lines else ""

    # ------------------------------------------------- which programs?
    heading("Welche Programme möchtest du einrichten?")
    note("SkyRelay besteht aus zwei getrennten Bots, die auch getrennte")
    note("Bluesky-Konten benutzen können:")
    print("     · Spieltags-Ticker: WhatsApp-Kanal → Bluesky, nur an Spieltagen")
    print("     · Instagram-Feed:   Instagram → Bluesky, im Dauerbetrieb")
    picked_programs = choose(
        [("both", "Beide"),
         ("matchday", "Nur den Spieltags-Ticker (WhatsApp-Kanal)"),
         ("feed", "Nur die Instagram-Spiegelung")],
        "Auswahl", 0,
    )
    does_matchday = picked_programs in ("both", "matchday")
    does_feed = picked_programs in ("both", "feed")
    parts = "1 von 2: " if picked_programs == "both" else ""
    ticker_handle = feed_handle = ""

    if does_matchday:
        program_banner(parts, "SPIELTAGS-TICKER",
                       "WhatsApp-Kanal  ──►  Bluesky", "läuft nur an Spieltagen")

    # ------------------------------------------------------ what it is for
    purpose = "sport_plan"
    if does_matchday:
        heading("Wofür wird der Ticker eingesetzt?")
        note("Davon hängt ab, ob Spieltage automatisch erkannt werden können")
        note("und wie die vorgeschlagenen Texte formuliert sind.")
        purpose = choose(
            [("sport_plan", "Sport mit Spielplan bei OpenLigaDB (Fußball, Eishockey …)"),
             ("sport_no_schedule", "Sport ohne Spielplan-Daten (z.B. Basketball, Handball-Liga)"),
             ("custom", "anderer Zweck (Verein, Veranstaltung, Projekt …)")],
            "Auswahl", 0,
        )
    generic = purpose == "custom"
    # Match the wording of the defaults to the purpose
    W = {
        "event": "Ereignis" if generic else "Spiel",
        "prefix": "📡 [Inoffizieller Bot]" if generic else "⚽ [Inoffizieller Bot]",
        "source": "WhatsApp-Kanal" if generic else "WhatsApp-Kanal des Vereins",
        "line_on": ("🟢 Bot ist an {hashtag}" if generic
               else "🟢 Bot ist an - {info} {hashtag} ⚫⚪🔵"),
        "line_off": ("🔴 Bot ist aus" if generic
                else "🔴 Bot ist aus - nächstes Spiel {hashtag} ⚫⚪🔵"),
        "line_off_empty": "🔴 Bot ist aus" if generic else "🔴 Bot ist aus ⚫⚪🔵",
        "fallback": "aktiv" if generic else "Testspiel",
    }

    # ---------------------------------------------------------- matchday
    if does_matchday:
        heading("Bluesky-Konto FÜR DEN TICKER")
        note("Auf dieses Konto werden die Beiträge aus dem WhatsApp-Kanal")
        note("veröffentlicht - ohne führendes @.")
        ticker_handle = ask("Handle", current_value("bluesky", "handle") or "mein-ticker.bsky.social",
                            required=True, validator=check_handle)
        set_value(lines, "bluesky", "handle", ticker_handle)

        heading("WhatsApp-Kanal (Quelle des Tickers)")
        note("Im Handy: Kanal öffnen → Kanalnamen antippen → Teilen → Link kopieren.")
        link = ask("Einladungslink", current_value("source", "channel_invite_link"),
                   required=True, validator=check_channel_link)
        set_value(lines, "source", "channel_invite_link", link)

        league = season = None
        choice = "ohne"
        if purpose == "sport_plan":
            heading("Liga und Verein")
            note("Daraus erkennt der Ticker, an welchen Tagen er überhaupt laufen muss,")
            note("und bildet den Spiel-Hashtag. Grundlage ist OpenLigaDB.")
            choice = choose(
                [(k, b) for k, _, b in SUGGESTED_LEAGUES]
                + [("suchen", "andere Liga aus OpenLigaDB wählen …"),
                   ("ohne", "doch kein Spielplan – Ticker läuft an jedem Starttag")],
                "Liga", 1,
            )

        if choice == "ohne":
            heading("Ohne Spielplan")
            note("Es findet keine automatische Prüfung statt, ob heute etwas ansteht:")
            note("Der Ticker läuft an jedem Tag, an dem er gestartet wird.")
            note(f"Ein wechselnder {W['event']}-Hashtag lässt sich beim Start über")
            note("SKYRELAY_HASHTAG mitgeben. Den cron-Eintrag also nur für die Tage")
            note("einrichten, an denen etwas läuft - oder von Hand starten.")
            set_value(lines, "team", "openligadb_filter", "")
            set_value(lines, "team", "openligadb_team_id", "0")
        elif choice == "suchen":
            try:
                leagues = fetch_current_leagues()
            except Exception as error:
                print(f"  ✗ Abruf fehlgeschlagen: {error}")
                leagues = []
            if leagues:
                note(f"{len(leagues)} Ligen mit laufender Saison:")
                picked = choose(
                    [(l, f'{(l.get("sport") or {}).get("sportName", "?")} · '
                         f'{l["leagueName"]} ({l["leagueShortcut"]}/{l["leagueSeason"]})')
                     for l in leagues],
                    "Liga",
                )
                league, season = picked["leagueShortcut"], str(picked["leagueSeason"])
            else:
                league = ask("Ligakürzel", "bl2", required=True)
                season = ask("Saison (Startjahr)", "2026", required=True)
        else:
            league = choice
            season = next(s for k, s, _ in SUGGESTED_LEAGUES if k == choice)
            season = ask("Saison (Startjahr)", season, required=True)

        teams = []
        if league:
            try:
                teams = fetch_teams(league, season)
            except Exception as error:
                print(f"  ✗ Abruf fehlgeschlagen: {error}")

        if teams:
            print(f"\n  {len(teams)} Mannschaften in {league}/{season}:")
            by_name = sorted(teams, key=lambda t: t.get("shortName") or "")
            old_id = current_value("team", "openligadb_team_id")
            default = next((i for i, t in enumerate(by_name) if str(t["teamId"]) == old_id), None)
            chosen = choose(
                [(t, f'{t.get("shortName") or t["teamName"]}  ({t["teamName"]})') for t in by_name],
                "Dein Verein", default,
            )
            set_value(lines, "team", "openligadb_team_id", chosen["teamId"])
            search_term = (chosen.get("shortName") or chosen["teamName"]).split()[-1].lower()
            search_term = ask("Suchbegriff für OpenLigaDB", current_value("team", "openligadb_filter") or search_term,
                              required=True)
            set_value(lines, "team", "openligadb_filter", search_term)

            # ------------------------------------------- table of codes
            heading("Kürzel für die Hashtags")
            note("Aus Heim- und Auswärtskürzel entsteht der Spiel-Hashtag, z.B. #KSCDSC.")
            codes, taken = {}, {}
            for team in teams:
                codes[team["teamId"]], taken[team["teamId"]] = suggest_code(team)
            # Codes already maintained always take precedence - and they stay
            # even when the team does not play in the chosen league (a move
            # between divisions, a cup opponent from elsewhere).
            for line in old_lines:
                if re.match(r"^\d+\s*=", line):
                    team_id, code = line.split("=", 1)
                    codes[int(team_id.strip())] = code.strip()
                    taken[int(team_id.strip())] = True

            derived_count = sum(1 for t in teams if not taken[t["teamId"]])
            if derived_count:
                note(f"{len(teams) - derived_count} Kürzel sind hinterlegt, "
                     f"{derived_count} aus dem Namen abgeleitet (mit ? markiert).")
                note("Abgeleitete entsprechen oft nicht dem üblichen Kürzel - bitte prüfen.")
            else:
                note("Für alle Mannschaften dieser Liga sind Kürzel hinterlegt.")

            own = codes.get(chosen["teamId"], "XXX")
            print(f"\n  Kürzel deines Vereins ({chosen['teamName']}): {own}")
            note("Es steht in jedem Spiel-Hashtag - bitte genau prüfen.")
            codes[chosen["teamId"]] = ask("Kürzel", own, required=True).upper()

            print("\n  Übrige Mannschaften (? = abgeleitet, ungeprüft):")
            for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                if team["teamId"] != chosen["teamId"]:
                    mark = " " if taken[team["teamId"]] else "?"
                    print(f"   {mark} {codes[team['teamId']]:5} {team['teamName']}")
            if confirm("\n  Diese Kürzel einzeln anpassen?", bool(derived_count)):
                for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                    if team["teamId"] == chosen["teamId"]:
                        continue
                    codes[team["teamId"]] = ask(
                        team["teamName"], codes[team["teamId"]], required=True).upper()
            else:
                note("Später jederzeit im Abschnitt [team_codes] änderbar.")
            set_team_codes(lines, codes)

        heading("Beiträge des Tickers")
        note("Es gibt zwei Sorten Hashtags:")
        note(f"  · Dauer-Hashtag - steht unter JEDEM Beitrag (z.B. der Vereinsname)")
        note(f"  · {W['event']}-Hashtag - wechselt je Termin"
             + (" und wird aus dem Spielplan gebildet" if choice != "ohne"
                   else ", kommt aus SKYRELAY_HASHTAG"))
        mark = ask("Dauer-Hashtag (ohne #, leer = keiner)",
                   current_value("post", "standing_hashtag"))
        set_value(lines, "post", "standing_hashtag", mark)
        set_value(lines, "post", "prefix", current_value("post", "prefix") or W["prefix"])
        label = ask("Beschriftung des Quell-Links",
                    current_value("post", "source_label") or W["source"])
        set_value(lines, "post", "source_label", label)

        # ------------------------------------------------ profile and time
        heading("Profil-Statuszeile des Ticker-Kontos")
        note(f"Die erste Zeile der Biografie von @{ticker_handle} kann anzeigen,")
        note("ob der Bot gerade läuft - beim Beenden wird sie zurückgestellt.")
        was_on = current_value("profile", "enabled")
        if confirm("Statuszeile verwenden?", was_on.lower() != "false" if was_on else True):
            set_value(lines, "profile", "enabled", "true")
            note("Platzhalter: {hashtag}" + (", {info} (z.B. '1. Spieltag'), {date}, {time}"
                                             if choice != "ohne" else ""))
            line_on = ask("Text während des Betriebs",
                          current_value("profile", "line_on") or W["line_on"], required=True)
            line_off = ask("Text nach dem Beenden",
                           current_value("profile", "line_off") or W["line_off"], required=True)
            set_value(lines, "profile", "line_on", line_on)
            set_value(lines, "profile", "line_off", line_off)
            set_value(lines, "profile", "line_off_no_match",
                      current_value("profile", "line_off_no_match") or W["line_off_empty"])
            note("Erkannt wird eine vorhandene Statuszeile am Text 'Bot ist'.")
            note("Wer andere Formulierungen nutzt, passt [profile] marker an.")
        else:
            set_value(lines, "profile", "enabled", "false")
        set_value(lines, "profile", "fallback_match_info",
                  current_value("profile", "fallback_match_info") or W["fallback"])

        heading("Zeitfenster")
        note("Bis zu dieser Uhrzeit lauscht der Ticker, danach beendet er sich selbst.")
        set_value(lines, "schedule", "day_end",
                  ask("Betriebsende (HH:MM)", current_value("schedule", "day_end") or "23:59",
                       required=True))

    # ------------------------------------------------------------- feed
    if does_feed:
        program_banner("2 von 2: " if picked_programs == "both" else "", "INSTAGRAM-SPIEGELUNG",
                       "Instagram  ──►  Bluesky", "läuft im Dauerbetrieb")

        heading("Instagram-Profil (Quelle des Feeds)")
        profile_key = ask("Profil, das gespiegelt wird (ohne @)",
                          current_value("feed", "instagram_profile"), required=True)
        set_value(lines, "feed", "instagram_profile", profile_key)

        note("Zweitkonto für den Abruf - NICHT das gespiegelte Profil.")
        note("Sitzung anlegen mit: venv/bin/instaloader -l <name>")
        secondary_account = ask("Instagram-Zweitkonto", current_value("feed", "instagram_session_user"),
                                required=True)
        set_value(lines, "feed", "instagram_session_user", secondary_account)

        heading("Bluesky-Konto FÜR DEN FEED")
        own_account = current_value("feed", "bluesky_handle")
        if does_matchday:
            note(f"Der Ticker veröffentlicht auf @{ticker_handle}.")
            own = confirm("Soll die Instagram-Spiegelung ein ANDERES Konto verwenden?",
                          bool(own_account))
        else:
            own = True
        if own:
            note("Auf dieses Konto werden die Instagram-Beiträge veröffentlicht.")
            feed_handle = ask("Handle", own_account or current_value("bluesky", "handle")
                              or "mein-feed.bsky.social", required=True, validator=check_handle)
            if does_matchday:
                set_value(lines, "feed", "bluesky_handle", feed_handle)
                note("Dessen App-Passwort gehört in SKYRELAY_FEED_APP_PASSWORD,")
                note("nicht in BLUESKY_APP_PASSWORD (das gilt für den Ticker).")
            else:
                # Without the ticker the general account is the feed's as well.
                set_value(lines, "bluesky", "handle", feed_handle)
                set_value(lines, "feed", "bluesky_handle", "")
        else:
            feed_handle = ticker_handle
            set_value(lines, "feed", "bluesky_handle", "")

    # ---------------------------------------------------------- summary
    heading("Zusammenfassung")
    note("Bitte prüfen, ob Quellen und Konten richtig zugeordnet sind:")
    if does_matchday:
        print(f"  Spieltags-Ticker:  WhatsApp-Kanal  ──►  @{ticker_handle}")
    if does_feed:
        print(f"  Instagram-Feed:    @{profile_key}  ──►  @{feed_handle}")
    if does_matchday and does_feed and ticker_handle == feed_handle:
        note("Beide veröffentlichen auf demselben Konto - das ist zulässig,")
        note("führt aber dazu, dass Ticker und Feed sich vermischen.")
    if not confirm("\n  Stimmt das so?", True):
        print("\nAbgebrochen - es wurde nichts geändert. Starte den Assistenten erneut.")
        return

    # ---------------------------------------------------------- writing
    heading("Speichern")
    print(f"  Ziel: {TARGET}")
    if os.path.exists(TARGET):
        if not confirm("Vorhandene Konfiguration überschreiben? (Sicherung wird angelegt)", True):
            print("\nAbgebrochen - es wurde nichts geändert.")
            return
        backup = TARGET + ".bak"
        try:
            with open(backup, "w", encoding="utf-8") as handle:
                handle.writelines(old_lines)
            print(f"  ✓ Sicherung: {os.path.basename(backup)}")
        except Exception as error:
            print(f"  ⚠️ Sicherung fehlgeschlagen: {error}")

    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    print(f"  ✓ Geschrieben: {os.path.basename(TARGET)}")

    # Check each account with the password variable that applies in operation.
    if does_matchday:
        test_bluesky(ticker_handle, "BLUESKY_APP_PASSWORD")
    if does_feed:
        if not does_matchday:
            test_bluesky(feed_handle, "BLUESKY_APP_PASSWORD")
        elif feed_handle != ticker_handle:
            test_bluesky(feed_handle, "SKYRELAY_FEED_APP_PASSWORD")

    print("\n\033[1mFertig.\033[0m Nächste Schritte:\n")
    if does_matchday:
        print(f"  SPIELTAGS-TICKER (postet auf @{ticker_handle})")
        print("    Erste WhatsApp-Kopplung, interaktiv im Terminal:")
        print("      SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py")
    if does_feed:
        print(f"\n  INSTAGRAM-FEED (postet auf @{feed_handle})")
        print("    Instagram-Sitzung einmalig anlegen:")
        print(f"      venv/bin/instaloader -l {secondary_account}")
    print("\n  Danach den Dauerbetrieb per cron einrichten - siehe README.md.\n")


# ============================= the menu surface ===============================
# It feels like raspi-config: one main menu from which single areas can be
# changed on purpose - instead of fifteen questions in a row.

def _show(lines, section, key, fallback="– nicht gesetzt –", short=40):
    value = read_value(lines, section, key)
    if not value or value.startswith("dein") or "HIER-DEN" in value:
        return fallback
    return value if len(value) <= short else value[:short - 1] + "…"


def m_ticker(lines):
    """Everything about the matchday ticker."""
    while True:
        channel = read_value(lines, "source", "channel_invite_link")
        channel_text = "– nicht gesetzt –" if not channel or "HIER-DEN" in channel else "gesetzt"
        league = read_value(lines, "team", "openligadb_filter") or "ohne Spielplan"
        choice = tui.menu(
            "Spieltags-Ticker", "WhatsApp-Kanal → Bluesky, läuft nur an Spieltagen.",
            [("konto", f"Bluesky-Konto ......  {_show(lines, 'bluesky', 'handle')}"),
             ("kanal", f"WhatsApp-Kanal ....  {channel_text}"),
             ("liga", f"Liga und Verein ...  {league}")])
        if choice is None:
            return
        if choice == "konto":
            value = tui.ask("Bluesky-Konto für den TICKER",
                             "Konto, auf dem die Kanalbeiträge veröffentlicht werden.\n"
                             "Ohne führendes @, z.B. mein-ticker.bsky.social",
                             read_value(lines, "bluesky", "handle"))
            if value:
                error = check_handle(value)
                if error:
                    tui.message("Ungültig", error)
                else:
                    set_value(lines, "bluesky", "handle", value)
        elif choice == "kanal":
            value = tui.ask("WhatsApp-Kanal",
                             "Einladungslink des Kanals.\n\n"
                             "Im Handy: Kanal öffnen → Kanalnamen antippen →\n"
                             "Teilen → Link kopieren.",
                             read_value(lines, "source", "channel_invite_link"))
            if value:
                error = check_channel_link(value)
                if error:
                    tui.message("Ungültig", error)
                else:
                    set_value(lines, "source", "channel_invite_link", value)
        elif choice == "liga":
            m_league(lines)


def m_league(lines):
    """Pick a league and a club - or switch the fixture list off entirely."""
    entries = [(k, b) for k, _, b in SUGGESTED_LEAGUES]
    entries += [("suchen", "andere Liga aus OpenLigaDB …"),
                ("ohne", "kein Spielplan (läuft an jedem Starttag)")]
    choice = tui.menu("Liga", "Grundlage für Spieltags-Erkennung und Hashtag.", entries)
    if choice is None:
        return

    if choice == "ohne":
        set_value(lines, "team", "openligadb_filter", "")
        set_value(lines, "team", "openligadb_team_id", "0")
        tui.message("Ohne Spielplan",
                    "Die Spieltags-Erkennung ist abgeschaltet.\n\n"
                    "Der Ticker läuft an jedem Tag, an dem er gestartet wird.\n"
                    "Einen wechselnden Hashtag gibst du beim Start über\n"
                    "SKYRELAY_HASHTAG mit. Den cron-Eintrag also nur für die\n"
                    "Tage einrichten, an denen etwas läuft.")
        return

    if choice == "suchen":
        tui.progress("Ligen werden von OpenLigaDB geladen …")
        try:
            leagues = fetch_current_leagues()
        except Exception as error:
            tui.message("Abruf fehlgeschlagen", str(error))
            return
        chosen = tui.choose(
            "Liga wählen", f"{len(leagues)} Ligen mit laufender Saison:",
            [(f'{l["leagueShortcut"]}|{l["leagueSeason"]}',
              f'{(l.get("sport") or {}).get("sportName", "?")} · {l["leagueName"]}')
             for l in leagues])
        if chosen is None:
            return
        league, season = chosen.split("|")
    else:
        league = choice
        season = next(s for k, s, _ in SUGGESTED_LEAGUES if k == choice)
        entry = tui.ask("Saison", "Startjahr der Saison:", season)
        if entry is None:
            return
        season = entry

    tui.progress(f"Mannschaften aus {league}/{season} werden geladen …")
    try:
        teams = fetch_teams(league, season)
    except Exception as error:
        tui.message("Abruf fehlgeschlagen", str(error))
        return
    if not teams:
        tui.message("Nichts gefunden", f"Zu {league}/{season} liefert OpenLigaDB keine Mannschaften.")
        return

    by_name = sorted(teams, key=lambda t: t.get("shortName") or "")
    chosen = tui.choose(
        "Verein wählen", "Für welchen Verein läuft der Ticker?",
        [(t["teamId"], f'{t.get("shortName") or t["teamName"]}  ({t["teamName"]})')
         for t in by_name],
        read_value(lines, "team", "openligadb_team_id"))
    if chosen is None:
        return

    club = next(t for t in teams if str(t["teamId"]) == str(chosen))
    set_value(lines, "team", "openligadb_team_id", club["teamId"])
    search_term = (club.get("shortName") or club["teamName"]).split()[-1].lower()
    entry = tui.ask("Suchbegriff",
                        "Begriff, mit dem OpenLigaDB nach dem Verein sucht:", search_term)
    set_value(lines, "team", "openligadb_filter", entry or search_term)

    # Add the league shortcut for the matchday filter and keep what is there:
    # a club often plays in several competitions (league, cup, women's
    # league), and each of them needs its entry.
    current_value = [s.strip() for s
                     in (read_value(lines, "team", "league_shortcuts") or "").split(",")
                     if s.strip()]
    if league not in current_value:
        current_value.append(league)
    set_value(lines, "team", "league_shortcuts", ", ".join(current_value))

    # Add the league's codes, keep the ones already there
    existing = {int(z.split("=")[0].strip()) for z in lines if re.match(r"^\d+\s*=", z)}
    new = 0
    codes = {int(z.split("=")[0].strip()): z.split("=", 1)[1].strip()
             for z in lines if re.match(r"^\d+\s*=", z)}
    for team in teams:
        if team["teamId"] not in existing:
            codes[team["teamId"]] = suggest_code(team)[0]
            new += 1
    set_team_codes(lines, codes)
    tui.message("Verein gesetzt",
                f'{club["teamName"]}\n\n'
                f"Kürzeltabelle: {new} Mannschaft(en) ergänzt, "
                f"{len(existing)} bereits vorhanden.\n\n"
                f"Unter „Kürzel für Hashtags“ kannst du sie prüfen –\n"
                f"abgeleitete Vorschläge sind mit ? markiert.")


def m_codes(lines):
    """Edit the table of codes one by one."""
    while True:
        codes = {int(z.split("=")[0].strip()): z.split("=", 1)[1].strip()
                 for z in lines if re.match(r"^\d+\s*=", z)}
        if not codes:
            tui.message("Noch keine Kürzel",
                        "Wähle zuerst unter „Spieltags-Ticker“ eine Liga und einen Verein.")
            return
        own = read_value(lines, "team", "openligadb_team_id")
        entries = []
        for team_id, code in sorted(codes.items(), key=lambda x: x[1]):
            mark = " ←  eigener Verein" if str(team_id) == own else ""
            entries.append((team_id, f"{code:6} (Nr. {team_id}){mark}"))
        choice = tui.choose("Kürzel für Hashtags",
                                 "Aus Heim + Auswärts entsteht der Hashtag, z.B. #KSCDSC.\n"
                                 "Eintrag wählen zum Ändern.", entries)
        if choice is None:
            return
        new = tui.ask("Kürzel ändern", f"Kürzel für Team-Nummer {choice}:", codes[int(choice)])
        if new:
            codes[int(choice)] = new.strip().upper()
            set_team_codes(lines, codes)


def m_feed(lines):
    """Everything about mirroring Instagram."""
    while True:
        account = read_value(lines, "feed", "bluesky_handle") or \
            (read_value(lines, "bluesky", "handle") + "  (wie Ticker)")
        choice = tui.menu(
            "Instagram-Feed", "Instagram → Bluesky, läuft im Dauerbetrieb.",
            [("profil", f"Instagram-Profil ..  {_show(lines, 'feed', 'instagram_profile')}"),
             ("konto2", f"Zweitkonto (Abruf)   {_show(lines, 'feed', 'instagram_session_user')}"),
             ("bsky", f"Bluesky-Konto .....  {account[:40]}")])
        if choice is None:
            return
        if choice == "profil":
            value = tui.ask("Instagram-Profil",
                             "Profil, das gespiegelt wird (ohne @):",
                             read_value(lines, "feed", "instagram_profile"))
            if value:
                set_value(lines, "feed", "instagram_profile", value.lstrip("@"))
        elif choice == "konto2":
            value = tui.ask("Instagram-Zweitkonto",
                             "Konto, mit dem abgerufen wird – NICHT das gespiegelte Profil.\n\n"
                             "Sitzung anlegen mit:  venv/bin/instaloader -l <name>",
                             read_value(lines, "feed", "instagram_session_user"))
            if value:
                set_value(lines, "feed", "instagram_session_user", value.lstrip("@"))
        elif choice == "bsky":
            own = read_value(lines, "feed", "bluesky_handle")
            if tui.confirm("Bluesky-Konto für den Feed",
                           "Soll die Instagram-Spiegelung ein ANDERES Konto\n"
                           f"verwenden als der Ticker ({read_value(lines, 'bluesky', 'handle')})?",
                           bool(own)):
                value = tui.ask("Konto für den Feed", "Handle ohne @:",
                                 own or read_value(lines, "bluesky", "handle"))
                if value:
                    set_value(lines, "feed", "bluesky_handle", value)
                    tui.message("Getrennte Konten",
                                "Beide Bots brauchen dann eigene App-Passwörter:\n\n"
                                "  Ticker: BLUESKY_TICKER_APP_PASSWORD\n"
                                "  Feed:   BLUESKY_FEED_APP_PASSWORD")
            else:
                set_value(lines, "feed", "bluesky_handle", "")


def m_posts(lines):
    """Post texts and the profile status line."""
    while True:
        profile_on = read_value(lines, "profile", "enabled")
        choice = tui.menu(
            "Beiträge und Profil", "Wie die Beiträge aussehen und was in der Bio steht.",
            [("tag", f"Dauer-Hashtag .....  {_show(lines, 'post', 'standing_hashtag', '– keiner –')}"),
             ("kopf", f"Kopfzeile .........  {_show(lines, 'post', 'prefix', short=30)}"),
             ("hinweis", f"Kopfzeile zeigen ..  "
                         f"{NOTICE_LABELS.get(read_value(lines, 'post', 'bot_notice'), 'immer')}"),
             ("vorlage", f"Quellen-Vorlage ...  {_show(lines, 'post', 'source_template', short=30)}"),
             ("quelle", f"Quelle (Ticker) ...  {_show(lines, 'post', 'source_label', short=30)}"),
             ("quelle_feed", f"Quelle (Feed) .....  {_show(lines, 'feed', 'source_label', short=30)}"),
             ("profil", f"Statuszeile .......  {'ein' if profile_on != 'false' else 'aus'}")])
        if choice is None:
            return
        if choice == "tag":
            value = tui.ask("Dauer-Hashtag",
                             "Steht unter JEDEM Beitrag, ohne # (leer = keiner):",
                             read_value(lines, "post", "standing_hashtag"))
            if value is not None:
                set_value(lines, "post", "standing_hashtag", value.lstrip("#"))
        elif choice == "kopf":
            value = tui.ask("Kopfzeile",
                             "Erste Zeile jedes Hauptbeitrags\n"
                             "(leer = keine Kopfzeile):",
                             read_value(lines, "post", "prefix"))
            if value is not None:
                set_value(lines, "post", "prefix", value)
        elif choice == "hinweis":
            gewaehlt = tui.menu(
                "Kopfzeile zeigen",
                "Steht in der Bluesky-Biografie schon deutlich, dass hier ein\n"
                "Bot schreibt, ist der Hinweis in jedem Beitrag verschenkter\n"
                "Platz - bei 300 Zeichen zählt das.",
                [(k, NOTICE_LABELS[k]) for k in ("always", "auto", "never")],
                read_value(lines, "post", "bot_notice") or "always")
            if gewaehlt is not None:
                set_value(lines, "post", "bot_notice", gewaehlt)
                if gewaehlt == "auto":
                    value = tui.ask(
                        "Wonach in der Biografie suchen?",
                        "Die eigene Statuszeile wird dabei übergangen -\n"
                        "sonst fände sich \"Bot\" immer im eigenen\n"
                        "\"Bot ist an\".",
                        read_value(lines, "post", "bot_notice_marker") or "Bot")
                    if value:
                        set_value(lines, "post", "bot_notice_marker", value)
        elif choice == "vorlage":
            value = tui.ask(
                "Quellen-Vorlage",
                "Der Teil in [eckigen Klammern] wird zum Link,\n"
                "{label} steht für die Beschriftung.\n\n"
                "  🔗 [Quelle]: {label}   das Wort ist der Link\n"
                "  🔗 [Quelle]            nur das Wort, spart Zeichen\n"
                "  🔗 Quelle: [{label}]   die Beschriftung ist der Link",
                read_value(lines, "post", "source_template")
                or layout.DEFAULT_SOURCE_TEMPLATE)
            if value is not None:
                set_value(lines, "post", "source_template", value)
        elif choice == "quelle":
            value = tui.ask("Quell-Beschriftung (Ticker)",
                             "Text des Links zum WhatsApp-Kanal\n"
                             "(leer = keine Quellenangabe im Beitrag):",
                             read_value(lines, "post", "source_label"))
            if value is not None:
                set_value(lines, "post", "source_label", value)
        elif choice == "quelle_feed":
            value = tui.ask("Quell-Beschriftung (Feed)",
                             "Text des Links zum Instagram-Beitrag\n"
                             "(leer = keine Quellenangabe im Beitrag):",
                             read_value(lines, "feed", "source_label"))
            if value is not None:
                set_value(lines, "feed", "source_label", value)
        elif choice == "profil":
            if tui.confirm("Profil-Statuszeile",
                           "Soll die erste Zeile der Bluesky-Biografie anzeigen,\n"
                           "ob der Bot gerade läuft?", profile_on != "false"):
                set_value(lines, "profile", "enabled", "true")
                for key, label in (("line_on", "Text während des Betriebs"),
                                   ("line_off", "Text nach dem Beenden")):
                    value = tui.ask(label,
                                     "Platzhalter: {info}, {hashtag}, {date}, {time}",
                                     read_value(lines, "profile", key))
                    if value:
                        set_value(lines, "profile", key, value)
            else:
                set_value(lines, "profile", "enabled", "false")


def m_times(lines):
    """Time window and time zone."""
    choice = tui.menu("Zeitfenster", "Wann der Ticker arbeitet.",
                     [("ende", f"Betriebsende ......  {_show(lines, 'schedule', 'day_end')}"),
                      ("zone", f"Zeitzone ..........  {_show(lines, 'team', 'timezone')}")])
    if choice == "ende":
        value = tui.ask("Betriebsende",
                         "Bis zu dieser Uhrzeit lauscht der Ticker (HH:MM),\n"
                         "danach beendet er sich selbst:",
                         read_value(lines, "schedule", "day_end"))
        if value:
            set_value(lines, "schedule", "day_end", value)
    elif choice == "zone":
        value = tui.ask("Zeitzone", "z.B. Europe/Berlin:",
                         read_value(lines, "team", "timezone"))
        if value:
            set_value(lines, "team", "timezone", value)


def m_check_login(lines):
    """Check the login to Bluesky."""
    ticker = read_value(lines, "bluesky", "handle")
    feed = read_value(lines, "feed", "bluesky_handle")
    reports = []
    for handle, variable in ((ticker, "BLUESKY_TICKER_APP_PASSWORD"),
                             (feed, "BLUESKY_FEED_APP_PASSWORD")):
        if not handle:
            continue
        password = os.environ.get(variable) or os.environ.get("BLUESKY_APP_PASSWORD")
        if not password:
            reports.append(f"@{handle}\n   übersprungen – {variable} ist nicht gesetzt")
            continue
        try:
            from atproto import Client
            Client().login(handle, password)
            reports.append(f"@{handle}\n   Anmeldung erfolgreich")
        except Exception as error:
            reports.append(f"@{handle}\n   FEHLER: {str(error)[:60]}")
    tui.message("Anmeldung geprüft",
                "\n\n".join(reports) or "Es ist noch kein Konto eingetragen.")


def m_add_missing(lines):
    """Add missing keys from the template - along with their explanations.

    Works on the state inside the assistant, not on the file: as always,
    saving happens through the menu entry for it."""
    draft = list(lines)
    added = config.add_missing_keys(draft, BASE_DIR)
    if not added:
        tui.message("Nichts nachzuziehen",
                    "Alle Schlüssel der Vorlage stehen bereits in der "
                    "Konfiguration.")
        return

    items = "\n".join(f"  [{a}] {s} = {w}" for a, s, w in added[:18])
    if len(added) > 18:
        items += f"\n  … und {len(added) - 18} weitere"
    if not tui.confirm("Konfiguration nachziehen",
                       f"{len(added)} Schlüssel fehlen. Sie werden mit den "
                       f"Erklärungen aus der Vorlage ergänzt; vorhandene Werte "
                       f"bleiben unverändert.\n\n{items}\n\nErgänzen?", True):
        return
    lines[:] = draft
    tui.message("Nachgezogen",
                f"{len(added)} Schlüssel ergänzt.\n\n"
                f"Noch nicht gespeichert - das erledigt der Menüpunkt "
                f"\"Speichern und beenden\".")


def m_check_config(lines):
    """Check the configuration against the sources and the template. What is
    checked is the current state in the assistant, unsaved changes included."""
    findings = config.collect_findings(BASE_DIR, "".join(lines))
    problems = [t for severity, t in findings if severity == "problem"]
    notes = [t for severity, t in findings if severity == "note"]
    if not findings:
        tui.message("Konfiguration geprüft", "Keine Auffälligkeiten.")
        return
    parts = []
    if problems:
        parts.append("Probleme:\n" + "\n".join(f"  ✗ {t}" for t in problems))
    if notes:
        parts.append("Hinweise (Vorgaben greifen):\n"
                     + "\n".join(f"  ℹ {t}" for t in notes))
    tui.message("Konfiguration geprüft", "\n\n".join(parts))


# German labels for the layout matrix - interface, so German until #14.
BLOCK_LABELS = {
    "prefix": "Kopfzeile",
    "source": "Quelle",
    "match_hashtag": "Spiel-Hashtag",
    "standing_hashtag": "Dauer-Hashtag",
}
POST_LABELS = {"first": "erster", "last": "letzter", "all": "jeder", "none": "gar nicht"}
NOTICE_LABELS = {"always": "immer", "never": "nie",
                 "auto": "nur wenn die Bio ihn nicht nennt"}
SPOT_LABELS = {"top": "oben", "bottom": "unten"}


class _PreviewBuilder:
    """Stands in for atproto's TextBuilder so the assistant can render the same
    layout the bots would produce - without importing atproto."""

    def __init__(self):
        self.parts = []

    def text(self, piece):
        self.parts.append(piece)
        return self

    def link(self, piece, url):
        self.parts.append(piece)
        return self

    def tag(self, piece, value):
        self.parts.append(piece)
        return self

    def build_text(self):
        return "".join(self.parts)


def _layout_of(lines):
    """The layout as it currently stands in the assistant."""
    return layout.load_layout(
        lambda section, key, default=None: read_value(lines, section, key) or default,
        warn=lambda message: None)


def _layout_preview(lines, posts=2):
    """Renders an example thread with the layout as it stands."""
    current = _layout_of(lines)
    writers = {
        "prefix": layout.text_block(
            read_value(lines, "post", "prefix") or "⚽ [Inoffizieller Bot]"),
        "source": layout.source_block(
            read_value(lines, "post", "source_template")
            or layout.DEFAULT_SOURCE_TEMPLATE,
            read_value(lines, "post", "source_label") or "Original-Kanal",
            "https://whatsapp.com/channel/…"),
        "match_hashtag": layout.tag_block("DSCWOB"),
        "standing_hashtag": layout.tag_block(
            read_value(lines, "post", "standing_hashtag") or "arminia"),
    }
    parts = []
    for index in range(posts):
        body = ("Beispieltext." if posts == 1
                else f"Beispieltext, Teil {index + 1}. ({index + 1}/{posts})")
        tb = layout.build_post(_PreviewBuilder(), index, posts,
                               lambda builder, body=body: builder.text(body),
                               writers, current)
        parts.append(f"── Beitrag {index + 1} von {posts} " + "─" * 30 + "\n"
                     + tb.build_text())
    return "\n\n".join(parts)


def m_layout(lines):
    """Wo Kopfzeile, Quelle und Hashtags im Beitrag stehen (#6)."""
    while True:
        current = _layout_of(lines)
        entries = []
        for block in layout.LAYOUT_BLOCKS:
            selector, spot, order = current[block]
            entries.append((block,
                            f"{BLOCK_LABELS[block]:<14} "
                            f"{POST_LABELS[selector]:<9} "
                            f"{SPOT_LABELS[spot]:<6} {order}"))
        entries.append(("vorschau", "Vorschau ansehen …"))

        choice = tui.menu(
            "Aufbau der Beiträge",
            "Baustein       Beiträge  Stelle  Reihenfolge\n"
            "(Reihenfolge zählt nur, wenn zwei Bausteine an derselben Stelle "
            "landen.)",
            entries)
        if choice is None:
            return
        if choice == "vorschau":
            tui.message("So sähen die Beiträge aus", _layout_preview(lines), 22)
            continue

        selector, spot, order = current[choice]
        gewaehlt = tui.menu(
            f"{BLOCK_LABELS[choice]}: an welchen Beiträgen?",
            "Ein Thread entsteht, sobald ein Beitrag zu lang wird.",
            [(k, POST_LABELS[k]) for k in layout.POST_SELECTORS], selector)
        if gewaehlt is None:
            continue
        selector = gewaehlt

        if selector != "none":
            gewaehlt = tui.menu(f"{BLOCK_LABELS[choice]}: an welcher Stelle?",
                                "Über oder unter dem eigentlichen Text.",
                                [(k, SPOT_LABELS[k]) for k in layout.SPOTS], spot)
            if gewaehlt is None:
                continue
            spot = gewaehlt

            eingabe = tui.ask(f"{BLOCK_LABELS[choice]}: Reihenfolge",
                              "Kleinere Zahl steht weiter vorne:", str(order))
            if eingabe is None:
                continue
            try:
                order = int(eingabe)
            except ValueError:
                tui.message("Keine Zahl", f"{eingabe!r} ist keine Zahl - "
                                          f"die Reihenfolge bleibt bei {order}.")

        set_value(lines, "layout", choice, f"{selector} ; {spot} ; {order}")


def menu_mode():
    """The main menu - the way into the surface."""
    if not os.path.exists(TEMPLATE):
        print(f"Error: the template is missing: {TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    source = TARGET if os.path.exists(TARGET) else TEMPLATE
    with open(source, encoding="utf-8") as handle:
        lines = handle.readlines()
    saved = list(lines)

    while True:
        pending = []
        if _show(lines, "bluesky", "handle") == "– nicht gesetzt –":
            pending.append("Bluesky-Konto")
        channel = read_value(lines, "source", "channel_invite_link")
        if not channel or "HIER-DEN" in channel:
            pending.append("WhatsApp-Kanal")
        note_text = ("Noch offen: " + ", ".join(pending)) if pending else \
            "Alle Pflichtangaben sind gesetzt."
        asterisk = " *" if lines != saved else ""

        choice = tui.menu(
            "SkyRelay einrichten",
            f"{note_text}\n\nDatei: {os.path.basename(TARGET)}{asterisk}",
            [("1", "Spieltags-Ticker    WhatsApp-Kanal → Bluesky"),
             ("2", "Instagram-Feed      Instagram → Bluesky"),
             ("3", "Kürzel für Hashtags"),
             ("4", "Beiträge und Profil"),
             ("5", "Aufbau der Beiträge (Kopfzeile, Quelle, Hashtags)"),
             ("6", "Zeitfenster"),
             ("7", "Anmeldung bei Bluesky prüfen"),
             ("8", "Konfiguration prüfen"),
             ("9", "Konfiguration nachziehen"),
             ("0", "Speichern und beenden")],
            abbruch_text="Beenden")

        if choice == "1":
            m_ticker(lines)
        elif choice == "2":
            m_feed(lines)
        elif choice == "3":
            m_codes(lines)
        elif choice == "4":
            m_posts(lines)
        elif choice == "5":
            m_layout(lines)
        elif choice == "6":
            m_times(lines)
        elif choice == "7":
            m_check_login(lines)
        elif choice == "8":
            m_check_config(lines)
        elif choice == "9":
            m_add_missing(lines)
        elif choice == "0":
            if save(lines, saved):
                return
        else:  # quit or escape
            if lines == saved:
                return
            if tui.confirm("Ungespeicherte Änderungen",
                           "Es gibt Änderungen, die noch nicht gespeichert sind.\n\n"
                           "Jetzt speichern?", True):
                if save(lines, saved):
                    return
            else:
                return


def save(lines, saved):
    """Writes the configuration; makes a backup first."""
    ticker = read_value(lines, "bluesky", "handle")
    feed = read_value(lines, "feed", "bluesky_handle") or ticker
    profile_key = read_value(lines, "feed", "instagram_profile")
    summary = [f"Ticker:  WhatsApp-Kanal  →  @{ticker}" if ticker else "Ticker:  – kein Konto –"]
    if profile_key:
        summary.append(f"Feed:    @{profile_key}  →  @{feed}")
    if not tui.confirm("Speichern",
                       "\n".join(summary) + f"\n\nNach {os.path.basename(TARGET)} schreiben?"):
        return False

    if os.path.exists(TARGET):
        try:
            with open(TARGET + ".bak", "w", encoding="utf-8") as handle:
                handle.writelines(saved)
        except Exception as error:
            tui.message("Sicherung fehlgeschlagen", str(error))
    with open(TARGET, "w", encoding="utf-8") as handle:
        handle.writelines(lines)

    steps = ["Gespeichert: " + os.path.basename(TARGET), ""]
    if read_value(lines, "source", "channel_invite_link"):
        steps += ["Erste WhatsApp-Kopplung (interaktiv):",
                  "  SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 \\",
                  "     venv/bin/python skyrelay-matchday.py", ""]
    if profile_key:
        steps += ["Instagram-Sitzung anlegen (einmalig):",
                  f"  venv/bin/instaloader -l {read_value(lines, 'feed', 'instagram_session_user')}", ""]
    steps.append("Danach cron einrichten – siehe README.md")
    tui.message("Fertig", "\n".join(steps))
    return True


def add_missing_without_menu():
    """--add-missing: add missing keys straight into the file.

    For anyone who does not need the assistant at all - after an update on a
    server that gets by without whiptail, for instance."""
    def confirm_callback(added):
        print(f"{len(added)} key(s) are missing and will be added along with "
              f"their explanations:")
        for section, key, value in added:
            print(f"  [{section}] {key} = {value}")
        answer = input("\nAdd them? [y/N] ").strip().lower()
        return answer in ("y", "yes", "j", "ja")

    added, error = config.add_missing_keys_to_file(BASE_DIR, confirm_callback)
    # The wording has to match what the module returns - it used to say
    # "abgebrochen" and the check was left behind when it was translated.
    if error == "cancelled":
        print("Cancelled - the file is unchanged.")
        return 1
    if error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    if not added:
        print("Every key of the template is already in the configuration.")
        return 0
    print(f"\n{len(added)} key(s) added. "
          f"Backup: {os.path.basename(config.config_path(BASE_DIR))}.bak")
    return 0


if __name__ == "__main__":
    if "--add-missing" in sys.argv:
        sys.exit(add_missing_without_menu())
    try:
        # The menu surface, when whiptail is there and a terminal is attached.
        # SKYRELAY_SETUP_TEXT=1 forces the line by line questions.
        if (tui.available() and sys.stdin.isatty()
                and os.environ.get("SKYRELAY_SETUP_TEXT") != "1"):
            menu_mode()
        else:
            main()
    except KeyboardInterrupt:
        print("\n\nAbgebrochen - es wurde nichts geschrieben.\n")
        sys.exit(1)
