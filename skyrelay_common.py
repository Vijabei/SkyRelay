"""
SkyRelay - gemeinsame Bausteine

Alles, was skyrelay-matchday.py und skyrelay-feed.py gleichermaßen brauchen:
Protokollierung, Konfiguration, Bildaufbereitung und der Video-Upload zu
Bluesky. Damit gibt es für jede dieser Aufgaben nur noch eine Stelle.

Wird von beiden Programmen importiert und ist nicht zum direkten Aufruf gedacht.
"""

import atexit
import configparser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

import requests
from PIL import Image
from atproto import models
from atproto_client.models.blob_ref import BlobRef

# Die Konfigurationswerkzeuge liegen bewusst in einem eigenen Modul ohne
# Fremdpakete - so kann der Einrichtungsassistent sie auch benutzen, wenn
# atproto und Pillow noch gar nicht installiert sind.
from skyrelay_konfig import konfig_pfad


# ----------------------------------------------------------------- Protokoll
def log(*args, **kwargs):
    """print mit vorangestelltem Zeitstempel - für nachvollziehbare Protokolle
    (etwa aus cron, wo die Startzeit sonst nicht ersichtlich wäre)."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]", *args, **kwargs, flush=True)


def rotate_log(path, max_bytes, backups):
    """Rotiert die Protokolldatei beim Start, wenn sie zu groß geworden ist:
    datei.log -> datei.log.1 -> ... -> datei.log.N (die älteste entfällt)."""
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return
        aelteste = f"{path}.{backups}"
        if os.path.exists(aelteste):
            os.remove(aelteste)
        for i in range(backups - 1, 0, -1):
            quelle = f"{path}.{i}"
            if os.path.exists(quelle):
                os.replace(quelle, f"{path}.{i + 1}")
        os.replace(path, f"{path}.1")
    except Exception as fehler:
        print(f"⚠️ Protokoll-Rotation fehlgeschlagen: {fehler}", flush=True)


def start_file_logging(path, max_bytes=2_000_000, backups=5):
    """Schreibt ALLE Ausgaben zusätzlich in eine Datei - auch die des Go-Anteils
    von neonize, der nicht durch Python läuft und den ein gewöhnliches
    Python-Protokoll deshalb verpassen würde. Dafür werden stdout und stderr in
    eine Pipe umgehängt; ein Hintergrund-Thread schreibt jede Zeile in die Datei
    UND auf die echte Konsole (wie "tee"). Schlägt das fehl, läuft das Programm
    normal weiter - nur eben ohne Protokolldatei."""
    try:
        rotate_log(path, max_bytes, backups)
        logdatei = open(path, "ab", buffering=0)
        lese_fd, schreib_fd = os.pipe()
        konsole_fd = os.dup(1)  # Kopie der echten Konsole, bevor umgehängt wird
        os.dup2(schreib_fd, 1)
        os.dup2(schreib_fd, 2)
        os.close(schreib_fd)

        def verteile():
            with os.fdopen(lese_fd, "rb", 0) as pipe:
                for zeile in iter(pipe.readline, b""):
                    for ziel in (logdatei.write, lambda b: os.write(konsole_fd, b)):
                        try:
                            ziel(zeile)
                        except Exception:
                            pass

        verteiler = threading.Thread(target=verteile, name="log-tee", daemon=True)
        verteiler.start()
        # Ohne Terminal ist stdout sonst blockgepuffert - Zeilenpufferung sorgt
        # dafür, dass "tail -f" sofort mitläuft.
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)

        def abschluss():
            """Beim Beenden dem Verteiler-Thread Zeit geben, die Pipe zu leeren.
            Ohne das gehen Ausgaben verloren, sobald sich das Programm kurz nach
            dem Start beendet - etwa mit einer Meldung zur Konfiguration oder mit
            "heute kein Spiel". Also genau die Zeilen, die man dann braucht."""
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            time.sleep(0.2)

        atexit.register(abschluss)
        return True
    except Exception as fehler:
        print(f"⚠️ Datei-Protokoll konnte nicht gestartet werden: {fehler}", flush=True)
        return False


# -------------------------------------------------------------- Konfiguration
def lade_config(basis_dir):
    """Lädt skyrelay.conf (oder den Pfad aus SKYRELAY_CONFIG) und liefert
    (cfg, cfg_int, cfg_bool, pfad) zurück. Fehlt die Datei oder ist sie
    unlesbar, endet das Programm mit einer verständlichen Meldung."""
    pfad = konfig_pfad(basis_dir)

    if not os.path.exists(pfad):
        print(f"Fehler: Konfigurationsdatei nicht gefunden: {pfad}\n"
              f"Am einfachsten mit dem Assistenten anlegen:\n"
              f"    venv/bin/python skyrelay-setup.py\n"
              f"oder von Hand:  cp skyrelay.conf.example skyrelay.conf",
              file=sys.stderr)
        sys.exit(1)

    # interpolation=None: sonst deutet configparser Prozentzeichen in Texten.
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(pfad, encoding="utf-8") as datei:
            parser.read_file(datei)
    except Exception as fehler:
        print(f"Fehler beim Lesen von {pfad}: {fehler}", file=sys.stderr)
        sys.exit(1)

    def cfg(section, key, default=None):
        """Wert aus der Konfiguration; fehlt er, greift die Vorgabe."""
        return parser.get(section, key, fallback=default)

    def cfg_int(section, key, default):
        try:
            return int(str(parser.get(section, key, fallback=default)).strip())
        except (ValueError, TypeError):
            return default

    def cfg_bool(section, key, default):
        return parser.getboolean(section, key, fallback=default)

    return cfg, cfg_int, cfg_bool, pfad


# ----------------------------------------------------------- Beitragsaufbau
def baue_quellzeile(tb, prefix, label, url):
    """Schreibt Kopfzeile und Quellenangabe eines Hauptbeitrags in den
    TextBuilder - eine Stelle für beide Programme.

    Der Feed hatte diese Zeilen früher fest verdrahtet. Deshalb blieb dort
    [post] prefix wirkungslos, und als Linktext stand die nackte URL (#2).
    Ohne Beschriftung bleibt es bei der URL - besser als ein leerer Link."""
    if prefix:
        tb.text(f"{prefix}\n")
    tb.text("🔗 Quelle: ")
    tb.link(label or url, url)
    tb.text("\n\n")


def zeige_vorschau(bausteine):
    """Zeigt im Trockenlauf, wie die Beiträge auf Bluesky aussähen - Zeile für
    Zeile eingerückt, mit der Zeichenzahl je Beitrag.

    Ohne das bleibt vom Trockenlauf nur eine Zusammenfassung ("2 Beiträge,
    1 Video"), und ob Kopfzeile, Quelle und Hashtags an der richtigen Stelle
    stehen, sieht man erst am fertigen Beitrag - also zu spät."""
    for nummer, tb in enumerate(bausteine, start=1):
        text = tb.build_text()
        log(f"   ┌─ Beitrag {nummer}/{len(bausteine)} ({len(text)} Zeichen)")
        for zeile in text.split("\n"):
            log(f"   │ {zeile}")
        log("   └─")


# -------------------------------------------------------------------- Login
def hole_app_passwort(*namen):
    """Liefert (Passwort, Variablenname) aus der ersten gesetzten Umgebungs-
    variablen. Die Reihenfolge geht vom spezifischen zum allgemeinen Namen:
    Wer Ticker und Feed auf getrennten Konten betreibt, setzt die jeweils
    eigene Variable; wer ein einziges Konto nutzt, kommt mit
    BLUESKY_APP_PASSWORD aus."""
    for name in namen:
        wert = os.environ.get(name)
        if wert:
            return wert, name
    return None, namen[0]


def melde_bei_bluesky_an(client, handle, passwort, passwort_variable):
    """Meldet sich an und erklärt im Fehlerfall, woran es liegen kann. Ein
    nackter 401 hilft niemandem - typisch ist, dass Passwort und Konto nicht
    zusammenpassen, weil Ticker und Feed getrennte Konten verwenden."""
    if not passwort:
        log(f"Fehler: {passwort_variable} ist nicht gesetzt.")
        log(f'Setzen mit:  export {passwort_variable}="xxxx-xxxx-xxxx-xxxx"')
        raise SystemExit(1)
    try:
        client.login(handle, passwort)
    except Exception as fehler:
        log(f"✗ Anmeldung bei Bluesky als @{handle} fehlgeschlagen: {fehler}")
        if "RateLimitExceeded" in str(fehler):
            log("   Das Anmeldelimit ist erschöpft: Bluesky erlaubt 10 Anmeldungen")
            log("   pro Tag und Konto. Dagegen hilft nur warten - weitere Versuche")
            log("   verlängern die Sperre zwar nicht, bringen aber auch nichts.")
            treffer = re.search(r"['\"]ratelimit-reset['\"]:\s*['\"](\d+)['\"]", str(fehler))
            if treffer:
                frei_ab = datetime.fromtimestamp(int(treffer.group(1)))
                log(f"   Wieder möglich ab: {frei_ab.strftime('%d.%m.%Y %H:%M')} (Ortszeit)")
        elif "Invalid identifier or password" in str(fehler):
            log(f"   Gehört das App-Passwort aus {passwort_variable} wirklich zu")
            log(f"   genau diesem Konto? Werden Ticker und Feed auf getrennten")
            log(f"   Konten betrieben, brauchen sie auch getrennte Passwörter:")
            log(f"     Ticker: BLUESKY_TICKER_APP_PASSWORD")
            log(f"     Feed:   BLUESKY_FEED_APP_PASSWORD")
            log("   Achtung: Nur 10 Anmeldeversuche pro Tag - nicht blind wiederholen.")
        raise


# ------------------------------------------------------------------- Medien
def compress_image_for_bluesky(quelle, max_dim=2000, max_bytes=1_500_000, start_quality=85):
    """Verkleinert ein Bild so weit, dass es unter Blueskys Größengrenze passt.
    Nimmt Bilddaten (bytes) oder einen Dateipfad entgegen."""
    bild = Image.open(io.BytesIO(quelle) if isinstance(quelle, (bytes, bytearray)) else quelle)
    if bild.mode in ("RGBA", "P"):
        bild = bild.convert("RGB")
    if max(bild.size) > max_dim:
        bild.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    quality = start_quality
    puffer = io.BytesIO()
    bild.save(puffer, format="JPEG", quality=quality)
    while puffer.tell() > max_bytes and quality > 50:
        puffer = io.BytesIO()
        quality -= 10
        bild.save(puffer, format="JPEG", quality=quality)
    return puffer.getvalue()


def resolve_pds_did_web(actor_did):
    """Ermittelt die DID des tatsächlichen Servers (PDS) eines Kontos - nötig
    als 'aud' für die Zugangsmarke des Video-Uploads."""
    if actor_did.startswith("did:plc:"):
        antwort = requests.get(f"https://plc.directory/{actor_did}", timeout=15)
    elif actor_did.startswith("did:web:"):
        host = actor_did.split(":", 2)[2]
        antwort = requests.get(f"https://{host}/.well-known/did.json", timeout=15)
    else:
        raise ValueError(f"Unbekanntes DID-Format: {actor_did}")

    antwort.raise_for_status()
    dokument = antwort.json()

    for dienst in dokument.get("service", []):
        if dienst.get("id") == "#atproto_pds":
            endpunkt = dienst["serviceEndpoint"]
            host = endpunkt.split("://", 1)[-1].rstrip("/")
            return f"did:web:{host}"

    raise ValueError(f"Server-Adresse nicht im DID-Dokument gefunden: {dokument}")


_pds_aud = None  # einmal ermittelt, danach wiederverwendet


def upload_video_to_bluesky(client, video, filename,
                            max_bytes=100_000_000, timeout_seconds=600,
                            versuche=3):
    """Lädt ein Video zu Bluesky hoch und liefert das fertige Embed zurück.
    `video` sind Bilddaten (bytes) oder ein Dateipfad. Bluesky nimmt Videos
    nicht als einfachen Anhang: erst hochladen, dann verarbeitet der Server sie,
    und erst danach steht die Einbettung bereit. Bei Fehlschlag fliegt eine
    Ausnahme - der Aufrufer entscheidet über den Ersatz (z.B. Vorschaubild)."""
    global _pds_aud

    if not isinstance(video, (bytes, bytearray)):
        with open(video, "rb") as datei:
            video = datei.read()

    if len(video) > max_bytes:
        raise RuntimeError(
            f"Video ist {len(video)} Bytes groß und überschreitet die Bluesky-Grenze "
            f"von {max_bytes} Bytes - der Upload wird gar nicht erst versucht."
        )

    if _pds_aud is None:
        try:
            _pds_aud = resolve_pds_did_web(client.me.did)
            log(f"   ✓ Server-Adresse ermittelt: {_pds_aud}")
        except Exception as fehler:
            log(f"   ⚠️ Server-Adresse nicht ermittelbar: {fehler}")
            _pds_aud = "did:web:bsky.social"

    service_auth = client.com.atproto.server.get_service_auth({
        "aud": _pds_aud,
        "lxm": "com.atproto.repo.uploadBlob",
        "exp": int(time.time()) + 60 * 15,
    })
    token = service_auth.token

    upload_url = "https://video.bsky.app/xrpc/app.bsky.video.uploadVideo"
    params = {"did": client.me.did, "name": filename}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(len(video)),
    }

    log(f"   Sende Videodaten an: {upload_url} ({len(video)} Bytes)")

    antwort = None
    job_id = None

    for versuch in range(1, versuche + 1):
        try:
            antwort = requests.post(upload_url, params=params, headers=headers,
                                    data=video, timeout=180)

            if antwort.status_code == 409:
                # In einem früheren Lauf bereits hochgeladen und fertig verarbeitet -
                # dann die bestehende Vorgangsnummer weiterverwenden.
                konflikt = antwort.json()
                if konflikt.get("error") == "already_exists" and konflikt.get("jobId"):
                    job_id = konflikt["jobId"]
                    log(f"   ℹ️ Video war schon verarbeitet, nutze Vorgang: {job_id}")
                    break

            antwort.raise_for_status()
            break
        except requests.exceptions.HTTPError as fehler:
            auszug = antwort.text[:500] if antwort is not None else "(keine Antwort)"
            log(f"   ⚠️ Upload-Versuch {versuch}/{versuche} fehlgeschlagen: {fehler}")
            log(f"      Server-Antwort: {auszug}")
            if versuch == versuche:
                raise
            wartezeit = 10 * versuch
            log(f"      Warte {wartezeit}s vor dem nächsten Versuch...")
            time.sleep(wartezeit)

    if job_id is None:
        daten = antwort.json()
        job_id = daten.get("jobStatus", daten).get("jobId")
        log(f"   ✓ Video übertragen, Vorgangsnummer: {job_id}")

    log("   Warte auf die Verarbeitung durch Bluesky...")

    status_url = "https://video.bsky.app/xrpc/app.bsky.video.getJobStatus"
    frist = time.time() + timeout_seconds
    blob_daten = None
    while True:
        if time.time() > frist:
            raise RuntimeError(
                f"Verarbeitung nicht innerhalb von {timeout_seconds}s abgeschlossen "
                f"(Vorgang {job_id})."
            )
        status = requests.get(status_url, params={"jobId": job_id},
                              headers={"Authorization": f"Bearer {token}"}, timeout=30)
        status.raise_for_status()
        daten = status.json()
        vorgang = daten.get("jobStatus", daten)
        zustand = vorgang.get("state")

        if zustand == "JOB_STATE_COMPLETED":
            blob_daten = vorgang.get("blob")
            log("   ✓ Verarbeitung abgeschlossen.")
            break
        if zustand == "JOB_STATE_FAILED":
            raise RuntimeError("Bluesky meldet einen Fehler bei der Videoverarbeitung: "
                               + str(vorgang.get("error", "unbekannt")))
        log(f"   ⏳ Zustand: {zustand}... (warte 5 Sekunden)")
        time.sleep(5)

    return models.AppBskyEmbedVideo.Main(
        video=models.get_or_create(blob_daten, model=BlobRef)
    )


# ------------------------------------------------- Nachreichen von Videos
#
# Scheitert ein Video-Upload - etwa weil die Video-API von Bluesky gerade
# stört -, geht der Beitrag trotzdem sofort mit dem Vorschaubild raus: beim
# Live-Ticker zählt die Zeit. Die Videodaten bleiben dann hier liegen, und ein
# späterer Lauf hängt das Video als Antwort an den Beitrag. So wird aus einer
# vorübergehenden Störung kein dauerhaft bebilderter Beitrag.
#
# Je Vorgang liegen zwei Dateien im Nachreich-Ordner:
#   <kennung>.mp4   die Videodaten
#   <kennung>.json  wohin geantwortet wird - kommt erst dazu, wenn der
#                   Beitrag steht und seine URI bekannt ist
# Eine .mp4 ohne .json stammt aus einem abgebrochenen Lauf: ohne Ziel lässt
# sich nichts nachreichen, sie wird beim nächsten Durchgang verworfen.

NACHREICH_VIDEO = ".mp4"
NACHREICH_INFO = ".json"


def _nachreich_pfade(ordner, kennung):
    basis = os.path.join(ordner, kennung)
    return basis + NACHREICH_VIDEO, basis + NACHREICH_INFO


def _nachreich_aufraeumen(*pfade):
    for pfad in pfade:
        try:
            os.remove(pfad)
        except FileNotFoundError:
            pass
        except OSError as fehler:
            log(f"   ⚠️ {os.path.basename(pfad)} nicht entfernbar: {fehler}")


def merke_video_daten(ordner, kennung, video):
    """Legt die Videodaten für einen späteren Versuch ab. `video` sind Bytes oder
    ein Dateipfad. Wird direkt beim Fehlschlag gerufen - der Beitrag steht dann
    noch nicht, deshalb folgt das Ziel erst mit merke_nachreich_ziel()."""
    try:
        os.makedirs(ordner, exist_ok=True)
        video_pfad, _ = _nachreich_pfade(ordner, kennung)
        if not isinstance(video, (bytes, bytearray)):
            with open(video, "rb") as quelle:
                video = quelle.read()
        with open(video_pfad, "wb") as ziel:
            ziel.write(video)
        return True
    except OSError as fehler:
        log(f"   ⚠️ Video nicht zum Nachreichen ablegbar: {fehler}")
        return False


def merke_nachreich_ziel(ordner, kennung, did, root_uri, root_cid,
                         parent_uri, parent_cid, dateiname, alt_text=""):
    """Hält fest, an welchen Beitrag das Video später gehängt wird. Erst damit
    wird der Vorgang gültig."""
    video_pfad, info_pfad = _nachreich_pfade(ordner, kennung)
    if not os.path.exists(video_pfad):
        return False
    try:
        with open(info_pfad, "w", encoding="utf-8") as ziel:
            json.dump({
                "did": did,
                "root_uri": root_uri,
                "root_cid": root_cid,
                "parent_uri": parent_uri,
                "parent_cid": parent_cid,
                "dateiname": dateiname,
                "alt_text": alt_text,
                "versuche": 0,
                "erstellt": datetime.now().isoformat(timespec="seconds"),
            }, ziel, ensure_ascii=False, indent=2)
        log(f"   📌 Video zum Nachreichen vorgemerkt ({kennung}).")
        return True
    except OSError as fehler:
        log(f"   ⚠️ Nachreich-Vermerk fehlgeschlagen: {fehler}")
        _nachreich_aufraeumen(video_pfad)
        return False


def _sende_nachreich_antwort(client, info, embed, antwort_text):
    """Hängt das nachgereichte Video als Antwort an den ursprünglichen Beitrag."""
    alt = info.get("alt_text")
    if alt:
        embed.alt = alt[:1000]
    record = models.AppBskyFeedPost.Record(
        text=antwort_text,
        embed=embed,
        reply=models.AppBskyFeedPost.ReplyRef(
            parent=models.ComAtprotoRepoStrongRef.Main(uri=info["parent_uri"],
                                                      cid=info["parent_cid"]),
            root=models.ComAtprotoRepoStrongRef.Main(uri=info["root_uri"],
                                                     cid=info["root_cid"]),
        ),
        created_at=client.get_current_time_iso(),
    )
    client.com.atproto.repo.create_record(
        models.ComAtprotoRepoCreateRecord.Data(
            repo=client.me.did,
            collection=models.ids.AppBskyFeedPost,
            record=record,
        )
    )


def reiche_videos_nach(client, ordner, antwort_text, max_versuche=8,
                       max_bytes=100_000_000, timeout_seconds=600):
    """Holt zuvor gescheiterte Video-Uploads nach und hängt jedes geglückte Video
    als Antwort an seinen Beitrag. Gehört an den Anfang jedes Laufs, direkt nach
    der Anmeldung. Liefert die Zahl der erledigten Vorgänge."""
    if not os.path.isdir(ordner):
        return 0

    erledigt = 0
    for name in sorted(os.listdir(ordner)):
        if not name.endswith(NACHREICH_VIDEO):
            continue

        kennung = name[:-len(NACHREICH_VIDEO)]
        video_pfad, info_pfad = _nachreich_pfade(ordner, kennung)

        if not os.path.exists(info_pfad):
            log(f"   Verwerfe Video-Rest ohne Ziel: {kennung}")
            _nachreich_aufraeumen(video_pfad)
            continue

        try:
            with open(info_pfad, encoding="utf-8") as quelle:
                info = json.load(quelle)
        except (OSError, ValueError) as fehler:
            log(f"   ⚠️ Nachreich-Vermerk {kennung} unlesbar, wird verworfen: {fehler}")
            _nachreich_aufraeumen(video_pfad, info_pfad)
            continue

        # Beide Bots dürfen sich einen Ordner teilen - fremde Vorgänge liegen lassen.
        if info.get("did") and info["did"] != client.me.did:
            continue

        versuch = int(info.get("versuche", 0)) + 1
        log(f"🎥 Reiche Video nach ({kennung}, Versuch {versuch}/{max_versuche})...")
        try:
            embed = upload_video_to_bluesky(client, video_pfad,
                                            info.get("dateiname") or name,
                                            max_bytes, timeout_seconds,
                                            versuche=1)
            _sende_nachreich_antwort(client, info, embed, antwort_text)
            log(f"   ✓ Video nachgereicht ({kennung}).")
            _nachreich_aufraeumen(video_pfad, info_pfad)
            erledigt += 1
        except Exception as fehler:
            log(f"   ⚠️ Nachreichen fehlgeschlagen ({kennung}): {fehler}")
            if versuch >= max_versuche:
                log(f"   Nach {versuch} Versuchen aufgegeben - {kennung} wird verworfen.")
                _nachreich_aufraeumen(video_pfad, info_pfad)
                continue
            info["versuche"] = versuch
            try:
                with open(info_pfad, "w", encoding="utf-8") as ziel:
                    json.dump(info, ziel, ensure_ascii=False, indent=2)
            except OSError as schreib_fehler:
                log(f"   ⚠️ Versuchszähler nicht gespeichert: {schreib_fehler}")

    return erledigt


# ------------------------------------------- Sprachnachrichten und Sticker
#
# Bluesky kennt weder ein Audio-Format noch Animationen. Beides lässt sich aber
# in etwas übersetzen, das Bluesky darstellt:
#   Sprachnachricht -> Video mit animierter Wellenform (Ton bleibt erhalten)
#   Sticker         -> einzelnes Bild (bei animierten Stickern das erste)

def _ffmpeg_vorhanden():
    return shutil.which("ffmpeg") is not None


def audio_zu_video(audio_daten, groesse="720x720", wellenfarbe="White",
                   hintergrund="0x0b1220", bildrate=25, timeout_seconds=120):
    """Baut aus einer Sprachnachricht ein Video mit animierter Wellenform und
    unveränderter Tonspur. Liefert die mp4-Daten.

    ACHTUNG bei `wellenfarbe`: Der ffmpeg-Filter showwaves nimmt ausschließlich
    FARBNAMEN ("White", "DodgerBlue", "Cyan", ...). Hex-Angaben wie 0x38BDF8
    oder #38BDF8 verwirft er stillschweigend und zeichnet stattdessen grün -
    ohne Fehlermeldung. Für `hintergrund` gilt das nicht, der nimmt auch Hex.
    """
    if not _ffmpeg_vorhanden():
        raise RuntimeError("ffmpeg ist nicht installiert - Sprachnachrichten "
                           "lassen sich ohne ffmpeg nicht umwandeln.")

    if wellenfarbe.startswith(("0x", "#")):
        log(f"   ⚠️ Wellenfarbe {wellenfarbe!r} ist eine Hex-Angabe - ffmpeg "
            f"ignoriert die und zeichnet grün. Bitte einen Farbnamen eintragen.")

    try:
        breite, hoehe = (int(t) for t in groesse.lower().split("x"))
    except ValueError:
        raise RuntimeError(f"Ungültige Größenangabe für das Audio-Video: {groesse!r} "
                           f"(erwartet z.B. 720x720).")
    wellen_hoehe = max(2, hoehe // 2)

    filter_kette = (
        f"color=c={hintergrund}:s={breite}x{hoehe}:r={bildrate}[bg];"
        f"[0:a]showwaves=s={breite}x{wellen_hoehe}:mode=cline:"
        f"colors={wellenfarbe}:scale=sqrt:r={bildrate}[w];"
        f"[bg][w]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[v]"
    )

    with tempfile.TemporaryDirectory(prefix="skyrelay-audio-") as ordner:
        quelle = os.path.join(ordner, "stimme")
        ziel = os.path.join(ordner, "stimme.mp4")
        with open(quelle, "wb") as f:
            f.write(audio_daten)

        ergebnis = subprocess.run(
            ["ffmpeg", "-y", "-i", quelle, "-filter_complex", filter_kette,
             "-map", "[v]", "-map", "0:a",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
             "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
             ziel, "-loglevel", "error"],
            capture_output=True, text=True, timeout=timeout_seconds)

        if ergebnis.returncode != 0 or not os.path.exists(ziel):
            meldung = (ergebnis.stderr or "").strip()[:300] or "keine Ausgabe"
            raise RuntimeError(f"ffmpeg konnte kein Video erzeugen: {meldung}")

        with open(ziel, "rb") as f:
            return f.read()


def video_standbild(video_daten, zeitpunkt=1.0, timeout_seconds=60):
    """Zieht ein Einzelbild aus einem Video - als Ersatzbild, falls der
    Video-Upload scheitert. Liefert JPEG-Daten oder None."""
    if not _ffmpeg_vorhanden():
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="skyrelay-frame-") as ordner:
            quelle = os.path.join(ordner, "video.mp4")
            ziel = os.path.join(ordner, "bild.jpg")
            with open(quelle, "wb") as f:
                f.write(video_daten)
            ergebnis = subprocess.run(
                ["ffmpeg", "-y", "-ss", str(zeitpunkt), "-i", quelle,
                 "-frames:v", "1", ziel, "-loglevel", "error"],
                capture_output=True, text=True, timeout=timeout_seconds)
            if ergebnis.returncode != 0 or not os.path.exists(ziel):
                return None
            with open(ziel, "rb") as f:
                return f.read()
    except Exception as fehler:
        log(f"   ⚠️ Standbild nicht erzeugbar: {fehler}")
        return None


def sticker_zu_bild(daten, hintergrund="white", max_dim=2000, max_bytes=1_500_000):
    """Macht aus einem WhatsApp-Sticker ein Bild für Bluesky. Sticker sind WebP,
    meist mit transparentem Grund und manchmal animiert. Genommen wird das erste
    Einzelbild, und die Transparenz kommt auf einen festen Hintergrund - sonst
    stünde das Motiv auf Schwarz, weil beim Verwerfen des Alphakanals nichts
    anderes übrig bleibt."""
    bild = Image.open(io.BytesIO(daten) if isinstance(daten, (bytes, bytearray)) else daten)

    # Animierte Sticker: erstes Einzelbild. Bei unbewegten ist seek(0) folgenlos.
    try:
        bild.seek(0)
    except EOFError:
        pass

    bild = bild.convert("RGBA")
    grund = Image.new("RGB", bild.size, hintergrund)
    grund.paste(bild, mask=bild.split()[3])

    puffer = io.BytesIO()
    grund.save(puffer, format="PNG")
    return compress_image_for_bluesky(puffer.getvalue(), max_dim, max_bytes)
