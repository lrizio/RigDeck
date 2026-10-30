"""The two radio panels and the centre FREQUENCIES panel."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..backends.base import LINK_ERROR, LINK_LIVE
from .theme import Theme, px
from .widgets import (S_LABELS, DeckButton, Fieldset, FreqDisplay, LedLabel, Screen, SegMeter, Tag,
                      s_meter_fraction)

PO_LABELS = [(0.25, "25"), (0.5, "50"), (0.75, "75"), (0.98, "100%")]


def set_css(widget, css: str) -> None:
    """setStyleSheet only when it changes (re-polishing every frame is slow)."""
    if widget.property("_css") != css:
        widget.setProperty("_css", css)
        widget.setStyleSheet(css)


def glow(widget, color: str, radius: int = 14):
    """LED-style glow behind a label's text."""
    eff = widget.graphicsEffect()
    if not isinstance(eff, QGraphicsDropShadowEffect):
        eff = QGraphicsDropShadowEffect(widget)
        eff.setOffset(0, 0)
        widget.setGraphicsEffect(eff)
    eff.setBlurRadius(px(radius))
    eff.setColor(QColor(color))


def fmt_freq(hz: int | None) -> str:
    if not hz:
        return "--.---.---"
    return f"{hz:,}".replace(",", ".")


def meter_value(kind: str, st: dict, maxw: float) -> float:
    if kind == "PO":
        return st.get("po", 0) / max(1.0, maxw)
    if kind == "ALC":
        return st.get("alc", 0)
    if kind == "SWR":
        return min(1.0, (st.get("swr", 1.0) - 1.0) / 2.0)        # 1:1 .. 3:1 full scale
    if kind == "COMP":
        return min(1.0, st.get("comp", 0) / 20.0)
    return 0.0


METER_SCALES = {
    "PO": ([(0.25, "25"), (0.5, "50"), (0.75, "75"), (0.97, "100%")], 0.9),
    "ALC": ([(0.25, "25"), (0.5, "50"), (0.75, "75"), (0.97, "MAX")], 0.8),
    "SWR": ([(0.0, "1"), (0.25, "1.5"), (0.5, "2"), (0.97, "3+")], 0.5),
    "COMP": ([(0.25, "5"), (0.5, "10"), (0.75, "15"), (0.97, "20dB")], 0.75),
}


def link_color(t: Theme, b) -> str:
    if b is None:
        return t.led_off
    if b.link == LINK_LIVE:
        return t.main
    return t.red if b.link == LINK_ERROR else t.amber


class RadioPanel(Fieldset):
    focusRequested = Signal()
    infoRequested = Signal()

    def __init__(self, theme: Theme, slot: str, color: str, parent=None):
        super().__init__(theme, slot.upper(), dot=theme.led_off, parent=parent)
        self.slot = slot
        self.color = color
        self._focused = False

        v = QVBoxLayout(self)
        v.setContentsMargins(*self.margins(14))
        v.setSpacing(px(6))

        head = QHBoxLayout()
        self.mode = QLabel("")
        self.mode.setStyleSheet(f"color:{color}; font-size:{px(19)}px; font-weight:700; font-family:Consolas;")
        glow(self.mode, color, 16)
        self.link = QLabel("")
        self.link.setStyleSheet(f"color:{theme.amber}; font-size:{px(12)}px;")
        self.info_btn = QPushButton("INFO")
        self.info_btn.setStyleSheet(f"font-size:{px(12)}px; padding:{px(3)}px {px(16)}px;")
        self.info_btn.clicked.connect(self.infoRequested)
        self.busy = LedLabel(theme, "BUSY")
        head.addWidget(self.mode)
        head.addStretch(1)
        head.addWidget(self.link)
        head.addSpacing(px(8))
        head.addWidget(self.info_btn)
        head.addSpacing(px(10))
        head.addWidget(self.busy)
        v.addLayout(head)

        self.screen = Screen(theme)
        sv = QVBoxLayout(self.screen)
        sv.setContentsMargins(px(14), px(8), px(14), px(10))
        sv.setSpacing(px(3))
        self.freq = FreqDisplay(theme, color)
        sv.addWidget(self.freq, 9)
        grid = QGridLayout()
        grid.setHorizontalSpacing(px(10))
        grid.setVerticalSpacing(px(1))
        self.tags: dict[str, Tag] = {}
        layout = [["ant", "rx_ant", "agc", "preamp", "psel"],
                  ["filter", "tbw", "nb1", "nb2", "nr", "bc"],
                  ["rit", "xit", "att", "notch", "vfo"]]
        for r, row in enumerate(layout):
            for c, key in enumerate(row):
                tag = Tag(theme)
                self.tags[key] = tag
                grid.addWidget(tag, r, c)
        sv.addLayout(grid)
        sv.addSpacing(px(2))
        self.meter = SegMeter(theme, segments=40, split=0.6, labels=S_LABELS, title="S")
        sv.addWidget(self.meter, 4)
        v.addWidget(self.screen, 1)

    def set_focused(self, on: bool):
        self._focused = on
        # edge set in refresh() together with the title/dot

    def mousePressEvent(self, e):
        self.focusRequested.emit()
        super().mousePressEvent(e)

    def refresh(self, b, st: dict, maxw: float):
        t = self.t
        live = b is not None and b.link == LINK_LIVE
        model = b.model if b else "NOT USED"
        self.set_title(f"{self.slot.upper()}  ·  {model}", dot=link_color(t, b),
                       edge=self.color if self._focused else t.edge, edge_w=3 if self._focused else 2)
        self.mode.setText(b.mode_label(st) if b and live else "")
        if b is None:
            self.link.setText("")
        elif b.link != LINK_LIVE:
            msg = b.link if not b.error else f"{b.link}: {b.error}"
            self.link.setText(msg if len(msg) < 58 else msg[:56] + "…")
            self.link.setToolTip(msg)
        else:
            self.link.setText("")
        self.freq.set_value(st.get("freq") if b else None, live)

        tx = bool(st.get("tx")) and live
        busy = st.get("busy") if live else None
        if tx:
            self.busy.set_state("TX", t.red, True)
        else:
            self.busy.set_state("BUSY", t.main, bool(busy), available=busy is not None)
            self.busy.setToolTip("" if busy is not None or not live else
                                 "This radio does not report squelch/busy status over CAT")

        def ctl(key):
            return b.controls.get(key) if b else None

        def onoff(key, default_label):
            c = ctl(key)
            label = c.label if c else default_label
            if not c or not live:
                self.tags[key].show_state(f"{label} —" if not c else label, False, available=bool(c))
                return
            on = bool(st.get(key))
            self.tags[key].show_state(f"{label} {'ON' if on else 'OFF'}", on)

        def choice(key, default_label, off_values=("OFF",), fmt=None):
            c = ctl(key)
            if not c or not live:
                self.tags[key].show_state((c.label if c else default_label) + ("" if c else " —"),
                                          False, available=bool(c))
                return
            val = st.get(key, "")
            self.tags[key].show_state(fmt(val) if fmt else f"{c.label} {val}", val not in off_values)

        choice("ant", "ANT", off_values=("ANT1",), fmt=lambda v: v.replace("ANT", "ANT "))
        onoff("rx_ant", "RX ANT")
        choice("agc", "AGC", off_values=(),
               fmt=lambda v: f"AGC-{v[:1]}" if v in ("FAST", "MID", "SLOW") else f"AGC {v}")
        choice("preamp", "PRE", off_values=("OFF", "IPO"), fmt=lambda v: "PRE OFF" if v == "OFF" else v)
        onoff("psel", "PSEL")
        f = ctl("filter")
        if f and live:
            self.tags["filter"].show_state(f"RX {st.get('filter', '')}", True, color=self.color)
        else:
            self.tags["filter"].show_state("RX FIL", False, available=bool(f))
        c = ctl("tbw")
        if c and live:
            self.tags["tbw"].show_state(f"TBW {st.get('tbw', '')}", True, color=t.amber)
        else:
            self.tags["tbw"].show_state("TBW —" if not c else "TBW", False, available=bool(c))
        for key, lbl in (("nb1", "NB1"), ("nb2", "NB2"), ("nr", "NR"), ("bc", "BC")):
            onoff(key, lbl)
        rit_c = ctl("rit")
        if rit_c and live:
            on = bool(st.get("rit"))
            hz = st.get("rit_hz", 0)
            self.tags["rit"].show_state(f"{rit_c.label} {hz:+d}" if on else f"{rit_c.label} OFF", on)
        else:
            self.tags["rit"].show_state("RIT", False, available=bool(rit_c))
        onoff("xit", "XIT")
        choice("att", "ATT", fmt=lambda v: f"ATT {v}")
        onoff("notch", "NOTCH")
        if live:
            if st.get("split"):
                self.tags["vfo"].show_state("SPLIT", True, color=t.amber)
            else:
                self.tags["vfo"].show_state(st.get("vfo", "VFO"), True, color=self.color)
        else:
            self.tags["vfo"].show_state("VFO", False)

        if tx:
            self.meter.configure(PO_LABELS, "PO", 0.9, lo=t.meter_lo, hi=t.meter_hi)
            self.meter.set_value(st.get("po", 0) / max(1.0, maxw))
        else:
            if self.meter.title != "S":
                self.meter.configure(S_LABELS, "S", 0.6)
            self.meter.set_value(s_meter_fraction(st.get("smeter", -54)) if live else 0.0)


class CenterPanel(Fieldset):
    down = Signal()
    up = Signal()
    enter = Signal()

    def __init__(self, theme: Theme, parent=None):
        super().__init__(theme, "FREQUENCIES", dot=theme.amber, parent=parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(*self.margins(14))
        v.setSpacing(px(8))

        scr = Screen(theme)
        sv = QVBoxLayout(scr)
        sv.setContentsMargins(px(14), px(8), px(14), px(8))
        sv.setSpacing(px(2))

        def row(left, right):
            h = QHBoxLayout()
            h.addWidget(left)
            h.addStretch(1)
            h.addWidget(right)
            sv.addLayout(h)

        self.hdr = QLabel("POWER")
        self.hdr.setStyleSheet(f"color:{theme.dim}; font-size:{px(13)}px;")
        self.pwr = QLabel("--")
        self.pwr.setStyleSheet(f"color:{theme.amber}; font-size:{px(17)}px; font-weight:700; font-family:Consolas;")
        glow(self.pwr, theme.amber)
        row(self.hdr, self.pwr)
        self.main_lbl, self.main_f = QLabel("MAIN"), QLabel("--")
        self.sub_lbl, self.sub_f = QLabel("SUB"), QLabel("--")
        for l in (self.main_lbl, self.sub_lbl):
            l.setStyleSheet(f"color:{theme.dim}; font-size:{px(13)}px;")
        self.main_f.setStyleSheet(f"color:{theme.main}; font-size:{px(21)}px; font-weight:700; font-family:Consolas;")
        self.sub_f.setStyleSheet(f"color:{theme.sub}; font-size:{px(21)}px; font-weight:700; font-family:Consolas;")
        glow(self.main_f, theme.main)
        glow(self.sub_f, theme.sub)
        row(self.main_lbl, self.main_f)
        row(self.sub_lbl, self.sub_f)
        self.splitl, self.xitl = QLabel("SIMPLEX"), QLabel("XIT OFF")
        for l in (self.splitl, self.xitl):
            l.setStyleSheet(f"color:{theme.dim}; font-size:{px(13)}px;")
        row(self.splitl, self.xitl)
        sv.addSpacing(px(4))
        self.meter_title = QLabel("ALC")
        self.meter_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.meter_title.setStyleSheet(f"color:{theme.text}; font-size:{px(13)}px; font-weight:600;")
        sv.addWidget(self.meter_title)
        self.meter = SegMeter(theme, segments=28, split=0.8)
        sv.addWidget(self.meter, 1)
        v.addWidget(scr, 5)

        h = QHBoxLayout()
        h.setSpacing(px(8))
        self.b_down = DeckButton(theme, "▼")
        self.b_ent = DeckButton(theme, "ENT")
        self.b_up = DeckButton(theme, "▲")
        self.b_down.clicked.connect(self.down)
        self.b_up.clicked.connect(self.up)
        for b in (self.b_down, self.b_ent, self.b_up):
            b.stepped.connect(lambda d: (self.up if d > 0 else self.down).emit())
        self.b_ent.clicked.connect(self.enter)
        self.b_ent.setToolTip("▼/▲ (or the wheel) adjust the selected item.\n"
                              "ENT: back to tuning; when tuning, type a frequency.")
        for b in (self.b_down, self.b_ent, self.b_up):
            h.addWidget(b)
        v.addLayout(h, 2)
