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
import os
import re
import sys
import threading
import time
from datetime import datetime

import requests
from PIL import Image
from atproto import models
from atproto_client.models.blob_ref import BlobRef


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
    pfad = os.environ.get("SKYRELAY_CONFIG") or os.path.join(basis_dir, "skyrelay.conf")

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
                            max_bytes=100_000_000, timeout_seconds=600):
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

    versuche = 3
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
