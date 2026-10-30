"""The console window: two radio panels, centre readout, band scale and the
button deck. The button deck always acts on the selected radio (MAIN or
SUB) and relabels itself with that radio's own names for things."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QSizeGrip, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import config, takeover
from ..backends.base import LINK_ERROR, LINK_LIVE
from ..share import ShareServer
from ..updatecheck import PAGE_URL, UpdateChecker
from .dialogs import DiagDialog, SetupDialog
from .panels import CenterPanel, RadioPanel, METER_SCALES, fmt_freq, meter_value, set_css
from .theme import DESIGN_H, DESIGN_W, THEMES, px, scale, set_scale, stylesheet, theme_by_name
from .scope import SpectrumScope
from .widgets import BandScale, DeckButton, Fieldset

ZOOMS = [0.8, 1.0, 1.25, 1.5, 2.0]
TUNE_STEP_HZ = 1000
LEVEL_KEYS = {"power": "PWR", "rf": "RF GAIN", "sql": "SQL", "af": "AF", "mon_level": "MON", "rit_hz": "RIT",
              "scope_ref": "REF"}
SCOPE_H = 235            # extra design height when a radio supplies a spectrum scope


def passband(mode: str, dial: int, filt: str) -> tuple[int, int] | None:
    """Rough RX passband for shading the scope, from the filter label ('FIL1 3.0k', '2.4k', '500')."""
    tok = (filt or "").split()[-1] if filt else ""
    try:
        w = float(tok[:-1]) * 1000 if tok.endswith("k") else float(tok)
    except ValueError:
        return None
    if mode.startswith("USB") or mode in ("RTTY-R", "PSK", "RTTY-U", "DATA-U"):
        return dial + 100, dial + int(w)
    if mode.startswith("LSB") or mode in ("RTTY", "PSK-R", "RTTY-L"):
        return dial - int(w), dial - 100
    return dial - int(w / 2), dial + int(w / 2)


def parse_freq(text: str) -> int | None:
    """'7.074' / '7074' / '7074000' / '14.074.000' -> Hz."""
    t = text.strip().lower().replace(",", "").replace(" ", "")
    mult = 1
    for suffix, m in (("mhz", 1e6), ("khz", 1e3), ("hz", 1)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)], m
            break
    if t.count(".") > 1:                      # 14.074.000 style
        t = t.replace(".", "")
        mult = 1
    try:
        v = float(t)
    except ValueError:
        return None
    if mult == 1 and "." in t:
        mult = 1e6 if v < 1000 else 1e3
    elif mult == 1:
        mult = 1e6 if v < 1000 else (1e3 if v < 1e6 else 1)
    hz = int(round(v * mult))
    return hz if 30_000 <= hz <= 3_000_000_000 else None


class DragBar(QFrame):
    """Top bar that moves the frameless window."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._off: QPoint | None = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self.win.isFullScreen():
            self._off = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._off is not None:
            self.win.move(e.globalPosition().toPoint() - self._off)

    def mouseReleaseEvent(self, _):
        self._off = None


class DeckWindow(QWidget):
    def __init__(self, cfg: dict, icon: QIcon | None = None):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("Rig Deck")
        if icon:
            self.setWindowIcon(icon)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.focus_slot = "main"
        self._update: tuple[str, str] | None = None
        self.adjust = "TUNE"
        self.meter_kind = "ALC"
        self.track = False
        self._track_last: int | None = None
        self._muted: dict[str, float] = {}
        self._tx_since: dict[str, float] = {}
        self._diag: DiagDialog | None = None
        self._auto_focus_until = time.monotonic() + 6     # start on whichever radio comes up
        self._zoom_before_full = cfg.get("zoom", 1.0)
        self.backends = {slot: config.make_backend(cfg["radios"][slot], slot) for slot in ("main", "sub")}
        for b in self.backends.values():
            if b:
                b.start()
        self.shares: dict[str, ShareServer] = {}
        self.start_shares()

        self.root: QWidget | None = None
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._init_resize()
        saved = cfg.get("win_size")
        self.apply_zoom(cfg.get("zoom", 1.0))
        if saved and len(saved) == 2:
            scr = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
            w, h = min(int(saved[0]), scr.width()), min(int(saved[1]), scr.height())
            if w >= DESIGN_W * 0.5 and h >= self.design_h() * 0.5:
                self.resize(w, h)
                self._refit()
                self.cfg["win_size"] = [w, h]

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(50)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick_clock)
        self.clock.start(1000)
        self._takeover_thread: threading.Thread | None = None
        self._takeover_next: dict[str, float] = {}
        self.share_timer = QTimer(self)
        self.share_timer.timeout.connect(self.retry_shares)
        self.share_timer.start(1000)
        if cfg.get("check_updates", True) and not cfg.get("_demo"):
            self._checker = UpdateChecker(config.VERSION, self)
            self._checker.found.connect(self._update_found)
            QTimer.singleShot(4000, self._checker.start)
        QShortcut(QKeySequence("F11"), self, activated=self.toggle_full)
        QShortcut(QKeySequence("Escape"), self, activated=lambda: self.isFullScreen() and self.toggle_full())
        QShortcut(QKeySequence("Tab"), self, activated=lambda: self.set_focus("sub" if self.focus_slot == "main" else "main"))

    # ---- build ---------------------------------------------------------------

    @property
    def fb(self):
        return self.backends.get(self.focus_slot)

    def other(self, slot):
        return "sub" if slot == "main" else "main"

    def scope_slot(self):
        """The radio the scope panel shows: the selected one if it has a scope
        (so with an IC-705 and an IC-7610 on the deck it follows MAIN/SUB),
        else whichever one does."""
        has = lambda s: (b := self.backends.get(s)) is not None and "scope_span" in b.controls
        if has(self.focus_slot):
            return self.focus_slot
        for slot in ("main", "sub"):
            if has(slot):
                return slot
        return None

    def design_h(self):
        return DESIGN_H + (SCOPE_H if self.scope_slot() else 0)

    def apply_zoom(self, z: float, full: bool = False):
        screen = (self.screen() or QGuiApplication.primaryScreen()).availableGeometry()
        if full:
            s = min(screen.width() / DESIGN_W, screen.height() / self.design_h())
        else:
            s = min(z, screen.width() / DESIGN_W * 0.98, screen.height() / self.design_h() * 0.96)
            self.cfg["zoom"] = z
            self.cfg.pop("win_size", None)       # a zoom button overrides a dragged size
        set_scale(s)
        self.build_ui()
        if not full:
            self._fitting = True
            self.setMinimumSize(round(DESIGN_W * 0.5), round(self.design_h() * 0.5))
            self.setMaximumSize(16777215, 16777215)
            self.resize(round(DESIGN_W * s), round(self.design_h() * s))
            self._fitting = False
            self._place_grip()

    # -- free resizing: the whole UI re-scales to fit the window (debounced)

    def _init_resize(self):
        self._fitting = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._refit)
        self._grip = QSizeGrip(self)
        self._grip.setFixedSize(16, 16)
        self._grip.raise_()

    def _place_grip(self):
        g = getattr(self, "_grip", None)
        if g is not None:
            g.move(self.width() - g.width(), self.height() - g.height())
            g.setVisible(not self.isFullScreen())
            g.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_grip()
        if hasattr(self, "_fit_timer") and not self._fitting and not self.isFullScreen():
            self._fit_timer.start(120)

    def _refit(self):
        from .theme import scale as cur_scale
        if not self.isFullScreen():
            self.cfg["win_size"] = [self.width(), self.height()]
            config.save(self.cfg)
        s = min(self.width() / DESIGN_W, self.height() / self.design_h())
        if abs(s - cur_scale()) / cur_scale() < 0.02:
            return
        self._fitting = True
        set_scale(s)
        self.build_ui()
        self._fitting = False
        self._place_grip()

    def build_ui(self):
        self.t = theme_by_name(self.cfg.get("theme", "SHACK"))
        QApplication.instance().setStyleSheet(stylesheet(self.t))
        if self.root is not None:
            self.root.setParent(None)
            self.root.deleteLater()
        self.root = QWidget()
        self._outer.addWidget(self.root)
        t = self.t
        v = QVBoxLayout(self.root)
        v.setContentsMargins(px(14), px(4), px(14), px(6))
        v.setSpacing(px(6))

        # -- top bar
        bar = DragBar(self)
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(px(6), px(4), 0, 0)
        hb.setSpacing(px(6))
        call = self.cfg.get("callsign", "")
        title = QLabel(f"{config.APP_NAME}  v{config.VERSION}" + (f"  ·  {call}" if call else ""))
        title.setObjectName("title")
        hb.addWidget(title)
        hb.addSpacing(px(30))
        self.zoom_btns = []
        for z in ZOOMS:
            b = QPushButton(f"×{z:g}")
            b.setCheckable(True)
            b.setChecked(not self.isFullScreen() and abs(self.cfg.get("zoom", 1) - z) < 0.01)
            b.clicked.connect(lambda _=False, z=z: self._zoom_clicked(z))
            b.setMinimumWidth(px(62))
            hb.addWidget(b)
        fullb = QPushButton("FULL")
        fullb.setCheckable(True)
        fullb.setChecked(self.isFullScreen())
        fullb.clicked.connect(self.toggle_full)
        fullb.setMinimumWidth(px(62))
        hb.addWidget(fullb)
        hb.addStretch(1)
        self.link_lbl = QLabel("")
        self.link_lbl.setStyleSheet(f"font-size:{px(13)}px; font-weight:600;")
        hb.addWidget(self.link_lbl)
        hb.addSpacing(px(12))
        self.update_btn = QPushButton("")
        self.update_btn.setStyleSheet("QPushButton{color:#ffb000;font-weight:700;}")
        self.update_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self._update[1] if self._update else PAGE_URL)))
        self.update_btn.hide()
        hb.addWidget(self.update_btn)
        self._show_update()
        for text, fn in (("GUIDE", self.show_guide), ("DIAG", self.show_diag), ("THEME", self.next_theme),
                         ("SETUP", self.show_setup),
                         ("–", self.showMinimized), ("CLOSE", self.close)):
            b = QPushButton(text)
            b.setMinimumWidth(px(44 if text == "–" else 84))
            b.clicked.connect(fn)
            hb.addWidget(b)
        v.addWidget(bar)

        self.btn: dict[str, DeckButton] = {}

        # -- radio panels + centre
        hp = QHBoxLayout()
        hp.setSpacing(px(14))
        self.panels = {"main": RadioPanel(t, "main", t.main), "sub": RadioPanel(t, "sub", t.sub)}
        self.center = CenterPanel(t)
        hp.addWidget(self.panels["main"], 51)
        hp.addWidget(self.center, 31)
        hp.addWidget(self.panels["sub"], 51)
        for slot, p in self.panels.items():
            p.focusRequested.connect(lambda s=slot: self.set_focus(s))
            p.infoRequested.connect(lambda s=slot: self.show_info(s))
            p.freq.tune.connect(lambda d, s=slot: self.tune(s, d))
            p.freq.entryRequested.connect(lambda s=slot: self.enter_freq(s))
        self.center.up.connect(lambda: self.adjust_step(+1))
        self.center.down.connect(lambda: self.adjust_step(-1))
        self.center.enter.connect(self.adjust_enter)
        v.addLayout(hp, 30)

        self.scope = None
        slot = self.scope_slot()
        if slot:
            b = self.backends[slot]
            color = t.main if slot == "main" else t.sub
            self.scope_box = Fieldset(t, f"SCOPE  \u00b7  {b.model}", dot=color)
            sh = QHBoxLayout(self.scope_box)
            sh.setContentsMargins(*self.scope_box.margins(12))
            sh.setSpacing(px(12))
            self.scope = SpectrumScope(t, color)
            self._scope_shown = slot
            self.scope.qsy.connect(lambda hz: self.backends[self.scope_slot()].set("freq", hz))
            self.scope.tune.connect(lambda d: self.tune(self.scope_slot(), d))
            sh.addWidget(self.scope, 1)
            col = QVBoxLayout()
            col.setSpacing(px(8))
            for key, label, arrows in (("scope_span", "SPAN", True), ("scope_mode", "CTR / FIX", False),
                                       ("scope_ref", "REF", True)):
                btn = DeckButton(t, label, arrows=arrows)
                btn.setMinimumWidth(px(150))
                btn.clicked.connect(lambda k=key: self.on_scope_click(k))
                btn.stepped.connect(lambda d, k=key: self.on_scope_step(k, d))
                btn.menuRequested.connect(lambda k=key: self.on_scope_menu(k))
                self.btn[key] = btn
                col.addWidget(btn)
            sh.addLayout(col)
            v.addWidget(self.scope_box, 23)

        # -- band scale
        sb = Fieldset(t, "BAND", dot=t.sub)
        sl = QVBoxLayout(sb)
        sl.setContentsMargins(*sb.margins(10))
        self.scale = BandScale(t, self.cfg.get("band_plan", config.DEFAULT_BAND_PLAN))
        self.scale.qsy.connect(lambda hz: self.fb and self.fb.set("freq", hz))
        self.scale.tune.connect(lambda d: self.tune(self.focus_slot, d))
        sl.addWidget(self.scale)
        v.addWidget(sb, 8)

        # -- button deck
        lower = QHBoxLayout()
        lower.setSpacing(px(12))
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(px(10))
        right.setSpacing(px(10))
        top_left = QHBoxLayout()
        top_left.setSpacing(px(10))
        top_left.addWidget(self._block([["main", "sub", "power_sel", "meter"],
                                        ["rit", "clr", "xit", "atu", "split"]], "SELECT · VFO"), 6)
        ptt_block = Fieldset(t, "TX", dot=t.red)
        pl = QVBoxLayout(ptt_block)
        pl.setContentsMargins(*ptt_block.margins(12))
        self.btn["ptt"] = DeckButton(t, "PTT", role="red", tall=True)
        self.btn["ptt"].clicked.connect(self.toggle_ptt)
        self.btn["ptt"].setToolTip("Click to transmit on the selected radio, click again to stop.\n"
                                   "Released automatically after the time-out set in SETUP.")
        pl.addWidget(self.btn["ptt"])
        top_left.addWidget(ptt_block, 1)
        left.addLayout(top_left, 2)
        left.addWidget(self._block([["ant", "rx_ant", "agc", "att"],
                                    ["preamp", "psel", "filter", "tbw"]], "RECEIVER"), 2)
        right.addWidget(self._block([["m2s", "swap", "track", "mode", "data"]], "MAIN ⇄ SUB"), 1)
        right.addWidget(self._block([["vox", "proc", "mon", "af_sel"],
                                     ["rf_sel", "sql_sel", "mute", "nr"],
                                     ["nb1", "nb2", "notch", "bc"]], "DSP · AUDIO"), 3)
        lower.addLayout(left, 60)
        lower.addLayout(right, 62)
        v.addLayout(lower, 33)

        # -- footer
        fh = QHBoxLayout()
        foot = QLabel(f"{config.APP_NAME}  •  DUAL CONSOLE  •  v{config.VERSION}")
        foot.setObjectName("footer")
        self.clock_lbl = QLabel("")
        self.clock_lbl.setObjectName("footer")
        fh.addWidget(foot)
        fh.addStretch(1)
        fh.addWidget(self.clock_lbl)
        v.addLayout(fh)
        self._tick_clock()
        self.refresh()

    BUTTONS = {
        # key: (label, role, arrows)
        "main": ("MAIN", "green", False), "sub": ("SUB", "teal", False),
        "power_sel": ("PWR", "teal", False), "meter": ("METER", "teal", False),
        "rit": ("RIT", "teal", True), "clr": ("CLR", "teal", False), "xit": ("XIT", "teal", True),
        "atu": ("ATU", "teal", False), "split": ("SPLIT", "amber", True),
        "ant": ("ANT", "teal", False), "rx_ant": ("RX ANT", "teal", False), "agc": ("AGC", "teal", False),
        "att": ("ATT", "amber", False), "preamp": ("PRE AMP", "teal", False), "psel": ("PSEL", "teal", False),
        "filter": ("RX FILTER", "teal", True), "tbw": ("TX FILTER", "teal", False),
        "m2s": ("M > S", "teal", False), "swap": ("M / S", "teal", False), "track": ("TRACK", "teal", False),
        "mode": ("MODE", "teal", False), "data": ("DATA", "teal", False),
        "vox": ("VOX", "amber", False), "proc": ("PROC", "purple", False), "mon": ("MON", "teal", True),
        "af_sel": ("AF", "teal", True), "rf_sel": ("RF GAIN", "teal", False), "sql_sel": ("SQL", "teal", False),
        "mute": ("MUTE", "amber", False), "nr": ("NR", "teal", False),
        "nb1": ("NB1", "teal", False), "nb2": ("NB2", "teal", False), "notch": ("NOTCH", "teal", False),
        "bc": ("BC", "teal", False),
    }

    LED_KEYS = {"rit", "xit", "atu", "split", "rx_ant", "psel", "vox", "proc", "mon", "nr", "nb1", "nb2",
                "notch", "bc", "data", "track", "mute"}

    def _block(self, rows, title=""):
        f = Fieldset(self.t, title)
        v = QVBoxLayout(f)
        v.setContentsMargins(*f.margins(12))
        v.setSpacing(px(10))
        for row in rows:
            h = QHBoxLayout()
            h.setSpacing(px(8))
            for key in row:
                label, role, arrows = self.BUTTONS[key]
                b = DeckButton(self.t, label, role=role, arrows=arrows, led=key in self.LED_KEYS)
                b.clicked.connect(lambda k=key: self.on_click(k))
                b.stepped.connect(lambda d, k=key: self.on_step(k, d))
                b.menuRequested.connect(lambda k=key: self.on_menu(k))
                if key == "atu":             # hold ATU = start a tune cycle, as on the radio
                    b.holdable = True
                    b.held.connect(self.start_tune)
                self.btn[key] = b
                h.addWidget(b)
            v.addLayout(h)
        return f

    # ---- actions ---------------------------------------------------------------

    def set_focus(self, slot):
        if self.backends.get(slot) is None:
            return
        self._auto_focus_until = 0                          # the user has chosen
        self.focus_slot = slot
        if self.adjust not in ("TUNE",) and not self.fb.supports(self.adjust):
            self.adjust = "TUNE"
        self.refresh()

    def tune(self, slot, delta):
        b = self.backends.get(slot)
        if b and b.link == LINK_LIVE:
            f = b.snapshot().get("freq")
            if f:
                b.set("freq", f + delta)

    def enter_freq(self, slot):
        b = self.backends.get(slot)
        if not b:
            return
        text, ok = QInputDialog.getText(self, "Frequency", f"{slot.upper()} {b.model} frequency\n"
                                        "(e.g. 7.074, 14074 or 14074000):")
        if ok:
            hz = parse_freq(text)
            if hz:
                b.set("freq", hz)
            else:
                QMessageBox.warning(self, "Frequency", f"Could not read '{text}' as a frequency.")

    def _level(self, key, delta_steps):
        b = self.fb
        if not b or not b.supports(key):
            return
        c = b.controls[key]
        st = b.snapshot()
        step = c.step if key in ("power", "rit_hz", "scope_ref") else 2
        cur = st.get(key, c.lo)
        b.set(key, max(c.lo, min(c.hi, cur + delta_steps * step)))
        if key == "af":
            self._muted.pop(self.focus_slot, None)

    def adjust_step(self, d):
        if self.adjust == "TUNE":
            self.tune(self.focus_slot, d * TUNE_STEP_HZ)
        else:
            self._level(self.adjust, d)

    def adjust_enter(self):
        if self.adjust != "TUNE":
            self.adjust = "TUNE"
            self.refresh()
        else:
            self.enter_freq(self.focus_slot)

    def _select_adjust(self, key):
        self.adjust = "TUNE" if self.adjust == key else key
        self.refresh()

    def _toggle(self, key):
        b = self.fb
        if b and b.supports(key):
            b.set(key, not b.snapshot().get(key))

    def _cycle(self, key, d=1):
        b = self.fb
        if b and b.supports(key):
            opts = b.controls[key].options
            cur = b.snapshot().get(key)
            i = opts.index(cur) if cur in opts else -1
            b.set(key, opts[(i + d) % len(opts)])

    def on_click(self, key):
        b = self.fb
        st = b.snapshot() if b else {}
        if key in ("main", "sub"):
            self.set_focus(key)
        elif key == "power_sel":
            self._select_adjust("power")
        elif key == "af_sel":
            self._select_adjust("af")
        elif key == "rf_sel":
            self._select_adjust("rf")
        elif key == "sql_sel":
            self._select_adjust("sql")
        elif key == "meter":
            meters = (b.meters if b else []) or ["ALC"]
            i = meters.index(self.meter_kind) if self.meter_kind in meters else -1
            self.meter_kind = meters[(i + 1) % len(meters)]
        elif key == "clr":
            b and b.action("rit_clear")
        elif key == "split":
            if b and b.supports("split"):
                on = not st.get("split")
                fa, fb_ = st.get("freq"), st.get("freq_b")
                if on and fa and (not fb_ or abs(fb_ - fa) > 500_000):
                    b.set("freq_b", fa + 1000)          # sensible start: up 1 kHz
                b.set("split", on)
        elif key == "filter":
            b and b.supports("filter") and b.action("filter_click")
        elif key in ("ant", "agc", "att", "preamp", "tbw"):
            self._cycle(key)
        elif key == "mode":
            self.on_menu("mode")
        elif key == "m2s":
            self.copy_main_to_sub()
        elif key == "swap":
            self.swap_main_sub()
        elif key == "track":
            self.track = not self.track
            self._track_last = None
        elif key == "mute":
            self.toggle_mute()
        else:
            self._toggle(key)
        self.refresh()

    def _scope_blank(self):
        """True when the scope panel exists but the selected radio has no scope."""
        slot = self.scope_slot()
        return slot is not None and slot != self.focus_slot

    def _scope_backend(self):
        if self._scope_blank():
            return None
        slot = self.scope_slot()
        return self.backends.get(slot) if slot else None

    def on_scope_click(self, key):
        b = self._scope_backend()
        if not b:
            return
        if key == "scope_ref":
            self.focus_slot = self.scope_slot()
            self._select_adjust("scope_ref")
        elif key == "scope_span":
            self.on_scope_menu(key)
        else:
            opts = b.controls[key].options
            cur = b.snapshot().get(key)
            b.set(key, opts[(opts.index(cur) + 1) % len(opts)] if cur in opts else opts[0])

    def on_scope_step(self, key, d):
        b = self._scope_backend()
        if not b:
            return
        if key == "scope_ref":
            c = b.controls[key]
            cur = b.snapshot().get(key, 0)
            b.set(key, max(c.lo, min(c.hi, cur + d * c.step)))
        else:
            opts = b.controls[key].options
            cur = b.snapshot().get(key)
            i = opts.index(cur) if cur in opts else 0
            b.set(key, opts[max(0, min(len(opts) - 1, i + d))])

    def on_scope_menu(self, key):
        b = self._scope_backend()
        if not b or b.controls[key].kind != "choice":
            return
        menu = QMenu(self)
        cur = b.snapshot().get(key)
        for o in b.controls[key].options:
            a = menu.addAction(o)
            a.setCheckable(True)
            a.setChecked(o == cur)
            a.triggered.connect(lambda _=False, o=o: b.set(key, o))
        menu.exec(QCursor.pos())

    def _refresh_scope(self, snaps):
        if self.scope is None:
            return
        slot = self.scope_slot()
        if self._scope_blank():
            # the selected radio has no scope: show nothing rather than the other radio's
            fb = self.backends.get(self.focus_slot)
            color = self.t.main if self.focus_slot == "main" else self.t.sub
            if self._scope_shown is not None:
                self._scope_shown = None
                self.scope.reset(color)
                self.scope_box.set_title("SCOPE", dot=color)
            self.scope.set_message(f"{fb.model if fb else 'radio'} has no spectrum scope")
            self.scope.set_overlay([], None)
            for key in ("scope_span", "scope_mode", "scope_ref"):
                self.btn[key].set_state(sub="", available=False, selected=False)
            return
        b = self.backends.get(slot)
        st = snaps.get(slot, {})
        if slot != self._scope_shown:               # selection moved to the other scope radio
            self._scope_shown = slot
            color = self.t.main if slot == "main" else self.t.sub
            self.scope.reset(color)
            self.scope_box.set_title(f"SCOPE  ·  {b.model}", dot=color)
        live = b is not None and b.link == LINK_LIVE
        if not live:
            self.scope.set_message(f"{b.model if b else 'radio'} not connected")
        else:
            self.scope.add_sweep(st.get("scope"))
            if not st.get("scope"):
                self.scope.set_message("waiting for scope data\u2026")
        t = self.t
        marks = []
        f = st.get("freq")
        if f:
            marks.append((f, t.main if slot == "main" else t.sub))
        other = self.other(slot)
        of = snaps.get(other, {}).get("freq")
        if of and self.backends.get(other):
            marks.append((of, t.sub if other == "sub" else t.main))
        pb = passband(b.mode_label(st), f, st.get("filter", "")) if (b and f and live) else None
        self.scope.set_overlay(marks, pb)
        for key in ("scope_span", "scope_mode", "scope_ref"):
            v = st.get(key)
            sub = "" if v is None or not live else (f"{v:+.1f} dB" if key == "scope_ref" else str(v))
            self.btn[key].set_state(sub=sub, available=live,
                                    selected=(key == "scope_ref" and self.adjust == "scope_ref"))

    def on_step(self, key, d):
        if key in ("rit", "xit"):
            self._level("rit_hz", d)
        elif key == "split":
            b = self.fb
            st = b.snapshot() if b else {}
            if b and st.get("freq_b"):
                b.set("freq_b", st["freq_b"] + d * 1000)
        elif key == "filter":
            b = self.fb
            b and b.supports("filter") and b.action("filter_step", d)
        elif key == "mon":
            self._level("mon_level", d)
        elif key in ("af_sel", "rf_sel", "sql_sel", "power_sel"):
            self._level(key.replace("_sel", "").replace("power", "power"), d)
        elif key in ("ant", "agc", "att", "preamp", "tbw"):
            self._cycle(key, d)

    def start_tune(self):
        b = self.fb
        if b and b.supports("atu"):
            b.action("tune")

    def on_menu(self, key):
        b = self.fb
        if not b:
            return
        menu = QMenu(self)
        if key == "mode" and b.supports("mode"):
            cur = b.snapshot().get("mode")
            for m in b.modes:
                a = menu.addAction(m)
                a.setCheckable(True)
                a.setChecked(m == cur)
                a.triggered.connect(lambda _=False, m=m: b.set("mode", m))
        elif key == "atu" and b.supports("atu"):
            a = menu.addAction("Start tune cycle  (transmits a carrier)")
            a.triggered.connect(self.start_tune)
        elif key in b.controls and b.controls[key].kind == "choice" and b.controls[key].options:
            cur = b.snapshot().get(key)
            for o in b.controls[key].options:
                a = menu.addAction(o)
                a.setCheckable(True)
                a.setChecked(o == cur)
                a.triggered.connect(lambda _=False, o=o, k=key: b.set(k, o))
        if not menu.isEmpty():
            menu.exec(QCursor.pos())

    def copy_main_to_sub(self):
        m, s = self.backends.get("main"), self.backends.get("sub")
        if not (m and s):
            return
        st = m.snapshot()
        if st.get("freq"):
            s.set("freq", st["freq"])
        mode = self._map_mode(st.get("mode"), s)
        if mode:
            s.set("mode", mode)

    def swap_main_sub(self):
        m, s = self.backends.get("main"), self.backends.get("sub")
        if not (m and s):
            return
        a, b = m.snapshot(), s.snapshot()
        if a.get("freq") and b.get("freq"):
            m.set("freq", b["freq"])
            s.set("freq", a["freq"])
        ma, mb = self._map_mode(b.get("mode"), m), self._map_mode(a.get("mode"), s)
        if ma and mb:
            m.set("mode", ma)
            s.set("mode", mb)
        self._track_last = None

    @staticmethod
    def _map_mode(mode, target):
        """Translate a mode name between radio families (CW-U <-> CW etc.)."""
        if not mode or not target.supports("mode"):
            return None
        if mode in target.modes:
            return mode
        aliases = {"CW": "CW-U", "CW-U": "CW", "CW-R": "CW-L", "CW-L": "CW-R", "RTTY": "RTTY-L",
                   "RTTY-L": "RTTY", "RTTY-R": "RTTY-U", "RTTY-U": "RTTY-R", "FM-N": "FM", "AM-N": "AM",
                   "PSK": "PSK", "CWR": "CW-R"}
        alt = aliases.get(mode)
        return alt if alt in target.modes else None

    def toggle_mute(self):
        b = self.fb
        if not b or not b.supports("af"):
            return
        slot = self.focus_slot
        if slot in self._muted:
            b.set("af", self._muted.pop(slot))
        else:
            self._muted[slot] = b.snapshot().get("af", 30) or 30
            b.set("af", 0)

    # ---- sharing with other programs (rigctld protocol) -----------------------

    def start_shares(self):
        """One rigctld-compatible server per configured radio, so JTDX / WSJT-X /
        ICOMNodeDisplay can use the radio Rig Deck holds the COM port of."""
        for srv in self.shares.values():
            srv.stop()
        self.shares = {}
        sh = config.share_cfg(self.cfg)
        if not sh["enabled"] or self.cfg.get("_demo"):
            return                            # --demo radios must never take the real ports
        for slot in ("main", "sub"):
            if self.cfg["radios"][slot].get("backend", "none") in ("none", "hamlib"):
                continue                      # nothing to share / Hamlib radios are already shared by rigctld
            srv = ShareServer(slot, sh[f"{slot}_port"], lambda s=slot: self.backends.get(s),
                              ptt=lambda on, s=slot: self.remote_ptt(s, on))
            srv.start()
            self.shares[slot] = srv

    def retry_shares(self):
        """Once a second: try a share port that was busy again, and if a radio's
        COM port is held by a stand-alone rigctld (ICOMNodeDisplay was started
        first), close that rigctld so Rig Deck gets the radio. The programs that
        were using it reconnect to Rig Deck's share port by themselves, which is
        why this is only done for a radio that is being shared."""
        for slot, srv in self.shares.items():
            if not srv.running and not srv.start():
                continue
            b = self.backends.get(slot)
            port = getattr(b, "port", "")
            if not b or not port or b.link != LINK_ERROR or not takeover.port_is_busy(b.error):
                continue
            now = time.monotonic()
            busy = self._takeover_thread is not None and self._takeover_thread.is_alive()
            if busy or now < self._takeover_next.get(port, 0):
                continue
            self._takeover_next[port] = now + 10      # the lookup starts PowerShell: not every second
            self._takeover_thread = threading.Thread(target=self._take_over, args=(b, port),
                                                     name="takeover", daemon=True)
            self._takeover_thread.start()

    @staticmethod
    def _take_over(backend, port):
        note = takeover.take_over(port)
        if note:
            backend._log("INFO", note)

    def remote_ptt(self, slot, on) -> bool:
        """PTT from a sharing program: same rules as the PTT key (one transmitter
        at a time, and the PTT time-out applies). Called from a server thread."""
        b = self.backends.get(slot)
        if not b or b.link != LINK_LIVE:
            return False
        if on:
            other = self.backends.get(self.other(slot))
            if other and other.snapshot().get("tx"):
                other.set("ptt", False)                # never two transmitters at once
            self._tx_since[slot] = time.monotonic()
        b.set("ptt", on)
        return True

    def toggle_ptt(self):
        b = self.fb
        if not b or not b.supports("ptt") or b.link != LINK_LIVE:
            return
        on = not b.snapshot().get("tx")
        if on:
            other = self.backends.get(self.other(self.focus_slot))
            if other and other.snapshot().get("tx"):
                other.set("ptt", False)                # never two transmitters at once
            self._tx_since[self.focus_slot] = time.monotonic()
        b.set("ptt", on)
        self.refresh()

    # ---- periodic update ---------------------------------------------------

    def refresh(self):
        t = self.t
        snaps = {s: (b.snapshot() if b else {}) for s, b in self.backends.items()}
        if self._auto_focus_until and time.monotonic() < self._auto_focus_until:
            mine, other = self.backends.get(self.focus_slot), self.backends.get(self.other(self.focus_slot))
            if (not mine or mine.link != LINK_LIVE) and other and other.link == LINK_LIVE:
                self.focus_slot = self.other(self.focus_slot)
                self._auto_focus_until = 0
        maxw = {s: (b.controls["power"].hi if b and "power" in b.controls else 100) for s, b in self.backends.items()}

        for slot, p in self.panels.items():
            b = self.backends[slot]
            p.refresh(b, snaps[slot], maxw[slot])
            p.set_focused(slot == self.focus_slot)

        # TX safety: time-out, and stop tracking our own TX once the radio says it's off
        tot = self.cfg.get("tot_s", 180)
        for slot, b in self.backends.items():
            if not b:
                continue
            tx = snaps[slot].get("tx")
            if tx and slot in self._tx_since and time.monotonic() - self._tx_since[slot] > tot:
                b.set("ptt", False)
                self._tx_since.pop(slot, None)
            elif not tx and slot in self._tx_since and time.monotonic() - self._tx_since[slot] > 2:
                self._tx_since.pop(slot, None)

        # TRACK: SUB follows MAIN's tuning
        m = snaps["main"].get("freq")
        if self.track and m and self.backends.get("sub"):
            if self._track_last is not None and m != self._track_last and abs(m - self._track_last) < 1_000_000:
                sf = snaps["sub"].get("freq")
                if sf:
                    self.backends["sub"].set("freq", sf + (m - self._track_last))
            self._track_last = m

        # link summary
        parts, worst = [], 0
        for slot, b in self.backends.items():
            if b is None:
                continue
            ok = b.link == LINK_LIVE
            worst = max(worst, 0 if ok else 1)
            parts.append(f"{slot.upper()} {'LIVE' if ok else b.link}")
        shared = [f":{s.port}" + (f"({s.clients})" if s.clients else "") for s in self.shares.values() if s.running]
        if shared:
            parts.append("SHARED " + " ".join(shared))     # e.g. SHARED :4532(2) :4533
        for srv in self.shares.values():
            if not srv.running:
                worst = 1
                parts.append(f":{srv.port} IN USE")
        self.link_lbl.setText("  •  ".join(parts) or "NO RADIOS")
        set_css(self.link_lbl, f"color:{t.main if worst == 0 else t.amber}; font-size:{px(13)}px; font-weight:600;")

        # centre panel
        fb = self.fb
        st = snaps.get(self.focus_slot, {})
        c = self.center
        pw = st.get("power")
        unit = fb.controls["power"].unit if fb and "power" in fb.controls else "W"
        c.pwr.setText(f"{pw} {unit}" if pw is not None and fb and fb.link == LINK_LIVE else "--")
        c.main_f.setText(fmt_freq(snaps["main"].get("freq")) if self.backends.get("main") else "--")
        c.sub_f.setText(fmt_freq(snaps["sub"].get("freq")) if self.backends.get("sub") else "--")
        if st.get("split") and st.get("freq_b"):
            c.splitl.setText(f"SPLIT  TX {fmt_freq(st['freq_b'])}")
            set_css(c.splitl, f"color:{t.amber}; font-size:{px(13)}px;")
        else:
            c.splitl.setText("SIMPLEX")
            set_css(c.splitl, f"color:{t.dim}; font-size:{px(13)}px;")
        xit_lbl = fb.controls["xit"].label if fb and "xit" in fb.controls else "XIT"
        c.xitl.setText(f"{xit_lbl} {st.get('rit_hz', 0):+d}" if st.get("xit") else f"{xit_lbl} OFF")
        set_css(c.xitl, f"color:{t.amber if st.get('xit') else t.dim}; font-size:{px(13)}px;")
        if fb and fb.meters and self.meter_kind not in fb.meters:
            self.meter_kind = fb.meters[0]
        labels, split = METER_SCALES.get(self.meter_kind, METER_SCALES["ALC"])
        c.meter_title.setText(self.meter_kind + ("" if st.get("tx") else "   (RX)"))
        if getattr(c, "kind", None) != self.meter_kind:
            c.meter.configure(labels, "", split)
            c.kind = self.meter_kind
        c.meter.set_value(meter_value(self.meter_kind, st, maxw[self.focus_slot]) if st.get("tx") else 0.0)
        if self.adjust == "TUNE":
            c.b_ent.set_state(text="ENT", sub="", selected=False)
        else:
            lvl = st.get(self.adjust)
            ctrl = fb.controls.get(self.adjust) if fb else None
            unit = ctrl.unit if ctrl and ctrl.unit else "%"
            c.b_ent.set_state(text=LEVEL_KEYS.get(self.adjust, self.adjust.upper()),
                              sub=f"{lvl}{unit}" if lvl is not None else "", selected=True)

        # band scale follows the selected radio
        self.scale.follow(st.get("freq"))
        marks = []
        for slot, color, above in (("main", t.main, True), ("sub", t.sub, False)):
            f = snaps[slot].get("freq")
            if f and self.backends.get(slot):
                marks.append((f, color, above))
        self.scale.set_markers(marks)

        self._refresh_buttons(fb, st)
        self._refresh_scope(snaps)

    def _refresh_buttons(self, b, st):
        live = bool(b) and b.link == LINK_LIVE
        B = self.btn

        def ctl(k):
            return b.controls.get(k) if b else None

        B["main"].set_state(active=self.focus_slot == "main", available=self.backends.get("main") is not None)
        B["sub"].set_state(active=self.focus_slot == "sub", available=self.backends.get("sub") is not None)
        B["meter"].set_state(sub=self.meter_kind, available=bool(b and b.meters))
        B["track"].set_state(active=self.track,
                             available=bool(self.backends.get("main") and self.backends.get("sub")))
        B["m2s"].set_state(available=bool(self.backends.get("main") and self.backends.get("sub")))
        B["swap"].set_state(available=bool(self.backends.get("main") and self.backends.get("sub")))
        B["clr"].set_state(available=live and ctl("rit") is not None)
        B["mode"].set_state(sub=b.mode_label(st) if live else "", available=live and ctl("mode") is not None)
        B["mute"].set_state(active=self.focus_slot in self._muted, available=live and ctl("af") is not None)

        for key in ("rit", "xit", "atu", "split", "rx_ant", "psel", "vox", "proc", "mon", "nr", "nb1", "nb2",
                    "notch", "bc", "data"):
            c = ctl(key)
            label = self.BUTTONS[key][0] if key in ("split",) else (c.label if c else self.BUTTONS[key][0])
            sub = ""
            if key in ("rit", "xit") and c and st.get(key):
                sub = f"{st.get('rit_hz', 0):+d} Hz"
            if key == "mon" and c and live and "mon_level" in b.controls:
                sub = f"{st.get('mon_level', 0)}%"
            if key == "atu" and c:
                sub = "TUNING" if st.get("tuning") else "HOLD: TUNE"
            B[key].set_state(text=label, sub=sub, active=bool(st.get(key)), available=live and c is not None)
            if c and c.tip:
                B[key].setToolTip(c.tip)

        for key, off in (("ant", ("ANT1",)), ("agc", ()), ("att", ("OFF",)), ("preamp", ("OFF", "IPO")),
                         ("tbw", ())):
            c = ctl(key)
            label = {"ant": "ANT", "agc": "AGC", "att": "ATT", "preamp": c.label if c else "PRE AMP",
                     "tbw": "TX FILTER"}[key]
            val = st.get(key, "") if c else ""
            B[key].set_state(text=label, sub=str(val),
                             active=bool(c) and key not in ("agc", "tbw") and val not in off and val != "",
                             available=live and c is not None)

        c = ctl("filter")
        B["filter"].set_state(text="RX FILTER", sub=st.get("filter", "") if c else "", available=live and c is not None)
        if c and c.tip:
            B["filter"].setToolTip(c.tip)
        for key, lk in (("power_sel", "power"), ("af_sel", "af"), ("rf_sel", "rf"), ("sql_sel", "sql")):
            c = ctl(lk)
            v = st.get(lk)
            unit = c.unit if c and c.unit else "%"
            B[key].set_state(sub=f"{v}{unit}" if c and v is not None and live else "",
                             selected=self.adjust == lk, available=live and c is not None)

        ptt = B["ptt"]
        tx = bool(st.get("tx"))
        ptt.set_state(text="TX" if tx else "PTT", sub=(b.model.split(" (")[0] if b else ""), active=tx,
                      available=live and ctl("ptt") is not None)

    # ---- window chrome -----------------------------------------------------

    def _tick_clock(self):
        if hasattr(self, "clock_lbl"):
            self.clock_lbl.setText(f"{time.strftime('%H:%M:%S', time.gmtime())} UTC     "
                                   f"{time.strftime('%H:%M:%S')} LOCAL")

    def _zoom_clicked(self, z):
        if self.isFullScreen():
            self.showNormal()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.apply_zoom(z)
        config.save(self.cfg)

    def toggle_full(self):
        if self.isFullScreen():
            self.showNormal()
            self.apply_zoom(self._zoom_before_full)
        else:
            self._zoom_before_full = self.cfg.get("zoom", 1.0)
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.showFullScreen()
            self.apply_zoom(self._zoom_before_full, full=True)

    def next_theme(self):
        names = [t.name for t in THEMES]
        cur = self.cfg.get("theme", names[0])
        self.cfg["theme"] = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        config.save(self.cfg)
        self.build_ui()

    def _update_found(self, version: str, url: str) -> None:
        self._update = (version, url)
        self._show_update()

    def _show_update(self) -> None:
        if not self._update:
            return
        self.update_btn.setText(f"UPDATE v{self._update[0]} AVAILABLE")
        self.update_btn.setToolTip("A newer Rig Deck is on GitHub. Click to open the download page.")
        self.update_btn.show()

    def show_guide(self):
        """Open the User Guide PDF. The exe carries its own copy (PyInstaller
        --add-data), which is copied out to %TEMP% first: the bundle folder is
        deleted when Rig Deck exits, and the PDF viewer may still have it open."""
        name = "Rig Deck User Guide.pdf"
        base = getattr(sys, "_MEIPASS", None)
        src = os.path.join(base, name) if base else \
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "build", name)
        if not os.path.exists(src):
            QMessageBox.warning(self, "User Guide", f"The User Guide wasn't found:\n{src}")
            return
        dst = os.path.join(tempfile.gettempdir(), "Rig Deck User Guide.pdf")
        try:
            shutil.copyfile(src, dst)
        except OSError:
            if not os.path.exists(dst):             # copy failed and no earlier copy: open the original
                dst = src
        try:
            os.startfile(dst)
        except OSError as exc:
            QMessageBox.warning(self, "User Guide", f"Couldn't open the User Guide:\n{exc}")

    def show_diag(self):
        if self._diag is None:
            self._diag = DiagDialog(lambda: self.backends, self)
        self._diag.show()
        self._diag.raise_()

    def show_info(self, slot):
        b = self.backends.get(slot)
        if not b:
            QMessageBox.information(self, "Info", f"No radio configured for {slot.upper()}. Use SETUP.")
            return
        rows = [f"<b>{slot.upper()}: {b.model}</b>", f"Driver: {config.BACKENDS.get(b.KIND, b.KIND)}",
                f"Link: {b.link}" + (f" ({b.error})" if b.error else "")]
        rows += [f"{k}: {v}" for k, v in b.info.items()]
        rows.append("Controls: " + ", ".join(sorted(c.label for c in b.controls.values())))
        rows.append("TX meters: " + (", ".join(b.meters) or "none"))
        srv = self.shares.get(slot)
        if srv and srv.running:
            rows.append(f"Shared with other programs: Hamlib NET rigctl, {srv.host}:{srv.port} "
                        f"({srv.clients} connected)")
        elif srv:
            rows.append(f"Sharing: {srv.error} (another rigctld running?)")
        QMessageBox.information(self, f"{slot.upper()} radio", "<br>".join(rows))

    def show_setup(self):
        dlg = SetupDialog(self.cfg, self)
        if dlg.exec():
            new = dlg.result_cfg()
            changed = new["radios"] != self.cfg["radios"]
            share_changed = config.share_cfg(new) != config.share_cfg(self.cfg)
            self.cfg = new
            config.save(self.cfg)
            if changed:
                self.restart_backends()
            if changed or share_changed:
                self.start_shares()
            self.apply_zoom(self.cfg.get("zoom", 1.0))

    def restart_backends(self):
        for b in self.backends.values():
            if b:
                b.stop()
        self._muted.clear()
        self._tx_since.clear()
        self.backends = {slot: config.make_backend(self.cfg["radios"][slot], slot) for slot in ("main", "sub")}
        for b in self.backends.values():
            if b:
                b.start()
        if self.backends.get(self.focus_slot) is None:
            self.focus_slot = "sub" if self.focus_slot == "main" else "main"

    def closeEvent(self, e):
        self.timer.stop()
        self.share_timer.stop()
        for srv in self.shares.values():
            srv.stop()
        for slot, b in self.backends.items():
            if b:
                if slot in self._muted:
                    b.set("af", self._muted[slot])   # don't leave a radio silently muted
                b.stop()
        config.save(self.cfg)
        super().closeEvent(e)
