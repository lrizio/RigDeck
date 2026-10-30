"""Radio-agnostic backend plumbing.

Every radio driver (Icom CI-V, Yaesu newcat, Hamlib rigctld, simulator)
subclasses Backend. The UI never touches serial ports: it reads a state
snapshot (a plain dict) and calls set()/action(). All I/O happens on the
backend's own thread, so a slow or dead radio can never freeze the window.

State keys (all optional -- a backend only fills what its radio has):
    freq, freq_b        Hz (freq_b = the other VFO, used for split)
    mode, data          base mode name, data-mode flag
    filter              label for the current RX filter/width
    rit, xit, rit_hz    clarifier on/off and offset (Hz)
    split, atu, tuning  booleans
    ant, rx_ant, agc, att, preamp, tbw   labels / booleans
    psel, vox, proc, mon, nr, nb1, nb2, notch, bc   booleans
    af, rf, sql, mon_level   0-100 %
    power               watts
    tx                  transmitting
    smeter              dB relative to S9 (S0 = -54, S9+60 = +60)
    busy                squelch open / signal present (None = unknown)
    po, swr, alc, comp  TX meters (W, ratio, 0-1 fraction, dB)
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

LINK_OFFLINE = "OFFLINE"
LINK_CONNECTING = "CONNECTING"
LINK_LIVE = "LIVE"
LINK_NO_REPLY = "NO REPLY"
LINK_ERROR = "ERROR"

# A user-set value is trusted over poll results for this long, so a poll
# that raced the set can't make a button flicker back to its old state.
USER_HOLD_S = 1.2
RETRY_OPEN_S = 5.0
NO_REPLY_AFTER = 3          # consecutive timeouts before the radio is presumed off


@dataclass
class Control:
    """Describes one control a backend supports.

    kind: 'toggle' (bool), 'choice' (one of options), 'level' (lo..hi),
    'action' (fire-and-forget)."""
    key: str
    label: str
    kind: str
    options: list[str] = field(default_factory=list)
    lo: float = 0
    hi: float = 100
    step: float = 1
    unit: str = ""
    tip: str = ""


class RadioTimeout(Exception):
    pass


class Backend:
    KIND = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.model = cfg.get("model", "?")
        self.controls: dict[str, Control] = {}
        self.modes: list[str] = []
        self.meters: list[str] = []          # TX meters this radio reports
        self.state: dict[str, Any] = {}
        self.link = LINK_OFFLINE
        self.error = ""
        self.info: dict[str, str] = {}
        self.log: deque = deque(maxlen=800)
        self.stats = {"tx": 0, "rx": 0, "timeouts": 0, "errors": 0}

        self._lock = threading.Lock()
        self._pending: list[tuple[str, str, Any]] = []   # (kind, key, value)
        self._hold: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._timeouts_in_row = 0
        self._slow_idx = 0
        self.version = 0                     # bumps on every state change

    # ---- UI-thread API ---------------------------------------------------

    def supports(self, key: str) -> bool:
        return key in self.controls

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def set(self, key: str, value: Any) -> None:
        """Optimistically update state and queue the write. Repeated sets of
        the same key (wheel tuning) coalesce into one pending write."""
        self._apply({key: value}, user=True)
        with self._lock:
            for i, (k, kk, _) in enumerate(self._pending):
                if k == "set" and kk == key:
                    self._pending[i] = ("set", key, value)
                    return
            self._pending.append(("set", key, value))

    def action(self, name: str, value: Any = None) -> None:
        with self._lock:
            self._pending.append(("act", name, value))

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"rig-{self.model}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    # ---- helpers for subclasses -----------------------------------------

    def _apply(self, values: dict, user: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            changed = False
            for k, v in values.items():
                if not user and now < self._hold.get(k, 0):
                    continue
                if user:
                    self._hold[k] = now + USER_HOLD_S
                if self.state.get(k) != v:
                    self.state[k] = v
                    changed = True
            if changed:
                self.version += 1

    def _log(self, direction: str, text: str) -> None:
        self.log.append((time.time(), direction, text))

    def _set_link(self, link: str, error: str = "") -> None:
        self.link = link
        self.error = error
        self.version += 1

    def _got_reply(self) -> None:
        self._timeouts_in_row = 0
        if self.link != LINK_LIVE:
            self._set_link(LINK_LIVE)

    def _got_timeout(self) -> None:
        self.stats["timeouts"] += 1
        self._timeouts_in_row += 1
        if self._timeouts_in_row >= NO_REPLY_AFTER and self.link == LINK_LIVE:
            self._set_link(LINK_NO_REPLY, "radio not answering (off?)")
        elif self._timeouts_in_row >= NO_REPLY_AFTER and self.link == LINK_CONNECTING:
            self._set_link(LINK_NO_REPLY, "radio not answering (off? wrong port/baud?)")

    # ---- subclass hooks --------------------------------------------------

    def open(self) -> None: ...
    def close(self) -> None: ...
    def apply_set(self, key: str, value: Any) -> None: ...
    def apply_action(self, name: str, value: Any) -> None: ...
    def fast_polls(self) -> list[Callable[[], None]]: return []
    def slow_polls(self) -> list[Callable[[], None]]: return []
    def safe_shutdown(self) -> None:
        """Called before close(): make sure we are not left transmitting."""

    # ---- thread ----------------------------------------------------------

    def _next_pending(self):
        with self._lock:
            return self._pending.pop(0) if self._pending else None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._set_link(LINK_CONNECTING)
            try:
                self.open()
            except Exception as exc:  # port busy / missing / rigctld failed
                self.stats["errors"] += 1
                self._set_link(LINK_ERROR, str(exc))
                self._log("ERR", f"open failed: {exc}")
                self._stop.wait(RETRY_OPEN_S)
                continue
            try:
                self._loop()
                self._drain_pending()      # stopping: still send queued writes (PTT off, AF restore)
            except Exception as exc:  # cable pulled, rigctld died...
                self.stats["errors"] += 1
                self._set_link(LINK_ERROR, str(exc))
                self._log("ERR", f"link dropped: {exc}")
            finally:
                try:
                    self.safe_shutdown()
                except Exception:
                    pass
                try:
                    self.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                self._stop.wait(RETRY_OPEN_S)
        self._set_link(LINK_OFFLINE)

    def _run_poll(self, fn) -> None:
        try:
            fn()
        except RadioTimeout as exc:
            self._log("ERR", f"no reply to {exc}")
            self._got_timeout()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._drain_pending()
            if self.link == LINK_NO_REPLY:
                # Radio is off/not answering: probe gently instead of hammering.
                fast = self.fast_polls()
                if fast:
                    self._run_poll(fast[0])
                self._stop.wait(1.0)
                continue
            for fn in self.fast_polls():
                self._drain_pending()
                self._run_poll(fn)
            slow = self.slow_polls()
            if slow:
                self._drain_pending()
                self._run_poll(slow[self._slow_idx % len(slow)])
                self._slow_idx += 1
            self._stop.wait(0.005)

    def _drain_pending(self) -> None:
        while True:
            item = self._next_pending()
            if item is None:
                return
            kind, key, value = item
            try:
                if kind == "set":
                    self.apply_set(key, value)
                else:
                    self.apply_action(key, value)
            except RadioTimeout as exc:
                self._log("ERR", f"no reply to {exc}")
                self._got_timeout()


def interp(table: list[tuple[float, float]], raw: float) -> float:
    """Piecewise-linear lookup (Hamlib-style calibration tables)."""
    if raw <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if raw <= x1:
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0) if x1 != x0 else y1
    return table[-1][1]
