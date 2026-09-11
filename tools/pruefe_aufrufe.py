"""Passt jeder Aufruf zu seiner Funktion?

py_compile prueft nur die Syntax, pyflakes nur Namen. Keines von beiden zaehlt
Argumente - und genau so blieb split_caption(caption, 180, 240) fuenf Tage
unbemerkt, nachdem die Funktion nur noch (caption, source_url) nahm.

Geprueft werden Aufrufe von Funktionen, die in unseren eigenen Dateien stehen:
im selben Modul, oder mit "from skyrelay_x import name" hereingeholt. Zu
wenige oder zu viele Positionsargumente, unbekannte Schluesselwoerter und
fehlende Pflichtargumente werden gemeldet.
"""
import ast
import pathlib
import sys

WURZEL = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
# Einzelne Dateien lassen sich durch eine andere Fassung ersetzen:
#   tools/pruefe_aufrufe.py . skyrelay-feed.py=/pfad/zur/alten/fassung.py
ERSATZ = dict(a.split("=", 1) for a in sys.argv[2:])


def quelle(name):
    return pathlib.Path(ERSATZ.get(name, WURZEL / name)).read_text(encoding="utf-8")


class Unterschrift:
    def __init__(self, knoten):
        a = knoten.args
        positionell = a.posonlyargs + a.args
        self.name = knoten.name
        self.namen = [p.arg for p in positionell]
        self.pflicht = len(positionell) - len(a.defaults)
        self.hoechstens = len(positionell)
        self.rest = a.vararg is not None
        self.schluessel = a.kwarg is not None
        self.nur_schluessel = {k.arg for k in a.kwonlyargs}
        self.nur_pflicht = {k.arg for k, d in zip(a.kwonlyargs, a.kw_defaults)
                            if d is None}

    def pruefe(self, aufruf):
        if any(isinstance(x, ast.Starred) for x in aufruf.args) or \
                any(k.arg is None for k in aufruf.keywords):
            return None          # *args / **kwargs beim Aufruf: nicht zaehlbar
        n = len(aufruf.args)
        genannt = {k.arg for k in aufruf.keywords}
        if n > self.hoechstens and not self.rest:
            return f"{n} Positionsargumente, nimmt höchstens {self.hoechstens}"
        erlaubt = set(self.namen) | self.nur_schluessel
        fremd = genannt - erlaubt
        if fremd and not self.schluessel:
            return f"unbekannte Schlüsselwörter {sorted(fremd)}"
        gedeckt = set(self.namen[:n]) | genannt
        fehlt = [p for p in self.namen[:self.pflicht] if p not in gedeckt]
        if fehlt:
            return f"es fehlt {fehlt}"
        fehlt_k = self.nur_pflicht - genannt
        if fehlt_k:
            return f"es fehlt {sorted(fehlt_k)}"
        return None


dateien = sorted(p.name for p in WURZEL.glob("skyrelay*.py"))
baeume = {n: ast.parse(quelle(n), n) for n in dateien}
modul_von = {n.removesuffix(".py").replace("-", "_"): n for n in dateien}

# Oberste Ebene jedes Moduls: name -> Unterschrift
eigene = {n: {k.name: Unterschrift(k) for k in b.body
              if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))}
          for n, b in baeume.items()}

befunde = 0
for datei, baum in baeume.items():
    bekannt = dict(eigene[datei])
    for k in baum.body:
        if isinstance(k, ast.ImportFrom) and k.module in modul_von:
            fremd = eigene[modul_von[k.module]]
            for alias in k.names:
                if alias.name in fremd:
                    bekannt[alias.asname or alias.name] = fremd[alias.name]
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name):
            u = bekannt.get(knoten.func.id)
            if u:
                problem = u.pruefe(knoten)
                if problem:
                    befunde += 1
                    print(f"  ✗ {datei}:{knoten.lineno}  {u.name}(): {problem}")

print(f"{len(dateien)} Dateien, {befunde} Befund(e)")
sys.exit(1 if befunde else 0)
