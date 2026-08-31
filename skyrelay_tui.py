"""
SkyRelay - menu surface for the setup assistant

A thin wrapper around `whiptail`, which comes preinstalled on Debian and
Raspberry Pi OS and also drives `raspi-config`. That makes the setup feel
familiar, works over SSH, and lets people change single items instead of
working through fifteen questions in a row.

If whiptail is missing, `available()` says so and the assistant falls back to
asking line by line.

whiptail returns the selection on standard error and reports through its exit
status whether the dialog was cancelled (cancel button or escape).

The labels below are what the user sees, so they go through the translation
layer. They are looked up when a dialog opens, not when this module is
imported - at import time the language has not been settled yet.
"""

import shutil
import subprocess

from skyrelay_i18n import _, N_

WIDTH = 78
HEIGHT = 20

# N_ marks these for the catalogue; _() further down translates them when a
# dialog actually opens.
BACKTITLE = N_("SkyRelay - Einrichtung")
LABEL_SELECT = N_("Auswählen")
LABEL_BACK = N_("Zurück")
LABEL_CANCEL = N_("Abbrechen")


def available():
    """Can we use a menu surface at all?"""
    return shutil.which("whiptail") is not None


def _call(arguments, stdin=None):
    """Runs whiptail. Returns (cancelled, output)."""
    result = subprocess.run(
        ["whiptail", "--backtitle", _(BACKTITLE)] + arguments,
        stderr=subprocess.PIPE, input=stdin, text=True,
    )
    return result.returncode != 0, (result.stderr or "").strip()


def message(title, text, height=None):
    """A note with a single OK button."""
    lines = max(text.count("\n") + 7, 10)
    _call(["--title", title, "--msgbox", text, str(height or lines), str(WIDTH)])


def ask(title, text, default="", password=False):
    """Input field. Returns None if the dialog was cancelled - an empty string
    means the user deliberately cleared the value, which is not the same thing."""
    kind = "--passwordbox" if password else "--inputbox"
    lines = max(text.count("\n") + 9, 11)
    cancelled, value = _call(
        ["--title", title, kind, text, str(lines), str(WIDTH), default])
    return None if cancelled else value


def confirm(title, text, default=True):
    """Yes/no question. Returns True or False."""
    lines = max(text.count("\n") + 8, 10)
    arguments = ["--title", title]
    if not default:
        arguments.append("--defaultno")
    arguments += ["--yesno", text, str(lines), str(WIDTH)]
    cancelled, _ = _call(arguments)
    return not cancelled


def menu(title, text, entries, default=None, cancel_label=None):
    """Selection menu. `entries` is a list of (key, label).
    Returns the key, or None if cancelled."""
    visible = min(len(entries), HEIGHT - 8)
    arguments = ["--title", title, "--ok-button", _(LABEL_SELECT),
                 "--cancel-button", cancel_label or _(LABEL_BACK)]
    if default:
        arguments += ["--default-item", default]
    arguments += ["--menu", text, str(HEIGHT), str(WIDTH), str(visible)]
    for key, label in entries:
        arguments += [str(key), label]
    cancelled, value = _call(arguments)
    return None if cancelled else value


def choose(title, text, entries, default=None):
    """Like menu(), but for long lists (clubs, leagues) - uses the full window
    height and lets people jump by first letter."""
    visible = min(len(entries), 14)
    arguments = ["--title", title, "--ok-button", _(LABEL_SELECT),
                 "--cancel-button", _(LABEL_CANCEL)]
    if default:
        arguments += ["--default-item", str(default)]
    arguments += ["--menu", text, "22", str(WIDTH), str(visible)]
    for key, label in entries:
        arguments += [str(key), label[:WIDTH - 20]]
    cancelled, value = _call(arguments)
    return None if cancelled else value


def progress(text):
    """A short note without a button - for waits, such as network lookups."""
    subprocess.Popen(["whiptail", "--backtitle", _(BACKTITLE),
                      "--infobox", text, "8", str(WIDTH)])
