"""
SkyRelay - inspecting, checking and topping up the configuration.

Deliberately free of third party imports: the setup assistant must be able to
use these tools before atproto and Pillow are installed.

Which values the programs read is not kept by hand but derived from the sources.
A hand-kept list would be wrong after the first refactoring - and finding
exactly that kind of drift is what these tools are for.
"""

import ast
import configparser
import os

# Sections whose keys are free-form and therefore appear in no source file:
# [team_codes] holds OpenLigaDB team numbers.
FREE_SECTIONS = {"team_codes"}

SOURCE_FILES = {
    "skyrelay-matchday.py": "ticker",
    "skyrelay-feed.py": "feed",
    "skyrelay-setup.py": "assistant",
    "skyrelay-testlauf.py": "test run",
    "skyrelay_common.py": "shared module",
}

# The bots read through cfg("section", "key", default); the assistant works on
# raw lines through read_value(lines, "section", "key"). The number says how
# many arguments come before the section name.
READ_FUNCTIONS = {"cfg": 0, "cfg_int": 0, "cfg_bool": 0}
LINE_FUNCTIONS = {"read_value": 1, "set_value": 1,
                  # The German spellings stay listed until the assistant is
                  # translated as well; dropping them now would make every key
                  # it touches look unread.
                  "lies_wert": 1, "setze_wert": 1}

NO_DEFAULT = object()  # the default is not a literal, or there is none

# [layout] is read through cfg("layout", block, "") with block as a variable, so
# parsing the sources cannot find those keys - it would report every one of them
# as read by nobody. They are registered here instead.
from skyrelay_layout import LAYOUT_BLOCKS, DEFAULT_LAYOUT  # noqa: E402


def config_path(base_dir):
    """Path of the own configuration - in the program directory, or wherever
    SKYRELAY_CONFIG points (which is how several clubs run side by side)."""
    return os.environ.get("SKYRELAY_CONFIG") or os.path.join(base_dir, "skyrelay.conf")


# ------------------------------------------------------ accesses in the source
def _literal(node):
    """The value of a literal; NO_DEFAULT if the node is not one."""
    try:
        return ast.literal_eval(node)
    except Exception:
        return NO_DEFAULT


def accessed_keys(base_dir):
    """Works out from the sources which values are read.

    Returns {(section, key): {"programs": set, "default": value}}. The sources
    are parsed, not searched - so an example call inside a comment or a string
    does not count as a real access."""
    found = {}
    for filename, label in SOURCE_FILES.items():
        path = os.path.join(base_dir, filename)
        try:
            with open(path, encoding="utf-8") as source:
                tree = ast.parse(source.read(), path)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = getattr(node.func, "id", None)
            if function in READ_FUNCTIONS:
                offset = READ_FUNCTIONS[function]
            elif function in LINE_FUNCTIONS:
                offset = LINE_FUNCTIONS[function]
            else:
                continue
            if len(node.args) < offset + 2:
                continue

            section = _literal(node.args[offset])
            key = _literal(node.args[offset + 1])
            if not isinstance(section, str) or not isinstance(key, str):
                continue

            entry = found.setdefault((section, key),
                                     {"programs": set(), "default": NO_DEFAULT})
            entry["programs"].add(label)
            if function in READ_FUNCTIONS and len(node.args) > offset + 2:
                default = _literal(node.args[offset + 2])
                if default is not NO_DEFAULT and entry["default"] is NO_DEFAULT:
                    entry["default"] = default

    for block in LAYOUT_BLOCKS:
        selector, spot, order = DEFAULT_LAYOUT[block]
        found.setdefault(("layout", block),
                         {"programs": {"ticker", "feed"},
                          "default": f"{selector} ; {spot} ; {order}"})
    return found


# ----------------------------------------------------------- reading templates
def load_template(base_dir):
    """Reads skyrelay.conf.example in file order.

    Returns entries of (section, key, value, comment) - comment being the lines
    directly above the key. They travel along when keys are topped up, because
    without them a new key in someone's own file is a riddle."""
    path = os.path.join(base_dir, "skyrelay.conf.example")
    try:
        with open(path, encoding="utf-8") as template:
            lines = template.readlines()
    except OSError:
        return []

    entries = []
    section = None
    comment = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            comment = []
        elif stripped.startswith("#"):
            comment.append(line)
        elif stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.strip("[]")
            comment = []
        elif "=" in stripped and section is not None:
            key, _, value = stripped.partition("=")
            entries.append((section, key.strip(), value.strip(), comment))
            comment = []
    return entries


def _key_pairs(text):
    """Every (section, key) of a configuration."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return {(section, key)
            for section in parser.sections()
            for key in parser[section]}


def _read_own(base_dir, config_text=None):
    """(text, parser) of the own configuration - or (None, error message)."""
    if config_text is None:
        try:
            with open(config_path(base_dir), encoding="utf-8") as config:
                config_text = config.read()
        except OSError as error:
            return None, f"Cannot read the configuration: {error}"
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(config_text)
    except configparser.Error as error:
        return None, f"The configuration is broken: {error}"
    return config_text, parser


# ------------------------------------------------------------------ the check
def collect_findings(base_dir, config_text=None):
    """Compares the own configuration with what the programs read and with the
    template. Returns (severity, text) pairs; severity is "problem" or "note".
    Without config_text the file on disk is read - the assistant hands in its
    unsaved state instead."""
    text, parser = _read_own(base_dir, config_text)
    if text is None:
        return [("problem", parser)]

    own = _key_pairs(text)
    read = accessed_keys(base_dir)
    template = {(s, k) for s, k, _v, _c in load_template(base_dir)}

    findings = []
    if not template:
        findings.append(("note", "skyrelay.conf.example is missing or "
                                 "unreadable - skipping the comparison "
                                 "against the template."))

    # 1. Is something in the own file that nobody reads? That is how
    #    [post] prefix stayed ineffective in the feed for months.
    for section, key in sorted(own):
        if section in FREE_SECTIONS:
            continue
        if (section, key) not in read:
            findings.append(("problem",
                             f"[{section}] {key} is read by no program - "
                             f"a typo, or left over?"))

    # 2. Is something missing that is read? Then the default quietly applies.
    # 3. Is something missing from the template? Then nobody learns about it.
    for (section, key), entry in sorted(read.items()):
        who = ", ".join(sorted(entry["programs"]))
        if (section, key) not in own:
            findings.append(("note",
                             f"[{section}] {key} is missing - the program's "
                             f"default applies ({who})"))
        if template and (section, key) not in template:
            findings.append(("problem",
                             f"[{section}] {key} is missing from "
                             f"skyrelay.conf.example ({who})"))
    return findings


def check_config(base_dir):
    """Prints the report and returns the exit status: 0 = nothing wrong,
    1 = at least one problem. Connects to nothing and changes nothing."""
    print(f"Configuration: {config_path(base_dir)}")
    findings = collect_findings(base_dir)
    problems = [text for severity, text in findings if severity == "problem"]
    notes = [text for severity, text in findings if severity == "note"]

    for heading, entries, mark in (("Problems", problems, "✗"),
                                   ("Notes", notes, "ℹ")):
        if entries:
            print(f"\n{heading}:")
            for entry in entries:
                print(f"  {mark} {entry}")

    if not findings:
        print("\n✓ Nothing out of order.")
    else:
        print(f"\n{len(problems)} problem(s), {len(notes)} note(s).")
        if notes:
            print("Missing keys can be added along with their explanations:\n"
                  "    ./config.sh --add-missing")
    return 1 if problems else 0


# ---------------------------------------------------------- what is in effect?
def _shorten(value, width=60):
    """A single line, cut to width."""
    text = "" if value is None else str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[:width - 1] + "…"


def show_config(base_dir):
    """Lists every value the programs read, together with where it comes from:
    the own file, or the default built into the program.

    Without this it stays opaque what actually applies: a missing key does not
    stand out, because the default quietly steps in for it."""
    print(f"Configuration: {config_path(base_dir)}\n")

    text, parser = _read_own(base_dir)
    if text is None:
        print(parser)
        return 1

    read = accessed_keys(base_dir)
    template = load_template(base_dir)

    # Follow the template's order so the output resembles the file.
    order = [(s, k) for s, k, _v, _c in template if (s, k) in read]
    order += [pair for pair in sorted(read) if pair not in order]

    from_file = from_default = 0
    last_section = None
    # The origin goes BEFORE the value: emoji are double width in a terminal,
    # so a column after the value would drift out of line.
    width = max((len(k) for _s, k in order), default=20)
    for section, key in order:
        if section != last_section:
            print(f"[{section}]")
            last_section = section
        if parser.has_option(section, key):
            value = parser.get(section, key)
            origin = "(file)"
            from_file += 1
        else:
            default = read[(section, key)]["default"]
            value = "" if default is NO_DEFAULT else default
            origin = "(default)" if default is not NO_DEFAULT else "(default?)"
            from_default += 1
        shown = _shorten(value) if str(value) else "– empty –"
        print(f"  {key:<{width}}  {origin:<11}  {shown}")

    for section in sorted(FREE_SECTIONS):
        if parser.has_section(section):
            print(f"[{section}]")
            print(f"  – free table with {len(parser[section])} entries –")

    unknown = [(s, k) for s in parser.sections() if s not in FREE_SECTIONS
               for k in parser[s] if (s, k) not in read]
    if unknown:
        print("\nIn the file, but read by no program:")
        for section, key in unknown:
            print(f"  ✗ [{section}] {key}")

    print(f"\n{from_file} value(s) from the file, {from_default} from the "
          f"programs' defaults.")
    if from_default:
        print("Careful: a commented out line counts as missing, so the default\n"
              "applies and the setting is NOT switched off. To switch something\n"
              "off, leave the value empty (for example \"source_label =\").")
        print("Missing keys can be added along with their explanations:\n"
              "    ./config.sh --add-missing")
    return 0


# ------------------------------------------------------------ topping up keys
def _section_bounds(lines):
    """{section: (start, end)} - end is the index just past the last line."""
    bounds = {}
    current = None
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if current is not None:
                bounds[current] = (start, i)
            current = stripped.strip("[]")
            start = i
    if current is not None:
        bounds[current] = (start, len(lines))
    return bounds


def add_missing_keys(lines, base_dir):
    """Adds to `lines` every key the template knows and the own configuration
    does not - each with the comments that explain it.

    Existing values, order and comments are left alone; this only ever adds.
    Returns the list of added (section, key, value)."""
    present = _key_pairs("".join(lines))
    missing = [(s, k, v, c) for s, k, v, c in load_template(base_dir)
               if (s, k) not in present and s not in FREE_SECTIONS]
    if not missing:
        return []

    by_section = {}
    for section, key, value, comment in missing:
        by_section.setdefault(section, []).append((key, value, comment))

    bounds = _section_bounds(lines)
    # Insert from the back so the bounds worked out above stay valid.
    for section in sorted(by_section,
                          key=lambda s: bounds.get(s, (len(lines),))[0],
                          reverse=True):
        # Do not repeat comments the section already carries: someone who only
        # deleted the value line would otherwise get it explained twice.
        start_old, end_old = bounds.get(section, (0, 0))
        known = {line.strip() for line in lines[start_old:end_old]
                 if line.strip().startswith("#")}
        block = []
        for key, value, comment in by_section[section]:
            block.extend(line for line in comment if line.strip() not in known)
            block.append(f"{key} = {value}\n")

        if section not in bounds:
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append(f"[{section}]\n")
            lines.extend(block)
            continue

        start, end = bounds[section]
        at = end
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
        lines[at:at] = block

    return [(s, k, v) for s, k, v, _c in missing]


def add_missing_keys_to_file(base_dir, confirm=None):
    """Tops up the configuration file, writing a backup first.

    `confirm` receives the list of additions and decides whether to write.
    Without a confirmation function the file is written."""
    path = config_path(base_dir)
    try:
        with open(path, encoding="utf-8") as config:
            lines = config.readlines()
    except OSError as error:
        return None, f"Cannot read the configuration: {error}"

    draft = list(lines)
    added = add_missing_keys(draft, base_dir)
    if not added:
        return [], None
    if confirm is not None and not confirm(added):
        return None, "cancelled"

    try:
        with open(path + ".bak", "w", encoding="utf-8") as backup:
            backup.writelines(lines)
        with open(path, "w", encoding="utf-8") as config:
            config.writelines(draft)
    except OSError as error:
        return None, f"Writing failed: {error}"
    return added, None
