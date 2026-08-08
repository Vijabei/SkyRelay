<!--
=============================================================================
DEUTSCHER VORSPANN - VOR DEM ABSENDEN LÖSCHEN
=============================================================================
Entwurf für ein GitHub-Issue bei https://github.com/krypton-byte/neonize/issues
("New issue" -> Titel + Text unten einfügen).

Bitte vor dem Absenden prüfen:
1. Stimmen die beiden Stacktraces mit deinen Logs überein? (Sind 1:1 aus
   deinen Terminal-Ausgaben vom 13.07. übernommen.)
2. Stimmt die Beschreibung der Reproduktion (N=3 ok, N=4 crash)?
3. Python-Version ggf. anpassen (angenommen: 3.13 laut deinen Tracebacks).
4. Der Disclosure-Satz am Ende ist bewusst enthalten - streichen wäre
   unehrlich, umformulieren jederzeit ok.
Alles, was du nicht selbst beobachtet hast, ist als Analyse ("appears to",
"root cause analysis") gekennzeichnet, nicht als Beobachtung.
=============================================================================
-->

# Titel (in das GitHub-Titelfeld):

`get_newsletter_messages` causes uncatchable Go panic ("required field neonize.NewsletterMessage.Message not set") when the fetch window contains a deleted channel post

# Text (in das Beschreibungsfeld):

## Describe the bug

Calling `get_newsletter_messages()` on a WhatsApp channel (newsletter) crashes the **entire Python process** with a Go panic when the fetched window contains a message without content — in my case a **deleted channel post**. Deleted posts keep their `MessageServerID` but have no message body, so `types.NewsletterMessage.Message` is `nil` on the whatsmeow side. Because `Neonize.proto` declares this field as `required`, `proto.Marshal` fails inside `ProtoReturnV3`, which panics — and a Go panic cannot be caught from Python (`try/except` never sees it, the process just aborts).

```
panic: proto: required field neonize.NewsletterMessage.Message not set

goroutine 19 [running, locked to thread]:
main.ProtoReturnV3({0x7fa60ffde8?, 0x4000112e10?})
        /home/runner/work/neonize/neonize/goneonize/main.go:327 +0x154
main.GetNewsletterMessages(0x7fa6f033b0, 0x7fa6f2b890, 0x28, 0x4, 0x0)
        /home/runner/work/neonize/neonize/goneonize/main.go:1633 +0x40c
Aborted
```

When the same message was originally received live, whatsmeow logged (and skipped it — live events are unaffected):

```
19:16:03.191 [whatsmeow.Client WARNING] - Plaintext message from 120363246785630110@newsletter doesn't have byte content
```

## Environment

- **neonize:** 0.3.18.post0 (prebuilt `manylinux2014_aarch64` wheel from PyPI)
- **protobuf:** ≥ 7.34 (runtime matching the wheel's gencode 7.34.1)
- **Python:** 3.13
- **OS:** Raspberry Pi OS 64-bit (Debian, kernel 6.12, aarch64)

**Why tested on 0.3.18 instead of the latest release:** I was pinned to 0.3.18.post0 because on 0.4.0/0.4.1 this call path was unusable for a different reason — every return value failed to parse with `DecodeError: Wire format was corrupt` (#199, apparently resolved by #198 in 0.4.2). The code paths involved in *this* issue are unchanged on current `master` as of 0.4.3.post0 (`EncodeNewsletterMessage` passes `message.Message` through as-is; `NewsletterMessage.Message` is `required` in `Neonize.proto`; all `ProtoReturn*` variants panic on marshal errors), so the latest release is affected as well.

## Reproduction

Deterministic against a real channel that contains a deleted post among its most recent messages (a large public channel I follow had one: the deleted post sits at ServerID 6542, followed by three normal posts 6543–6545):

```python
import asyncio
from neonize.aioze.client import NewAClient
from neonize.types import MessageServerID

client = NewAClient("session.sqlite3")  # already paired

async def main():
    await client.connect()
    await asyncio.sleep(5)  # wait until logged in
    meta = await client.get_newsletter_info_with_invite(
        "https://whatsapp.com/channel/<invite-code>"
    )
    # count=3 -> newest 3 messages, all with content -> works fine
    # count=4 -> window now includes the deleted post -> hard crash (panic above)
    msgs = await client.get_newsletter_messages(meta.ID, 4, MessageServerID(0))

asyncio.run(main())
```

I narrowed it down experimentally: `count=3` (excluding the deleted post) succeeds every time, `count=4` (including it) panics every time. The same crash also killed a long-running poller that fetched the newest 30 messages (`count=30` — the `0x1e` in the trace below) as soon as the deleted post appeared in the channel:

```
panic: proto: required field neonize.NewsletterMessage.Message not set

goroutine 35 [running, locked to thread]:
main.ProtoReturnV3({0x7f852bfde8?, 0x40003bed70?})
        /home/runner/work/neonize/neonize/goneonize/main.go:327 +0x154
main.GetNewsletterMessages(0x7f860bb1d0, 0x7f7c1e4320, 0x28, 0x1e, 0x0)
        /home/runner/work/neonize/neonize/goneonize/main.go:1633 +0x40c
```

## Root cause analysis

- whatsmeow returns the deleted post as a `types.NewsletterMessage` whose `Message` field is `nil`.
- `goneonize/utils/encoder.go` → `EncodeNewsletterMessage()` assigns it unchanged: `Message: message.Message`.
- `Neonize.proto` declares `required WAWebProtobufsE2E.Message Message = 4;` in `message NewsletterMessage`.
- `proto.Marshal` therefore returns an error, and `ProtoReturnV3` (`goneonize/main.go`) panics on any marshal error, taking the host process down with it.

`GetNewsletterMessageUpdate` shares the same encoder and should be affected the same way.

## Expected behavior

Messages without content should either be skipped, or returned with an empty/absent `Message` field — and marshal failures should surface as a Python exception, never as a process-killing panic.

## Suggested fix

Any of these would solve it (the first two are tiny):

1. Skip newsletter messages with `Message == nil` in `GetNewsletterMessages` / `GetNewsletterMessageUpdate`, or
2. change `NewsletterMessage.Message` from `required` to `optional` in `Neonize.proto`, or
3. make `ProtoReturn*` return an error payload instead of panicking, so callers get a catchable Python exception.

## Workaround (for anyone hitting this)

- For live operation, consume newsletter posts via `MessageEv` events instead of polling — whatsmeow filters content-less messages before dispatch.
- For history fetches, reduce `count` until the window no longer includes the deleted post.

---

*Disclosure: the analysis and this write-up were done with the help of an AI assistant (Claude). All observations, stack traces and the count=3 vs. count=4 reproduction are from my own testing on my own device.*
