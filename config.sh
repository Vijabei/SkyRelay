#!/usr/bin/env bash
#
# SkyRelay - Einrichtung
#
# Startet den Assistenten. Alles, was er braucht, steht im venv - fehlt das,
# sagt dieses Skript, was zu tun ist, statt mit einem Python-Fehler zu enden.
#
# Aufruf:
#   ./config.sh                 Fenster (oder zeilenweise, wenn textual fehlt)
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

# Das Fenster steckt in textual. Fehlt es, fragt der Assistent zeilenweise
# weiter - deshalb nur ein Hinweis, kein Abbruch.
if ! "$VENV_PY" -c 'import textual' >/dev/null 2>&1; then
    warn "textual fehlt - die Einrichtung läuft zeilenweise statt im Fenster."
    printf '    Nachinstallieren mit:  %s -m pip install textual\n' "$VENV_PY"
fi

# Zu wenig Farben? Dann sieht das Fenster grau aus, und niemand kaeme von
# selbst darauf, dass es an einer Umgebungsvariablen liegt.
case "$(farben)" in
wenige)
    warn "Dieses Terminal meldet nur 16 Farben - das Fenster wirkt dann grau."
    printf '    TERM=%s, COLORTERM ist nicht gesetzt.\n' "${TERM:-nicht gesetzt}"
    printf '    Kann dein Terminal mehr? Diese Zeile sollte orange sein:\n'
    printf '      \033[38;2;255;140;0m########  Testfarbe  ########\033[0m\n'
    printf '    Wenn ja, dauerhaft beheben mit:\n'
    printf "      echo 'export COLORTERM=truecolor' >> ~/.bashrc\n"
    printf '    Fuer diese Sitzung genuegt:  export COLORTERM=truecolor\n'
    ;;
256)
    warn "Dieses Terminal meldet 256 Farben - gedeckte Toene wirken flauer."
    printf '    Mehr davon:  export COLORTERM=truecolor\n'
    ;;
esac

if [ ! -t 0 ]; then
    warn "Kein Terminal - der Assistent kann nicht nachfragen."
    printf '    Über SSH läuft er, in einem cron-Job nicht.\n'
fi

exec "$VENV_PY" "$SCRIPT_DIR/skyrelay-setup.py" "$@"
