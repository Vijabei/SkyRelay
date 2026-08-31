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
def konfig_pfad(basis_dir):
    """Pfad der eigenen Konfiguration - im Programmverzeichnis oder dort, wohin
    SKYRELAY_CONFIG zeigt (so lassen sich mehrere Vereine parallel betreiben)."""
    return os.environ.get("SKYRELAY_CONFIG") or os.path.join(basis_dir, "skyrelay.conf")


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


# --------------------------------------------------------- Konfiguration prüfen
# Abschnitte, deren Schlüssel frei wählbar sind und deshalb in keinem Quelltext
# vorkommen: [team_codes] enthält OpenLigaDB-Team-Nummern.
FREIE_ABSCHNITTE = {"team_codes"}

# So greifen die Programme auf Werte zu: die beiden Bots über cfg, cfg_int und
# cfg_bool, der Einrichtungsassistent über lies_wert und setze_wert - jeweils
# mit Abschnitt und Schlüssel als feste Zeichenketten.
# (Ein Beispielaufruf hat hier bewusst nichts zu suchen: Er stünde als Zugriff
#  in der Auswertung. Deshalb werden Kommentarzeilen zusätzlich übergangen.)
_ZUGRIFFSMUSTER = (
    re.compile(r"""\bcfg(?:_int|_bool)?\(\s*["'](\w+)["']\s*,\s*["'](\w+)["']"""),
    re.compile(r"""\b(?:lies_wert|setze_wert)\(\s*\w+\s*,\s*["'](\w+)["']\s*,\s*["'](\w+)["']"""),
)

_QUELLTEXTE = {
    "skyrelay-matchday.py": "Ticker",
    "skyrelay-feed.py": "Feed",
    "skyrelay-setup.py": "Assistent",
    "skyrelay-testlauf.py": "Testlauf",
    "skyrelay_common.py": "gemeinsames Modul",
}


def _ohne_kommentare(text):
    """Schneidet Zeilenkommentare ab. Ohne das würde ein erklärender Kommentar
    mit einem Beispielaufruf als echter Zugriff gezählt. Zeichenketten mit
    Rautezeichen sind unkritisch: Der Aufruf steht davor, nicht dahinter."""
    return "\n".join(zeile.split("#", 1)[0] for zeile in text.splitlines())


def _gelesene_schluessel(basis_dir):
    """Ermittelt aus den Quelltexten, welche Werte tatsächlich gelesen werden.
    Eine von Hand gepflegte Liste wäre nach dem ersten Umbau falsch - und genau
    solche Abweichungen soll diese Prüfung ja finden."""
    gefunden = {}
    for datei, name in _QUELLTEXTE.items():
        try:
            with open(os.path.join(basis_dir, datei), encoding="utf-8") as quelle:
                text = quelle.read()
        except OSError:
            continue
        text = _ohne_kommentare(text)
        for muster in _ZUGRIFFSMUSTER:
            for abschnitt, schluessel in muster.findall(text):
                gefunden.setdefault((abschnitt, schluessel), set()).add(name)
    return gefunden


def _schluesselpaare(text):
    """Alle (Abschnitt, Schlüssel) einer Konfiguration."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(text)
    return {(abschnitt, schluessel)
            for abschnitt in parser.sections()
            for schluessel in parser[abschnitt]}


def sammle_konfig_befunde(basis_dir, konfig_text=None):
    """Vergleicht die eigene Konfiguration mit dem, was die Programme lesen, und
    mit der Vorlage. Liefert Paare (schwere, text); schwere ist "problem" oder
    "hinweis". Ohne konfig_text wird die Datei auf der Platte gelesen - der
    Assistent reicht stattdessen seinen noch ungespeicherten Stand herein."""
    if konfig_text is None:
        try:
            with open(konfig_pfad(basis_dir), encoding="utf-8") as datei:
                konfig_text = datei.read()
        except OSError as fehler:
            return [("problem", f"Konfiguration nicht lesbar: {fehler}")]
    try:
        eigene = _schluesselpaare(konfig_text)
    except configparser.Error as fehler:
        return [("problem", f"Konfiguration ist fehlerhaft: {fehler}")]

    befunde = []
    gelesen = _gelesene_schluessel(basis_dir)
    try:
        with open(os.path.join(basis_dir, "skyrelay.conf.example"),
                  encoding="utf-8") as datei:
            vorlage = _schluesselpaare(datei.read())
    except (OSError, configparser.Error):
        vorlage = None
        befunde.append(("hinweis", "skyrelay.conf.example fehlt oder ist "
                                   "fehlerhaft - der Abgleich mit der Vorlage "
                                   "entfällt."))

    # 1. Steht etwas in der eigenen Datei, das niemand liest? Genau so blieb
    #    [post] prefix im Feed monatelang wirkungslos (#2).
    for abschnitt, schluessel in sorted(eigene):
        if abschnitt in FREIE_ABSCHNITTE:
            continue
        if (abschnitt, schluessel) not in gelesen:
            befunde.append(("problem",
                            f"[{abschnitt}] {schluessel} wird von keinem "
                            f"Programm gelesen - Tippfehler oder veraltet?"))

    # 2. Fehlt etwas, das gelesen wird? Dann greift still die Vorgabe.
    # 3. Fehlt etwas in der Vorlage? Dann erfährt niemand davon.
    for (abschnitt, schluessel), programme in sorted(gelesen.items()):
        wer = ", ".join(sorted(programme))
        if (abschnitt, schluessel) not in eigene:
            befunde.append(("hinweis",
                            f"[{abschnitt}] {schluessel} fehlt - es greift die "
                            f"Vorgabe im Programm ({wer})"))
        if vorlage is not None and (abschnitt, schluessel) not in vorlage:
            befunde.append(("problem",
                            f"[{abschnitt}] {schluessel} fehlt in "
                            f"skyrelay.conf.example ({wer})"))
    return befunde


def pruefe_konfiguration(basis_dir):
    """Druckt den Bericht und liefert den Rückgabewert für die Kommandozeile:
    0 = keine Probleme, 1 = mindestens eines. Verbindet sich mit nichts und
    verändert nichts."""
    print(f"Konfiguration: {konfig_pfad(basis_dir)}")
    befunde = sammle_konfig_befunde(basis_dir)
    probleme = [text for schwere, text in befunde if schwere == "problem"]
    hinweise = [text for schwere, text in befunde if schwere == "hinweis"]

    for titel, eintraege, zeichen in (("Probleme", probleme, "✗"),
                                      ("Hinweise", hinweise, "ℹ")):
        if eintraege:
            print(f"\n{titel}:")
            for eintrag in eintraege:
                print(f"  {zeichen} {eintrag}")

    if not befunde:
        print("\n✓ Keine Auffälligkeiten.")
    else:
        print(f"\n{len(probleme)} Problem(e), {len(hinweise)} Hinweis(e).")
    return 1 if probleme else 0


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
