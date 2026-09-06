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

import skyrelay_i18n
from skyrelay_i18n import _, _f, N_
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
    ("bl1", "2026", _("Fußball · 1. Bundesliga")),
    ("bl2", "2026", _("Fußball · 2. Bundesliga")),
    ("bl3", "2026", _("Fußball · 3. Liga")),
    ("dfb", "2026", _("Fußball · DFB-Pokal")),
    ("fbl1", "2025", _("Frauenfußball · 1. Bundesliga")),
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
            print(_("    Bitte etwas eingeben."))
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
    # Nicht "_" als Wegwerfname: das ist die Uebersetzungsfunktion, und
    # hier drin waere sie damit ueberschrieben.
    for number, (_schluessel, label) in enumerate(entries, 1):
        marker = " ←" if default_index == number - 1 else ""
        print(f"    {number:2}) {label}{marker}")
    default = str(default_index + 1) if default_index is not None else ""
    while True:
        entry = ask(text, default, required=True)
        if entry.isdigit() and 1 <= int(entry) <= len(entries):
            return entries[int(entry) - 1][0]
        print(_f("    Bitte eine Zahl zwischen 1 und {n} eingeben.",
                 n=len(entries)))


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


def read_team_codes(lines):
    """The [team_codes] table as {team number: code}.

    The keys are numbers, which is what tells them apart from every other
    table in the file - no other section is keyed by digits."""
    return {int(line.split("=")[0].strip()): line.split("=", 1)[1].strip()
            for line in lines if re.match(r"^\d+\s*=", line)}


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


def read_league_hashtags(lines):
    """The [league_hashtags] table as {shortcut: tag}."""
    table = {}
    section = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]")
        elif section == "league_hashtags" and "=" in stripped \
                and not stripped.startswith("#"):
            league, tag = stripped.split("=", 1)
            if league.strip():
                table[league.strip().lower()] = tag.strip().lstrip("#")
    return table


# A commented out entry, as the template carries two of them by way of
# example: "# bl2 = arminia". Prose that happens to contain an equals sign is
# not one - the part in front of it has to look like a key.
_EXAMPLE = re.compile(r"^#\s*[A-Za-z0-9_-]+\s*=")


def _is_example(line):
    """Is this a commented out entry rather than an explanation?

    Once real entries stand below them, the examples only confuse - two lines
    saying bl2, one of them without effect."""
    return bool(_EXAMPLE.match(line.strip()))


def set_league_hashtags(lines, table):
    """Replaces the contents of [league_hashtags], keeping its comments.

    Creates the section where there is none: it is younger than most
    configurations, and topping up does not reach free tables."""
    start = end = None
    for i, line in enumerate(lines):
        if line.strip() == "[league_hashtags]":
            start = i
        elif start is not None and line.startswith("[") and i > start:
            end = i
            break

    entries = [f"{league} = {tag}\n"
               for league, tag in sorted(table.items()) if tag]

    if start is None:
        # In front of [team_codes], where the template has it - both are
        # tables about the same fixture data and belong side by side.
        where = next((i for i, line in enumerate(lines)
                      if line.strip() == "[team_codes]"), len(lines))
        # German, like the rest of the file: this is a comment in the
        # configuration, not something the interface says.
        lines[where:where] = [
            "[league_hashtags]\n",
            "# Ein eigener Dauer-Hashtag je Liga - für Vereine, die ihre\n",
            "# Mannschaften unterschiedlich kennzeichnen (#arminia für die\n",
            "# Männer, #arminiafrauen für die Frauen).\n",
            "# Format:  <Ligakürzel> = <Hashtag ohne #>\n",
            "# Ohne passenden Eintrag gilt [post] standing_hashtag.\n",
        ] + entries + ["\n"]
        return True

    end = end if end is not None else len(lines)
    header = [z for z in lines[start:end]
              if (z.startswith("#") or z.strip() == "[league_hashtags]")
              and not (entries and _is_example(z))]
    lines[start:end] = header + entries + ["\n"]
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
        return _("Das sieht nicht nach einem Kanal-Link aus (erwartet: https://whatsapp.com/channel/…).")
    if "HIER-DEN" in value:
        return _("Das ist noch der Platzhalter.")
    return None


def check_handle(value):
    if value.startswith("@"):
        return _("Bitte ohne führendes @ angeben.")
    if "." not in value:
        return _("Erwartet wird ein vollständiges Handle, z.B. mein-bot.bsky.social")
    return None


def test_bluesky(handle, password_variable="BLUESKY_APP_PASSWORD"):
    """Checks the login without publishing anything."""
    password = os.environ.get(password_variable)
    if not password:
        note(_f("{variable} ist nicht gesetzt - Anmeldung wird nicht geprüft.",
                variable=password_variable))
        note(_f('Später setzen mit:  export {variable}="xxxx-xxxx-xxxx-xxxx"',
                variable=password_variable))
        return
    if not confirm(_f("Anmeldung bei Bluesky als {handle} jetzt prüfen?",
                      handle=handle), True):
        return
    try:
        from atproto import Client
        Client().login(handle, password)
        print(_f("  ✓ Anmeldung erfolgreich: {handle}", handle=handle))
    except ImportError:
        note(_("Paket 'atproto' fehlt - Prüfung übersprungen (./install.sh ausführen)."))
    except Exception as error:
        print(_f("  ✗ Anmeldung fehlgeschlagen: {error}", error=error))
        note(_("Handle und App-Passwort prüfen. Die Einrichtung läuft trotzdem weiter."))


# ------------------------------------------------------------------- flow
def main():
    print("\n\033[1mSkyRelay - Einrichtung\033[0m")
    print(_("Mit [Enter] wird jeweils der Wert in eckigen Klammern übernommen."))
    print(_("Abbruch jederzeit mit Strg+C - dann wird nichts geschrieben."))

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
        print(_f("\nVorhandene Konfiguration gefunden: {file}",
                 file=os.path.basename(TARGET)))
        note(_("Die bisherigen Werte stehen als Vorgabe bereit."))

    def current_value(section, key):
        return read_value(old_lines, section, key) if old_lines else ""

    # ------------------------------------------------- which programs?
    heading(_("Welche Programme möchtest du einrichten?"))
    note(_("SkyRelay besteht aus zwei getrennten Bots, die auch getrennte"))
    note(_("Bluesky-Konten benutzen können:"))
    print(_("     · Spieltags-Ticker: WhatsApp-Kanal → Bluesky, nur an Spieltagen"))
    print(_("     · Instagram-Feed:   Instagram → Bluesky, im Dauerbetrieb"))
    picked_programs = choose(
        [("both", _("Beide")),
         ("matchday", _("Nur den Spieltags-Ticker (WhatsApp-Kanal)")),
         ("feed", _("Nur die Instagram-Spiegelung"))],
        _("Auswahl"), 0,
    )
    does_matchday = picked_programs in ("both", "matchday")
    does_feed = picked_programs in ("both", "feed")
    parts = _("1 von 2: ") if picked_programs == "both" else ""
    ticker_handle = feed_handle = ""

    if does_matchday:
        program_banner(parts, "SPIELTAGS-TICKER",
                       _("WhatsApp-Kanal  ──►  Bluesky"), _("läuft nur an Spieltagen"))

    # ------------------------------------------------------ what it is for
    purpose = "sport_plan"
    if does_matchday:
        heading(_("Wofür wird der Ticker eingesetzt?"))
        note(_("Davon hängt ab, ob Spieltage automatisch erkannt werden können"))
        note(_("und wie die vorgeschlagenen Texte formuliert sind."))
        purpose = choose(
            [("sport_plan", _("Sport mit Spielplan bei OpenLigaDB (Fußball, Eishockey …)")),
             ("sport_no_schedule", _("Sport ohne Spielplan-Daten (z.B. Basketball, Handball-Liga)")),
             ("custom", _("anderer Zweck (Verein, Veranstaltung, Projekt …)"))],
            _("Auswahl"), 0,
        )
    generic = purpose == "custom"
    # Match the wording of the defaults to the purpose
    W = {
        "event": "Ereignis" if generic else _("Spiel"),
        "prefix": "📡 [Inoffizieller Bot]" if generic else "⚽ [Inoffizieller Bot]",
        "source": _("WhatsApp-Kanal") if generic else _("WhatsApp-Kanal des Vereins"),
        "line_on": (_("🟢 Bot ist an {hashtag}") if generic
               else _("🟢 Bot ist an - {info} {hashtag} ⚫⚪🔵")),
        "line_off": (_("🔴 Bot ist aus") if generic
                else _("🔴 Bot ist aus - nächstes Spiel {hashtag} ⚫⚪🔵")),
        "line_off_empty": _("🔴 Bot ist aus") if generic else _("🔴 Bot ist aus ⚫⚪🔵"),
        "fallback": "aktiv" if generic else "Testspiel",
    }

    # ---------------------------------------------------------- matchday
    if does_matchday:
        heading(_("Bluesky-Konto FÜR DEN TICKER"))
        note(_("Auf dieses Konto werden die Beiträge aus dem WhatsApp-Kanal"))
        note(_("veröffentlicht - ohne führendes @."))
        ticker_handle = ask("Handle", current_value("bluesky", "handle") or "mein-ticker.bsky.social",
                            required=True, validator=check_handle)
        set_value(lines, "bluesky", "handle", ticker_handle)

        heading(_("WhatsApp-Kanal (Quelle des Tickers)"))
        note(_("Im Handy: Kanal öffnen → Kanalnamen antippen → Teilen → Link kopieren."))
        link = ask("Einladungslink", current_value("source", "channel_invite_link"),
                   required=True, validator=check_channel_link)
        set_value(lines, "source", "channel_invite_link", link)

        league = season = None
        choice = "ohne"
        if purpose == "sport_plan":
            heading(_("Liga und Verein"))
            note(_("Daraus erkennt der Ticker, an welchen Tagen er überhaupt laufen muss,"))
            note(_("und bildet den Spiel-Hashtag. Grundlage ist OpenLigaDB."))
            choice = choose(
                [(k, b) for k, _, b in SUGGESTED_LEAGUES]
                + [("suchen", _("andere Liga aus OpenLigaDB wählen …")),
                   ("ohne", _("doch kein Spielplan – Ticker läuft an jedem Starttag"))],
                _("Liga"), 1,
            )

        if choice == "ohne":
            heading("Ohne Spielplan")
            note(_("Es findet keine automatische Prüfung statt, ob heute etwas ansteht:"))
            note(_("Der Ticker läuft an jedem Tag, an dem er gestartet wird."))
            note(_f("Ein wechselnder {event}-Hashtag lässt sich beim Start über",
                    event=W["event"]))
            note(_("SKYRELAY_HASHTAG mitgeben. Den cron-Eintrag also nur für die Tage"))
            note(_("einrichten, an denen etwas läuft - oder von Hand starten."))
            set_value(lines, "team", "openligadb_filter", "")
            set_value(lines, "team", "openligadb_team_id", "0")
        elif choice == "suchen":
            try:
                leagues = fetch_current_leagues()
            except Exception as error:
                print(f"  ✗ Abruf fehlgeschlagen: {error}")
                leagues = []
            if leagues:
                note(_f("{n} Ligen mit laufender Saison:", n=len(leagues)))
                picked = choose(
                    [(l, f'{(l.get("sport") or {}).get("sportName", "?")} · '
                         f'{l["leagueName"]} ({l["leagueShortcut"]}/{l["leagueSeason"]})')
                     for l in leagues],
                    _("Liga"),
                )
                league, season = picked["leagueShortcut"], str(picked["leagueSeason"])
            else:
                league = ask(_("Ligakürzel"), "bl2", required=True)
                season = ask(_("Saison (Startjahr)"), "2026", required=True)
        else:
            league = choice
            season = next(s for k, s, _ in SUGGESTED_LEAGUES if k == choice)
            season = ask(_("Saison (Startjahr)"), season, required=True)

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
                _("Dein Verein"), default,
            )
            set_value(lines, "team", "openligadb_team_id", chosen["teamId"])
            search_term = (chosen.get("shortName") or chosen["teamName"]).split()[-1].lower()
            search_term = ask(_("Suchbegriff für OpenLigaDB"), current_value("team", "openligadb_filter") or search_term,
                              required=True)
            set_value(lines, "team", "openligadb_filter", search_term)

            # ------------------------------------------- table of codes
            heading(_("Kürzel für die Hashtags"))
            note(_("Aus Heim- und Auswärtskürzel entsteht der Spiel-Hashtag, z.B. #KSCDSC."))
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
                note(_f("{known} Kürzel sind hinterlegt, {derived} aus dem "
                        "Namen abgeleitet (mit ? markiert).",
                        known=len(teams) - derived_count, derived=derived_count))
                note(_("Abgeleitete entsprechen oft nicht dem üblichen Kürzel - bitte prüfen."))
            else:
                note(_("Für alle Mannschaften dieser Liga sind Kürzel hinterlegt."))

            own = codes.get(chosen["teamId"], "XXX")
            print(_f("\n  Kürzel deines Vereins ({club}): {code}",
                     club=chosen["teamName"], code=own))
            note(_("Es steht in jedem Spiel-Hashtag - bitte genau prüfen."))
            codes[chosen["teamId"]] = ask(_("Kürzel"), own, required=True).upper()

            print(_("\n  Übrige Mannschaften (? = abgeleitet, ungeprüft):"))
            for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                if team["teamId"] != chosen["teamId"]:
                    mark = " " if taken[team["teamId"]] else "?"
                    print(f"   {mark} {codes[team['teamId']]:5} {team['teamName']}")
            if confirm(_("\n  Diese Kürzel einzeln anpassen?"), bool(derived_count)):
                for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                    if team["teamId"] == chosen["teamId"]:
                        continue
                    codes[team["teamId"]] = ask(
                        team["teamName"], codes[team["teamId"]], required=True).upper()
            else:
                note(_("Später jederzeit im Abschnitt [team_codes] änderbar."))
            set_team_codes(lines, codes)

        heading(_("Beiträge des Tickers"))
        note("Es gibt zwei Sorten Hashtags:")
        note(_("  · Dauer-Hashtag - steht unter JEDEM Beitrag (z.B. der Vereinsname)"))
        note(_f("  · {event}-Hashtag - wechselt je Termin", event=W["event"])
             + (_(" und wird aus dem Spielplan gebildet") if choice != "ohne"
                   else _(", kommt aus SKYRELAY_HASHTAG")))
        mark = ask(_("Dauer-Hashtag (ohne #, leer = keiner)"),
                   current_value("post", "standing_hashtag"))
        set_value(lines, "post", "standing_hashtag", mark)
        note(_("Mehrere Mannschaften mit eigenen Hashtags? Später im Menü "
               "unter „Beiträge und Profil“ → „Hashtag je Liga“."))
        set_value(lines, "post", "prefix", current_value("post", "prefix") or W["prefix"])
        label = ask("Beschriftung des Quell-Links",
                    current_value("post", "source_label") or W["source"])
        set_value(lines, "post", "source_label", label)

        # ------------------------------------------------ profile and time
        heading(_("Profil-Statuszeile des Ticker-Kontos"))
        note(_f("Die erste Zeile der Biografie von @{handle} kann anzeigen,",
                handle=ticker_handle))
        note(_("ob der Bot gerade läuft - beim Beenden wird sie zurückgestellt."))
        was_on = current_value("profile", "enabled")
        if confirm("Statuszeile verwenden?", was_on.lower() != "false" if was_on else True):
            set_value(lines, "profile", "enabled", "true")
            note("Platzhalter: {hashtag}" + (", {info} (z.B. '1. Spieltag'), {date}, {time}"
                                             if choice != "ohne" else ""))
            line_on = ask(_("Text während des Betriebs"),
                          current_value("profile", "line_on") or W["line_on"], required=True)
            line_off = ask(_("Text nach dem Beenden"),
                           current_value("profile", "line_off") or W["line_off"], required=True)
            set_value(lines, "profile", "line_on", line_on)
            set_value(lines, "profile", "line_off", line_off)
            set_value(lines, "profile", "line_off_no_match",
                      current_value("profile", "line_off_no_match") or W["line_off_empty"])
            note(_("Erkannt wird eine vorhandene Statuszeile am Text 'Bot ist'."))
            note(_("Wer andere Formulierungen nutzt, passt [profile] marker an."))
        else:
            set_value(lines, "profile", "enabled", "false")
        set_value(lines, "profile", "fallback_match_info",
                  current_value("profile", "fallback_match_info") or W["fallback"])

        heading(_("Zeitfenster"))
        note(_("Bis zu dieser Uhrzeit lauscht der Ticker, danach beendet er sich selbst."))
        set_value(lines, "schedule", "day_end",
                  ask("Betriebsende (HH:MM)", current_value("schedule", "day_end") or "23:59",
                       required=True))

    # ------------------------------------------------------------- feed
    if does_feed:
        program_banner(_("2 von 2: ") if picked_programs == "both" else "", "INSTAGRAM-SPIEGELUNG",
                       "Instagram  ──►  Bluesky", _("läuft im Dauerbetrieb"))

        heading(_("Instagram-Profil (Quelle des Feeds)"))
        profile_key = ask(_("Profil, das gespiegelt wird (ohne @)"),
                          current_value("feed", "instagram_profile"), required=True)
        set_value(lines, "feed", "instagram_profile", profile_key)

        note(_("Zweitkonto für den Abruf - NICHT das gespiegelte Profil."))
        note(_("Sitzung anlegen mit: venv/bin/instaloader -l <name>"))
        secondary_account = ask("Instagram-Zweitkonto", current_value("feed", "instagram_session_user"),
                                required=True)
        set_value(lines, "feed", "instagram_session_user", secondary_account)

        heading(_("Bluesky-Konto FÜR DEN FEED"))
        own_account = current_value("feed", "bluesky_handle")
        if does_matchday:
            note(_f("Der Ticker veröffentlicht auf @{handle}.",
                    handle=ticker_handle))
            own = confirm(_("Soll die Instagram-Spiegelung ein ANDERES Konto verwenden?"),
                          bool(own_account))
        else:
            own = True
        if own:
            note(_("Auf dieses Konto werden die Instagram-Beiträge veröffentlicht."))
            feed_handle = ask("Handle", own_account or current_value("bluesky", "handle")
                              or "mein-feed.bsky.social", required=True, validator=check_handle)
            if does_matchday:
                set_value(lines, "feed", "bluesky_handle", feed_handle)
                note(_("Dessen App-Passwort gehört in SKYRELAY_FEED_APP_PASSWORD,"))
                note(_("nicht in BLUESKY_APP_PASSWORD (das gilt für den Ticker)."))
            else:
                # Without the ticker the general account is the feed's as well.
                set_value(lines, "bluesky", "handle", feed_handle)
                set_value(lines, "feed", "bluesky_handle", "")
        else:
            feed_handle = ticker_handle
            set_value(lines, "feed", "bluesky_handle", "")

    # ---------------------------------------------------------- summary
    heading("Zusammenfassung")
    note(_("Bitte prüfen, ob Quellen und Konten richtig zugeordnet sind:"))
    if does_matchday:
        print(_f("  Spieltags-Ticker:  WhatsApp-Kanal  ──►  @{handle}",
                 handle=ticker_handle))
    if does_feed:
        print(_f("  Instagram-Feed:    @{profile}  ──►  @{handle}",
                 profile=profile_key, handle=feed_handle))
    if does_matchday and does_feed and ticker_handle == feed_handle:
        note(_("Beide veröffentlichen auf demselben Konto - das ist zulässig,"))
        note(_("führt aber dazu, dass Ticker und Feed sich vermischen."))
    if not confirm(_("\n  Stimmt das so?"), True):
        print(_("\nAbgebrochen - es wurde nichts geändert. Starte den Assistenten erneut."))
        return

    # ---------------------------------------------------------- writing
    heading(_("Speichern"))
    print(f"  Ziel: {TARGET}")
    if os.path.exists(TARGET):
        if not confirm(_("Vorhandene Konfiguration überschreiben? (Sicherung wird angelegt)"), True):
            print(_("\nAbgebrochen - es wurde nichts geändert."))
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

    print(_("\n\033[1mFertig.\033[0m Nächste Schritte:\n"))
    if does_matchday:
        print(f"  SPIELTAGS-TICKER (postet auf @{ticker_handle})")
        print(_("    Erste WhatsApp-Kopplung, interaktiv im Terminal:"))
        print("      SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py")
    if does_feed:
        print(f"\n  INSTAGRAM-FEED (postet auf @{feed_handle})")
        print("    Instagram-Sitzung einmalig anlegen:")
        print(f"      venv/bin/instaloader -l {secondary_account}")
    print(_("\n  Danach den Dauerbetrieb per cron einrichten - siehe README.md.\n"))


# ============================= the menu surface ===============================
# It feels like raspi-config: one main menu from which single areas can be
# changed on purpose - instead of fifteen questions in a row.


# German labels for the layout matrix - interface, so German until #14.
# N_ statt _: Der Text steht hier, uebersetzt wird beim Benutzen. Mit _() an
# dieser Stelle staende die Sprache schon beim Import fest - also bevor die
# Konfiguration ueberhaupt gelesen wurde.
BLOCK_LABELS = {
    "prefix": N_("Kopfzeile"),
    "source": N_("Quelle"),
    "match_hashtag": N_("Spiel-Hashtag"),
    "standing_hashtag": N_("Dauer-Hashtag"),
}
POST_LABELS = {"first": N_("erster"), "last": N_("letzter"), "all": N_("jeder"),
               "none": N_("gar nicht")}
SPOT_LABELS = {"top": N_("oben"), "bottom": N_("unten")}


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
            read_value(lines, "post", "source_label") or _("Original-Kanal"),
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
        parts.append(_f("── Beitrag {n} von {total} ", n=index + 1, total=posts)
                     + "─" * 30 + "\n" + tb.build_text())
    return "\n\n".join(parts)


def schreibe(lines, saved):
    """Writes the configuration, backing up the previous version first.
    Returns None when it worked, otherwise something to show the user.

    Deliberately without a question of its own: save() asks through the old
    surface, and a function that both asks and writes cannot be used by a
    window that has already asked."""
    if os.path.exists(TARGET):
        try:
            with open(TARGET + ".bak", "w", encoding="utf-8") as handle:
                handle.writelines(saved)
        except OSError as error:
            return _f("Die Sicherungskopie ließ sich nicht anlegen: {error}\n\n"
                      "Es wurde nichts geschrieben.", error=error)
    try:
        with open(TARGET, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
    except OSError as error:
        return _f("Schreiben fehlgeschlagen: {error}", error=error)
    return None


def naechste_schritte(lines):
    """What is left to do once the file is written."""
    schritte = [_f("Gespeichert: {datei}", datei=os.path.basename(TARGET)), ""]
    if read_value(lines, "source", "channel_invite_link"):
        schritte += [_("Erste WhatsApp-Kopplung (einmalig, interaktiv):"),
                     "  SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 \\",
                     "     venv/bin/python skyrelay-matchday.py", ""]
    if read_value(lines, "feed", "instagram_profile"):
        schritte += [_("Instagram-Sitzung anlegen (einmalig):"),
                     "  venv/bin/instaloader -l "
                     + (read_value(lines, "feed", "instagram_session_user")
                        or "<Zweitkonto>"), ""]
    schritte.append(_("Danach cron einrichten - siehe README.md"))
    return "\n".join(schritte)


def fenster_modus():
    """The window. Returns True if something was written.

    It gets this module handed to it: the application owns the screens, this
    file owns everything that touches the configuration, and neither has to
    know how the other does its job."""
    import sys as _sys

    import skyrelay_setup_app

    if not os.path.exists(TEMPLATE):
        print(f"Error: the template is missing: {TEMPLATE}", file=_sys.stderr)
        _sys.exit(1)
    source = TARGET if os.path.exists(TARGET) else TEMPLATE
    with open(source, encoding="utf-8") as handle:
        lines = handle.readlines()
    return skyrelay_setup_app.run(lines, _sys.modules[__name__])


def add_missing_without_menu():
    """--add-missing: add missing keys straight into the file.

    For anyone who does not need the assistant at all - from a cron job, or
    over a connection where a menu is more trouble than it is worth."""
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


# Sprachnamen in der jeweiligen Sprache - so findet sich auch jemand zurecht,
# der die gerade eingestellte nicht versteht.
LANGUAGE_NAMES = {"de": "Deutsch", "en": "English"}


def settle_language():
    """Picks the interface language before the first dialog appears.

    Read straight from the file rather than through the assistant's own line
    list: at this point that list does not exist yet, and every entry point -
    menu, line mode, --add-missing - has to end up in the same language."""
    configured = ""
    try:
        with open(TARGET, encoding="utf-8") as handle:
            configured = read_value(handle.readlines(), "general", "language")
    except OSError:
        pass
    return skyrelay_i18n.use(configured)


if __name__ == "__main__":
    settle_language()
    if "--add-missing" in sys.argv:
        sys.exit(add_missing_without_menu())
    try:
        # The window, when Textual is installed and a terminal is attached.
        # SKYRELAY_SETUP_TEXT=1 forces the line by line questions - for a
        # connection where a full screen application is more trouble than it
        # is worth, and for anyone who prefers being asked.
        # Das Fenster, wenn Textual da ist und ein Terminal daranhängt.
        # SKYRELAY_SETUP_TEXT=1 erzwingt die zeilenweise Abfrage - für eine
        # Verbindung, auf der ein Vollbild mehr stört als hilft, und für
        # jeden, der lieber gefragt wird.
        fenster = False
        if (sys.stdin.isatty()
                and os.environ.get("SKYRELAY_SETUP_TEXT") != "1"):
            try:
                import textual  # noqa: F401
                fenster = True
            except ImportError:
                print("Hinweis: Das Paket 'textual' fehlt - die Einrichtung "
                      "läuft zeilenweise.\n"
                      "Nachinstallieren mit:  venv/bin/pip install textual\n",
                      file=sys.stderr)

        if fenster:
            fenster_modus()
        else:
            main()
    except KeyboardInterrupt:
        print(_("\n\nAbgebrochen - es wurde nichts geschrieben.\n"))
        sys.exit(1)
