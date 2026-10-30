"""Spectrum scope + waterfall for radios that stream their own scope data
(the IC-705 over CI-V). Drawn in the same recessed-LCD style as the rest."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import Theme, px, shade
from .widgets import font, paint_recess, qc


def waterfall_palette(n: int = 161) -> list[int]:
    """Level 0..n-1 -> ARGB: black, deep blue, cyan, yellow, red, white."""
    stops = [(0.0, (4, 8, 16)), (0.18, (10, 30, 110)), (0.38, (20, 150, 220)), (0.58, (240, 230, 60)),
             (0.8, (240, 70, 30)), (1.0, (255, 255, 255))]
    out = []
    for i in range(n):
        x = i / (n - 1)
        for (x0, c0), (x1, c1) in zip(stops, stops[1:]):
            if x <= x1:
                k = (x - x0) / (x1 - x0)
                r, g, b = (round(c0[j] + (c1[j] - c0[j]) * k) for j in range(3))
                break
        out.append(0xFF000000 | (r << 16) | (g << 8) | b)
    return out


def smooth_path(pts: list[QPointF]) -> QPainterPath:
    """Quadratic curve through the midpoints, so the coarse scope points read as a trace, not a saw."""
    path = QPainterPath(pts[0])
    for a, b in zip(pts, pts[1:]):
        path.quadTo(a, QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2))
    path.lineTo(pts[-1])
    return path


def grid_step(span: int) -> int:
    for s in (500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000):
        if span / s <= 10:
            return s
    return 1_000_000


class SpectrumScope(QWidget):
    """Glowing spectrum trace over a scrolling waterfall. Click to tune the
    radio there; wheel tunes in 100 Hz steps."""
    qsy = Signal(int)
    tune = Signal(int)

    LEVEL_MAX = 160          # Icom scope data_level_max
    ROWS = 300               # waterfall history; each sweep is ROW_PX tall so it fills fast
    ROW_PX = 3

    def __init__(self, theme: Theme, color: str, parent=None):
        super().__init__(parent)
        self.t = theme
        self.color = color
        self.lo = self.hi = 0
        self.data = b""
        self.seq = -1
        self.oor = False
        self.markers: list[tuple[int, str]] = []
        self.passband: tuple[int, int] | None = None
        self.message = "waiting for scope data…"
        self.level_max = self.LEVEL_MAX
        self._pal = waterfall_palette(self.level_max + 1)
        self._wf: QImage | None = None
        self._floor: float | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip("Click to tune here.  Wheel tunes in 100 Hz steps.")

    # ---- data ------------------------------------------------------------

    def add_sweep(self, sweep: dict | None) -> None:
        if not sweep or sweep.get("seq") == self.seq or not sweep.get("data"):
            return
        data = sweep["data"]
        self.seq = sweep["seq"]
        n = len(data)
        lo, hi = sweep["lo"], sweep["hi"]
        if sweep.get("max", self.level_max) != self.level_max:     # IC-705 0-160, IC-7610 0-200
            self.level_max = sweep["max"]
            self._pal = waterfall_palette(self.level_max + 1)
            self._wf = None
        old = self._wf
        same = old is not None and hi > lo and abs((self.hi - self.lo) - (hi - lo)) <= (hi - lo) * 0.01
        if same and old.width() != n:
            # a short/odd sweep (dropped CI-V frame) must not wipe the history:
            # resample this row onto the existing width instead
            m = old.width()
            data = bytes(data[min(n - 1, x * n // m)] for x in range(m))
            n = m
        new = QImage(n, self.ROWS, QImage.Format.Format_RGB32)
        new.fill(self._pal[0])
        if same:
            # scroll down a row; slide sideways if the centre frequency moved
            dx = round((lo - self.lo) / (hi - lo) * n)
            p = QPainter(new)
            p.drawImage(-dx, 1, old)
            p.end()
        pal, top = self._pal, self.level_max
        # contrast stretch: black just under the noise floor (tracked), full scale ~120 levels above it, so noise peaks stay blue
        floor = sorted(data)[len(data) * 15 // 100]
        self._floor = floor if self._floor is None else self._floor * 0.9 + floor * 0.1
        b = max(0.0, self._floor - 10)
        k = top / max(80.0, min(top - b, 120.0))
        for x, v in enumerate(data):
            new.setPixel(x, 0, pal[max(0, min(top, int((v - b) * k)))])
        self._wf = new
        self.lo, self.hi, self.data, self.oor = lo, hi, data, bool(sweep.get("oor"))
        self.message = ""
        self.update()

    def reset(self, color: str) -> None:
        """Start over for another radio (the panel follows the selected one)."""
        self.color = color
        self._wf = None
        self._floor = None
        self.data = b""
        self.seq = -1
        self.lo = self.hi = 0
        self.message = "waiting for scope data…"
        self.update()

    def set_overlay(self, markers, passband) -> None:
        if markers != self.markers or passband != self.passband:
            self.markers, self.passband = markers, passband
            self.update()

    def set_message(self, msg: str) -> None:
        if msg != self.message:
            self.message = msg
            self.update()

    # ---- drawing ---------------------------------------------------------

    def _inner(self) -> QRectF:
        return QRectF(self.rect()).adjusted(px(10), px(8), -px(10), -px(8))

    def _x(self, hz, r: QRectF) -> float:
        return r.left() + (hz - self.lo) / (self.hi - self.lo) * r.width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        t = self.t
        paint_recess(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), t, px(10))
        inner = self._inner()
        axis_h = px(16)
        spec = QRectF(inner.left(), inner.top(), inner.width(), (inner.height() - axis_h) * 0.45)
        axis = QRectF(inner.left(), spec.bottom(), inner.width(), axis_h)
        wf = QRectF(inner.left(), axis.bottom(), inner.width(), inner.bottom() - axis.bottom())

        if self.message or not self.data or self.hi <= self.lo:
            p.setPen(qc(t.dim))
            p.setFont(font(px(14)))
            p.drawText(inner, Qt.AlignmentFlag.AlignCenter, self.message or "no scope data")
            return

        grid = QPen(qc(t.edge, 120), 1, Qt.PenStyle.DotLine)
        p.setPen(grid)
        for i in range(1, 4):
            y = spec.top() + spec.height() * i / 4
            p.drawLine(QPointF(spec.left(), y), QPointF(spec.right(), y))
        step = grid_step(self.hi - self.lo)
        p.setFont(font(axis_h * 0.72, mono=True))
        f = (self.lo // step + 1) * step
        while f < self.hi:
            x = self._x(f, spec)
            p.setPen(grid)
            p.drawLine(QPointF(x, spec.top()), QPointF(x, spec.bottom()))
            p.setPen(qc(t.dim))
            p.drawText(QRectF(x - 45, axis.top(), 90, axis_h), Qt.AlignmentFlag.AlignCenter,
                       f"{f / 1e6:.3f}" if step >= 1000 else f"{f / 1e6:.4f}")
            f += step

        if self.passband:
            a, b = (self._x(v, spec) for v in self.passband)
            p.fillRect(QRectF(min(a, b), spec.top(), abs(b - a), spec.height()), qc(self.color, 40))

        n = len(self.data)
        pts = [QPointF(spec.left() + spec.width() * i / max(1, n - 1),
                       spec.bottom() - spec.height() * min(1.0, v / self.level_max))
               for i, v in enumerate(self.data)]
        trace = smooth_path(pts)
        fill = QPainterPath(QPointF(spec.left(), spec.bottom()))
        fill.lineTo(pts[0])
        fill.connectPath(trace)
        fill.lineTo(QPointF(spec.right(), spec.bottom()))
        fill.closeSubpath()
        g = QLinearGradient(spec.topLeft(), spec.bottomLeft())
        g.setColorAt(0, qc(self.color, 150))
        g.setColorAt(1, qc(self.color, 16))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawPath(fill)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc(self.color, 28), px(1.3)))          # glow
        p.drawPath(trace)
        p.setPen(QPen(qc(shade(self.color, 55)), max(0.7, px(0.6))))
        p.drawPath(trace)

        if self._wf is not None:
            rows = min(self.ROWS, int(wf.height() / self.ROW_PX) + 1)
            dst = QRectF(wf.left(), wf.top(), wf.width(), min(wf.height(), rows * self.ROW_PX))
            p.setClipRect(wf)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)     # crisp cells, like the radio
            p.drawImage(dst, self._wf, QRectF(0, 0, self._wf.width(), rows))
            p.setClipping(False)

        for hz, color in self.markers:
            if self.lo <= hz <= self.hi:
                x = self._x(hz, spec)
                p.setPen(QPen(qc(color, 230), max(1.0, px(1.4))))
                p.drawLine(QPointF(x, spec.top()), QPointF(x, wf.bottom()))
        if self.oor:
            p.setPen(qc(t.amber))
            p.setFont(font(px(12), bold=True))
            p.drawText(spec.adjusted(px(6), px(2), 0, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       "OUT OF RANGE")

    # ---- mouse -----------------------------------------------------------

    def _hz_at(self, x: float) -> int | None:
        r = self._inner()
        if self.hi <= self.lo or not self.data:
            return None
        return int(round((self.lo + (x - r.left()) / r.width() * (self.hi - self.lo)) / 100) * 100)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            hz = self._hz_at(e.position().x())
            if hz:
                self.qsy.emit(hz)

    def wheelEvent(self, e):
        steps = e.angleDelta().y() // 120 or (1 if e.angleDelta().y() > 0 else -1)
        self.tune.emit(steps * 100)
        e.accept()
