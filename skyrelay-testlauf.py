#!/usr/bin/env python3
"""
SkyRelay - Testlauf: echte Kanal-Nachrichten durch die Pipeline schicken,
ohne etwas zu veröffentlichen.

Wozu das nötig ist: Der eingebaute REPLAY-Modus holt die NEUESTEN N Nachrichten
(`MessageServerID(0)`). Liegt darunter eine inhaltslose Meta-Nachricht, stürzt
die Go-Schicht hart ab - siehe ISSUE-DRAFT-neonize-newsletter-panic.md. Stand
19.08.2026 ist genau das der Fall: `count=1` läuft, ab `count=2` knallt es.

Dieses Werkzeug blättert stattdessen mit einem ausdrücklichen Startpunkt in
kleinen Blöcken RÜCKWÄRTS durch den Verlauf und kommt so an ältere Nachrichten
heran, ohne die kaputte Stelle zu berühren.

Es wird NICHTS gepostet: SKYRELAY_DRY_RUN wird erzwungen, bevor der Ticker
geladen wird. Wasserzeichen und Merklisten bleiben unberührt.

ServerIDs sind je Kanal verschieden - erst den Startpunkt ermitteln, dann von
dort aus rückwärts suchen.

Beispiele:
    # 1. Startpunkt finden: neueste ServerID ausgeben (einziger Abruf, der an
    #    der neuesten Nachricht sicher ist)
    venv/bin/python3 skyrelay-testlauf.py --neueste

    # 2. Von dort aus rückwärts nach Sprachnachrichten und Stickern suchen
    venv/bin/python3 skyrelay-testlauf.py --vor <ServerID> --typ audio,sticker

    # alles Mögliche, die ersten 5 Treffer
    venv/bin/python3 skyrelay-testlauf.py --vor <ServerID> --typ alle --anzahl 5
"""
import argparse
import asyncio
import importlib.util
import os
import sys

BASIS = os.path.dirname(os.path.abspath(__file__))

# MUSS vor dem Laden des Tickers gesetzt sein - danach liest er es nicht mehr.
os.environ["SKYRELAY_DRY_RUN"] = "1"

TYPEN = {
    "audio": "audioMessage",
    "sticker": "stickerMessage",
    "bild": "imageMessage",
    "video": "videoMessage",
    "text": "conversation",
}


def lade_ticker():
    """Lädt skyrelay-matchday.py als Modul (der Bindestrich verbietet import)."""
    pfad = os.path.join(BASIS, "skyrelay-matchday.py")
    if not os.path.exists(pfad):
        sys.exit(f"Nicht gefunden: {pfad}")
    sys.path.insert(0, BASIS)
    spec = importlib.util.spec_from_file_location("skyrelay_matchday", pfad)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


async def hauptlauf(args, md):
    from neonize.events import ConnectedEv
    from neonize.types import MessageServerID

    verbunden = asyncio.Event()

    @md.client.event(ConnectedEv)
    async def _(_a, _b):
        verbunden.set()

    print("Verbinde mit WhatsApp...")
    await md.client.connect()
    try:
        await asyncio.wait_for(verbunden.wait(), timeout=120)
    except asyncio.TimeoutError:
        sys.exit("Keine WhatsApp-Verbindung innerhalb von 120s.")
    await asyncio.sleep(5)

    jid = (await md.client.get_newsletter_info_with_invite(md.CHANNEL_INVITE_LINK)).ID
    print(f"✓ Kanal {jid.User}@{jid.Server}\n")

    if args.neueste:
        # count=1 ist der einzige Abruf, der an der neuesten Nachricht sicher ist.
        nachr = await md.client.get_newsletter_messages(jid, 1, MessageServerID(0))
        for nm in nachr:
            print(f"Neueste ServerID: {nm.MessageServerID}")
        print("\nDamit als --vor starten, z.B.:  --vor", nachr[0].MessageServerID)
        return

    gesuchte_felder = (list(TYPEN.values()) if args.typ == "alle"
                       else [TYPEN[t.strip()] for t in args.typ.split(",")])

    treffer = []
    vor = args.vor
    for runde in range(1, args.runden + 1):
        if len(treffer) >= args.anzahl:
            break
        try:
            nachr = await md.client.get_newsletter_messages(jid, args.block,
                                                            MessageServerID(vor))
        except Exception as fehler:
            print(f"Abruf gestoppt: {fehler}")
            break
        if not nachr:
            print("(keine weiteren Nachrichten)")
            break
        nachr = sorted(nachr, key=lambda n: n.MessageServerID)
        for nm in nachr:
            msg = md.unwrap_message(nm.Message)
            felder = [fd.name for fd, _ in msg.ListFields()]
            if any(f in felder for f in gesuchte_felder):
                treffer.append(nm)
        vor = nachr[0].MessageServerID
        print(f"  Block {runde}: bis ServerID {vor} durchsucht, "
              f"{len(treffer)} Treffer")

    if not treffer:
        print("\nKeine passenden Nachrichten gefunden - mit kleinerem --vor "
              "oder mehr --runden erneut versuchen.")
        return

    print(f"\n{'=' * 60}\n{len(treffer)} Nachricht(en) werden durchgespielt "
          f"(es wird NICHTS gepostet)\n{'=' * 60}")
    for nm in treffer[:args.anzahl]:
        print(f"\n----- ServerID {nm.MessageServerID} -----")
        try:
            await md.process_newsletter_message(md.client, nm.Message,
                                                nm.MessageServerID)
        except Exception as fehler:
            print(f"  ⚠️ Verarbeitung fehlgeschlagen: {type(fehler).__name__}: {fehler}")


def main():
    p = argparse.ArgumentParser(
        description="Spielt echte Kanal-Nachrichten durch die Ticker-Pipeline, "
                    "ohne zu veröffentlichen.")
    p.add_argument("--vor", type=int, default=0,
                   help="ServerID, unterhalb derer gesucht wird. 0 = ab der "
                        "neuesten - ACHTUNG, das kann die Go-Schicht abstürzen "
                        "lassen (siehe ISSUE-DRAFT). Lieber einen Wert setzen.")
    p.add_argument("--typ", default="audio,sticker",
                   help="Kommaliste aus " + ", ".join(TYPEN) + " oder 'alle'. "
                        "Vorgabe: audio,sticker")
    p.add_argument("--anzahl", type=int, default=2,
                   help="So viele Treffer werden durchgespielt (Vorgabe 2).")
    p.add_argument("--block", type=int, default=3,
                   help="Nachrichten je Abruf (Vorgabe 3, klein halten).")
    p.add_argument("--runden", type=int, default=10,
                   help="So viele Blöcke werden höchstens durchsucht.")
    p.add_argument("--neueste", action="store_true",
                   help="Nur die neueste ServerID ausgeben und beenden.")
    args = p.parse_args()

    if args.typ != "alle":
        unbekannt = [t.strip() for t in args.typ.split(",") if t.strip() not in TYPEN]
        if unbekannt:
            sys.exit(f"Unbekannter Typ: {', '.join(unbekannt)} "
                     f"(erlaubt: {', '.join(TYPEN)}, alle)")

    md = lade_ticker()
    if not md.DRY_RUN:
        sys.exit("Sicherheitshalt: DRY_RUN ist nicht aktiv - Abbruch.")
    asyncio.run(hauptlauf(args, md))


if __name__ == "__main__":
    main()
