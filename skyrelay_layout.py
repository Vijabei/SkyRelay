"""
SkyRelay - where the parts of a post go.

Deliberately free of third party imports, like skyrelay_config: the setup
assistant shows a live preview of the layout, and it must be able to do that
before atproto and Pillow are installed.
"""

import re


# Where the parts of a post go used to be fixed: header and source at the top of
# the first post, hashtags at the bottom of the last. [layout] turns that into a
# decision - one line per block:
#
#     prefix = first ; top ; 1
#              ^post    ^spot ^order within that spot
#
# post: first | last | all | none        spot: top | bottom
#
# A block whose value is empty writes nothing at all, the way
# [post] standing_hashtag has always worked. Note the difference to commenting a
# line out: for configparser a missing key does not mean "off", it means "the
# program's default applies". What is in effect is what --show-config reports.

LAYOUT_BLOCKS = ("prefix", "source", "match_hashtag", "standing_hashtag")

# Line blocks each sit on a line of their own; tag blocks line up next to one
# another separated by a space - the way hashtags have always been written.
BLOCK_KINDS = {"prefix": "line", "source": "line",
               "match_hashtag": "tag", "standing_hashtag": "tag"}

# The defaults reproduce exactly what the two bots did before [layout] existed.
DEFAULT_LAYOUT = {
    "prefix": ("first", "top", 1),
    "source": ("first", "top", 2),
    "match_hashtag": ("last", "bottom", 1),
    "standing_hashtag": ("last", "bottom", 2),
}

POST_SELECTORS = ("first", "last", "all", "none")
SPOTS = ("top", "bottom")


def load_layout(cfg, warn=print):
    """Reads [layout] and returns {block: (post selector, spot, order)}.
    A line that cannot be read falls back to that block's default and says so -
    silently ignoring it would move a block without anyone noticing.

    `warn` takes the message; the bots pass their log function, the assistant
    something that fits its dialog."""
    layout = {}
    for block in LAYOUT_BLOCKS:
        fallback = DEFAULT_LAYOUT[block]
        raw = (cfg("layout", block, "") or "").strip()
        if not raw:
            layout[block] = fallback
            continue
        parts = [part.strip().lower() for part in raw.split(";")]
        if len(parts) != 3 or parts[0] not in POST_SELECTORS or parts[1] not in SPOTS:
            warn(f"⚠️ [layout] {block} = {raw!r} cannot be read (expected "
                f'"first|last|all|none ; top|bottom ; number") - falling back to '
                f"{fallback[0]} ; {fallback[1]} ; {fallback[2]}.")
            layout[block] = fallback
            continue
        try:
            order = int(parts[2])
        except ValueError:
            warn(f"⚠️ [layout] {block}: {parts[2]!r} is not a number - "
                f"using {fallback[2]}.")
            order = fallback[2]
        layout[block] = (parts[0], parts[1], order)
    return layout


def _blocks_at(layout, spot, index, total, writers):
    """The blocks belonging in this spot of this post, already in order."""
    chosen = []
    for block in LAYOUT_BLOCKS:
        selector, where, order = layout[block]
        if where != spot or writers.get(block) is None or selector == "none":
            continue
        if selector == "first" and index != 0:
            continue
        if selector == "last" and index != total - 1:
            continue
        chosen.append((order, block))
    # By order, and by block name where two share an order - so the result never
    # depends on the order the blocks happen to be listed in.
    chosen.sort()
    return [block for _order, block in chosen]


def _write_group(tb, blocks, writers):
    previous = None
    for block in blocks:
        if previous is not None:
            two_tags = BLOCK_KINDS[block] == "tag" and BLOCK_KINDS[previous] == "tag"
            tb.text(" " if two_tags else "\n")
        writers[block](tb)
        previous = block


def build_post(tb, index, total, write_body, writers, layout):
    """Assembles one post: the blocks for the top, the body, the blocks for the
    bottom. `writers` maps a block name to something that writes it into the
    TextBuilder, or to None when there is nothing to show for it."""
    top = _blocks_at(layout, "top", index, total, writers)
    bottom = _blocks_at(layout, "bottom", index, total, writers)
    if top:
        _write_group(tb, top, writers)
        tb.text("\n\n")
    write_body(tb)
    if bottom:
        tb.text("\n\n")
        _write_group(tb, bottom, writers)
    return tb


def text_block(text):
    """A writer for a plain text block - None when there is no text."""
    if not text:
        return None
    return lambda tb: tb.text(text)


# The part in [square brackets] becomes the link; {label} stands for the
# configured label.
_ANCHOR = re.compile(r"\[([^\]]*)\]")
# What is left dangling when {label} is empty: "🔗 [Quelle]: " -> "🔗 [Quelle]"
_DANGLING = re.compile(r"[\s:,\-]+$")

DEFAULT_SOURCE_TEMPLATE = "🔗 [Quelle]: {label}"


def source_block(template, label, url):
    """A writer for the source block - None when there is nothing to link.

    The template decides which words carry the link. Everything inside [square
    brackets] becomes the anchor, the rest stays plain text, and {label} is
    replaced by the configured label. So "🔗 [Quelle]: {label}" makes the word
    Quelle itself clickable and leaves the label beside it as text (#7) -
    before, the anchor was the label and the word in front of it was dead
    text."""
    if not url:
        return None

    text = (template or DEFAULT_SOURCE_TEMPLATE).replace("{label}", label or "")
    if not (label or "").strip():
        text = _DANGLING.sub("", text)
    text = text.strip()
    if not text:
        return None

    found = _ANCHOR.search(text)
    anchor = found.group(1).strip() if found else ""
    if anchor:
        before, after = text[:found.start()], text[found.end():]
    else:
        # No brackets, or empty ones: link the whole line rather than writing a
        # source that cannot be clicked.
        anchor = _ANCHOR.sub("", text).strip() or text
        before = after = ""

    def write(tb):
        if before:
            tb.text(before)
        tb.link(anchor, url)
        if after:
            tb.text(after)

    return write


def tag_block(tag):
    """A writer for a hashtag block - None when there is no tag."""
    if not tag:
        return None
    return lambda tb: tb.tag(f"#{tag}", tag)


