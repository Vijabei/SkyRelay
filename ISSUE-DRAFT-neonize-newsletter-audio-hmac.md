<!--
=============================================================================
DEUTSCHER VORSPANN - VOR DEM ABSENDEN LÖSCHEN
=============================================================================
Entwurf für ein GitHub-Issue bei https://github.com/krypton-byte/neonize/issues
("New issue" -> Titel + Text unten einfügen).

Stand: 19.08.2026, gemessen auf neonize 0.4.3.post0 gegen einen echten
WhatsApp-Kanal.

Bitte vor dem Absenden prüfen:
1. Die Zahlen sind alle aus dem Lauf vom 19.08.2026: 0 von 5 Sprachnachrichten
   luden regulär, 5 von 5 ohne mediaKey, jeweils byte-genau.
2. Die Kanal-Kennung 120363246785630110 steht im Text. Das ist der öffentliche
   Arminia-Kanal - unkritisch, aber falls du ihn nicht nennen willst, vorher
   herausnehmen. Die Einladungs-URL steht bewusst NICHT drin.
3. Die Ursachenanalyse ist als Vermutung gekennzeichnet ("appears to"). Der
   whatsmeow-Quelltext wurde NICHT gelesen - beobachtet ist nur das Verhalten
   von außen. Bitte so lassen, sonst wird aus einer Messung eine Behauptung.
4. Der Disclosure-Satz am Ende ist bewusst enthalten.

Hinweis: Der Fehler steckt vermutlich in whatsmeow, nicht in neonize selbst.
Es kann sein, dass das Projekt dich an https://github.com/tulir/whatsmeow
weiterverweist - der Bericht ist so geschrieben, dass er dort ebenso passt.
=============================================================================
-->

# Titel (in das GitHub-Titelfeld):

`download_any` fails with "invalid media hmac" for newsletter voice notes — clearing `mediaKey` makes the identical file download successfully

# Text (in das Beschreibungsfeld):

## Describe the bug

Voice notes (`audioMessage`, PTT) posted in a WhatsApp **channel** (newsletter) cannot be downloaded. Every call to `download_any()` fails with:

```
DownloadError: failed to download media from last host: invalid media hmac
```

Images, videos and stickers from the *same* channel download without any problem. The difference is not the media type as such — it is that voice notes are the only kind that still carry a **`mediaKey`**.

Removing that key makes the very same file download instantly and byte-exactly.

## Environment

- **neonize:** 0.4.3.post0 (prebuilt `manylinux2014_aarch64` wheel from PyPI)
- **Python:** 3.13.5
- **OS:** Debian GNU/Linux 13 (trixie), kernel 6.18.39, aarch64 (Raspberry Pi)
- Channel: a large public channel I follow (`120363246785630110@newsletter`), followed and subscribed

## Measurements

All five voice notes found in the channel history behaved identically:

| ServerID | mimetype | fileLength | seconds | regular download |
|---|---|---|---|---|
| 6896 | `audio/ogg; codecs=opus` | 21081 | 8 | ✗ invalid media hmac |
| 6892 | `audio/ogg; codecs=opus` | 13314 | 5 | ✗ invalid media hmac |
| 6886 | `audio/ogg; codecs=opus` | 13524 | 5 | ✗ invalid media hmac |
| 6885 | `audio/ogg; codecs=opus` | 24859 | 10 | ✗ invalid media hmac |
| 6881 | `audio/ogg; codecs=opus` | 16283 | 6 | ✗ invalid media hmac |

**0 of 5 downloaded. After clearing `mediaKey`, 5 of 5 downloaded**, each one exactly `fileLength` bytes. For ServerID 6896 the result is a valid file — `ffprobe` reports `codec_name=opus, sample_rate=48000, channels=1, duration=8.9665`, matching the announced 8 seconds.

The decisive observation is what the message fields actually contain:

| field | `audioMessage` | `videoMessage` | `stickerMessage` |
|---|---|---|---|
| `URL` | empty | empty | set |
| `directPath` | set | set | set |
| `mediaKey` | **32 bytes** | **empty** | **empty** |
| `fileSHA256` | 32 bytes | 32 bytes | 32 bytes |
| `fileEncSHA256` | **empty** | **empty** | **empty** |
| `download_any` | ✗ hmac | ✓ | ✓ |

The pattern is exact: every message type that arrives **without** a `mediaKey`
downloads fine, and the one type that arrives **with** one fails. `fileEncSHA256`
is empty on all of them.

A `videoMessage` with an empty `mediaKey` and an empty `fileEncSHA256` downloads perfectly: ServerID 6883, `fileLength` 4566697, and `download_any` returns exactly 4566697 bytes. A `stickerMessage` behaves the same way: ServerID 6897, empty `mediaKey`, empty `fileEncSHA256`, `download_any` returns exactly its 139420 bytes. So a missing key is not a problem at all here — it is the *presence* of a key that breaks the download.

## Root cause analysis

This is inference from the observed behaviour; I have not read the whatsmeow media code.

Newsletter media appears to be served **unencrypted** behind `directPath`. Images and videos carry no `mediaKey`, so the download path fetches them plainly and they work. Voice notes appear to keep a `mediaKey` from the ordinary chat flow, where PTT media *is* encrypted. Its presence appears to send the download down the decrypt-and-verify path, which then fails the HMAC check against content that was never encrypted with that key in the first place.

That `fileEncSHA256` is empty on all of these messages points the same way: there is no ciphertext hash to verify against, because there is no ciphertext.

Passing a different `MediaType` does not help — with `download_media_with_path` and each of `MediaAudio`, `MediaImage`, `MediaVideo`, `MediaDocument` the call fails earlier, at `invalid checksum length: expected 32, got 0`, which is the empty `fileEncSHA256`. So this is not a wrong key-derivation string; the media simply is not encrypted.

## Reproduction

```python
import asyncio
from neonize.aioze.client import NewAClient
from neonize.types import MessageServerID

client = NewAClient("session.sqlite3")  # already paired, channel followed

async def main():
    await client.connect()
    await asyncio.sleep(5)
    meta = await client.get_newsletter_info_with_invite("https://whatsapp.com/channel/<code>")

    # Pick any message that has an audioMessage. Use an explicit `before`
    # cursor rather than the newest messages - see the separate panic issue.
    msgs = await client.get_newsletter_messages(meta.ID, 3, MessageServerID(<id>))
    nm = next(m for m in msgs if m.Message.HasField("audioMessage"))

    # 1) regular way -> DownloadError: invalid media hmac
    try:
        await client.download_any(nm.Message)
    except Exception as e:
        print("with mediaKey :", e)

    # 2) same message without the key -> works, exactly fileLength bytes
    copy = type(nm.Message)()
    copy.CopyFrom(nm.Message)
    copy.audioMessage.ClearField("mediaKey")
    data = await client.download_any(copy)
    print("without key   :", len(data), "bytes, expected",
          nm.Message.audioMessage.fileLength)

asyncio.run(main())
```

Output on my machine:

```
with mediaKey : failed to download media from last host: invalid media hmac
without key   : 21081 bytes, expected 21081
```

## Expected behavior

`download_any()` should return the audio bytes for a newsletter voice note, the same way it already does for newsletter images and videos.

## Suggested fix

I do not know which layer this belongs in, so these are alternatives rather than a recommendation:

1. For newsletter messages, take the plaintext path regardless of `mediaKey` — this is effectively what already happens for images and videos, since those arrive without a key.
2. Or treat an empty `fileEncSHA256` as "not encrypted" and skip decryption and HMAC verification.
3. Or, if neither is safe in general, fall back to the plaintext path when HMAC verification fails and there is no `fileEncSHA256` to verify against — and surface a clearer error than `invalid media hmac` when it genuinely is a key mismatch.

## Workaround (for anyone hitting this)

Clear `mediaKey` on a copy of the message and download that copy, as in the snippet above. Worth doing only on an HMAC failure, so that properly encrypted media keeps going through the normal path if this ever changes:

```python
async def download_channel_media(client, msg):
    try:
        return await client.download_any(msg)
    except Exception as err:
        if "hmac" not in str(err).lower():
            raise
        copy = type(msg)()
        copy.CopyFrom(msg)
        for field in ("audioMessage", "videoMessage", "imageMessage",
                      "stickerMessage", "documentMessage"):
            if copy.HasField(field):
                getattr(copy, field).ClearField("mediaKey")
                break
        return await client.download_any(copy)
```

---

*Disclosure: the analysis and this write-up were done with the help of an AI assistant (Claude). All measurements, field dumps and error messages are from my own testing against my own device and a channel I follow.*
