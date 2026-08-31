"""
SkyRelay - translations for the parts people read.

Only the interface is translated: the menus, questions and hints of the setup
assistant. The log and everything a bot writes to the console stays English on
purpose, so that the same message reads the same wherever it turns up - in a
mail from cron, in a pasted excerpt, in an issue.

The source language of the interface is GERMAN, while the code around it speaks
English. That split is deliberate and worth explaining, because it looks odd at
first glance:

  * The dialog was written in German, by the person who runs the thing. Those
    sentences are the original - not a translation of anything - and gettext
    wants the original as the msgid.
  * It also means the German interface needs no catalogue at all. On a machine
    where nobody ever ran tools/i18n.sh, the assistant still speaks proper
    German instead of falling back to a language nobody chose.
  * And it kept the change that introduced all this to wrapping strings in _(),
    which can be checked mechanically, rather than rewriting two hundred
    sentences by hand.

English therefore lives in locales/en, like any other language.

Reading needs nothing beyond the standard library. Writing a catalogue does -
see tools/i18n.sh.
"""

import gettext
import locale
import os

DOMAIN = "skyrelay"
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

# The language the interface is written in. It needs no catalogue.
SOURCE_LANGUAGE = "de"

_translation = gettext.NullTranslations()
_language = SOURCE_LANGUAGE


def _(message):
    """The translated text - or the German original when nothing is known.

    Deliberately a function rather than a name bound once: the language is only
    settled after the configuration has been read, and by then the modules have
    long been imported."""
    return _translation.gettext(message)


def _f(message, **values):
    """A translated text with values filled in.

    f-strings cannot be translated - the text is already assembled by the time
    anyone could look it up. So the sentence stays whole and named values go in
    afterwards, which also lets a translation move them around."""
    return _(message).format(**values)


def N_(message):
    """Marks a text for the catalogue without translating it here and now.

    Needed wherever a text is written down in one place and translated in
    another - a label in a dictionary, say. Babel reads the source, so it only
    finds what stands there literally; _(SOME_NAME) would leave the catalogue
    empty and the interface half German."""
    return message


def language():
    """Which language is in use right now."""
    return _language


def available():
    """Every language a catalogue has been compiled for, the source language
    first. That is what the assistant offers."""
    found = [SOURCE_LANGUAGE]
    try:
        for name in sorted(os.listdir(LOCALE_DIR)):
            catalogue = os.path.join(LOCALE_DIR, name, "LC_MESSAGES",
                                     DOMAIN + ".mo")
            if name != SOURCE_LANGUAGE and os.path.exists(catalogue):
                found.append(name)
    except OSError:
        pass
    return found


def _from_environment():
    """The language the system suggests - "de_DE.UTF-8" becomes "de"."""
    for name in ("SKYRELAY_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if value:
            return value.split(".")[0].split("_")[0].lower()
    try:
        value = locale.getdefaultlocale()[0]
    except (ValueError, TypeError):
        value = None
    return (value or SOURCE_LANGUAGE).split("_")[0].lower()


def use(configured=""):
    """Settles the language and returns the one actually in use.

    In order: what the configuration says, then the environment (SKYRELAY_LANG
    before the usual LC_ variables), then the source language."""
    global _translation, _language

    wanted = (configured or "").strip().lower() or _from_environment()

    if wanted == SOURCE_LANGUAGE:
        _translation = gettext.NullTranslations()
        _language = wanted
        return _language

    try:
        _translation = gettext.translation(DOMAIN, LOCALE_DIR, [wanted])
        _language = wanted
    except (FileNotFoundError, OSError):
        # No catalogue - which is the normal state right after a fresh clone,
        # because .mo files are built, not committed. Falling back to the
        # source language keeps the interface whole; half of it translated
        # would be worse than none.
        _translation = gettext.NullTranslations()
        _language = SOURCE_LANGUAGE
    return _language
