"""Is every text in English that should be?

SkyRelay's code, comments and logs are English. Text a person reads in the
interface is written in German and goes through the translation layer
(`_()`, `N_()`, `_f()`), which turns it into the configured language. German
anywhere else slips past that layer and stays German for everyone - a log
line, a comment, a label someone forgot to wrap.

Two rules:

  A  everywhere: German outside the translation layer, in strings,
     docstrings and comments. Recognised by common German function words,
     umlauts and ß, and capitalised nouns ending in -ung/-keit/-heit/-schaft.
  B  in the interface files (INTERFACE below): every text handed to an
     output function (print, note, heading, ask, confirm, ...) must go
     through _() or _f(). Pure command lines and placeholders are fine, and
     so, for now, is English text: moving the scripts to English is a step
     of its own.

Not covered: key labels in Textual's BINDINGS. Textual reads them at import,
before the language is set, so they stay German for now.

  C  everywhere: _() or _f() that runs at import - at module level, in a
     class body or as an argument default. The language is not set yet at
     that point, so the text stays German for good. Use N_() there and
     _() where the text is used.

Intentionally German text - post defaults such as "Bot ist an", comments
written into skyrelay.conf, test data - is marked with `# lang: de` on the
line where the string or the call starts.

Usage:
    venv/bin/python tools/check_language.py            # the default files
    venv/bin/python tools/check_language.py file.py    # just these

Exit code 0 when nothing is found, 1 otherwise.
"""
import ast
import io
import pathlib
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The bots, their modules and the setup assistant. tools/ and the shell
# scripts are still German and get their own pass later.
DEFAULT_FILES = [
    "skyrelay-matchday.py", "skyrelay-feed.py", "skyrelay-heartbeat.py",
    "skyrelay-testlauf.py", "skyrelay-setup.py", "skyrelay_setup_app.py",
    "skyrelay_common.py", "skyrelay_config.py", "skyrelay_health.py",
    "skyrelay_i18n.py", "skyrelay_layout.py", "tools/check_language.py",
]
INTERFACE = {"skyrelay-setup.py", "skyrelay_setup_app.py"}
UI_CALLS = {"print", "note", "heading", "ask", "confirm", "choose", "warn",
            "program_banner", "banner", "title", "Warten", "Hinweis", "Frage",
            "ok", "fail", "info", "notify", "Label", "Static", "Button",
            "Input", "Checkbox", "Header", "Footer"}

TRANSLATED = {"_", "N_", "_f", "ngettext", "gettext"}
# Defaults that are configuration content: German on purpose.
CONFIG_DEFAULTS = {"cfg", "cfg_int", "cfg_bool", "cfg_float", "current_value"}
MARK = "# lang: de"

# Words that are German and not also everyday English ("also", "bin", "die",
# "hat", "was" and the like stay out).
GERMAN_WORDS = set(  # lang: de
    """
aber alle allem allen aller alles als andere anderen anders auch auf aus bei
beide beiden beim bereits bis bisher bitte bleibt dabei dafür damit danach
dann daran darf darin das dass davon dem den denn der des dich dies diese
diesem diesen dieser dir doch dort durch ein eine einem einen einer eines
einmal erst es etwa etwas euch euer fehlt für gegen genau gibt hatte hier ich
ihm ihn ihr immer ist jede jedem jeden jeder jetzt kann kein keine keinen
können kommt läuft mehr mein mich mir mit muss müssen nach nicht nichts noch
nun nur ob oder ohne schon sehr sein seine sich sie sind soll sollte sowie
statt über und uns unter viel vom von vor waren weil weiter welche wenn wer
werden wie wieder wir wird wurde wurden zum zur zwei zwischen
""".split())
ENGLISH_WORDS = set("""
a an and are as at be but by can do for from has have if in is it its no not
of on one only or so than that the then there this to was what when which
with without you your
added already backup cancelled done error failed file found key keys missing
none run template unchanged warning will
""".split())
NOUN_ENDING = re.compile(r"\b[A-ZÄÖÜ][a-zäöüß]{3,}(?:ung|ungen|keit|heit|schaft)\b")
# Umlauts count in lower-case words only ("für", "nächstes"); a capitalised
# one is often a name ("1. FC Köln", "Preußen Münster").
UMLAUT_WORD = re.compile(r"\b[a-zäöüß]*[äöüß][a-zäöüß]*\b")  # lang: de
NOISE = re.compile(r"https?://\S+|\{[^}]*\}|`[^`]*`|\[[a-z_]+\]|\(\w{1,3}\)"
                   r"|\S+\.(?:py|sh|txt|md|json|sqlite3)\b|\S*/\S*")
WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")


def looks_german(text):
    text = NOISE.sub(" ", text)
    words = [w.lower() for w in WORD.findall(text)]
    german = sum(w in GERMAN_WORDS for w in words)
    german += bool(UMLAUT_WORD.search(text))
    german += len(NOUN_ENDING.findall(text))
    english = sum(w in ENGLISH_WORDS for w in words)
    return german >= 1 and german > english


def looks_english(text):
    words = [w.lower() for w in WORD.findall(NOISE.sub(" ", text))]
    english = sum(w in ENGLISH_WORDS for w in words)
    german = sum(w in GERMAN_WORDS for w in words)
    return english >= 1 and english > german


# Names that read the same in every language.
NAMES = {"bluesky", "instagram", "whatsapp", "openligadb", "skyrelay", "textual",
         "neonize", "whatsmeow", "instaloader", "cron", "uptime", "kuma",
         "healthchecks", "handle"}


def has_prose(text):
    """Words a person reads, as opposed to a command line, a placeholder or
    a product name."""
    text = NOISE.sub(" ", text)
    words = []
    for token in text.split():
        if re.search(r"[/=_.$]|^-", token):
            continue
        for word in token.strip(":,;!?()·→…'\"„“").split("-"):
            if WORD.fullmatch(word) and word.lower() not in NAMES:
                words.append(word)
    return len(words) >= 2 or any(len(w) > 3 for w in words)


def call_name(node):
    return getattr(node.func, "id", None) or getattr(node.func, "attr", None)


def strings_in(node):
    """The literal text of a string or f-string node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return " ".join(v.value for v in node.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return None


def check(path):
    source = (ROOT / path).read_text(encoding="utf-8")
    lines = source.splitlines()
    marked = {i + 1 for i, line in enumerate(lines) if MARK in line}
    tree = ast.parse(source)
    findings = []

    exempt = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and call_name(node) in TRANSLATED | CONFIG_DEFAULTS:
            exempt.update(id(n) for n in ast.walk(node))
        if isinstance(node, ast.Call) and node.lineno in marked:
            exempt.update(id(n) for n in ast.walk(node))

    # Rule A: German text outside the translation layer. The parts of an
    # f-string are judged together, as the f-string.
    inside_fstring = {id(v) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                      for v in node.values}
    for node in ast.walk(tree):
        if id(node) in exempt or id(node) in inside_fstring:
            continue
        text = strings_in(node)
        if text is None:
            continue
        if node.lineno in marked or getattr(node, "end_lineno", 0) in marked:
            continue
        if looks_german(text):
            findings.append((node.lineno, "A", text))

    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and MARK not in token.string \
                and token.start[0] not in marked and looks_german(token.string):
            findings.append((token.start[0], "A", token.string))

    # Rule B: interface text that bypasses the translation layer.
    if pathlib.Path(path).name in INTERFACE:
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and call_name(node) in UI_CALLS):
                continue
            if node.lineno in marked:
                continue
            for arg in list(node.args) + [k.value for k in node.keywords
                                          if k.arg in ("label", "title", "placeholder")]:
                for part in _plain_parts(arg):
                    text = strings_in(part)
                    # English text without _() is left to the move to
                    # English-only scripts; here it is about German text
                    # that the translation never sees.
                    if text and has_prose(text) and not looks_english(text) \
                            and id(part) not in exempt:
                        findings.append((part.lineno, "B", text))

    # Rule C: translated at import, before the language is set.
    for node in _import_time_calls(tree):
        if call_name(node) in ("_", "_f") and node.lineno not in marked:
            text = strings_in(node.args[0]) if node.args else None
            findings.append((node.lineno, "C", text or "_()"))

    seen, unique = set(), []
    for line, rule, text in sorted(findings):
        if (line, rule) not in seen:
            seen.add((line, rule))
            unique.append((line, rule, text))
    return unique


def _import_time_calls(tree):
    """Calls that run when the module is imported: everything outside
    function bodies and lambdas, argument defaults included."""
    found = []

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d]:
                visit(default)
            for decorator in node.decorator_list:
                visit(decorator)
            return
        if isinstance(node, ast.Lambda):
            return
        # if __name__ == "__main__": runs as a program, after the language
        # has been set.
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and getattr(node.test.left, "id", None) == "__name__":
            return
        if isinstance(node, ast.Call):
            found.append(node)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def _plain_parts(node):
    """String parts of an argument, through + concatenation and conditional
    expressions; translated calls inside are left alone."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _plain_parts(node.left) + _plain_parts(node.right)
    if isinstance(node, ast.IfExp):
        return _plain_parts(node.body) + _plain_parts(node.orelse)
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        return [node]
    return []


def main():
    files = sys.argv[1:] or DEFAULT_FILES
    total = 0
    for path in files:
        for line, rule, text in check(path):
            total += 1
            shown = " ".join(text.split())[:90]
            print(f"{path}:{line}: [{rule}] {shown}")
    if total:
        print(f"\n{total} finding(s). A = German outside the translation layer, "
              f"B = interface text without _(), C = translated at import (use "
              f"N_()). Intentional German: mark the line with '{MARK}'.")
        return 1
    print(f"✓ Nothing found in {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
