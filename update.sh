#!/usr/bin/env bash
#
# SkyRelay - Aktualisierung
#
# Holt den neuen Stand, zieht die Abhängigkeiten nach und trägt die
# Konfigurationsschlüssel nach, die seitdem dazugekommen sind.
#
# Laufende Dienste werden NICHT angefasst. Der nächste cron-Lauf nimmt den
# neuen Stand von selbst - ein Ticker, der gerade an einem Spieltag lauscht,
# läuft mit dem alten weiter, bis er sich abends beendet.
#
# Aufruf:   ./update.sh
#
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

title "SkyRelay - Aktualisierung"
printf 'Verzeichnis: %s\n' "$SCRIPT_DIR"

# ------------------------------------------------------- 1. Lage prüfen
step "1/4  Arbeitskopie prüfen"

in_git_repo || abort "Das hier ist keine git-Arbeitskopie." \
    "Ohne git gibt es nichts zu holen - dann von Hand aktualisieren."

if local_changes; then
    fail "Es gibt lokale Änderungen - sie würden beim Holen im Weg stehen."
    git -C "$SCRIPT_DIR" status --short | sed 's/^/    /'
    printf '\n    Entweder sichern und zurücknehmen:\n'
    printf '      git stash\n'
    printf '    oder behalten und selbst zusammenführen:\n'
    printf '      git pull --rebase\n'
    printf '\n    Die eigene skyrelay.conf ist nicht gemeint - die steht in\n'
    printf '    .gitignore und taucht hier gar nicht auf.\n'
    exit 1
fi
ok "keine lokalen Änderungen"

VORHER="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"

# ------------------------------------------------------- 2. Neuen Stand holen
step "2/4  Neuen Stand holen"

if ! git -C "$SCRIPT_DIR" pull --ff-only --quiet; then
    abort "git pull ist fehlgeschlagen." \
        "Meldung ansehen mit:  git -C '$SCRIPT_DIR' pull --ff-only"
fi
NACHHER="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"

if [ "$VORHER" = "$NACHHER" ]; then
    ok "war bereits aktuell"
    NEUES=0
else
    NEUES="$(git -C "$SCRIPT_DIR" rev-list --count "$VORHER..$NACHHER")"
    ok "$NEUES neue Änderung(en):"
    git -C "$SCRIPT_DIR" log --oneline "$VORHER..$NACHHER" | sed 's/^/    /'
fi

# ------------------------------------------------------- 3. Abhängigkeiten
step "3/4  Abhängigkeiten"

require_venv
if "$VENV_PY" -m pip install --quiet --upgrade -r "$SCRIPT_DIR/requirements.txt"; then
    ok "auf dem Stand von requirements.txt"
else
    abort "Die Abhängigkeiten ließen sich nicht installieren." \
        "Vollständige Meldung:  $VENV_PY -m pip install -r requirements.txt"
fi

# Uebersetzungen: .mo-Dateien werden gebaut, nicht mitgeliefert. Ohne sie
# spricht die Oberflaeche Deutsch - die Sprache, in der sie geschrieben ist.
if [ -x "$SCRIPT_DIR/tools/i18n.sh" ]; then
    if "$SCRIPT_DIR/tools/i18n.sh" compile >/dev/null 2>&1; then
        ok "Übersetzungen übersetzt"
    else
        warn "Übersetzungen konnten nicht gebaut werden - die Oberfläche"
        printf '    bleibt deutsch. Nachholen mit:  ./tools/i18n.sh compile\n'
    fi
fi

# ------------------------------------------------------- 4. Konfiguration
step "4/4  Konfiguration nachziehen"

if [ -t 0 ]; then
    "$VENV_PY" "$SCRIPT_DIR/skyrelay-setup.py" --add-missing
else
    warn "Kein Terminal - fehlende Schlüssel wurden nicht abgefragt."
    printf '    Später nachholen mit:  ./config.sh --add-missing\n'
fi

# ------------------------------------------------------------------ Abschluss
step "Fertig"
printf '  Was jetzt gilt, zeigt:\n'
printf '    ./config.sh --check\n'
printf '    venv/bin/python skyrelay-feed.py --show-config\n'
printf '\n  Laufende Dienste wurden nicht angefasst; der nächste cron-Lauf\n'
printf '  nimmt den neuen Stand von selbst.\n\n'
