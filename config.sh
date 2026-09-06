#!/usr/bin/env bash
#
# SkyRelay - Einrichtung
#
# Startet den Assistenten. Alles, was er braucht, steht im venv - fehlt das,
# sagt dieses Skript, was zu tun ist, statt mit einem Python-Fehler zu enden.
#
# Aufruf:
#   ./config.sh                 Menü (oder zeilenweise, wenn questionary fehlt)
#   ./config.sh --add-missing   nur fehlende Schlüssel nachtragen
#   ./config.sh --check         Konfiguration prüfen, nichts ändern
#
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

title "SkyRelay - Einrichtung"
printf 'Verzeichnis: %s\n' "$SCRIPT_DIR"

require_venv

# --check ist eine Abkürzung: Die Prüfung steckt in den Bots, nicht im
# Assistenten - sie braucht ja gerade den Blick von außen.
if [ "${1:-}" = "--check" ]; then
    exec "$VENV_PY" "$SCRIPT_DIR/skyrelay-feed.py" --check-config
fi

# Die Menüoberfläche steckt in questionary. Fehlt es, fragt der Assistent
# zeilenweise weiter - deshalb nur ein Hinweis, kein Abbruch.
if ! "$VENV_PY" -c 'import questionary' >/dev/null 2>&1; then
    warn "questionary fehlt - die Einrichtung läuft zeilenweise statt im Menü."
    printf '    Nachinstallieren mit:  %s -m pip install questionary\n' "$VENV_PY"
fi

if [ ! -t 0 ]; then
    warn "Kein Terminal - der Assistent kann nicht nachfragen."
    printf '    Über SSH läuft er, in einem cron-Job nicht.\n'
fi

exec "$VENV_PY" "$SCRIPT_DIR/skyrelay-setup.py" "$@"
