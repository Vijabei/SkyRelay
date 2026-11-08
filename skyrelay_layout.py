"""
SkyRelay - where the parts of a post go.

Deliberately free of third party imports, like skyrelay_config: the setup
assistant shows a live preview of the layout, and it must be able to do that
before atproto and Pillow are installed.
"""

import re
import unicodedata


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




# ============================ measuring a post ==============================
#
# Bluesky limits a post twice, and the lexicon of app.bsky.feed.post says so:
#
#     { "type": "string", "maxLength": 3000, "maxGraphemes": 300 }
#
# So 300 graphemes AND 3000 bytes. There is no endpoint to ask - the counter in
# the Bluesky app runs in the browser, and the only word the server would say is
# no, on the finished post. So we count ourselves, by the same rule: extended
# grapheme clusters as in UAX #29.

GRAPHEME_LIMIT = 300
BYTE_LIMIT = 3000

try:  # the regex module implements UAX #29 properly through \X
    import regex as _regex

    _CLUSTER = _regex.compile(r"\X")

    def grapheme_clusters(text):
        return _CLUSTER.findall(text)

    GRAPHEME_SOURCE = "regex"
except ImportError:
    # Without it, an approximation that covers what actually turns up in posts:
    # combining marks, emoji joined by a zero width joiner, variation selectors,
    # skin tones, keycaps and flags. It is deliberately not the full standard -
    # but it is never worse than counting code points, which is what happened
    # before, and the package stays optional so a pull without pip install
    # cannot stop the bots.
    _JOINER = "\u200d"
    _VARIATION = tuple(range(0xFE00, 0xFE10)) + tuple(range(0xE0100, 0xE01F0))
    _SKIN_TONE = tuple(range(0x1F3FB, 0x1F400))
    _REGIONAL = tuple(range(0x1F1E6, 0x1F200))

    def _continues(previous, char):
        """Does this character belong to the cluster that is already open?"""
        code = ord(char)
        if unicodedata.combining(char) or unicodedata.category(char) in ("Mn", "Me", "Mc"):
            return True
        if char == _JOINER or code in _VARIATION or code in _SKIN_TONE:
            return True
        if previous and previous[-1] == _JOINER:
            return True
        # Two regional indicators make one flag - but only two.
        if (code in _REGIONAL and previous and ord(previous[-1]) in _REGIONAL
                and len(previous) == 1):
            return True
        return False

    def grapheme_clusters(text):
        clusters = []
        for char in text:
            if clusters and _continues(clusters[-1], char):
                clusters[-1] += char
            else:
                clusters.append(char)
        return clusters

    GRAPHEME_SOURCE = "built in (regex is not installed)"


def grapheme_len(text):
    """As many characters as Bluesky counts."""
    return len(grapheme_clusters(text))


def utf8_len(text):
    """As many bytes as Bluesky counts."""
    return len(text.encode("utf-8"))


def fits(text):
    """Would Bluesky accept this as one post?"""
    return grapheme_len(text) <= GRAPHEME_LIMIT and utf8_len(text) <= BYTE_LIMIT


class PlainBuilder:
    """Collects what atproto's TextBuilder would produce, as plain text.

    Used to measure a post before it exists, and by the setup assistant for its
    preview - both need the finished wording without needing atproto."""

    def __init__(self):
        self.parts = []

    def text(self, piece):
        self.parts.append(piece)
        return self

    def link(self, piece, url):
        self.parts.append(piece)
        return self

    def tag(self, piece, value):
        self.parts.append(piece)
        return self

    def build_text(self):
        return "".join(self.parts)


def counter_suffix(index, total):
    """The " (2/3)" behind the body - nothing at all for a single post."""
    return "" if total <= 1 else f" ({index + 1}/{total})"


def post_overhead(index, total, writers, layout):
    """Everything this very post carries besides the body: the blocks the layout
    puts on it, their blank lines, and the counter. Measured by assembling the
    post with an empty body - so it can never drift from what is really sent."""
    built = build_post(PlainBuilder(), index, total,
                       lambda builder: builder.text(counter_suffix(index, total)),
                       writers, layout).build_text()
    return grapheme_len(built), utf8_len(built)


# --------------------------------------------------------- splitting a body

_URL = re.compile(r"https?://[^\s<>()\[\]]+")
# Where to break, best first. A break is only taken when it leaves at least
# MIN_FILL of the budget used - otherwise a single early paragraph mark would
# produce a two word post followed by a wall of text.
_BREAKS = ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ")
MIN_FILL = 0.55


def _fitting_cut(text, max_graphemes, max_bytes):
    """The offset up to which `text` still fits - always on a cluster boundary."""
    offset = 0
    graphemes = 0
    used_bytes = 0
    for cluster in grapheme_clusters(text):
        size = len(cluster.encode("utf-8"))
        if graphemes + 1 > max_graphemes or used_bytes + size > max_bytes:
            break
        offset += len(cluster)
        graphemes += 1
        used_bytes += size
    return offset


def _nicer_cut(text, hard_cut):
    """Moves the cut back to a boundary a reader would recognise."""
    minimum = int(hard_cut * MIN_FILL)
    for mark in _BREAKS:
        found = text.rfind(mark, 0, hard_cut)
        if found > minimum:
            return found + (len(mark) if mark.strip() else len(mark))
    return hard_cut


def _outside_urls(text, cut):
    """Never cut through a link: Bluesky does not shorten them, and half a URL
    is neither clickable nor readable. The whole link moves to the next post -
    unless it alone is longer than a post, where there is nothing to save."""
    for found in _URL.finditer(text):
        if found.start() < cut < found.end():
            return found.start() if found.start() > 0 else cut
    return cut


def _usable(budget):
    """A budget the blocks have used up entirely still leaves the body at least
    something - otherwise splitting would never end."""
    graphemes, size = budget
    return (graphemes, size) if graphemes > 0 and size > 0 else (1, 4)


def _split_once(text, budget_for, total, min_total):
    """One pass: cuts `text` into chunks, measuring each post as one of `total`.

    The last chunk is measured twice. As the post it is in a thread of `total`,
    and as the post it really becomes: the last one, in a thread of however
    many there turn out to be. Those are not the same post. The layout puts
    the hashtags on the last post only, so a first post in a thread of two has
    room the single post of a thread of one does not - and a text that fits
    the one but not the other used to swing between one and two posts on every
    pass, ending on whichever the last pass happened to produce (#8 follow-up:
    313 graphemes on 30.10.)."""
    chunks = []
    rest = text
    while rest:
        index = len(chunks)
        max_graphemes, max_bytes = _usable(budget_for(index, max(total, index + 1)))
        hard = _fitting_cut(rest, max_graphemes, max_bytes)
        if hard >= len(rest):
            final = max(index + 1, min_total)
            last_graphemes, last_bytes = _usable(budget_for(index, final))
            if _fitting_cut(rest, last_graphemes, last_bytes) >= len(rest):
                chunks.append(rest)
                break
            # Fits only as a post that something follows - so let something
            # follow, and cut it to suit both readings.
            hard = _fitting_cut(rest, min(max_graphemes, last_graphemes),
                                min(max_bytes, last_bytes))
        cut = _outside_urls(rest, _nicer_cut(rest, hard))
        cut = max(cut, 1)
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return [chunk for chunk in chunks if chunk]


def _all_fit(chunks, budget_for, min_total):
    """Does every chunk fit the post it really becomes?"""
    total = max(len(chunks), min_total)
    for index, chunk in enumerate(chunks):
        graphemes, size = budget_for(index, total)
        if grapheme_len(chunk) > max(graphemes, 1) or utf8_len(chunk) > max(size, 4):
            return False
    return True


def split_body(text, budget_for, rounds=8, min_total=1):
    """Splits `text` so that every finished post stays inside both limits.

    `budget_for(index, total)` says how many graphemes and bytes are left for
    the body of that post. Since the number of posts decides the width of the
    counter and which post counts as the last one, and that in turn changes the
    budget, this is settled by iterating until the number of posts the chunks
    were measured for is the number they make.

    `min_total` is for threads that are longer than their text: the feed gives
    every video a post of its own, and those posts carry the counter too. The
    text is then measured for a thread of at least that many posts.

    Whatever the iteration ends on is checked once more against the posts the
    chunks really become. Should that ever fail, every chunk is cut to the
    smallest budget it could meet - a post one or two words shorter than
    necessary is the lesser evil next to one Bluesky refuses."""
    text = text.strip()
    if not text:
        return []
    min_total = max(1, min_total)

    total = min_total
    chunks = [text]
    for _ in range(rounds):
        chunks = _split_once(text, budget_for, total, min_total)
        made = max(len(chunks), min_total)
        if made == total:
            break
        total = made
    if _all_fit(chunks, budget_for, min_total):
        return chunks

    def cautious(index, guess):
        options = [budget_for(index, t) for t in
                   {index + 1, guess, guess + 1, max(index + 1, min_total)}]
        return min(g for g, _b in options), min(b for _g, b in options)

    return _split_once(text, cautious, total, min_total)



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
    """A writer for a hashtag block - None when there is no tag.

    One tag or several: a club that labels its teams differently hands in a
    list, and they end up on one line separated by a space. Each one is written
    as its own tag, so every one of them stays clickable."""
    tags = [tag] if isinstance(tag, str) else list(tag or [])
    tags = [t.strip().lstrip("#") for t in tags if t and t.strip().strip("#")]
    if not tags:
        return None

    def write(tb):
        for position, one in enumerate(tags):
            if position:
                tb.text(" ")
            tb.tag(f"#{one}", one)

    return write


