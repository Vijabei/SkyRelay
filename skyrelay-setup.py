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

LIGEN = [
    ("bl1", "1. Bundesliga"),
    ("bl2", "2. Bundesliga"),
    ("bl3", "3. Liga"),
]


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
    """Leitet ein Kürzel aus dem Kurznamen ab (Umlaute werden aufgelöst)."""
    name = team.get("shortName") or team.get("teamName") or ""
    normal = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    buchstaben = re.sub(r"[^A-Za-z]", "", normal)
    return (buchstaben[:3] or "XXX").upper()


def hole_teams(liga, saison):
    antwort = requests.get(
        f"https://api.openligadb.de/getavailableteams/{liga}/{saison}", timeout=20
    )
    antwort.raise_for_status()
    return antwort.json()


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

        titel("Verein")
        liga = auswahl([(k, b) for k, b in LIGEN] + [("andere", "andere Liga (Kürzel selbst eingeben)")],
                       "Liga", 1)
        if liga == "andere":
            hinweis("Ligakürzel wie in der OpenLigaDB-Adresse, z.B. 'bl4' oder 'dfb'.")
            liga = frage("Ligakürzel", "bl2", pflicht=True)
        saison = frage("Saison (Startjahr)", "2026", pflicht=True)

        try:
            teams = hole_teams(liga, saison)
        except Exception as fehler:
            print(f"  ✗ Abruf fehlgeschlagen: {fehler}")
            teams = []

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
            hinweis("Die Vorschläge sind aus den Vereinsnamen abgeleitet und weichen oft von")
            hinweis("den offiziellen ab (Hertha BSC ergibt 'HER', üblich wäre 'BSC').")
            hinweis("Damit die Hashtags zu denen des Vereins passen, lohnt das Anpassen.")
            codes = {t["teamId"]: kuerzel_vorschlag(t) for t in teams}
            # bereits vorhandene Kürzel haben Vorrang vor dem Vorschlag
            for team_id, code in [(k, v) for k, v in
                                  [(z.split("=")[0].strip(), z.split("=")[1].strip())
                                   for z in alt if re.match(r"^\d+\s*=", z)]]:
                if team_id.isdigit() and int(team_id) in codes:
                    codes[int(team_id)] = code

            eigenes = codes.get(gewaehlt["teamId"], "XXX")
            print(f"\n  Kürzel deines Vereins ({gewaehlt['teamName']}): {eigenes}")
            hinweis("Es steht in jedem Spiel-Hashtag - bitte genau prüfen.")
            codes[gewaehlt["teamId"]] = frage("Kürzel", eigenes, pflicht=True).upper()

            print("\n  Vorschläge für die übrigen Mannschaften:")
            for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                if team["teamId"] != gewaehlt["teamId"]:
                    print(f"    {codes[team['teamId']]:5} {team['teamName']}")
            if ja_nein("\n  Diese Kürzel einzeln anpassen?", False):
                for team in sorted(teams, key=lambda t: t.get("shortName") or ""):
                    if team["teamId"] == gewaehlt["teamId"]:
                        continue
                    codes[team["teamId"]] = frage(
                        team["teamName"], codes[team["teamId"]], pflicht=True).upper()
            else:
                hinweis("Später jederzeit im Abschnitt [team_codes] änderbar.")
            setze_team_codes(zeilen, codes)

        titel("Beiträge")
        marke = frage("Dauer-Hashtag (ohne #, leer = keiner)",
                      bisher("post", "standing_hashtag"))
        setze_wert(zeilen, "post", "standing_hashtag", marke)
        beschriftung = frage("Beschriftung des Quell-Links",
                             bisher("post", "source_label") or "WhatsApp-Kanal des Vereins")
        setze_wert(zeilen, "post", "source_label", beschriftung)

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
