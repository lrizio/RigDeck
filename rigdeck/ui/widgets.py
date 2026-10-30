"""Custom-painted instruments in the HamProjects "3D" look: raised metal
buttons with a drop shadow and glossy cap (they sink when pressed), LED
glow dots, recessed glowing LCD screens, and rounded fieldset panels with
the title inline on the border."""
from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF,
                           QRadialGradient)
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

from .theme import Theme, px, shade

def qc(color: str, alpha: int | None = None) -> QColor:
    c = QColor(color)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def font(size_px: float, bold: bool = False, mono: bool = False) -> QFont:
    f = QFont("Consolas" if mono else "Segoe UI")
    f.setPixelSize(max(6, round(size_px)))
    if bold:
        f.setWeight(QFont.Weight.DemiBold)
    return f


# ---- shared painting helpers ------------------------------------------------

def paint_recess(p: QPainter, r: QRectF, t: Theme, radius: float) -> None:
    """A screen sunk into the panel: dark fill, shadowed top/left inner edge,
    a faint light catch on the bottom/right lip."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qc(t.screen))
    p.drawRoundedRect(r, radius, radius)
    bevel = QLinearGradient(r.topLeft(), r.bottomRight())
    bevel.setColorAt(0.0, qc("#000000", 230))
    bevel.setColorAt(0.5, qc("#000000", 60))
    bevel.setColorAt(1.0, qc(shade(t.edge, 50), 200))
    p.setPen(QPen(bevel, max(1.5, px(2.2))))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(r.adjusted(1, 1, -1, -1), radius, radius)
    # soft inner shadow along the top edge
    top = QLinearGradient(r.left(), r.top(), r.left(), r.top() + px(10))
    top.setColorAt(0, qc("#000000", 120))
    top.setColorAt(1, qc("#000000", 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(top)
    p.drawRoundedRect(QRectF(r.left() + 2, r.top() + 2, r.width() - 4, px(10)), radius * 0.6, radius * 0.6)
    p.restore()


def paint_glow_dot(p: QPainter, c: QPointF, size: float, color: str, lit: bool, off: str) -> None:
    """The family glow_dot(): soft halo, solid core, small white highlight."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    r = size / 2
    if lit:
        halo = QRadialGradient(c, r * 1.6)
        halo.setColorAt(0, qc(color, 170))
        halo.setColorAt(0.5, qc(color, 60))
        halo.setColorAt(1, qc(color, 0))
        p.setBrush(halo)
        p.drawEllipse(c, r * 1.6, r * 1.6)
    core = QRadialGradient(QPointF(c.x() - r * 0.25, c.y() - r * 0.25), r)
    base = color if lit else off
    core.setColorAt(0, qc(shade(base, 70)))
    core.setColorAt(1, qc(base if lit else shade(off, -10)))
    p.setBrush(core)
    p.setPen(QPen(qc("#000000", 150), max(1.0, size * 0.08)))
    p.drawEllipse(c, r * 0.62, r * 0.62)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qc("#ffffff", 150 if lit else 40))
    p.drawEllipse(QPointF(c.x() - r * 0.2, c.y() - r * 0.22), r * 0.2, r * 0.16)
    p.restore()


# ---- containers --------------------------------------------------------------

class Fieldset(QFrame):
    """Thin rounded outline with the title inline on the top border (the
    family's rounded_panel(): an HTML fieldset/legend), optional glow dot."""

    def __init__(self, theme: Theme, title: str = "", dot: str | None = None, parent=None):
        super().__init__(parent)
        self.t = theme
        self.title = title
        self.dot = dot
        self.edge = theme.edge
        self.edge_w = 2

    def set_title(self, title: str, dot: str | None = None, edge: str | None = None, edge_w: int | None = None):
        new = (title, dot, edge or self.t.edge, edge_w or 2)
        if new != (self.title, self.dot, self.edge, self.edge_w):
            self.title, self.dot, self.edge, self.edge_w = new
            self.update()

    def margins(self, pad: int = 14):
        """Content margins that leave room for the border and inline title."""
        return px(pad), px(pad + (12 if self.title else 2)), px(pad), px(pad)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.t
        fs = px(14)
        top = fs * 0.75 if self.title else 2
        w = px(self.edge_w)
        rect = QRectF(w / 2 + 1, top, self.width() - w - 2, self.height() - top - w / 2 - 1)
        p.setPen(QPen(qc(self.edge), w))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, px(14), px(14))
        if self.title:
            p.setFont(font(fs, bold=True))
            tw = p.fontMetrics().horizontalAdvance(self.title)
            dot_w = px(16) if self.dot else 0
            x = px(18)
            p.fillRect(QRectF(x - px(6), 0, tw + dot_w + px(12), top * 2), qc(t.bg))
            if self.dot:
                paint_glow_dot(p, QPointF(x + px(6), top), px(12), self.dot, True, t.led_off)
            p.setPen(qc(t.text))
            p.drawText(QRectF(x + dot_w, 0, tw + 4, top * 2), Qt.AlignmentFlag.AlignVCenter, self.title)


class Screen(QFrame):
    """A recessed LCD screen; child widgets draw on top of it."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.t = theme

    def paintEvent(self, _):
        p = QPainter(self)
        paint_recess(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.t, px(10))


class LedLabel(QWidget):
    """A glow-dot lamp with a caption (BUSY / TX)."""

    def __init__(self, theme: Theme, text: str, parent=None):
        super().__init__(parent)
        self.t = theme
        self.text = text
        self.color = theme.main
        self.lit = False
        self.available = True
        self.setMinimumWidth(px(78))
        self.setMinimumHeight(px(26))

    def set_state(self, text: str, color: str, lit: bool, available: bool = True):
        if (text, color, lit, available) != (self.text, self.color, self.lit, self.available):
            self.text, self.color, self.lit, self.available = text, color, lit, available
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        h = self.height()
        paint_glow_dot(p, QPointF(h * 0.5, h / 2), h * 0.62, self.color, self.lit and self.available,
                       self.t.led_off)
        p.setFont(font(px(14), bold=True))
        p.setPen(qc(self.t.text if self.lit else (self.t.dim if self.available else self.t.faint)))
        p.drawText(QRectF(h, 0, self.width() - h, h), Qt.AlignmentFlag.AlignVCenter, self.text)


# ---- frequency readout ------------------------------------------------------

FREQ_FONTS = ("Bahnschrift", "Segoe UI")


class FreqDisplay(QWidget):
    """DDD.DDD.DDD frequency readout in a smooth font with an LED-style glow
    (9 digits, so UHF like 434.300.000 fits). Each digit sits in its own
    fixed-width cell so the mouse wheel over a digit tunes that digit;
    double-click asks for direct frequency entry."""
    tune = Signal(int)
    entryRequested = Signal()

    DIGITS = 9
    GROUPS_AFTER = (2, 5)           # separator dots after these digit indexes

    def __init__(self, theme: Theme, color: str, parent=None):
        super().__init__(parent)
        self.t = theme
        self.color = color
        self.hz: int | None = None
        self.live = True
        self._hover = -1
        self._cells: list[QRectF] = []
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Mouse wheel over a digit tunes it.  Double-click to type a frequency.")

    def set_value(self, hz: int | None, live: bool = True) -> None:
        if hz != self.hz or live != self.live:
            self.hz, self.live = hz, live
            self.update()

    def _font(self, size: float) -> QFont:
        f = QFont(FREQ_FONTS[0])
        f.setStyleHint(QFont.StyleHint.SansSerif)
        f.setPixelSize(max(6, round(size)))
        f.setWeight(QFont.Weight.DemiBold)
        return f

    def _layout(self):
        """Pick the biggest font whose 9 digit cells + 2 dots fit the widget."""
        from PySide6.QtGui import QFontMetricsF
        h = self.height()
        size = h * 1.05
        for _ in range(2):
            fm = QFontMetricsF(self._font(size))
            cell = max(fm.horizontalAdvance(c) for c in "0123456789") * 1.04
            dot = fm.horizontalAdvance(".") * 1.1
            total = self.DIGITS * cell + len(self.GROUPS_AFTER) * dot
            k = min((self.width() - px(8)) / total, (h * 0.96) / fm.capHeight() * 0.72)
            size *= k
        font_ = self._font(size)
        fm = QFontMetricsF(font_)
        cell = max(fm.horizontalAdvance(c) for c in "0123456789") * 1.04
        dot = fm.horizontalAdvance(".") * 1.1
        total = self.DIGITS * cell + len(self.GROUPS_AFTER) * dot
        x = (self.width() - total) / 2
        base = (h + fm.capHeight()) / 2
        cells, dots = [], []
        for i in range(self.DIGITS):
            cells.append(QRectF(x, base - fm.capHeight(), cell, fm.capHeight()))
            x += cell
            if i in self.GROUPS_AFTER:
                dots.append(x)
                x += dot
        return font_, fm, cells, dots, base, dot

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font_, fm, cells, dots, base, dot_w = self._layout()
        self._cells = cells
        text = f"{self.hz:9d}" if self.hz is not None else "---------"
        text = text[-self.DIGITS:]
        color = self.color if self.live else self.t.dim
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)     # odd-even punched holes where a "4" overlaps itself
        for i, r in enumerate(cells):
            ch = text[i]
            if ch == " ":
                continue
            w = fm.horizontalAdvance(ch)
            path.addText(QPointF(r.left() + (r.width() - w) / 2, base), font_, ch)
        first = next((i for i, ch in enumerate(text) if ch != " "), self.DIGITS)
        for j, x in enumerate(dots):
            if self.GROUPS_AFTER[j] >= first:               # no dot ahead of the first digit
                w = fm.horizontalAdvance(".")
                path.addText(QPointF(x + (dot_w - w) / 2, base), font_, ".")
        if self.live:
            for width, alpha in ((fm.capHeight() * 0.16, 30), (fm.capHeight() * 0.07, 70)):   # glow
                p.setPen(QPen(qc(color, alpha), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
        g = QLinearGradient(QPointF(0, base - fm.capHeight()), QPointF(0, base))
        g.setColorAt(0, qc(shade(color, 55)))
        g.setColorAt(1, qc(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPath(path)
        if 0 <= self._hover < len(cells) and self.hz is not None:
            r = cells[self._hover]
            p.setBrush(qc(color, 120))
            p.drawRoundedRect(QRectF(r.left() + r.width() * 0.1, base + px(5), r.width() * 0.8,
                                     max(2.0, px(3))), 1, 1)

    def _digit_at(self, x: float) -> int:
        for i, r in enumerate(self._cells):
            if r.left() - px(3) <= x <= r.right() + px(3):
                return i
        return -1

    def mouseMoveEvent(self, e):
        d = self._digit_at(e.position().x())
        if d != self._hover:
            self._hover = d
            self.update()

    def leaveEvent(self, _):
        self._hover = -1
        self.update()

    def wheelEvent(self, e):
        d = self._digit_at(e.position().x())
        if d < 0:
            d = self.DIGITS - 3                     # default to 100 Hz steps
        steps = e.angleDelta().y() // 120 or (1 if e.angleDelta().y() > 0 else -1)
        self.tune.emit(steps * 10 ** (self.DIGITS - 1 - d))
        e.accept()

    def mouseDoubleClickEvent(self, _):
        self.entryRequested.emit()


SevenSegFreq = FreqDisplay          # old name, kept for imports


# ---- bar meter -----------------------------------------------------------------

class SegMeter(QWidget):
    """Segmented LED bar meter with optional scale labels above it."""

    def __init__(self, theme: Theme, segments: int = 40, split: float = 0.6,
                 labels: list[tuple[float, str]] | None = None, title: str = "", parent=None):
        super().__init__(parent)
        self.t = theme
        self.n = segments
        self.split = split
        self.labels = labels or []
        self.title = title
        self.value = 0.0
        self.peak = 0.0
        self._peak_t = 0.0
        self.lo, self.hi = theme.meter_lo, theme.meter_hi
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def configure(self, labels, title, split, lo=None, hi=None):
        self.labels, self.title, self.split = labels, title, split
        self.lo, self.hi = lo or self.t.meter_lo, hi or self.t.meter_hi
        self.update()

    def set_value(self, v: float) -> None:
        v = max(0.0, min(1.0, v))
        now = time.monotonic()
        if v >= self.peak:
            self.peak, self._peak_t = v, now
        elif now - self._peak_t > 1.0:
            self.peak = max(v, self.peak - 0.02)
        self.value = v
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        label_h = h * 0.42 if self.labels or self.title else 0
        if label_h:
            p.setFont(font(label_h * 0.6))
            p.setPen(qc(self.t.dim))
            if self.title:
                p.drawText(QRectF(0, 0, w, label_h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                           self.title)
            for frac, txt in self.labels:
                tw = p.fontMetrics().horizontalAdvance(txt)
                x = max(tw / 2, min(w - tw / 2 - 1, frac * w))
                p.drawText(QRectF(x - tw / 2 - 2, 0, tw + 4, label_h), Qt.AlignmentFlag.AlignCenter, txt)
        gap = max(1.0, w / self.n * 0.2)
        sw = (w - gap * (self.n - 1)) / self.n
        top, bh = label_h + (h - label_h) * 0.12, (h - label_h) * 0.76
        lit_n = self.value * self.n
        peak_i = int(self.peak * self.n - 0.001)
        rad = min(sw, bh) * 0.25
        for i in range(self.n):
            hi = (i + 0.5) / self.n > self.split
            col = self.hi if hi else self.lo
            r = QRectF(i * (sw + gap), top, sw, bh)
            p.setPen(Qt.PenStyle.NoPen)
            if i < lit_n - 0.001 or (i == peak_i and self.peak > self.value + 0.02):
                p.setBrush(qc(col, 60))
                p.drawRoundedRect(r.adjusted(-gap * 0.6, -gap * 0.9, gap * 0.6, gap * 0.9), rad * 2, rad * 2)
                g = QLinearGradient(r.topLeft(), r.bottomLeft())
                g.setColorAt(0, qc(shade(col, 60)))
                g.setColorAt(1, qc(col))
                p.setBrush(g if i != peak_i or i < lit_n - 0.001 else qc(self.t.text))
            else:
                p.setBrush(qc(col, 30))
            p.drawRoundedRect(r, rad, rad)


def s_meter_fraction(db: float) -> float:
    """S0..S9 (-54..0 dB) fills 60%; S9..S9+60 fills the rest."""
    if db <= 0:
        return max(0.0, (db + 54) / 54 * 0.6)
    return min(1.0, 0.6 + db / 60 * 0.4)


S_LABELS = [(s_meter_fraction(-48), "1"), (s_meter_fraction(-36), "3"), (s_meter_fraction(-24), "5"),
            (s_meter_fraction(-12), "7"), (s_meter_fraction(0), "9"), (s_meter_fraction(20), "+20"),
            (s_meter_fraction(40), "+40"), (s_meter_fraction(60), "+60")]


# ---- band scale ----------------------------------------------------------------

class BandScale(QWidget):
    """Recessed frequency scale with a marker per radio. Click to QSY the
    selected radio, wheel to tune it."""
    qsy = Signal(int)
    tune = Signal(int)

    def __init__(self, theme: Theme, band_plan, parent=None):
        super().__init__(parent)
        self.t = theme
        self.plan = band_plan
        self.lo, self.hi = 7_040_000, 7_260_000
        self.markers: list[tuple[int, str, bool]] = []
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to move the selected radio here.  Wheel tunes it in 1 kHz steps.")

    def follow(self, hz: int | None) -> None:
        if not hz:
            return
        span = 220_000 if hz < 30_000_000 else (1_100_000 if hz < 100_000_000 else 2_200_000)
        if self.hi - self.lo != span or not (self.lo + span * 0.1 <= hz <= self.hi - span * 0.1):
            step = span // 11
            centre = round(hz / step) * step
            self.lo, self.hi = centre - span // 2, centre + span // 2
            self.update()

    def set_markers(self, markers):
        if markers != self.markers:
            self.markers = markers
            self.update()

    def _m(self):
        return px(22)

    def _x(self, hz):
        m = self._m()
        return m + (hz - self.lo) / (self.hi - self.lo) * (self.width() - 2 * m)

    def _hz(self, x):
        m = self._m()
        return self.lo + (x - m) / (self.width() - 2 * m) * (self.hi - self.lo)

    def _in_band(self, hz):
        return any(a <= hz <= b for a, b in self.plan)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        paint_recess(p, QRectF(0.5, 0.5, w - 1, h - 1), self.t, px(10))
        span = self.hi - self.lo
        major = span // 11
        yl, ybar = h * 0.34, h * 0.62
        p.setFont(font(h * 0.24, mono=True))
        p.setPen(qc(self.t.text))
        f = (self.lo // major + 1) * major
        while f < self.hi:
            x = self._x(f)
            p.drawText(QRectF(x - 60, h * 0.06, 120, yl), Qt.AlignmentFlag.AlignCenter, f"{f / 1e6:.3f}")
            f += major
        steps = 220
        bw = max(2.0, h * 0.08)
        m = self._m()
        for i in range(steps):
            f0 = self.lo + span * i / steps
            ok = self._in_band(f0 + span / steps / 2)
            col = self.t.band_ok if ok else self.t.meter_hi
            r = QRectF(self._x(f0), ybar - bw / 2, (w - 2 * m) / steps + 0.6, bw)
            p.fillRect(r.adjusted(0, -bw * 0.6, 0, bw * 0.6), qc(col, 45))
            p.fillRect(r, qc(col))
        minor = major // 4
        f = (self.lo // minor + 1) * minor
        while f < self.hi:
            x = self._x(f)
            big = f % major == 0
            p.setPen(QPen(qc(self.t.text if big else self.t.dim), max(1, px(1.2 if big else 1))))
            th = h * (0.13 if big else 0.07)
            p.drawLine(QPointF(x, ybar - th), QPointF(x, ybar + th))
            f += minor
        tri = h * 0.12
        for hz, color, above in self.markers:
            if not (self.lo <= hz <= self.hi):
                continue
            x = self._x(hz)
            sign = -1 if above else 1
            y = ybar + sign * (bw / 2 + 1)
            poly = QPolygonF([QPointF(x - tri, y + sign * tri * 1.3), QPointF(x + tri, y + sign * tri * 1.3),
                              QPointF(x, y)])
            p.setPen(QPen(qc(color, 70), tri * 0.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.setBrush(qc(color))
            p.drawPolygon(poly)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.qsy.emit(int(round(self._hz(e.position().x()) / 100) * 100))

    def wheelEvent(self, e):
        steps = e.angleDelta().y() // 120 or (1 if e.angleDelta().y() > 0 else -1)
        self.tune.emit(steps * 1000)
        e.accept()


# ---- the 3D console button --------------------------------------------------------

class DeckButton(QWidget):
    """Raised rounded metal key: drop shadow, darker rim, glossy top cap.
    Held down it loses the shadow and sinks into its place. Toggle keys
    carry a small LED that glows when the function is on. Optional ‹ ›
    arrow zones (step -1 / +1), a small value line, and a right-click menu.
    Keys with holdable=True also emit held() after a long press (instead of
    clicked), like the radio's own push-and-hold keys."""
    clicked = Signal()
    stepped = Signal(int)
    menuRequested = Signal()
    held = Signal()
    HOLD_MS = 800

    def __init__(self, theme: Theme, text: str, role: str = "teal", arrows: bool = False,
                 tall: bool = False, led: bool = False, parent=None):
        super().__init__(parent)
        self.t = theme
        self.text = text
        self.sub = ""
        self.role = role
        self.arrows = arrows
        self.tall = tall
        self.led = led
        self.active = False
        self.selected = False
        self.available = True
        self._pressed = None         # -1 left arrow, 0 body, +1 right arrow, None = not pressed
        self.holdable = False
        self._held = False
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._on_hold)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(px(36))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, text=None, sub=None, active=None, selected=None, available=None, role=None, led=None):
        changed = False
        for name, val in (("text", text), ("sub", sub), ("active", active), ("selected", selected),
                          ("available", available), ("role", role), ("led", led)):
            if val is not None and getattr(self, name) != val:
                setattr(self, name, val)
                changed = True
        if changed:
            self.setCursor(Qt.CursorShape.PointingHandCursor if self.available else Qt.CursorShape.ArrowCursor)
            self.update()

    def _role_color(self) -> str:
        t = self.t
        return {"teal": t.teal_edge, "purple": t.purple, "amber": t.amber, "red": t.red,
                "green": t.main}.get(self.role, t.teal_edge)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self.t
        sdx, sdy = px(3), px(4)
        w, h = self.width() - sdx - 1, self.height() - sdy - 1
        rad = min(px(9), h * 0.3)
        pressed = self._pressed is not None and self.available
        role = self._role_color()
        if not self.available:
            base = shade(t.btn, -14)
        elif self.active and not self.led:
            base = shade(role, -70) if self.role != "red" else t.red
        elif self.active and self.role in ("red",):
            base = t.red
        else:
            base = t.btn

        if pressed:
            ox, oy = sdx * 0.7, sdy * 0.7
            body = QRectF(ox, oy, w, h)
            p.setPen(QPen(qc(shade(base, -55)), px(2)))
            p.setBrush(qc(shade(base, -18)))
            p.drawRoundedRect(body, rad, rad)
            cap = QRectF(body.left() + px(3), body.top() + px(3), body.width() - px(6), (h - px(6)) * 0.4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc(shade(base, -34)))
            p.drawRoundedRect(cap, max(2, rad - 3), max(2, rad - 3))
        else:
            body = QRectF(0.5, 0.5, w, h)
            if self.available:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qc("#000000", 115))
                p.drawRoundedRect(body.translated(sdx, sdy), rad, rad)
            g = QLinearGradient(body.topLeft(), body.bottomLeft())
            g.setColorAt(0, qc(shade(base, 10)))
            g.setColorAt(1, qc(shade(base, -16)))
            p.setBrush(g)
            edge = t.teal_edge if self.selected else shade(base, -45)
            p.setPen(QPen(qc(edge), px(2) if self.selected else px(1.6)))
            p.drawRoundedRect(body, rad, rad)
            if self.available:
                cap = QRectF(body.left() + px(3), body.top() + px(3), body.width() - px(6), (h - px(6)) * 0.42)
                cg = QLinearGradient(cap.topLeft(), cap.bottomLeft())
                cg.setColorAt(0, qc(shade(base, 48)))
                cg.setColorAt(1, qc(shade(base, 18)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(cg)
                p.drawRoundedRect(cap, max(2, rad - 3), max(2, rad - 3))
        if self.active and self.available and not self.led and self.role != "red":
            # lit key: coloured rim glow
            p.setPen(QPen(qc(role, 200), px(1.6)))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(1, 1, -1, -1), rad, rad)

        # LED indicator
        if self.led:
            d = max(px(13), h * 0.3)
            paint_glow_dot(p, QPointF(body.left() + d * 0.85, body.top() + d * 0.85), d, role,
                           self.active and self.available, t.led_off)

        ox, oy = body.left(), body.top()
        fs = min(px(15), h * (0.26 if self.tall else 0.33))
        txt_color = qc(t.faint) if not self.available else qc("#ffffff") if (self.active or pressed) else qc(t.text)
        if self.arrows:
            p.setFont(font(fs * 1.35, bold=True))
            p.setPen(qc(t.dim) if self.available else qc(t.faint))
            aw = w * 0.16
            p.drawText(QRectF(ox, oy, aw, h), Qt.AlignmentFlag.AlignCenter, "‹")
            p.drawText(QRectF(ox + w - aw, oy, aw, h), Qt.AlignmentFlag.AlignCenter, "›")
        p.setFont(font(fs * (1.3 if self.tall else 1.0), bold=True))
        # engraved text: dark shadow one pixel below
        def text(rect, flags, s, col):
            p.setPen(qc("#000000", 150))
            p.drawText(rect.translated(0, max(1, px(1))), flags, s)
            p.setPen(col)
            p.drawText(rect, flags, s)
        if self.sub:
            text(QRectF(ox, oy, w, h * 0.6), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                 self.text, txt_color)
            p.setFont(font(fs * 0.74, mono=True))
            sub_col = qc(role if self.active else t.teal_edge) if self.available else qc(t.faint)
            if self.active and not self.led:
                sub_col = qc("#ffffff")
            text(QRectF(ox, oy + h * 0.58, w, h * 0.38), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                 self.sub, sub_col)
        else:
            text(QRectF(ox, oy, w, h), Qt.AlignmentFlag.AlignCenter, self.text, txt_color)

    def _zone(self, x):
        if self.arrows:
            aw = self.width() * 0.2
            if x < aw:
                return -1
            if x > self.width() - aw:
                return 1
        return 0

    def mousePressEvent(self, e):
        if not self.available:
            return
        if e.button() == Qt.MouseButton.RightButton:
            self.menuRequested.emit()
            return
        self._pressed = self._zone(e.position().x())
        self._held = False
        if self.holdable and self._pressed == 0:
            self._hold_timer.start(self.HOLD_MS)
        self.update()

    def _on_hold(self):
        if self._pressed == 0 and self.available:
            self._held = True
            self.held.emit()

    def mouseReleaseEvent(self, e):
        self._hold_timer.stop()
        if self._pressed is None or not self.available or e.button() != Qt.MouseButton.LeftButton:
            return
        zone, self._pressed = self._pressed, None
        self.update()
        if self._held or not self.rect().contains(e.position().toPoint()):
            return
        if zone == 0:
            self.clicked.emit()
        else:
            self.stepped.emit(zone)

    def mouseDoubleClickEvent(self, e):
        if self.available and e.button() == Qt.MouseButton.LeftButton:
            zone = self._zone(e.position().x())
            self._pressed = zone
            self.update()

    def wheelEvent(self, e):
        if self.available:
            d = e.angleDelta().y()
            if d:
                self.stepped.emit(1 if d > 0 else -1)
        e.accept()


class Tag(QLabel):
    """Status text on the radio screens; lights up when active."""

    def __init__(self, theme: Theme, text="", parent=None):
        super().__init__(text, parent)
        self.t = theme
        self._key = None

    def show_state(self, text: str, active: bool, color: str | None = None, available: bool = True):
        key = (text, active, color, available)
        if key == self._key:
            return
        self._key = key
        self.setText(text)
        c = self.t.faint if not available else (color or self.t.teal_edge) if active else self.t.dim
        self.setStyleSheet(f"color: {c}; font-size: {px(14)}px; font-family: 'Segoe UI';")
