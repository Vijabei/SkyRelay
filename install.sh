#!/usr/bin/env bash
#
# SkyRelay - Installationshilfe
#
# Legt ein Python-venv an und installiert die Abhängigkeiten.
# Ändert NICHTS am System: Fehlende Systempakete werden nur gemeldet,
# nicht automatisch installiert (kein sudo, keine Überraschungen).
#
# Aufruf:   ./install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
PYTHON_MIN="3.10"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$1"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

abort() {
    fail "$1"
    [ $# -gt 1 ] && printf '    %s\n' "$2"
    exit 1
}

printf '\n\033[1mSkyRelay - Installation\033[0m\n'
printf 'Verzeichnis: %s\n' "$SCRIPT_DIR"

# ---------------------------------------------------------------- 1. System
step "1/5  System prüfen"

case "$(uname -s)" in
    Linux) ok "Betriebssystem: Linux" ;;
    *)     warn "Nicht getestet auf $(uname -s) - der Dauerbetrieb ist für Linux gedacht." ;;
esac

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64|aarch64|arm64)
        ok "Architektur: $ARCH"
        ;;
    armv7l|armv6l|i686|i386)
        abort "Architektur $ARCH wird nicht unterstützt." \
              "neonize (WhatsApp) liefert nur 64-Bit-Pakete. Auf dem Raspberry Pi ein 64-Bit-System (arm64) installieren."
        ;;
    *)
        warn "Unbekannte Architektur $ARCH - für neonize werden 64-Bit-Pakete benötigt."
        ;;
esac

# ---------------------------------------------------------------- 2. Python
step "2/5  Python prüfen"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

[ -z "$PYTHON_BIN" ] && abort "Kein Python $PYTHON_MIN oder neuer gefunden." \
    "Installieren mit:  sudo apt install python3 python3-venv"

ok "Python: $("$PYTHON_BIN" --version) ($(command -v "$PYTHON_BIN"))"

# Achtung Debian/Ubuntu: 'import venv' gelingt auch ohne das Paket python3-venv -
# es fehlt dann aber ensurepip, und das erzeugte venv hätte kein pip.
# Deshalb wird hier gezielt ensurepip geprüft.
if ! "$PYTHON_BIN" -c "import venv, ensurepip" 2>/dev/null; then
    PY_MM="$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    abort "venv/ensurepip fehlt - damit hätte die virtuelle Umgebung kein pip." \
          "Installieren mit:  sudo apt install python3-venv   (ggf. python${PY_MM}-venv)"
fi
ok "venv- und ensurepip-Modul vorhanden"

# Zeitzonendaten werden für die Spieltags-Logik gebraucht (zoneinfo)
if ! "$PYTHON_BIN" -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin')" 2>/dev/null; then
    warn "Zeitzonendaten fehlen - 'Europe/Berlin' ist nicht auflösbar."
    printf '    Beheben mit:  sudo apt install tzdata      (oder: pip install tzdata)\n'
else
    ok "Zeitzonendaten vorhanden"
fi

# ---------------------------------------------------------------- 3. venv
step "3/5  Virtuelle Umgebung"

VENV_PY="$VENV_DIR/bin/python"

# Ein venv gilt nur dann als brauchbar, wenn darin auch pip läuft. Ein
# abgebrochener Anlauf (z.B. ohne python3-venv) hinterlässt sonst eine Ruine,
# die beim nächsten Lauf stillschweigend weiterverwendet würde.
venv_ist_brauchbar() {
    [ -x "$VENV_PY" ] && "$VENV_PY" -m pip --version >/dev/null 2>&1
}

if [ -d "$VENV_DIR" ]; then
    if venv_ist_brauchbar; then
        ok "venv existiert bereits: $VENV_DIR"
    else
        warn "Vorhandenes venv ist unvollständig (kein pip) - wird neu angelegt"
        rm -rf "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
        ok "venv neu angelegt: $VENV_DIR"
    fi
else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "venv angelegt: $VENV_DIR"
fi

# Letzter Rettungsversuch, falls pip trotzdem fehlt
if ! venv_ist_brauchbar; then
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

venv_ist_brauchbar || abort "In der virtuellen Umgebung fehlt pip." \
    "Paket nachinstallieren (sudo apt install python3-venv), dann: rm -rf '$VENV_DIR' && ./install.sh"

# ---------------------------------------------------------------- 4. Pakete
step "4/5  Abhängigkeiten installieren"

"$VENV_PY" -m pip install --quiet --upgrade pip
ok "pip aktualisiert"

printf '  … installiere aus requirements.txt (das dauert auf einem Pi einige Minuten)\n'
if "$VENV_PY" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"; then
    ok "Alle Abhängigkeiten installiert"
else
    abort "Installation der Abhängigkeiten fehlgeschlagen." \
          "Vollständige Meldung anzeigen:  $VENV_PY -m pip install -r requirements.txt"
fi

# ---------------------------------------------------------------- 5. Test
step "5/5  Installation prüfen"

if "$VENV_PY" - <<'PYCHECK'
import sys
fehler = []
for modul, zweck in [
    ("neonize", "WhatsApp-Kanal"),
    ("atproto", "Bluesky"),
    ("PIL", "Bildverarbeitung"),
    ("requests", "HTTP"),
    ("segno", "QR-Code"),
    ("instaloader", "Instagram"),
]:
    try:
        __import__(modul)
    except Exception as e:
        fehler.append(f"{modul} ({zweck}): {e}")
if fehler:
    print("\n".join("    " + f for f in fehler), file=sys.stderr)
    raise SystemExit(1)
PYCHECK
then
    ok "Alle Module importierbar"
else
    abort "Mindestens ein Modul lässt sich nicht importieren (Meldung siehe oben)."
fi

# ---------------------------------------------------------------- Abschluss
cat <<HINWEISE

$(printf '\033[1mFertig.\033[0m') Nächste Schritte:

  1. Konfiguration anlegen - am einfachsten mit dem Assistenten:
       venv/bin/python skyrelay-setup.py
     Er sucht den Verein bei OpenLigaDB, füllt die Kürzeltabelle vor und
     schreibt die fertige skyrelay.conf.
     (Wer lieber von Hand arbeitet: cp skyrelay.conf.example skyrelay.conf)

  2. Bluesky-App-Passwort als Umgebungsvariable bereitstellen
     (niemals in die Konfiguration oder ins Repository schreiben):
       export BLUESKY_APP_PASSWORD="xxxx-xxxx-xxxx-xxxx"

  3. Erste WhatsApp-Kopplung - muss interaktiv im Terminal laufen:
       SKYRELAY_FORCE=1 SKYRELAY_DRY_RUN=1 venv/bin/python skyrelay-matchday.py
     -> QR-Code mit dem Handy scannen (Verknüpfte Geräte -> Gerät hinzufügen)

  4. Danach Dauerbetrieb per cron einrichten - siehe README.md
     und CHEATSHEET-matchday.md

HINWEISE
