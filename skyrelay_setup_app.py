"""
SkyRelay - the setup assistant as one coherent application.

This module owns the presentation and nothing else. Every value it reads or
writes goes through the helpers of skyrelay-setup.py, which are handed in as
`helfer` - that file cannot be imported (its name carries a dash), and it has
no business knowing about screens either way.

Why an application rather than a chain of questions: the surface before this
one printed downwards, so every answered question stayed on screen and the
assistant read as a transcript of itself. A window that stays put, with the
sections on the left and one section at a time on the right, is what people
recognise from a settings dialog - and it leaves room for the thing that
matters most here: an explanation under every field, so nobody has to guess
what "Kopfzeile zeigen" does.

The explanations live here rather than being pulled from the comments in
skyrelay.conf.example. Those comments are reference material for someone
editing the file by hand - long, exhaustive, and German-only. What a form
needs is one or two sentences about what the setting does for you, and they
have to be translatable like every other piece of the interface.
"""

import os

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Footer, Header, Input, Label, LoadingIndicator,
                             OptionList, Select, Static, Switch)
from textual.widgets.option_list import Option

from skyrelay_i18n import _, _f, N_

# The window this was laid out for. Below it things still work, but the
# explanations wrap into ribbons - so say so rather than let it look broken.
MIN_BREITE = 96
MIN_HOEHE = 28

# Der Farbton, wenn die Konfiguration keinen nennt oder einen unbekannten.
VORGABE_THEMA = "flexoki"


# ---------------------------------------------------------------- the model
class Feld:
    """One setting in a form.

    `art` decides the widget: text, zahl (digits only), schalter (on/off),
    auswahl (a fixed set), aktion (a button that opens a screen of its own)."""

    def __init__(self, abschnitt, schluessel, name, erklaerung, art="text",
                 optionen=None, platzhalter="", aktion=None):
        self.abschnitt = abschnitt
        self.schluessel = schluessel
        self.name = name
        self.erklaerung = erklaerung
        self.art = art
        self.optionen = optionen or []
        self.platzhalter = platzhalter
        self.aktion = aktion

    @property
    def kennung(self):
        """A widget id - dots and brackets are not allowed in one."""
        if self.art == "aktion":
            return f"a-{self.aktion}"
        return f"f-{self.abschnitt}-{self.schluessel}".replace("_", "-")


class Bereich:
    """A section of the assistant: a heading, an introduction, some fields."""

    def __init__(self, kennung, name, einleitung, felder):
        self.kennung = kennung
        self.name = name
        self.einleitung = einleitung
        self.felder = felder


# The order is the order in which a new installation is set up, not the order
# the keys happen to have in the file: the account first, because nothing
# works without it, then the two bots, then how their posts look, then the
# things one only ever touches once.
def bereiche():
    """Built when the window opens, not at import time - the language is only
    settled after the configuration has been read."""
    return [
        Bereich("konto", _("1 · Bluesky-Konto"),
                _("Unter welchem Konto die Beiträge erscheinen.\n\n"
                  "Das App-Passwort steht bewusst NICHT hier, sondern in einer "
                  "Umgebungsvariablen – eine Konfigurationsdatei wandert zu "
                  "leicht in ein Repository oder eine Sicherungskopie."),
                [
                    Feld("bluesky", "handle", _("Handle des Ticker-Kontos"),
                         _("Vollständig und ohne @, z.B. mein-bot.bsky.social"),
                         platzhalter="mein-bot.bsky.social"),
                    Feld("feed", "bluesky_handle", _("Eigenes Konto für den Feed"),
                         _("Nur ausfüllen, wenn der Instagram-Feed unter einem "
                           "anderen Konto posten soll. Leer = dasselbe Konto "
                           "wie oben."),
                         platzhalter=_("leer = wie oben")),
                    Feld(None, None, _("Anmeldung prüfen"),
                         _("Meldet sich einmal an und sagt, ob Handle und "
                           "App-Passwort zusammenpassen. Es wird nichts "
                           "veröffentlicht."),
                         art="aktion", aktion="anmeldung"),
                ]),

        Bereich("ticker", _("2 · Spieltags-Ticker"),
                _("Spiegelt an Spieltagen den WhatsApp-Kanal des Vereins nach "
                  "Bluesky.\n\n"
                  "Der Spielplan von OpenLigaDB entscheidet, an welchen Tagen "
                  "er überhaupt anspringt – an spielfreien Tagen läuft nichts, "
                  "und es wird keine Verbindung aufgebaut."),
                [
                    Feld("source", "channel_invite_link", _("WhatsApp-Kanal"),
                         _("Der Einladungslink des Kanals, den der Bot "
                           "mitliest. In WhatsApp: Kanal öffnen → Name antippen "
                           "→ Link kopieren."),
                         platzhalter="https://whatsapp.com/channel/…"),
                    Feld(None, None, _("Liga und Verein suchen"),
                         _("Sucht bei OpenLigaDB und trägt Verein, Liga und die "
                           "Kürzeltabelle der ganzen Liga auf einmal ein. Der "
                           "bequemste Weg – die drei Felder darunter füllen "
                           "sich dabei von selbst."),
                         art="aktion", aktion="liga"),
                    Feld("team", "openligadb_filter", _("Suchbegriff"),
                         _("Teil des Vereinsnamens, mit dem OpenLigaDB gefunden "
                           "wird. Leer lassen, wenn es für die Sportart dort "
                           "keine Daten gibt – dann läuft der Ticker an jedem "
                           "Tag, an dem er gestartet wird."),
                         platzhalter="bielefeld"),
                    Feld("team", "openligadb_team_id", _("Team-Nummer"),
                         _("Zur Absicherung, weil der Suchbegriff auch fremde "
                           "Vereine treffen kann. 0 = keine "
                           "Spieltags-Erkennung."),
                         art="zahl"),
                    Feld("team", "league_shortcuts", _("Ligen"),
                         _("Nur Spiele aus diesen Ligen zählen – exakte "
                           "Kürzel, durch Komma getrennt, z.B. "
                           "„bl2, rlw-frauen“.\n"
                           "OpenLigaDB liefert vereinzelt Fantasie-Ligen mit "
                           "falschen Terminen; ohne diese Sperre spränge der "
                           "Bot an spielfreien Tagen an."),
                         platzhalter="bl2, rlw-frauen"),
                    Feld("team", "timezone", _("Zeitzone"),
                         _("Für Anstoßzeiten und das Tagesende."),
                         platzhalter="Europe/Berlin"),
                    Feld(None, None, _("Kürzel für Hashtags"),
                         _("Aus Heim- und Auswärtskürzel entsteht der "
                           "Spiel-Hashtag, z.B. #DSCSTP. Hier lassen sie sich "
                           "einzeln nachbessern."),
                         art="aktion", aktion="kuerzel"),
                ]),

        Bereich("feed", _("3 · Instagram-Feed"),
                _("Überträgt neue Instagram-Beiträge nach Bluesky. Anders als "
                  "der Ticker läuft er das ganze Jahr und wird regelmäßig von "
                  "cron gestartet.\n\n"
                  "Die Instagram-Sitzung wird einmalig außerhalb angelegt:\n"
                  "    venv/bin/instaloader -l <Zweitkonto>"),
                [
                    Feld("feed", "instagram_profile", _("Instagram-Profil"),
                         _("Das Profil, das gespiegelt wird – ohne @."),
                         platzhalter="arminia_bielefeld"),
                    Feld("feed", "instagram_session_user", _("Zweitkonto zum Abruf"),
                         _("Instagram zeigt Profile nur angemeldeten Konten. "
                           "Nimm dafür ein Zweitkonto, nicht dein eigenes – "
                           "häufiges Abrufen kann ein Konto sperren."),
                         platzhalter="mein_zweitkonto"),
                    Feld("feed", "posts_to_check", _("Wie viele Beiträge prüfen"),
                         _("Bei jedem Lauf werden so viele der neuesten "
                           "Beiträge angesehen. Höher heißt: eine Lücke nach "
                           "einem Ausfall wird eher geschlossen, aber es "
                           "kostet mehr Anfragen."),
                         art="zahl"),
                    Feld("feed", "pause_between_posts_seconds",
                         _("Pause zwischen zwei Beiträgen"),
                         _("In Sekunden. Zu schnell hintereinander posten "
                           "fällt bei Bluesky als Schwarmverhalten auf."),
                         art="zahl"),
                ]),

        Bereich("beitrag", _("4 · Wie ein Beitrag aussieht"),
                _("Gilt für beide Bots. Bluesky lässt 300 Zeichen zu – alles, "
                  "was hier fest im Beitrag steht, fehlt dem eigentlichen "
                  "Text.\n\n"
                  "Was am Ende herauskommt, zeigt ein Trockenlauf:\n"
                  "    SKYRELAY_DRY_RUN=1 SKYRELAY_REPLAY=3 \\\n"
                  "        venv/bin/python skyrelay-matchday.py"),
                [
                    Feld("post", "prefix", _("Kopfzeile"),
                         _("Erste Zeile jedes Hauptbeitrags. Leer = keine."),
                         platzhalter="⚽ [Inoffizieller Bot]"),
                    Feld("post", "bot_notice", _("Kopfzeile zeigen"),
                         _("Steht in der Bluesky-Biografie schon deutlich, "
                           "dass hier ein Bot schreibt, ist der Hinweis in "
                           "jedem Beitrag verschenkter Platz.\n"
                           "„automatisch“ lässt ihn weg, sobald die Biografie "
                           "es selbst sagt."),
                         art="auswahl",
                         optionen=[("always", N_("immer")),
                                   ("auto", N_("automatisch")),
                                   ("never", N_("nie"))]),
                    Feld("post", "source_template", _("Vorlage für den Quell-Link"),
                         _("Was in eckigen Klammern steht, wird zum "
                           "klickbaren Wort; {label} ist die Beschriftung "
                           "darunter."),
                         platzhalter="🔗 [Quelle]: {label}"),
                    Feld("post", "source_label", _("Beschriftung (Ticker)"),
                         _("Steht neben dem Quell-Link der WhatsApp-Beiträge. "
                           "Leer lassen = keine Quelle im Beitrag."),
                         platzhalter="WhatsApp-Kanal des Vereins"),
                    Feld("feed", "source_label", _("Beschriftung (Feed)"),
                         _("Dasselbe für die Instagram-Beiträge – ein "
                           "WhatsApp-Kanal passt dort nicht."),
                         platzhalter="Beitrag auf Instagram"),
                    Feld("post", "standing_hashtag", _("Dauer-Hashtag"),
                         _("Steht unter jedem Beitrag, ohne #. Leer = keiner."),
                         platzhalter="arminia"),
                    Feld(None, None, _("Hashtag je Liga"),
                         _("Für Vereine, die ihre Mannschaften unterschiedlich "
                           "kennzeichnen – #arminia für die Männer, "
                           "#arminiafrauen für die Frauen. Welche Mannschaft "
                           "spielt, sagt die Liga."),
                         art="aktion", aktion="ligatags"),
                    Feld("post", "overlap_hashtag", _("Hashtag bei mehreren Spielen"),
                         _("Spielen zwei Mannschaften am selben Tag, verrät "
                           "eine Kanalnachricht nicht, zu welcher Partie sie "
                           "gehört. Statt eines falschen Spiel-Hashtags steht "
                           "dann dieser hier. Leer = gar keiner."),
                         platzhalter="arminia"),
                ]),

        Bereich("aufbau", _("5 · Aufbau der Beiträge"),
                _("An welcher Stelle Kopfzeile, Quelle und Hashtags stehen: "
                  "oben oder unten, auf dem ersten, auf dem letzten oder auf "
                  "jedem Beitrag einer längeren Nachricht."),
                [
                    Feld(None, None, _("Aufbau bearbeiten"),
                         _("Mit Vorschau: Du siehst sofort, wie eine "
                           "zweiteilige Nachricht damit aussähe."),
                         art="aktion", aktion="aufbau"),
                ]),

        Bereich("profil", _("6 · Profil und Zeitfenster"),
                _("Die Biografie des Ticker-Kontos kann anzeigen, ob der Bot "
                  "gerade läuft – beim Beenden wird sie zurückgestellt.\n\n"
                  "Platzhalter: {hashtag}, {info}, {date}, {time}"),
                [
                    Feld("profile", "enabled", _("Statuszeile verwenden"),
                         _("Setzt die erste Zeile der Bluesky-Biografie. Alle "
                           "weiteren Zeilen, Avatar und Banner bleiben "
                           "unangetastet."),
                         art="schalter"),
                    Feld("profile", "line_on", _("Text während des Betriebs"),
                         _("Erscheint, solange der Ticker lauscht."),
                         platzhalter="🟢 Bot ist an – {info} {hashtag}"),
                    Feld("profile", "line_off", _("Text nach dem Beenden"),
                         _("Erscheint zwischen den Spieltagen und nennt die "
                           "nächste Partie."),
                         platzhalter="🔴 Bot ist aus – nächstes Spiel {hashtag}"),
                    Feld("schedule", "day_end", _("Feierabend des Tickers"),
                         _("Bis wann an einem Spieltag gelauscht wird; danach "
                           "beendet sich der Bot selbst."),
                         platzhalter="23:59"),
                ]),

        Bereich("pruefen", _("7 · Prüfen und nachziehen"),
                _("Nichts hiervon ändert deine Einstellungen – es sind "
                  "Auskünfte.\n\n"
                  "Die Prüfung vergleicht deine Datei mit dem, was die "
                  "Programme wirklich lesen. Sie findet Tippfehler und Reste "
                  "alter Fassungen, die sonst monatelang unbemerkt bleiben."),
                [
                    Feld(None, None, _("Konfiguration prüfen"),
                         _("Meldet Schlüssel, die kein Programm liest, und "
                           "solche, die fehlen und deshalb still auf ihrer "
                           "Vorgabe stehen."),
                         art="aktion", aktion="pruefung"),
                    Feld(None, None, _("Fehlende Schlüssel nachtragen"),
                         _("Trägt ein, was seit deiner Fassung dazugekommen "
                           "ist – samt der Erklärungen aus der Vorlage. "
                           "Vorhandene Werte bleiben unberührt."),
                         art="aktion", aktion="nachziehen"),
                    Feld(None, "theme", _("Farbton der Oberfläche"),
                         _("Wirkt sofort. Wer ein helles Terminal hat, findet "
                           "hier auch helle Töne – die Vorgabe ist für einen "
                           "dunklen Hintergrund gemacht."),
                         art="aktion", aktion="thema"),
                    Feld(None, "sprache", _("Sprache der Oberfläche"),
                         _("Gilt für diesen Assistenten. Die Protokolle der "
                           "Bots bleiben englisch, damit dieselbe Meldung "
                           "überall gleich aussieht."),
                         art="aktion", aktion="sprache"),
                ]),
    ]


CSS = """
Screen { layout: vertical; background: $surface; }

#rahmen { height: 1fr; }

/* "height: 1fr" ist hier nicht Geschmack, sondern noetig: ohne das misst
   sich die Liste an ihrem Inhalt und wird sieben Zeilen hoch - eine je
   Eintrag. Ein Polster an den Eintraegen selbst geht in diese Rechnung nicht
   ein, gezeichnet wird aber damit; was dann nicht mehr hineinpasst, bleibt
   schlicht leer. Genau so verschwanden die Menuepunkte. */
#navigation {
    width: 34;
    height: 1fr;
    border-right: solid $panel;
    padding: 1 1;
    background: $surface;
}

#inhalt {
    width: 1fr;
    padding: 1 2 2 2;
    align-horizontal: center;
}

/* Eine Zeile ist ab etwa 90 Zeichen schwer zu lesen - das Auge findet den
   Anfang der naechsten nicht mehr. Der Satz waechst deshalb nicht mit dem
   Fenster; die Spalte steht stattdessen in der Mitte, sonst klafft auf einem
   breiten Terminal rechts ein Loch von hundert Spalten. */
#spalte {
    width: 1fr;
    max-width: 96;
    height: auto;
}

#bereichsname {
    height: auto;
    text-style: bold;
    padding: 0 0 1 0;
}

#einleitung {
    height: auto;
    color: $text-muted;
    padding: 0 0 2 0;
    border-bottom: dashed $panel;
    margin-bottom: 1;
}

/* Ohne "height: auto" teilen sich die Feldbehaelter die verfuegbare Hoehe
   untereinander auf. Dann bleibt von jedem Feld nur die Beschriftung stehen -
   Eingabe und Erklaerung werden auf null Zeilen gequetscht. */
.feld { height: auto; padding: 0 0 2 0; }

.feldname { height: auto; text-style: bold; padding-bottom: 1; }

.hilfe {
    height: auto;
    color: $text-muted;
    padding-top: 1;
    padding-left: 1;
}

.feld Input, .feld Select {
    /* Mitwachsen, aber nicht ueber die Spalte hinaus: bei 96 Spalten ist die
       Spalte schmaler als ein festes Feld und das Feld liefe heraus. */
    width: 1fr;
    max-width: 68;
    border: round $panel;
}
.feld Input:focus, .feld Select:focus { border: round $accent; }

.feld Button { min-width: 34; }

#zuklein {
    padding: 2 4;
    color: $warning;
}

/* --- die Zwischenfenster ------------------------------------------------ */
ModalScreen { align: center middle; }

#kasten {
    width: 88;
    height: auto;
    max-height: 90%;
    border: round $primary;
    background: $surface;
    padding: 1 3 2 3;
}

#kastenname { text-style: bold; padding-bottom: 1; }

#kastentext { color: $text-muted; padding-bottom: 1; }

#knopfreihe { height: auto; padding-top: 1; align-horizontal: right; }
#knopfreihe Button { margin-left: 2; }

.ausgabe { padding: 1 0; }
"""


# ------------------------------------------------------------- small screens
class Hinweis(ModalScreen):
    """Something to read, with one way out."""

    BINDINGS = [("escape", "dismiss", "Schließen")]

    def __init__(self, name, text):
        super().__init__()
        self.kopf = name
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="kasten"):
            yield Label(self.kopf, id="kastenname")
            with VerticalScroll():
                yield Static(self.text, classes="ausgabe")
            with Horizontal(id="knopfreihe"):
                yield Button(_("Schließen"), variant="primary", id="zu")

    @on(Button.Pressed, "#zu")
    def zu(self):
        self.dismiss(None)


class Frage(ModalScreen[bool]):
    """Yes or no, with the consequence spelled out."""

    BINDINGS = [("escape", "nein", "Abbrechen")]

    def __init__(self, name, text, ja=None, nein=None):
        super().__init__()
        self.kopf = name
        self.text = text
        self.ja = ja or _("Ja")
        self.nein = nein or _("Abbrechen")

    def compose(self) -> ComposeResult:
        with Vertical(id="kasten"):
            yield Label(self.kopf, id="kastenname")
            yield Static(self.text, classes="ausgabe")
            with Horizontal(id="knopfreihe"):
                yield Button(self.nein, id="nein")
                yield Button(self.ja, variant="primary", id="ja")

    @on(Button.Pressed, "#ja")
    def _ja(self):
        self.dismiss(True)

    @on(Button.Pressed, "#nein")
    def _nein(self):
        self.dismiss(False)

    def action_nein(self):
        self.dismiss(False)


class Auswahl(ModalScreen[str]):
    """One out of a list, with a filter for the long ones."""

    BINDINGS = [("escape", "abbruch", "Abbrechen")]

    def __init__(self, name, text, eintraege, filtern=False):
        super().__init__()
        self.kopf = name
        self.text = text
        self.eintraege = eintraege
        self.filtern = filtern

    def compose(self) -> ComposeResult:
        with Vertical(id="kasten"):
            yield Label(self.kopf, id="kastenname")
            yield Static(self.text, id="kastentext")
            if self.filtern:
                yield Input(placeholder=_("Tippen filtert die Liste …"),
                            id="suche")
            yield OptionList(id="liste")
            with Horizontal(id="knopfreihe"):
                yield Button(_("Abbrechen"), id="nein")

    def on_mount(self):
        self._fuellen("")
        if self.filtern:
            self.query_one("#suche", Input).focus()
        else:
            self.query_one("#liste", OptionList).focus()

    def _fuellen(self, suche):
        liste = self.query_one("#liste", OptionList)
        liste.clear_options()
        suche = suche.lower()
        treffer = [(k, b) for k, b in self.eintraege if suche in str(b).lower()]
        for schluessel, beschriftung in treffer:
            liste.add_option(Option(str(beschriftung), id=str(schluessel)))
        # Immer etwas markieren, damit die Eingabetaste aus dem Suchfeld
        # heraus schon etwas zu uebernehmen hat.
        if treffer:
            liste.highlighted = 0
        if self.filtern:
            self.query_one("#kastentext", Static).update(
                _f("{treffer} von {gesamt}", treffer=len(treffer),
                   gesamt=len(self.eintraege)))

    def _markiertes(self):
        """Der gerade markierte Eintrag - oder None."""
        liste = self.query_one("#liste", OptionList)
        if liste.option_count and liste.highlighted is not None:
            return liste.get_option_at_index(liste.highlighted).id
        return None

    def on_key(self, ereignis):
        """Pfeiltasten und Eingabe wirken auch, waehrend man noch tippt.

        Ohne das liegt der Fokus im Suchfeld und die Liste daneben bekommt
        keine Taste ab: Man tippt drei Buchstaben, sieht den gesuchten Eintrag
        markiert vor sich und kommt nicht an ihn heran, ausser mit Tabulator.
        Das ist genau der Griff, den niemand errät."""
        if not self.filtern or self.focused is not self.query_one("#suche"):
            return
        liste = self.query_one("#liste", OptionList)
        if ereignis.key == "down":
            liste.action_cursor_down()
            ereignis.stop()
        elif ereignis.key == "up":
            liste.action_cursor_up()
            ereignis.stop()

    @on(Input.Changed, "#suche")
    def gefiltert(self, ereignis):
        self._fuellen(ereignis.value)

    @on(Input.Submitted, "#suche")
    def aus_der_suche(self):
        """Eingabe im Suchfeld nimmt den markierten Eintrag."""
        markiert = self._markiertes()
        if markiert is not None:
            self.dismiss(markiert)

    @on(OptionList.OptionSelected)
    def gewaehlt(self, ereignis):
        self.dismiss(ereignis.option.id)

    @on(Button.Pressed, "#nein")
    def abbrechen(self):
        self.dismiss(None)

    def action_abbruch(self):
        self.dismiss(None)


class Eingabe(ModalScreen[str]):
    """One value, with its explanation above it."""

    BINDINGS = [("escape", "abbruch", "Abbrechen")]

    def __init__(self, name, text, wert=""):
        super().__init__()
        self.kopf = name
        self.text = text
        self.wert = wert

    def compose(self) -> ComposeResult:
        with Vertical(id="kasten"):
            yield Label(self.kopf, id="kastenname")
            yield Static(self.text, id="kastentext")
            yield Input(value=self.wert or "", id="wert")
            with Horizontal(id="knopfreihe"):
                yield Button(_("Abbrechen"), id="nein")
                yield Button(_("Übernehmen"), variant="primary", id="ja")

    def on_mount(self):
        self.query_one("#wert", Input).focus()

    @on(Button.Pressed, "#ja")
    @on(Input.Submitted, "#wert")
    def uebernehmen(self):
        self.dismiss(self.query_one("#wert", Input).value)

    @on(Button.Pressed, "#nein")
    def abbrechen(self):
        self.dismiss(None)

    def action_abbruch(self):
        self.dismiss(None)


class Warten(ModalScreen):
    """While something on the network is happening."""

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="kasten"):
            yield Label(self.text, id="kastenname")
            yield LoadingIndicator()


# ------------------------------------------------------------ the assistant
class Assistent(App):
    """The window: sections on the left, one section at a time on the right."""

    CSS = CSS
    TITLE = "SkyRelay"
    BINDINGS = [
        ("ctrl+s", "speichern", N_("Speichern")),
        ("escape", "zurueck", N_("Zur Liste")),
        ("f1", "hilfe", N_("Hilfe")),
        ("ctrl+q", "beenden", N_("Beenden")),
    ]

    def __init__(self, zeilen, helfer):
        super().__init__()
        self.zeilen = zeilen
        self.gesichert = list(zeilen)
        self.helfer = helfer
        self.bereiche = bereiche()
        self.aktueller = self.bereiche[0]
        self.gespeichert = False

    # ------------------------------------------------------------- building
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="rahmen"):
            navigation = OptionList(
                *[Option(b.name, id=b.kennung) for b in self.bereiche],
                id="navigation")
            yield navigation
            yield VerticalScroll(id="inhalt")
        yield Footer()

    async def on_mount(self):
        self._thema_setzen()
        self._titel_setzen()
        self.query_one("#navigation", OptionList).focus()
        await self._bereich_zeigen(self.bereiche[0])
        if (self.size.width < MIN_BREITE or self.size.height < MIN_HOEHE):
            self.call_after_refresh(self._zu_klein_melden)

    def _zu_klein_melden(self):
        self.push_screen(Hinweis(
            _("Das Fenster ist recht klein"),
            _f("Der Assistent ist für mindestens {breite}×{hoehe} Zeichen "
               "gebaut, dein Fenster hat {ist_breite}×{ist_hoehe}.\n\n"
               "Er funktioniert auch so, aber die Erklärungen brechen in "
               "schmale Streifen um. Ein größeres Terminalfenster macht den "
               "Unterschied.",
               breite=MIN_BREITE, hoehe=MIN_HOEHE,
               ist_breite=self.size.width, ist_hoehe=self.size.height)))

    def _thema_setzen(self):
        """The colour the configuration asks for.

        An unknown name must not be fatal - a catalogue of themes changes
        with the library, and a configuration written a year ago should still
        open."""
        gewuenscht = (self.helfer.read_value(self.zeilen, "general", "theme")
                      or VORGABE_THEMA)
        if gewuenscht in self.available_themes:
            self.theme = gewuenscht
        else:
            self.theme = VORGABE_THEMA

    def _titel_setzen(self):
        stern = " *" if self.zeilen != self.gesichert else ""
        self.sub_title = f"{os.path.basename(self.helfer.TARGET)}{stern}"

    async def _bereich_zeigen(self, bereich):
        """Rebuilds the right-hand side for one section.

        Removing is awaited before mounting: Textual takes widgets away in its
        own time, and building the new heading while the old one is still
        there collides over the identifier."""
        self.aktueller = bereich
        inhalt = self.query_one("#inhalt", VerticalScroll)
        await inhalt.remove_children()
        spalte = _Gefuellt(
            [Label(bereich.name, id="bereichsname"),
             Static(bereich.einleitung, id="einleitung")]
            + [self._feld_bauen(feld) for feld in bereich.felder],
            id="spalte")
        await inhalt.mount(spalte)
        inhalt.scroll_home(animate=False)

    def _feld_bauen(self, feld):
        """One field: name, the thing you operate, the explanation below it.

        The explanation goes underneath rather than beside it - a column of
        its own would have to be narrow, and narrow is where text turns into
        ribbons."""
        if feld.art == "aktion":
            kinder = [
                Button(feld.name, id=feld.kennung),
                Static(feld.erklaerung, classes="hilfe"),
            ]
        else:
            wert = self.helfer.read_value(self.zeilen, feld.abschnitt,
                                          feld.schluessel)
            if feld.art == "schalter":
                bedienung = Switch(value=str(wert).lower() != "false",
                                   id=feld.kennung)
            elif feld.art == "auswahl":
                bedienung = Select(
                    [(_(name), schluessel) for schluessel, name in feld.optionen],
                    value=wert if any(wert == k for k, _n in feld.optionen)
                    else feld.optionen[0][0],
                    allow_blank=False, id=feld.kennung)
            else:
                bedienung = Input(value=wert or "",
                                  placeholder=feld.platzhalter,
                                  type="integer" if feld.art == "zahl" else "text",
                                  id=feld.kennung)
            kinder = [
                Label(feld.name, classes="feldname"),
                bedienung,
                Static(feld.erklaerung, classes="hilfe"),
            ]
        return _Gefuellt(kinder, classes="feld")

    # ------------------------------------------------------------- reacting
    @on(OptionList.OptionHighlighted, "#navigation")
    async def bereich_gewechselt(self, ereignis):
        for bereich in self.bereiche:
            if bereich.kennung == ereignis.option.id:
                await self._bereich_zeigen(bereich)
                break

    def _feld_zu(self, kennung):
        for bereich in self.bereiche:
            for feld in bereich.felder:
                if feld.kennung == kennung:
                    return feld
        return None

    @on(Input.Changed)
    def eingabe_geaendert(self, ereignis):
        feld = self._feld_zu(ereignis.input.id or "")
        if feld and feld.abschnitt:
            self.helfer.set_value(self.zeilen, feld.abschnitt, feld.schluessel,
                                  ereignis.value)
            self._titel_setzen()

    @on(Switch.Changed)
    def schalter_geaendert(self, ereignis):
        feld = self._feld_zu(ereignis.switch.id or "")
        if feld and feld.abschnitt:
            self.helfer.set_value(self.zeilen, feld.abschnitt, feld.schluessel,
                                  "true" if ereignis.value else "false")
            self._titel_setzen()

    @on(Select.Changed)
    def auswahl_geaendert(self, ereignis):
        feld = self._feld_zu(ereignis.select.id or "")
        if feld and feld.abschnitt and ereignis.value is not Select.BLANK:
            self.helfer.set_value(self.zeilen, feld.abschnitt, feld.schluessel,
                                  str(ereignis.value))
            self._titel_setzen()

    @on(Button.Pressed)
    def knopf(self, ereignis):
        name = (ereignis.button.id or "")
        if not name.startswith("a-"):
            return
        aufgabe = getattr(self, f"tue_{name[2:]}", None)
        if aufgabe:
            aufgabe()

    # -------------------------------------------------------------- actions
    @work
    async def tue_anmeldung(self):
        handle = self.helfer.read_value(self.zeilen, "bluesky", "handle")
        if not handle:
            await self.push_screen_wait(Hinweis(
                _("Kein Handle"),
                _("Trage zuerst das Handle des Ticker-Kontos ein.")))
            return
        variable = "BLUESKY_APP_PASSWORD"
        passwort = os.environ.get(variable)
        if not passwort:
            await self.push_screen_wait(Hinweis(
                _("Kein App-Passwort gesetzt"),
                _f("{variable} ist nicht gesetzt – ohne das Passwort lässt "
                   "sich die Anmeldung nicht prüfen.\n\n"
                   "In der Shell setzen:\n"
                   '    export {variable}="xxxx-xxxx-xxxx-xxxx"\n\n'
                   "Das App-Passwort legst du bei Bluesky an unter\n"
                   "Einstellungen → Datenschutz und Sicherheit → App-Passwörter.",
                   variable=variable)))
            return

        self.push_screen(Warten(_f("Melde mich als {handle} an …", handle=handle)))
        ergebnis = await self._anmelden(handle, passwort)
        self.pop_screen()
        await self.push_screen_wait(Hinweis(_("Anmeldung"), ergebnis))

    @work(thread=True)
    def _anmelden(self, handle, passwort):
        try:
            from atproto import Client
            Client().login(handle, passwort)
            return _f("✓ Anmeldung erfolgreich: {handle}", handle=handle)
        except ImportError:
            return _("Das Paket „atproto“ fehlt – bitte ./install.sh ausführen.")
        except Exception as fehler:
            return _f("✗ Anmeldung fehlgeschlagen:\n\n{fehler}\n\n"
                      "Handle und App-Passwort prüfen.", fehler=fehler)

    @work
    async def tue_liga(self):
        self.push_screen(Warten(_("Frage OpenLigaDB nach den Ligen …")))
        ligen = await self._ligen_holen()
        self.pop_screen()
        if isinstance(ligen, str):
            await self.push_screen_wait(Hinweis(_("Abruf fehlgeschlagen"), ligen))
            return

        wahl = await self.push_screen_wait(Auswahl(
            _("Liga wählen"),
            _("Alle Wettbewerbe mit laufender oder kommender Saison."),
            [(f'{l["leagueShortcut"]}|{l.get("leagueSeason", "")}',
              f'{l.get("leagueName", "")}  ·  {l["leagueShortcut"]}')
             for l in ligen if l.get("leagueShortcut")],
            filtern=True))
        if not wahl:
            return
        kuerzel, saison = wahl.split("|", 1)

        self.push_screen(Warten(_("Frage die Mannschaften ab …")))
        mannschaften = await self._mannschaften_holen(kuerzel, saison)
        self.pop_screen()
        if isinstance(mannschaften, str):
            await self.push_screen_wait(Hinweis(_("Abruf fehlgeschlagen"),
                                                mannschaften))
            return

        wahl = await self.push_screen_wait(Auswahl(
            _("Verein wählen"), _("Für welchen Verein läuft der Ticker?"),
            [(m["teamId"], f'{m.get("shortName") or m["teamName"]}'
                           f'  ·  {m["teamName"]}')
             for m in sorted(mannschaften, key=lambda m: m.get("shortName") or "")],
            filtern=True))
        if not wahl:
            return

        verein = next(m for m in mannschaften if str(m["teamId"]) == str(wahl))
        self.helfer.set_value(self.zeilen, "team", "openligadb_team_id",
                              verein["teamId"])
        suchbegriff = (verein.get("shortName")
                       or verein["teamName"]).split()[-1].lower()
        self.helfer.set_value(self.zeilen, "team", "openligadb_filter", suchbegriff)

        vorhandene = [s.strip() for s in
                      (self.helfer.read_value(self.zeilen, "team",
                                              "league_shortcuts") or "").split(",")
                      if s.strip()]
        if kuerzel not in vorhandene:
            vorhandene.append(kuerzel)
        self.helfer.set_value(self.zeilen, "team", "league_shortcuts",
                              ", ".join(vorhandene))

        codes = self.helfer.read_team_codes(self.zeilen)
        neu = 0
        for m in mannschaften:
            if m["teamId"] not in codes:
                codes[m["teamId"]] = self.helfer.suggest_code(m)[0]
                neu += 1
        self.helfer.set_team_codes(self.zeilen, codes)

        self._titel_setzen()
        await self._bereich_zeigen(self.aktueller)
        await self.push_screen_wait(Hinweis(
            _("Verein gesetzt"),
            _f("{verein}\n\n"
               "Suchbegriff: {suchbegriff}\n"
               "Liga ergänzt: {kuerzel}\n"
               "Kürzeltabelle: {neu} Mannschaft(en) dazugekommen, "
               "{alt} waren schon da.\n\n"
               "Unter „Kürzel für Hashtags“ kannst du sie durchsehen – "
               "abgeleitete Vorschläge sind nicht immer das übliche Kürzel.",
               verein=verein["teamName"], suchbegriff=suchbegriff,
               kuerzel=kuerzel, neu=neu, alt=len(codes) - neu)))

    @work(thread=True)
    def _ligen_holen(self):
        try:
            return self.helfer.fetch_current_leagues()
        except Exception as fehler:
            return _f("OpenLigaDB antwortet nicht: {fehler}", fehler=fehler)

    @work(thread=True)
    def _mannschaften_holen(self, kuerzel, saison):
        try:
            gefunden = self.helfer.fetch_teams(kuerzel, saison)
            if not gefunden:
                return _f("Für {kuerzel} (Saison {saison}) nennt OpenLigaDB "
                          "keine Mannschaften.", kuerzel=kuerzel, saison=saison)
            return gefunden
        except Exception as fehler:
            return _f("OpenLigaDB antwortet nicht: {fehler}", fehler=fehler)

    @work
    async def tue_kuerzel(self):
        while True:
            codes = self.helfer.read_team_codes(self.zeilen)
            if not codes:
                await self.push_screen_wait(Hinweis(
                    _("Noch keine Kürzel"),
                    _("Suche zuerst unter „Liga und Verein suchen“ eine Liga – "
                      "die Kürzeltabelle wird dabei gleich mit angelegt.")))
                return
            eigener = self.helfer.read_value(self.zeilen, "team",
                                             "openligadb_team_id")
            eintraege = []
            for nummer, code in sorted(codes.items(), key=lambda x: x[1]):
                marke = _("   ←  eigener Verein") if str(nummer) == eigener else ""
                eintraege.append((nummer, f"{code:<8} Nr. {nummer}{marke}"))
            wahl = await self.push_screen_wait(Auswahl(
                _("Kürzel für Hashtags"),
                _("Aus Heim + Auswärts entsteht der Spiel-Hashtag, z.B. "
                  "#KSCDSC.\nEintrag wählen zum Ändern."),
                eintraege, filtern=True))
            if not wahl:
                return
            neu = await self.push_screen_wait(Eingabe(
                _("Kürzel ändern"),
                _f("Kürzel für Team-Nummer {nummer}:", nummer=wahl),
                codes[int(wahl)]))
            if neu:
                codes[int(wahl)] = neu.strip().upper()
                self.helfer.set_team_codes(self.zeilen, codes)
                self._titel_setzen()

    @work
    async def tue_ligatags(self):
        while True:
            ligen = [s.strip().lower() for s in
                     (self.helfer.read_value(self.zeilen, "team",
                                             "league_shortcuts") or "").split(",")
                     if s.strip()]
            if not ligen:
                await self.push_screen_wait(Hinweis(
                    _("Noch keine Ligen"),
                    _("Trage zuerst unter „Spieltags-Ticker“ die Ligen ein, in "
                      "denen deine Mannschaften spielen.")))
                return
            tabelle = self.helfer.read_league_hashtags(self.zeilen)
            dauer = self.helfer.read_value(self.zeilen, "post", "standing_hashtag")
            eintraege = []
            for liga in ligen:
                if tabelle.get(liga):
                    eintraege.append((liga, f"{liga:<16} #{tabelle[liga]}"))
                elif dauer:
                    eintraege.append((liga, _f("{liga} (Dauer-Hashtag: #{tag})",
                                               liga=f"{liga:<16}", tag=dauer)))
                else:
                    eintraege.append((liga, f"{liga:<16} " + _("– keiner –")))
            wahl = await self.push_screen_wait(Auswahl(
                _("Hashtag je Liga"),
                _("Ohne Eintrag gilt der Dauer-Hashtag. Spielen mehrere "
                  "Mannschaften am selben Tag, stehen alle zugehörigen "
                  "Hashtags im Beitrag."),
                eintraege))
            if not wahl:
                return
            neu = await self.push_screen_wait(Eingabe(
                _("Hashtag der Liga"),
                _f("Hashtag für „{liga}“, ohne #\n"
                   "(leer = es gilt der Dauer-Hashtag):", liga=wahl),
                tabelle.get(wahl, "")))
            if neu is not None:
                tabelle[wahl] = neu.strip().lstrip("#")
                self.helfer.set_league_hashtags(self.zeilen, tabelle)
                self._titel_setzen()

    @work
    async def tue_aufbau(self):
        layout = self.helfer.layout
        while True:
            jetzt = self.helfer._layout_of(self.zeilen)
            eintraege = []
            for block in layout.LAYOUT_BLOCKS:
                wahl, stelle, folge = jetzt[block]
                eintraege.append((block,
                                  f"{_(self.helfer.BLOCK_LABELS[block]):<16}"
                                  f"{_(self.helfer.POST_LABELS[wahl]):<12}"
                                  f"{_(self.helfer.SPOT_LABELS[stelle]):<8}{folge}"))
            eintraege.append(("vorschau", _("Vorschau ansehen …")))
            gewaehlt = await self.push_screen_wait(Auswahl(
                _("Aufbau der Beiträge"),
                _("Baustein        Beiträge    Stelle  Reihenfolge\n"
                  "Die Reihenfolge zählt nur, wenn zwei Bausteine an derselben "
                  "Stelle landen."),
                eintraege))
            if not gewaehlt:
                return
            if gewaehlt == "vorschau":
                await self.push_screen_wait(Hinweis(
                    _("So sähen die Beiträge aus"),
                    self.helfer._layout_preview(self.zeilen)))
                continue

            wahl, stelle, folge = jetzt[gewaehlt]
            name = _(self.helfer.BLOCK_LABELS[gewaehlt])
            neu = await self.push_screen_wait(Auswahl(
                _f("{block}: an welchen Beiträgen?", block=name),
                _("Ein Thread entsteht, sobald eine Nachricht zu lang für "
                  "einen Beitrag wird."),
                [(k, _(self.helfer.POST_LABELS[k])) for k in layout.POST_SELECTORS]))
            if not neu:
                continue
            wahl = neu
            if wahl != "none":
                neu = await self.push_screen_wait(Auswahl(
                    _f("{block}: an welcher Stelle?", block=name),
                    _("Über oder unter dem eigentlichen Text."),
                    [(k, _(self.helfer.SPOT_LABELS[k])) for k in layout.SPOTS]))
                if not neu:
                    continue
                stelle = neu
                eingabe = await self.push_screen_wait(Eingabe(
                    _f("{block}: Reihenfolge", block=name),
                    _("Kleinere Zahl steht weiter vorne:"), str(folge)))
                if eingabe is None:
                    continue
                try:
                    folge = int(eingabe)
                except ValueError:
                    await self.push_screen_wait(Hinweis(
                        _("Keine Zahl"),
                        _f("„{eingabe}“ ist keine Zahl – die Reihenfolge "
                           "bleibt bei {folge}.", eingabe=eingabe, folge=folge)))
            self.helfer.set_value(self.zeilen, "layout", gewaehlt,
                                  f"{wahl} ; {stelle} ; {folge}")
            self._titel_setzen()

    @work
    async def tue_pruefung(self):
        befunde = self.helfer.config.collect_findings(
            self.helfer.BASE_DIR, "".join(self.zeilen))
        probleme = [t for schwere, t in befunde if schwere == "problem"]
        hinweise = [t for schwere, t in befunde if schwere == "note"]
        teile = []
        if probleme:
            teile.append(_("Probleme:") + "\n"
                         + "\n".join(f"  ✗ {t}" for t in probleme))
        if hinweise:
            teile.append(_("Hinweise:") + "\n"
                         + "\n".join(f"  ℹ {t}" for t in hinweise))
        if not teile:
            teile.append(_("✓ Nichts zu beanstanden."))
        teile.append(_f("{probleme} Problem(e), {hinweise} Hinweis(e).",
                        probleme=len(probleme), hinweise=len(hinweise)))
        await self.push_screen_wait(Hinweis(_("Konfiguration prüfen"),
                                            "\n\n".join(teile)))

    @work
    async def tue_nachziehen(self):
        entwurf = list(self.zeilen)
        dazu = self.helfer.config.add_missing_keys(entwurf, self.helfer.BASE_DIR)
        if not dazu:
            await self.push_screen_wait(Hinweis(
                _("Nichts nachzutragen"),
                _("Deine Datei kennt jeden Schlüssel, den die Programme "
                  "lesen.")))
            return
        liste = "\n".join(f"  [{abschnitt}] {schluessel} = {wert}"
                          for abschnitt, schluessel, wert in dazu)
        if await self.push_screen_wait(Frage(
                _("Fehlende Schlüssel nachtragen"),
                _f("{anzahl} Schlüssel fehlen. Sie werden samt ihrer "
                   "Erklärungen aus der Vorlage eingefügt; vorhandene Werte "
                   "bleiben unberührt.\n\n{liste}",
                   anzahl=len(dazu), liste=liste),
                ja=_("Nachtragen"))):
            self.zeilen[:] = entwurf
            self._titel_setzen()
            await self._bereich_zeigen(self.aktueller)

    @work
    async def tue_thema(self):
        jetzt = self.theme
        moeglich = sorted(self.available_themes)
        wahl = await self.push_screen_wait(Auswahl(
            _("Farbton der Oberfläche"),
            _("Wähle einen aus – er wirkt sofort. Abbrechen stellt den "
              "vorherigen wieder her."),
            [(name, name) for name in moeglich], filtern=True))
        if not wahl:
            self.theme = jetzt
            return
        self.theme = wahl
        self.helfer.set_value(self.zeilen, "general", "theme", wahl)
        self._titel_setzen()

    @work
    async def tue_sprache(self):
        import skyrelay_i18n
        moeglich = skyrelay_i18n.available()
        if len(moeglich) < 2:
            await self.push_screen_wait(Hinweis(
                _("Nur eine Sprache verfügbar"),
                _("Übersetzt ist bisher nur die Ausgangssprache.\n\n"
                  "Kataloge werden gebaut, nicht mitgeliefert:\n"
                  "    tools/i18n.sh compile")))
            return
        wahl = await self.push_screen_wait(Auswahl(
            _("Sprache / Language"),
            _("Gilt für diesen Assistenten. Protokoll und Konsolenausgabe der "
              "Bots bleiben englisch."),
            [(code, self.helfer.LANGUAGE_NAMES.get(code, code))
             for code in moeglich]))
        if not wahl:
            return
        self.helfer.set_value(self.zeilen, "general", "language", wahl)
        skyrelay_i18n.use(wahl)
        self.bereiche = bereiche()
        navigation = self.query_one("#navigation", OptionList)
        navigation.clear_options()
        for bereich in self.bereiche:
            navigation.add_option(Option(bereich.name, id=bereich.kennung))
        await self._bereich_zeigen(self.bereiche[0])
        self._titel_setzen()

    # --------------------------------------------------------- leaving again
    @work
    async def action_speichern(self):
        ticker = self.helfer.read_value(self.zeilen, "bluesky", "handle")
        feed = self.helfer.read_value(self.zeilen, "feed", "bluesky_handle") or ticker
        profil = self.helfer.read_value(self.zeilen, "feed", "instagram_profile")
        zusammenfassung = [
            _f("Ticker:  WhatsApp-Kanal  →  @{handle}", handle=ticker)
            if ticker else _("Ticker:  – kein Konto –")]
        if profil:
            zusammenfassung.append(_f("Feed:    @{profil}  →  @{handle}",
                                      profil=profil, handle=feed))
        if not await self.push_screen_wait(Frage(
                _("Speichern"),
                "\n".join(zusammenfassung)
                + _f("\n\nNach {datei} schreiben? Die bisherige Fassung wird "
                     "vorher als {datei}.bak gesichert.",
                     datei=os.path.basename(self.helfer.TARGET)),
                ja=_("Speichern"))):
            return
        fehler = self.helfer.schreibe(self.zeilen, self.gesichert)
        if fehler:
            await self.push_screen_wait(Hinweis(_("Nicht gespeichert"), fehler))
            return
        self.gesichert = list(self.zeilen)
        self.gespeichert = True
        self._titel_setzen()
        await self.push_screen_wait(Hinweis(
            _("Gespeichert"), self.helfer.naechste_schritte(self.zeilen)))

    @work
    async def action_beenden(self):
        if self.zeilen != self.gesichert:
            if not await self.push_screen_wait(Frage(
                    _("Ungespeicherte Änderungen"),
                    _("Es gibt Änderungen, die noch nicht in der Datei "
                      "stehen.\n\nWirklich beenden und verwerfen?"),
                    ja=_("Verwerfen und beenden"))):
                return
        self.exit(self.gespeichert)

    def action_zurueck(self):
        self.query_one("#navigation", OptionList).focus()

    @work
    async def action_hilfe(self):
        await self.push_screen_wait(Hinweis(
            _("Bedienung"),
            _("Tab / Umschalt+Tab   zwischen den Feldern\n"
              "Pfeiltasten          in der Liste links und in Auswahlfenstern\n"
              "Eingabe              Knopf auslösen, Auswahl übernehmen\n"
              "Esc                  zurück zur Bereichsliste, Fenster schließen\n"
              "Strg+S               speichern\n"
              "Strg+Q               beenden\n\n"
              "Die Maus geht auch: anklicken, scrollen, in Felder tippen.\n\n"
              "Geändertes wird sofort gemerkt, aber erst beim Speichern in die "
              "Datei geschrieben. Ein Stern hinter dem Dateinamen oben zeigt, "
              "dass etwas aussteht.")))


class _Gefuellt(Vertical):
    """A container that mounts its children when it is mounted itself.

    Textual wants widgets composed, not handed over as a list - and building
    one field at a time reads better where the sections are described."""

    def __init__(self, kinder, **kwargs):
        super().__init__(**kwargs)
        self._kinder = kinder

    def compose(self) -> ComposeResult:
        for kind in self._kinder:
            yield kind


def run(zeilen, helfer):
    """Opens the window. Returns True if something was written."""
    return bool(Assistent(zeilen, helfer).run())
