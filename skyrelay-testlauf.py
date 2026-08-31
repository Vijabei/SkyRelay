#!/usr/bin/env python3
"""
SkyRelay - test run: push real channel messages through the pipeline without
publishing anything.

Why this is needed: the built-in REPLAY mode fetches the LATEST N messages
(`MessageServerID(0)`). If a meta message without content sits among them, the
Go layer crashes hard - see ISSUE-DRAFT-neonize-newsletter-panic.md. As of
19.08.2026 that is exactly the situation: `count=1` works, from `count=2` it
blows up.

This tool instead pages BACKWARDS through the history in small blocks from an
explicit starting point, which reaches older messages without touching the
broken spot.

NOTHING is posted: SKYRELAY_DRY_RUN is forced before the ticker is loaded. The
watermark and the bookkeeping files stay untouched.

Server IDs differ per channel - find the starting point first, then search
backwards from there.

Examples:
    # 1. Find the starting point: print the latest server ID (the only fetch
    #    that is safe at the newest message)
    venv/bin/python3 skyrelay-testlauf.py --latest

    # 2. From there, search backwards for voice messages and stickers
    venv/bin/python3 skyrelay-testlauf.py --before <serverID> --type audio,sticker

    # anything at all, the first 5 hits
    venv/bin/python3 skyrelay-testlauf.py --before <serverID> --type all --count 5
"""
import argparse
import asyncio
import importlib.util
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# MUST be set before the ticker is loaded - afterwards it no longer reads it.
os.environ["SKYRELAY_DRY_RUN"] = "1"

TYPES = {
    "audio": "audioMessage",
    "sticker": "stickerMessage",
    "image": "imageMessage",
    "video": "videoMessage",
    "text": "conversation",
}


def load_ticker():
    """Loads skyrelay-matchday.py as a module (the hyphen forbids import)."""
    path = os.path.join(BASE_DIR, "skyrelay-matchday.py")
    if not os.path.exists(path):
        sys.exit(f"Not found: {path}")
    sys.path.insert(0, BASE_DIR)
    spec = importlib.util.spec_from_file_location("skyrelay_matchday", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run(args, md):
    from neonize.events import ConnectedEv
    from neonize.types import MessageServerID

    connected = asyncio.Event()

    @md.client.event(ConnectedEv)
    async def _(_a, _b):
        connected.set()

    print("Connecting to WhatsApp...")
    await md.client.connect()
    try:
        await asyncio.wait_for(connected.wait(), timeout=120)
    except asyncio.TimeoutError:
        sys.exit("No WhatsApp connection within 120s.")
    await asyncio.sleep(5)

    jid = (await md.client.get_newsletter_info_with_invite(md.CHANNEL_INVITE_LINK)).ID
    print(f"✓ Channel {jid.User}@{jid.Server}\n")

    if args.latest:
        # count=1 is the only fetch that is safe at the newest message.
        messages = await md.client.get_newsletter_messages(jid, 1, MessageServerID(0))
        for nm in messages:
            print(f"Latest server ID: {nm.MessageServerID}")
        print("\nStart from that with --before, e.g.:  --before",
              messages[0].MessageServerID)
        return

    wanted_fields = (list(TYPES.values()) if args.type == "all"
                     else [TYPES[t.strip()] for t in args.type.split(",")])

    hits = []
    before = args.before
    for round_number in range(1, args.rounds + 1):
        if len(hits) >= args.count:
            break
        try:
            messages = await md.client.get_newsletter_messages(jid, args.block,
                                                              MessageServerID(before))
        except Exception as error:
            print(f"Fetching stopped: {error}")
            break
        if not messages:
            print("(no further messages)")
            break
        messages = sorted(messages, key=lambda n: n.MessageServerID)
        for nm in messages:
            msg = md.unwrap_message(nm.Message)
            fields = [fd.name for fd, _ in msg.ListFields()]
            if any(f in fields for f in wanted_fields):
                hits.append(nm)
        before = messages[0].MessageServerID
        print(f"  Block {round_number}: searched down to server ID {before}, "
              f"{len(hits)} hit(s)")

    if not hits:
        print("\nNo matching messages found - try again with a smaller --before "
              "or more --rounds.")
        return

    print(f"\n{'=' * 60}\nPlaying {len(hits)} message(s) through the pipeline "
          f"(NOTHING is posted)\n{'=' * 60}")
    for nm in hits[:args.count]:
        print(f"\n----- server ID {nm.MessageServerID} -----")
        try:
            await md.process_newsletter_message(md.client, nm.Message,
                                                nm.MessageServerID)
        except Exception as error:
            print(f"  ⚠️ Processing failed: {type(error).__name__}: {error}")


def main():
    p = argparse.ArgumentParser(
        description="Plays real channel messages through the ticker pipeline "
                    "without publishing.")
    p.add_argument("--before", type=int, default=0,
                   help="Server ID to search below. 0 = from the newest one - "
                        "CAREFUL, that can crash the Go layer (see the issue "
                        "draft). Better to set a value.")
    p.add_argument("--type", default="audio,sticker",
                   help="Comma separated list of " + ", ".join(TYPES) +
                        " or 'all'. Default: audio,sticker")
    p.add_argument("--count", type=int, default=2,
                   help="How many hits are played through (default 2).")
    p.add_argument("--block", type=int, default=3,
                   help="Messages per fetch (default 3, keep it small).")
    p.add_argument("--rounds", type=int, default=10,
                   help="How many blocks are searched at most.")
    p.add_argument("--latest", action="store_true",
                   help="Only print the latest server ID, then stop.")
    args = p.parse_args()

    if args.type != "all":
        unknown = [t.strip() for t in args.type.split(",") if t.strip() not in TYPES]
        if unknown:
            sys.exit(f"Unknown type: {', '.join(unknown)} "
                     f"(allowed: {', '.join(TYPES)}, all)")

    md = load_ticker()
    if not md.DRY_RUN:
        sys.exit("Safety stop: DRY_RUN is not active - aborting.")
    asyncio.run(run(args, md))


if __name__ == "__main__":
    main()
