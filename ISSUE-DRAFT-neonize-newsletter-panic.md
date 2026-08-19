<!--
=============================================================================
DEUTSCHER VORSPANN - VOR DEM ABSENDEN LÖSCHEN
=============================================================================
Entwurf für ein GitHub-Issue bei https://github.com/krypton-byte/neonize/issues
("New issue" -> Titel + Text unten einfügen).

Stand: 19.08.2026 - auf neonize 0.4.3.post0 nachgestellt, der Version, die
SkyRelay laut requirements.txt tatsächlich verwendet. Der Fehler war damit
nicht mehr nur hergeleitet, sondern ist direkt beobachtet.

Bitte vor dem Absenden prüfen:
1. Die Stacktraces stammen jetzt aus dem Lauf vom 19.08.2026 auf 0.4.3.post0
   (Zeilennummern main.go:380/1710). Die alten von 0.3.18.post0
   (main.go:327/1633) stehen als Beleg für die Zeitspanne darunter.
2. Reproduktion neu vermessen: count=1 läuft, ab count=2 stürzt es ab -
   eine einzige inhaltslose Nachricht im Fenster genügt.
3. Versionsangaben sind aus dem laufenden System ausgelesen, nicht geschätzt.
4. Der Disclosure-Satz am Ende ist bewusst enthalten - streichen wäre
   unehrlich, umformulieren jederzeit ok.
Alles, was nicht selbst beobachtet wurde, ist als Analyse ("appears to",
"root cause analysis") gekennzeichnet, nicht als Beobachtung. Neu ist auch:
Für den Lauf vom 19.08. wurde NICHT unabhängig geprüft, ob die auslösende
Nachricht ein gelöschter Beitrag ist - das steht entsprechend vorsichtig da.
=============================================================================
-->

# Titel (in das GitHub-Titelfeld):

`get_newsletter_messages` causes uncatchable Go panic ("required field neonize.NewsletterMessage.Message not set") when the fetch window contains a content-less channel post (e.g. a deleted one)

# Text (in das Beschreibungsfeld):

## Describe the bug

Calling `get_newsletter_messages()` on a WhatsApp channel (newsletter) crashes the **entire Python process** with a Go panic when the fetched window contains a message without content — in my case a **deleted channel post**. Deleted posts keep their `MessageServerID` but have no message body, so `types.NewsletterMessage.Message` is `nil` on the whatsmeow side. Because `Neonize.proto` declares this field as `required`, `proto.Marshal` fails inside `ProtoReturnV3`, which panics — and a Go panic cannot be caught from Python (`try/except` never sees it, the process just aborts).

On **0.4.3.post0** (19 Aug 2026), fetching the five newest messages of a
channel (`0x5` in the trace is the `count` argument):

```
panic: proto: required field neonize.NewsletterMessage.Message not set

goroutine 51 [running, locked to thread]:
main.ProtoReturnV3({0x7f85c066a8, 0x7f2e25e780})
        /home/runner/work/neonize/neonize/goneonize/main.go:380 +0x1f8
main.GetNewsletterMessages(0x7f8c3a7430, 0x7f7c320640, 0x28, 0x5, 0x0)
        /home/runner/work/neonize/neonize/goneonize/main.go:1710 +0x598
```

The same crash on **0.3.18.post0** (13 Jul 2026), where I first hit it — same
call, same failure, different line numbers:

```
panic: proto: required field neonize.NewsletterMessage.Message not set

goroutine 19 [running, locked to thread]:
main.ProtoReturnV3({0x7fa60ffde8?, 0x4000112e10?})
        /home/runner/work/neonize/neonize/goneonize/main.go:327 +0x154
main.GetNewsletterMessages(0x7fa6f033b0, 0x7fa6f2b890, 0x28, 0x4, 0x0)
        /home/runner/work/neonize/neonize/goneonize/main.go:1633 +0x40c
Aborted
```

It has therefore survived at least from 0.3.18.post0 to 0.4.3.post0.

When the same message was originally received live, whatsmeow logged (and skipped it — live events are unaffected):

```
19:16:03.191 [whatsmeow.Client WARNING] - Plaintext message from 120363246785630110@newsletter doesn't have byte content
```

## Environment

- **neonize:** 0.4.3.post0 (prebuilt `manylinux2014_aarch64` wheel from PyPI)
  — also reproduced on 0.3.18.post0
- **protobuf:** ≥ 7.34 (runtime matching the wheel's gencode 7.34.1)
- **Python:** 3.13.5
- **OS:** Debian GNU/Linux 13 (trixie), kernel 6.18.39, aarch64 (Raspberry Pi)

**Version history of this report:** I first hit this on 0.3.18.post0, where I
was pinned because 0.4.0/0.4.1 made this call path unusable for an unrelated
reason — every return value failed to parse with `DecodeError: Wire format was
corrupt` (#199, apparently resolved by #198 in 0.4.2). I have since moved to
0.4.3.post0 and **re-confirmed the crash there directly**, so this is no longer
an inference from reading the source: `EncodeNewsletterMessage` still passes
`message.Message` through as-is, `NewsletterMessage.Message` is still `required`
in `Neonize.proto`, and all `ProtoReturn*` variants still panic on marshal
errors.

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
    # 0.3.18.post0, deleted post at ServerID 6542:
    #   count=3 -> newest 3 messages, all with content -> works fine
    #   count=4 -> window now includes the deleted post -> hard crash
    #
    # 0.4.3.post0, content-less message directly behind the newest post:
    #   count=1 -> works, returns ServerID 6940
    #   count=2 -> hard crash
    # and the minimal case, one single message is enough:
    #   get_newsletter_messages(meta.ID, 1, MessageServerID(6940)) -> hard crash
    msgs = await client.get_newsletter_messages(meta.ID, 2, MessageServerID(0))

asyncio.run(main())
```

I narrowed it down experimentally, twice.

On 0.3.18.post0: `count=3` (excluding the deleted post) succeeded every time,
`count=4` (including it) panicked every time.

On 0.4.3.post0, against the same channel on 19 Aug 2026, the boundary is even
tighter — a content-less message now sits directly behind the newest post:

| call | result |
|---|---|
| `count=1, before=0` | OK — returns exactly one message, ServerID 6940 |
| `count=2, before=0` | **panic** |
| `count=3, before=0` | **panic** |
| `count=4, before=0` | **panic** |
| `count=1, before=6940` | **panic** — the single message before 6940 is enough |

The last line is the minimal case: fetching **one** message crashes the process,
because that one message has no content. Note that for this 2026 run I could not
inspect the offending message (it cannot be decoded — that is the bug), so I
cannot independently confirm it is a *deleted* post rather than some other
content-less entry; on the 2025 run the deleted post was identified.

The same crash also killed a long-running poller that fetched the newest 30
messages (`count=30` — the `0x1e` in the trace below) as soon as the deleted
post appeared in the channel:

```
panic: proto: required field neonize.NewsletterMessage.Message not set

goroutine 35 [running, locked to thread]:
main.ProtoReturnV3({0x7f852bfde8?, 0x40003bed70?})
        /home/runner/work/neonize/neonize/goneonize/main.go:327 +0x154
main.GetNewsletterMessages(0x7f860bb1d0, 0x7f7c1e4320, 0x28, 0x1e, 0x0)
        /home/runner/work/neonize/neonize/goneonize/main.go:1633 +0x40c
```

## Root cause analysis

- whatsmeow returns the deleted post as a `types.NewsletterMessage` whose `Message` field is `nil`. That whatsmeow does hand out newsletter messages with a `nil` `Message` is not only my inference — it was reported upstream in [tulir/whatsmeow#761](https://github.com/tulir/whatsmeow/issues/761) (*"GetNewsletterMessageUpdates return nil Message"*, closed as *not planned* without any discussion). Whatever upstream decides, neonize has to survive it rather than abort the host process.
- `goneonize/utils/encoder.go` → `EncodeNewsletterMessage()` assigns it unchanged: `Message: message.Message`.
- `Neonize.proto` declares `required WAWebProtobufsE2E.Message Message = 4;` in `message NewsletterMessage`.
- `proto.Marshal` therefore returns an error, and `ProtoReturnV3` (`goneonize/main.go`) panics on any marshal error, taking the host process down with it.

`GetNewsletterMessageUpdate` shares the same encoder and should be affected the same way.

## Related issues (checked before filing)

This is not a duplicate, but it is not an isolated case either — it is the
third instance of the same pattern I can find:

- **#208** *UploadNewsletter panics the whole process — UploadResponse.MediaKey/FileEncSHA256 marked required but always nil for newsletter uploads* (open, filed 2026-08-18). **Same bug class, same crash site.** That report panics in `ProtoReturnV3` at `goneonize/main.go:380` — the exact line in my own trace above — only from `UploadNewsletter` (`main.go:422`) instead of `GetNewsletterMessages` (`main.go:1710`), and over `UploadResponse.MediaKey` instead of `NewsletterMessage.Message`. Both are `required` proto2 fields that whatsmeow legitimately leaves unset. Fixing one will not fix the other, but they deserve to be looked at together.
- **Commit [`fbdd740`](https://github.com/krypton-byte/neonize/commit/fbdd740786421ec5367e64b4f02d68d6b80600b7)** (14 Oct 2025) — *"fix proto: required field neonize.ProfilePictureInfo.Hash not set"*. Precedent: exactly this problem was already solved once by relaxing the field in `Neonize.proto`.
- **#194**, **#201**, **#181** — unrelated causes, but all of them are Go panics that take the whole Python process down. That recurrence is the argument for fixing the panic behaviour itself, not only the individual fields.

## Expected behavior

Messages without content should either be skipped, or returned with an empty/absent `Message` field — and marshal failures should surface as a Python exception, never as a process-killing panic.

## Suggested fix

Any of these would solve it (the first two are tiny):

1. Skip newsletter messages with `Message == nil` in `GetNewsletterMessages` / `GetNewsletterMessageUpdate`, or
2. change `NewsletterMessage.Message` from `required` to `optional` in `Neonize.proto`, or
3. make `ProtoReturn*` return an error payload instead of panicking, so callers get a catchable Python exception.

Option 2 has precedent in `fbdd740`, where `ProfilePictureInfo.Hash` was relaxed from `required` for exactly this reason. Option 3 is the one that would end the class: as long as any `required` field can be left unset by whatsmeow, the next such field will crash the host process again — #208 is that next one, filed while this report was being written.

## Workaround (for anyone hitting this)

- For live operation, consume newsletter posts via `MessageEv` events instead of polling — whatsmeow filters content-less messages before dispatch.
- For history fetches, reduce `count` until the window no longer includes the
  content-less message. Note this fails when the message sits directly behind
  the newest post — `count=1` is then already too much.
- More robustly, page **around** it with the `before` cursor: pass an explicit
  `MessageServerID` from before the bad message and walk backwards in small
  blocks. In my case `get_newsletter_messages(jid, 3, MessageServerID(6900))`
  and every further block below it worked without a single crash, while any
  fetch anchored at the newest message failed.

---

*Disclosure: the analysis and this write-up were done with the help of an AI assistant (Claude). All observations, stack traces and the count=3 vs. count=4 reproduction are from my own testing on my own device.*
