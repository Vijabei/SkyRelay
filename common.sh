# SkyRelay - shared ground for install.sh, config.sh and update.sh
#
# Sourced, never run on its own. It brings the paths, the little output helpers
# and the one question all three scripts have to ask: is there a usable venv?
#
# None of the three ever installs a system package. Missing ones are reported,
# not fixed - no sudo, no surprises on someone else's machine.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
VENV_PY="$VENV_DIR/bin/python"

ok()    { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }
step()  { printf '\n\033[1m%s\033[0m\n' "$1"; }
title() { printf '\n\033[1m%s\033[0m\n' "$1"; }

abort() {
    fail "$1"
    # Bewusst ein if statt "[ ... ] && printf": Als eigenstaendige Anweisung
    # unter "set -e" hat die Kurzform den Rueckgabewert verschluckt, wenn nur
    # eine Zeile uebergeben wurde - install.sh meldete dann einen Fehler und
    # endete trotzdem mit 0.
    if [ $# -gt 1 ]; then
        printf '    %s\n' "$2"
    fi
    exit 1
}

# A venv only counts as usable when pip runs inside it. An interrupted attempt -
# on Debian without python3-venv, say - leaves a ruin behind that would
# otherwise be reused silently.
venv_usable() {
    [ -x "$VENV_PY" ] && "$VENV_PY" -m pip --version >/dev/null 2>&1
}

require_venv() {
    venv_usable || abort "Es gibt noch keine brauchbare virtuelle Umgebung." \
        "Zuerst ./install.sh ausführen."
}

# Is this a git working copy, and is it untouched? Both scripts that pull need
# to know, and neither may overwrite someone's local edits.
in_git_repo() {
    git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

local_changes() {
    [ -n "$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null)" ]
}
