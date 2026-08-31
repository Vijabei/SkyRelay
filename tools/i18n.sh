#!/usr/bin/env bash
#
# SkyRelay - Übersetzungen
#
# Die Bedienoberfläche des Einrichtungsassistenten ist auf Deutsch geschrieben;
# jede weitere Sprache ist eine Übersetzung davon. Die fertigen Kataloge (.mo)
# werden gebaut, nicht mitgeliefert - im Repository liegen nur die .po-Dateien,
# die ein Mensch bearbeitet.
#
#   ./tools/i18n.sh extract        Texte aus dem Quelltext in die Vorlage ziehen
#   ./tools/i18n.sh update         vorhandene Übersetzungen an die Vorlage angleichen
#   ./tools/i18n.sh compile        .po -> .mo (das braucht das Programm)
#   ./tools/i18n.sh status         wie weit ist jede Sprache?
#   ./tools/i18n.sh add <kürzel>   neue Sprache anlegen, z.B. add nl
#
# Bearbeitet werden die .po-Dateien - mit Poedit, mit Weblate oder von Hand.
#
set -euo pipefail

HIER="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$HIER/venv/bin/python"
DOMAIN="skyrelay"
VORLAGE="$HIER/locales/$DOMAIN.pot"

if [ ! -x "$VENV_PY" ]; then
    echo "Kein venv gefunden - zuerst ./install.sh ausführen." >&2
    exit 1
fi
if ! "$VENV_PY" -m babel.messages.frontend --help >/dev/null 2>&1; then
    echo "Babel fehlt. Nachinstallieren mit:" >&2
    echo "  venv/bin/python -m pip install Babel" >&2
    exit 1
fi

babel() { "$VENV_PY" -m babel.messages.frontend "$@"; }

case "${1:-}" in
extract)
    mkdir -p "$HIER/locales"
    # Aus dem Projektverzeichnis heraus, damit in der Vorlage relative Pfade
    # stehen - absolute saehen bei jedem Uebersetzer anders aus und machten
    # jeden Extrakt zu einer Aenderung.
    ( cd "$HIER" && babel extract -F babel.cfg -k _ -k _f -k N_ \
        --copyright-holder="SkyRelay" --project="SkyRelay" \
        -o "locales/$DOMAIN.pot" . )
    echo "Vorlage geschrieben: locales/$DOMAIN.pot"
    ;;
update)
    babel update -i "$VORLAGE" -d "$HIER/locales" -D "$DOMAIN" --previous
    echo "Übersetzungen angeglichen - jetzt die .po-Dateien durchsehen."
    ;;
compile)
    babel compile -d "$HIER/locales" -D "$DOMAIN" --statistics
    ;;
status)
    # Ueber Babel statt ueber grep: Ein mehrzeiliges msgstr faengt selbst mit
    # msgstr "" an und wuerde als unuebersetzt gezaehlt.
    "$VENV_PY" - "$HIER/locales" "$DOMAIN" <<'PYENDE'
import os
import sys

from babel.messages.pofile import read_po

wurzel, domain = sys.argv[1], sys.argv[2]
for sprache in sorted(os.listdir(wurzel)):
    pfad = os.path.join(wurzel, sprache, "LC_MESSAGES", domain + ".po")
    if not os.path.exists(pfad):
        continue
    with open(pfad, encoding="utf-8") as datei:
        katalog = read_po(datei)
    texte = [eintrag for eintrag in katalog if eintrag.id]
    offen = [eintrag for eintrag in texte if not eintrag.string]
    hinweis = "" if offen else "  (vollstaendig)"
    print(f"  {sprache:<6} {len(texte):4d} Texte, {len(offen):4d} offen{hinweis}")
PYENDE
    ;;
add)
    [ $# -ge 2 ] || { echo "Sprachkürzel fehlt, z.B.:  ./tools/i18n.sh add nl" >&2; exit 1; }
    babel init -i "$VORLAGE" -d "$HIER/locales" -D "$DOMAIN" -l "$2"
    echo "Angelegt: locales/$2/LC_MESSAGES/$DOMAIN.po"
    ;;
*)
    sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
