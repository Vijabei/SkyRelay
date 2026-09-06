"""
SkyRelay - the surface the setup assistant asks its questions through.

One module, seven functions, and every question the assistant asks goes
through them. That seam is the reason the surface could be swapped at all:
skyrelay-setup.py calls tui.menu() and gets a value back, and it does not care
what drew the menu.

It used to be whiptail - the same dialogs raspi-config uses. Those needed no
Python package, but they drew fixed boxes on a fixed grid, and picking one club
out of twenty meant arrowing through twenty. questionary lists are filtered by
typing, which is where the time actually went.

If questionary is missing, `available()` says so and the assistant falls back
to asking line by line - the same fallback that used to catch a missing
whiptail.

Cancelling is part of the contract: every function that returns a value returns
None when the user backs out, and an empty string is NOT that. Someone who
clears a field means "no value", which is a different answer from "never mind".

The labels below are what the user sees, so they go through the translation
layer. They are looked up when a dialog opens, not when this module is
imported - at import time the language has not been settled yet.
"""

from skyrelay_i18n import _, N_

try:
    import questionary
    from questionary import Choice, Separator
except ImportError:  # pragma: no cover - depends on the installation
    questionary = None

# N_ marks these for the catalogue; _() further down translates them when a
# dialog actually opens.
LABEL_BACK = N_("Zurück")
LABEL_CANCEL = N_("Abbrechen")
LABEL_CONTINUE = N_("Weiter mit einer beliebigen Taste …")
LABEL_YES = N_("Ja")
LABEL_NO = N_("Nein")
# questionary writes its own "(Use arrow keys)" behind the question - in
# English, past the translation layer. Passing our own instruction is the only
# way to keep a German interface German. The brackets are added at the call
# site, so no translation can lose one of them.
HINT_KEYS = N_("Pfeiltasten, dann Enter")
HINT_FILTER = N_("Pfeiltasten oder tippen zum Filtern, dann Enter")

# Returned by a cancel entry, then turned into None. A private object rather
# than a string: no key of the assistant's can ever collide with it.
_CANCELLED = object()

# Sober and readable on the black-on-white and white-on-black terminals people
# actually use. No background colours - they are the first thing to look wrong
# on somebody else's palette.
_STYLE = None
if questionary is not None:
    _STYLE = questionary.Style([
        ("qmark", "bold"),
        ("question", "bold"),
        ("answer", "fg:#0087af"),
        ("pointer", "bold"),
        ("highlighted", "bold"),
        ("selected", "noreverse"),
        ("separator", "fg:#808080"),
        ("instruction", "fg:#808080"),
    ])

MARK = "›"


def available():
    """Can we use a menu surface at all?"""
    return questionary is not None


def _explain(text):
    """The sentences above a question.

    whiptail had room for them inside its box; here they are printed in front
    of the prompt, indented, so the question itself stays one short line."""
    if not text:
        return
    print()
    for line in str(text).split("\n"):
        print(f"  {line}")
    print()


def _answer(frage):
    """Runs one question. Returns None when the user backs out.

    unsafe_ask() rather than ask(): ask() prints "Cancelled by user" of its
    own accord, in English, past the translation layer."""
    try:
        return frage.unsafe_ask()
    except (KeyboardInterrupt, EOFError):
        return None


def message(title, text, height=None):
    """A note to be read, then acknowledged.

    `height` is ignored - it sized a whiptail box and has no meaning here. It
    stays in the signature so the twenty call sites did not have to change."""
    print()
    print(f"── {title}")
    _explain(text)
    _answer(questionary.press_any_key_to_continue(_(LABEL_CONTINUE),
                                                  style=_STYLE))


def confirm(title, text, default=True):
    """A yes/no question. Backing out counts as no.

    Two entries rather than questionary.confirm(): that one listens for "y"
    and "n", and this interface is written in German, where the obvious key is
    "j". A key that does nothing is worse than one more press of Enter - and
    picking from a list is the same gesture as everywhere else here."""
    _explain(text)
    answer = _answer(questionary.select(
        title,
        choices=[Choice(title=_(LABEL_YES), value=True),
                 Choice(title=_(LABEL_NO), value=False)],
        default=bool(default), qmark=MARK, pointer=MARK, style=_STYLE,
        use_jk_keys=False, show_selected=False,
        instruction=f"({_(HINT_KEYS)})"))
    return bool(answer)


def ask(title, text, default="", password=False):
    """Input field. Returns None if the question was cancelled - an empty
    string means the user deliberately cleared the value, which is not the
    same thing."""
    _explain(text)
    if password:
        return _answer(questionary.password(title, qmark=MARK, style=_STYLE))
    return _answer(questionary.text(title, default=default or "",
                                    qmark=MARK, style=_STYLE))


def _pick(title, text, entries, default, cancel_label, filterable):
    """Shared by menu() and choose(): a list of (key, label), a value back.

    The way out is an entry of its own rather than a hidden Escape. whiptail
    showed a Cancel button, and a surface that only answers to a key nobody
    mentioned is a surface people get stuck in."""
    _explain(text)
    choices = [Choice(title=str(label), value=key) for key, label in entries]
    choices.append(Separator(" "))
    choices.append(Choice(title=cancel_label or _(LABEL_BACK), value=_CANCELLED))

    # A default that is not on the list would raise - a stored value can name a
    # league that has since been removed.
    keys = [key for key, _label in entries]
    chosen_default = default if default in keys else None
    if chosen_default is None and default is not None:
        chosen_default = str(default) if str(default) in keys else None

    answer = _answer(questionary.select(
        title, choices=choices, default=chosen_default,
        qmark=MARK, pointer=MARK, style=_STYLE,
        use_search_filter=filterable, use_jk_keys=not filterable,
        show_selected=False,
        instruction=f"({_(HINT_FILTER if filterable else HINT_KEYS)})"))
    return None if answer is _CANCELLED else answer


def menu(title, text, entries, default=None, cancel_label=None):
    """Selection menu. `entries` is a list of (key, label).
    Returns the key, or None if cancelled."""
    return _pick(title, text, entries, default, cancel_label, filterable=False)


def choose(title, text, entries, default=None):
    """Like menu(), but for long lists (clubs, leagues): typing filters them.

    That is the one thing the old surface could not do. Twenty clubs meant
    twenty presses of the arrow key; now three letters are enough."""
    return _pick(title, text, entries, default, _(LABEL_CANCEL), filterable=True)


def progress(text):
    """A short note without a prompt - for waits, such as network lookups."""
    print(f"  {text}", flush=True)
