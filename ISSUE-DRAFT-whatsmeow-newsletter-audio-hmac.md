<!--
=============================================================================
DEUTSCHER VORSPANN - VOR DEM ABSENDEN LÖSCHEN
=============================================================================
Entwurf für ein GitHub-Issue.

ZIEL-REPOSITORY: https://github.com/tulir/whatsmeow/issues
NICHT neonize. Die Ursache steht in whatsmeows download.go; neonize reicht
den Aufruf nur durch.

Stand: 19.08.2026, gemessen gegen einen echten WhatsApp-Kanal.

Bitte vor dem Absenden prüfen:
1. Alle Zahlen stammen aus dem Lauf vom 19.08.2026: 0 von 5 Sprachnachrichten
   luden regulär, 5 von 5 ohne mediaKey, jeweils byte-genau, und der SHA-256
   stimmt mit fileSHA256 aus der Nachricht überein.
2. Die Kanal-Kennung 120363246785630110 steht im Text. Das ist der öffentliche
   Arminia-Kanal - unkritisch, aber falls du ihn nicht nennen willst, vorher
   herausnehmen. Die Einladungs-URL steht bewusst NICHT drin.
3. Die Ursachenanalyse stützt sich jetzt auf gelesenen whatsmeow-Quelltext
   (download.go, upload.go), nicht mehr auf Vermutung.
4. Der Disclosure-Satz am Ende ist bewusst enthalten.
=============================================================================
-->

# Titel (in das GitHub-Titelfeld):

Newsletter voice notes fail to download with "invalid media hmac": the plaintext branch in `downloadAndDecrypt` requires `mediaKey == nil`, but WhatsApp sends a `MediaKey` for unencrypted newsletter audio

# Text (in das Beschreibungsfeld):

## Describe the bug

Voice notes (`AudioMessage`, PTT) posted in a WhatsApp **channel** (newsletter) can never be downloaded. Every attempt fails with:

```
failed to download media from last host: invalid media hmac
```

Images, videos and stickers from the *same* channel download fine. The difference is not the media type: voice notes are the only kind whose message still carries a **`MediaKey`**, and its mere presence is enough to send the download down the decryption path — for media that is not encrypted.

The media is plaintext, and that is provable: fetching it with `MediaKey` cleared returns exactly `FileLength` bytes whose SHA-256 matches the message's own `FileSHA256`.

## Environment

- **whatsmeow:** as bundled in `neonize` 0.4.3.post0 (`manylinux2014_aarch64` wheel)
- **OS:** Debian GNU/Linux 13 (trixie), kernel 6.18.39, aarch64 (Raspberry Pi)
- Channel: a large public channel I follow (`120363246785630110@newsletter`), followed and subscribed

## Measurements

All five voice notes in the channel history behaved identically:

| ServerID | mimetype | FileLength | seconds | regular download |
|---|---|---|---|---|
| 6896 | `audio/ogg; codecs=opus` | 21081 | 8 | ✗ invalid media hmac |
| 6892 | `audio/ogg; codecs=opus` | 13314 | 5 | ✗ invalid media hmac |
| 6886 | `audio/ogg; codecs=opus` | 13524 | 5 | ✗ invalid media hmac |
| 6885 | `audio/ogg; codecs=opus` | 24859 | 10 | ✗ invalid media hmac |
| 6881 | `audio/ogg; codecs=opus` | 16283 | 6 | ✗ invalid media hmac |

whatsmeow tries every media host before giving up, so this is not a single
bad CDN node:

```
[whatsmeow.Client WARNING] - Failed to download media: invalid media hmac, trying with next host...
[whatsmeow.Client WARNING] - Failed to download media: invalid media hmac, trying with next host...
[whatsmeow.Client WARNING] - Failed to download media: invalid media hmac, trying with next host...
[whatsmeow.Client WARNING] - Failed to download media: invalid media hmac, trying with next host...
```

**0 of 5 downloaded normally. With `MediaKey` cleared, 5 of 5 downloaded**, each exactly `FileLength` bytes.

For ServerID 6896 the result is demonstrably the correct, intact file:

```
SHA-256 of downloaded bytes : 4d6b36a2978b960435f38ff85ff0e1bdac0cbcd1ae3f7d593f2ee16d159e55cc
FileSHA256 from the message : 4d6b36a2978b960435f38ff85ff0e1bdac0cbcd1ae3f7d593f2ee16d159e55cc
```

`ffprobe` on it reports `codec_name=opus, sample_rate=48000, channels=1, duration=8.9665`, matching the announced 8 seconds.

This is the decisive point: `FileSHA256` is the hash of the **plaintext**, and the bytes stored on the media host match it exactly. The media is not encrypted — while the message nevertheless carries a 32-byte `MediaKey`.

Field layout across message types in the same channel:

| field | `AudioMessage` | `VideoMessage` | `StickerMessage` |
|---|---|---|---|
| `URL` | empty | empty | set |
| `DirectPath` | set | set | set |
| `MediaKey` | **32 bytes** | **empty** | **empty** |
| `FileSHA256` | 32 bytes | 32 bytes | 32 bytes |
| `FileEncSHA256` | **empty** | **empty** | **empty** |
| download | ✗ hmac | ✓ | ✓ |

The pattern is exact: every type that arrives **without** a `MediaKey` downloads fine — a video of 4566697 bytes and a sticker of 139420 bytes, both byte-exact — and the one type that arrives **with** one fails.

## Root cause

In [`download.go`](https://github.com/tulir/whatsmeow/blob/main/download.go), `downloadAndDecrypt` selects the plaintext path with a three-part condition:

```go
} else if mediaKey == nil && fileEncSHA256 == nil && mac == nil {
	// Unencrypted media, just check the hash and return
	data = ciphertext
	if fileSHA256 != nil && (len(fileSHA256) != 32 || sha256.Sum256(data) != *(*[32]byte)(fileSHA256)) {
		err = ErrInvalidUnencryptedMediaSHA256
	}
}
```

For these voice notes `fileEncSHA256` is nil and `mac` is nil — but `mediaKey` is **not** nil. The condition therefore fails, control falls through to `validateMedia(iv, ciphertext, macKey, mac)`, and that returns `ErrInvalidMediaHMAC`: there is no MAC to validate, and nothing was ever encrypted with that key.

Had the plaintext branch been taken, the very check it performs would have **succeeded** — as measured above, `sha256(data) == fileSHA256`.

That newsletter media is unencrypted is whatsmeow's own design. `UploadNewsletter` sets only `FileLength` and `FileSHA256` before calling `rawUpload(..., newsletter=true, ...)`, and `UploadNewsletterReader` is documented as uploading "without encrypting it first". Neither ever produces a `MediaKey` or `FileEncSHA256`. The same fact was observed independently from the upload side in [krypton-byte/neonize#208](https://github.com/krypton-byte/neonize/issues/208).

What I cannot explain is why WhatsApp's own client puts a `MediaKey` into the `AudioMessage` of a channel post at all, when the upload is plaintext. Whatever the reason, that is what the server delivers, and whatsmeow currently cannot read those messages because of it.

## Reproduction

Any channel post that is a voice note reproduces it. Via the Python binding the shape is:

```python
nm = ...  # a newsletter message whose Message has an audioMessage

# 1) regular way -> invalid media hmac
await client.download_any(nm.Message)

# 2) same message, key cleared -> works, exactly FileLength bytes
copy = type(nm.Message)()
copy.CopyFrom(nm.Message)
copy.audioMessage.ClearField("mediaKey")
data = await client.download_any(copy)
assert hashlib.sha256(data).hexdigest() == nm.Message.audioMessage.fileSHA256.hex()
```

In Go the equivalent is to nil out `MediaKey` on the `AudioMessage` before calling `Download`.

## Expected behavior

A newsletter voice note should download like any other newsletter media.

## Suggested fix

Relax the plaintext condition so that a stray `MediaKey` does not force decryption when there is demonstrably nothing to decrypt. `fileEncSHA256 == nil && mac == nil` already characterises unencrypted media — the media host returned no MAC, and the message carries no ciphertext hash:

```go
} else if fileEncSHA256 == nil && mac == nil {
```

The existing `ErrInvalidUnencryptedMediaSHA256` check inside that branch keeps this safe: were the bytes in fact encrypted, the plaintext hash would not match and the download would still fail — with a far more accurate error than `invalid media hmac`.

If changing the condition generally is too broad, the same effect could be limited to newsletter messages, since that is where plaintext media occurs by design.

## Related issues (checked before filing)

- **#727** *client.DownloadToFile return error invalid media hmac* — a `DocumentMessage` in a **normal chat**; the reporter concluded the individual file was at fault. No newsletter involved.
- **#127** *Media File Length and Invalid HMAC* (2022) — about `file_length` being used for validation. Different cause, no newsletter involved.
- **[krypton-byte/neonize#208](https://github.com/krypton-byte/neonize/issues/208)** — not a whatsmeow issue, but describes the same underlying fact from the *upload* side: newsletter uploads legitimately produce no `MediaKey`/`FileEncSHA256`.

I found no existing report of newsletter media failing to download. If I have missed one, I am happy to have this closed as a duplicate.

## Workaround (for anyone hitting this)

Clear `MediaKey` and download again, but only after an HMAC failure, so that genuinely encrypted media keeps taking the normal path:

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

*Disclosure: the analysis and this write-up were done with the help of an AI assistant (Claude). All measurements, field dumps, hashes and error messages are from my own testing against my own device and a channel I follow.*
