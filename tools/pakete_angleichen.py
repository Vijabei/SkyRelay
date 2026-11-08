"""Stammt jedes Paket aus der Quelle, die requirements.txt nennt?

pip vergleicht beim Aktualisieren nur Name und Versionsnummer. Das reicht
nicht, sobald eine Fassung direkt aus git kommt: Der Stand eines Pull Requests
nennt sich oft genauso wie die letzte Veroeffentlichung (instaloader aus
instaloader#2795 heisst weiterhin 4.16). Dann gilt fuer pip

  * "instaloader @ git+...@<commit>" als erfuellt, solange 4.16 von PyPI da
    ist - der Fix kaeme nie an;
  * "instaloader==4.16" als erfuellt, solange der git-Stand da ist - zurueck
    ginge es nie.

Dieses Werkzeug schaut nach, woher das Installierte wirklich stammt (pip
vermerkt das in direct_url.json), und installiert genau die Pakete neu, bei
denen Quelle und requirements.txt auseinanderliegen. Alles andere bleibt
unberuehrt. update.sh ruft es nach "pip install -r requirements.txt" auf.

    venv/bin/python tools/pakete_angleichen.py requirements.txt

Rueckgabewert 0, wenn am Ende alles passt.
"""
import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution

AUS_GIT = re.compile(r"^([A-Za-z0-9_.\-]+)\s*@\s*(git\+\S+?)@([0-9a-fA-F]{7,40})$")
FESTE_FASSUNG = re.compile(r"^([A-Za-z0-9_.\-]+)\s*==\s*([^\s;]+)$")


def herkunft(name):
    """(Version, Commit oder None) des Installierten - None, wenn es fehlt."""
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        return None
    try:
        vermerk = json.loads(dist.read_text("direct_url.json") or "{}")
    except (OSError, ValueError):
        vermerk = {}
    commit = (vermerk.get("vcs_info") or {}).get("commit_id")
    return dist.version, commit


def neu_installieren(angabe):
    print(f"  → installiere neu: {angabe}", flush=True)
    return subprocess.call([sys.executable, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check",
                            "--force-reinstall", "--no-deps", angabe]) == 0


def main(pfad):
    fehler = 0
    with open(pfad, encoding="utf-8") as datei:
        for zeile in datei:
            angabe = zeile.split(" #", 1)[0].strip()
            if not angabe or angabe.startswith("#"):
                continue
            git = AUS_GIT.match(angabe)
            fest = FESTE_FASSUNG.match(angabe)
            if git:
                name, _url, commit = git.groups()
                da = herkunft(name)
                if da is None or not (da[1] or "").startswith(commit.lower()):
                    if da is None:
                        ist = "nichts"
                    elif da[1]:
                        ist = f"{da[0]} aus git ({da[1][:10]})"
                    else:
                        ist = f"{da[0]} von PyPI"
                    print(f"  {name}: verlangt ist der git-Stand {commit[:10]}, "
                          f"installiert ist {ist}.")
                    fehler += not neu_installieren(angabe)
            elif fest:
                name, version = fest.groups()
                da = herkunft(name)
                if da is not None and da[1]:
                    print(f"  {name}: verlangt ist {version} von PyPI, installiert "
                          f"ist ein git-Stand ({da[1][:10]}).")
                    fehler += not neu_installieren(angabe)
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "requirements.txt"))
