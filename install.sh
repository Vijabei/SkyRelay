#!/usr/bin/env bash
#
# SkyRelay - Installation
#
# Prüft, was das System mitbringt, holt den neuesten Stand, legt ein venv an
# und installiert die Abhängigkeiten. Zum Schluss startet die Einrichtung.
#
# Ändert NICHTS am System: Fehlende Systempakete werden nur gemeldet, nicht
# automatisch installiert (kein sudo, keine Überraschungen auf fremden
# Rechnern).
#
# Aufruf:   ./install.sh
#
set -euo pipefail
# shellcheck source=common.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

PYTHON_MIN="3.10"

title "SkyRelay - Installation"
printf 'Verzeichnis: %s\n' "$SCRIPT_DIR"

# ---------------------------------------------------------------- 1. System
step "1/6  System prüfen"

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
step "2/6  Python prüfen"

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

# whiptail treibt die Menüoberfläche der Einrichtung an (wie bei raspi-config)
if command -v whiptail >/dev/null 2>&1; then
    ok "whiptail vorhanden (Menüoberfläche für die Einrichtung)"
else
    warn "whiptail fehlt - die Einrichtung läuft dann zeilenweise statt im Menü."
    printf '    Nachinstallieren mit:  sudo apt install whiptail\n'
fi

# libmagic steckt hinter python-magic, das neonize mitbringt. Fehlt die
# Bibliothek, scheitert schon "import neonize" - und zwar erst nach Minuten
# beim Import-Test am Ende. Deshalb hier, wo es noch nichts gekostet hat.
if ldconfig -p 2>/dev/null | grep -q 'libmagic\.so'; then
    ok "libmagic vorhanden (von neonize benötigt)"
else
    warn "libmagic fehlt - neonize lässt sich ohne die Bibliothek nicht laden."
    printf '    Nachinstallieren mit:  sudo apt install libmagic1\n'
fi

# ffmpeg wandelt Sprachnachrichten in Videos mit Wellenform - ohne das Werkzeug
# werden Sprachnachrichten aus dem Kanal übersprungen, alles andere läuft weiter.
if command -v ffmpeg >/dev/null 2>&1; then
    ok "ffmpeg vorhanden (Sprachnachrichten aus dem WhatsApp-Kanal)"
else
    warn "ffmpeg fehlt - Sprachnachrichten können nicht übertragen werden."
    printf '    Nachinstallieren mit:  sudo apt install ffmpeg\n'
fi

# Zeitzonendaten werden für die Spieltags-Logik gebraucht (zoneinfo)
if ! "$PYTHON_BIN" -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin')" 2>/dev/null; then
    warn "Zeitzonendaten fehlen - 'Europe/Berlin' ist nicht auflösbar."
    printf '    Beheben mit:  sudo apt install tzdata      (oder: pip install tzdata)\n'
else
    ok "Zeitzonendaten vorhanden"
fi

# ----------------------------------------------------------------- 3. Stand
step "3/6  Neuesten Stand holen"

# Eine Neuinstallation soll nie auf einem alten Arbeitsstand aufsetzen. Wer
# lokal etwas geändert hat, behält es - dann wird nur nicht geholt.
if ! in_git_repo; then
    warn "keine git-Arbeitskopie - es wird nichts geholt"
elif local_changes; then
    warn "lokale Änderungen vorhanden - es wird nichts geholt"
    git -C "$SCRIPT_DIR" status --short | sed 's/^/    /'
elif git -C "$SCRIPT_DIR" pull --ff-only --quiet; then
    ok "auf dem neuesten Stand"
else
    warn "git pull ist fehlgeschlagen - es wird mit dem vorhandenen Stand"
    printf '    weitergemacht. Meldung ansehen mit:  git pull --ff-only\n'
fi

# ---------------------------------------------------------------- 4. venv
step "4/6  Virtuelle Umgebung"

if [ -d "$VENV_DIR" ]; then
    if venv_usable; then
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
if ! venv_usable; then
    "$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
fi

venv_usable || abort "In der virtuellen Umgebung fehlt pip." \
    "Paket nachinstallieren (sudo apt install python3-venv), dann: rm -rf '$VENV_DIR' && ./install.sh"

# ---------------------------------------------------------------- 5. Pakete
step "5/6  Abhängigkeiten installieren"

"$VENV_PY" -m pip install --quiet --upgrade pip
ok "pip aktualisiert"

printf '  … installiere aus requirements.txt (das dauert auf einem Pi einige Minuten)\n'
if "$VENV_PY" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"; then
    ok "Alle Abhängigkeiten installiert"
else
    abort "Installation der Abhängigkeiten fehlgeschlagen." \
          "Vollständige Meldung anzeigen:  $VENV_PY -m pip install -r requirements.txt"
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

# ---------------------------------------------------------------- 6. Test
step "6/6  Installation prüfen"

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
    abort "Mindestens ein Modul lässt sich nicht importieren (Meldung siehe oben)." \
          "Steht dort 'libmagic', hilft:  sudo apt install libmagic1"
fi

# ---------------------------------------------------------------- Abschluss
step "Fertig"

if [ -t 0 ]; then
    printf '  Weiter mit der Einrichtung.\n'
    exec "$SCRIPT_DIR/config.sh"
fi

printf '  Kein Terminal - die Einrichtung wurde nicht gestartet.\n'
printf '  Sie braucht Rückfragen und läuft deshalb nur von Hand:\n\n'
printf '    ./config.sh\n\n'
