"""Icom CI-V over the radio's USB virtual COM port.

Command numbers come from Hamlib's icom_defs.h / ic7610.c (the most
field-proven public description of CI-V). The protocol layer (framing,
BCD, meters) was exercised against a scripted fake IC-7610 on a com0com
pair (tests/fake_icom.py); against the real IC-7610 it is not yet
bench-tested.

Frame:  FE FE <to> <from> <cmd> [<sub>] [data...] FD
Replies: FB = OK, FA = NG, otherwise the command echoed with data.
With CI-V Transceive ON the radio also broadcasts cmd 00 (freq) and 01
(mode) frames addressed to 00 whenever the knob is turned; those are
decoded as they arrive.
"""
from __future__ import annotations

import time

import serial

from .base import Backend, Control, RadioTimeout, interp

CTRL = 0xE0
QUERY_TIMEOUT_S = 0.30

MODE_CODES = {0x00: "LSB", 0x01: "USB", 0x02: "AM", 0x03: "CW", 0x04: "RTTY", 0x05: "FM",
              0x06: "WFM", 0x07: "CW-R", 0x08: "RTTY-R", 0x12: "PSK", 0x13: "PSK-R", 0x17: "DV"}
CODE_OF_MODE = {v: k for k, v in MODE_CODES.items()}
DATA_CAPABLE = ("LSB", "USB", "AM", "FM")

GENERIC_STR_CAL = [(0, -54), (120, 0), (241, 60)]
IC7610_STR_CAL = [(0, -54), (11, -48), (21, -42), (34, -36), (50, -30), (59, -24), (75, -18),
                  (93, -12), (103, -6), (124, 0), (145, 10), (160, 20), (183, 30), (204, 40),
                  (222, 50), (246, 60)]
IC7610_PO_CAL = [(0, 0.0), (21, 5.0), (43, 10.0), (65, 15.0), (83, 20.0), (95, 25.0), (105, 30.0),
                 (114, 35.0), (124, 40.0), (143, 50.0), (183, 75.0), (213, 100.0), (255, 120.0)]
SWR_CAL = [(0, 1.0), (48, 1.5), (80, 2.0), (120, 3.0), (240, 6.0)]
COMP_CAL = [(0, 0.0), (130, 15.0), (241, 30.0)]
# Hamlib ic7300.c (IC-705 entries; the COMP table is from flrig)
IC705_PO_CAL = [(0, 0.0), (21, 0.5), (43, 1.0), (65, 1.5), (83, 2.0), (95, 2.5), (105, 3.0),
                (114, 3.5), (124, 4.0), (143, 5.0), (183, 7.5), (213, 10.0), (255, 12.0)]
# Supply voltage / drain current meters (cmd 15 15 / 15 16), Hamlib ic7300.c / ic7610.c
IC7300_VD_CAL = [(0, 0.0), (13, 10.0), (241, 16.0)]
IC7300_ID_CAL = [(0, 0.0), (97, 10.0), (146, 15.0), (241, 25.0)]
IC705_VD_CAL = [(0, 0.0), (241, 16.0)]
IC705_ID_CAL = [(0, 0.0), (241, 4.0)]
IC7610_VD_CAL = [(0, 0.0), (151, 10.0), (211, 16.0)]
IC7610_ID_CAL = [(0, 0.0), (77, 10.0), (165, 20.0), (241, 30.0)]
IC9700_VD_CAL = [(0, 0.0), (13, 10.0), (241, 16.0)]
IC9700_ID_CAL = [(0, 0.0), (121, 10.0), (241, 20.0)]
IC705_COMP_CAL = [(0, 0.0), (11, 0.0), (34, 3.0), (58, 6.0), (81, 9.0), (104, 12.0), (128, 15.0),
                  (151, 18.0), (174, 21.0), (197, 24.0), (221, 27.0), (244, 30.0)]

TBW_OPTS = ["WIDE", "MID", "NAR"]
AGC_OPTS = ["FAST", "MID", "SLOW"]          # 0x16 0x12 values 1-3 on most models

BASE_MODES = ["LSB", "USB", "CW", "CW-R", "AM", "FM", "RTTY", "RTTY-R"]
PSK_MODES = BASE_MODES + ["PSK", "PSK-R"]
PRE2 = ["OFF", "P.AMP1", "P.AMP2"]
PRE1 = ["OFF", "P.AMP"]


def _model(addr, maxw, att, pre=PRE2, **kw):
    """One MODELS entry. Defaults describe a current (IC-7300 era) radio;
    each entry only lists how it differs. Sources: Hamlib rigs/icom/*.c
    (addresses, att/preamp steps, AGC codes, mode lists, 0x25/0x26 support).

    att      dB codes sent with cmd 0x11 (0 = off)
    agc      labels for 0x16 0x12 values 1, 2, 3...
    data     0x1A sub-command for DATA mode (None = no data mode)
    rit/xit  cmd 0x21 clarifier support;  vfo_b: cmd 0x25 (other-VFO freq)
    scope    stream the spectrum scope (27 xx); scope_max = its data_level_max
    vd_cal/id_cal  supply V / drain A meter tables (only polled when present)
    """
    m = dict(addr=addr, maxw=maxw, att=[0] + att, pre=pre, baud=115200, modes=BASE_MODES,
             agc=AGC_OPTS, data=0x06, rit=True, xit=True, vfo_b=True, tuner=True,
             ant=False, rx_ant=False, digisel=False, ipplus=False, tbw=False, scope=False, scope_max=160)
    m.update(kw)
    return m


# Only the IC-7610, IC-7300, IC-705 and IC-9700 entries predate the Hamlib-sourced
# expansion (2026-09-30); only the IC-705 and IC-7610 have been on the bench.
_IC785X = _model(0x8E, 200, [3, 6, 9, 12, 15, 18, 21], modes=PSK_MODES, digisel=True, ipplus=True, tbw=True)
MODELS = {
    # HF / HF+6m
    "IC-7300":    _model(0x94, 100, [20], ipplus=True, tbw=True, vd_cal=IC7300_VD_CAL, id_cal=IC7300_ID_CAL),
    "IC-7300MK2": _model(0xB6, 100, [20], ipplus=True, tbw=True, vd_cal=IC7300_VD_CAL, id_cal=IC7300_ID_CAL),
    "IC-7610":    _model(0x98, 100, [6, 12, 18], modes=PSK_MODES, ant=True, rx_ant=True, digisel=True,
                         ipplus=True, tbw=True, str_cal=IC7610_STR_CAL, po_cal=IC7610_PO_CAL,
                         scope=True, scope_max=200,      # Hamlib ic7610.c: 689 points, 0-200
                         vd_cal=IC7610_VD_CAL, id_cal=IC7610_ID_CAL),
    "IC-7760":    _model(0xB2, 200, [6, 12, 18], modes=PSK_MODES, tbw=True),
    "IC-7850":    _IC785X,
    "IC-7851":    _IC785X,
    "IC-7800":    _model(0x6A, 200, [3, 6, 9, 12, 15, 18, 21], modes=PSK_MODES, baud=19200),
    "IC-7700":    _model(0x74, 200, [6, 12, 18], modes=PSK_MODES, baud=19200),
    "IC-7600":    _model(0x7A, 100, [6, 12, 18], modes=PSK_MODES, baud=19200),
    "IC-7410":    _model(0x80, 100, [6, 12, 18], baud=19200, agc=["SLOW", "MID", "FAST"],
                         rit=False, xit=False, vfo_b=False),
    "IC-7200":    _model(0x76, 100, [20], pre=PRE1, baud=19200, data=0x04, agc=["FAST", "SLOW"],
                         modes=["LSB", "USB", "CW", "CW-R", "AM", "RTTY", "RTTY-R"],
                         rit=False, xit=False, vfo_b=False),
    # All-band / VHF-UHF
    "IC-705":     _model(0xA4, 10, [20], modes=BASE_MODES + ["WFM", "DV"], tuner=False, tbw=True,
                         scope=True, po_cal=IC705_PO_CAL, comp_cal=IC705_COMP_CAL,
                         vd_cal=IC705_VD_CAL, id_cal=IC705_ID_CAL),
    "IC-7100":    _model(0x88, 100, [20], baud=19200, modes=BASE_MODES + ["DV"], rit=False, xit=False),
    "IC-7000":    _model(0x70, 100, [12], pre=PRE1, baud=19200, modes=BASE_MODES + ["WFM"], data=None,
                         rit=False, xit=False, vfo_b=False, tuner=False),
    "IC-9700":    _model(0xA2, 100, [10], pre=PRE1, modes=BASE_MODES + ["DV"], xit=False, tuner=False,
                         tbw=True, vd_cal=IC9700_VD_CAL, id_cal=IC9700_ID_CAL),
    "IC-9100":    _model(0x7C, 100, [20], pre=PRE1, baud=19200, rit=False, xit=False, vfo_b=False),
    "IC-905":     _model(0xAC, 10, [20], modes=BASE_MODES + ["DV"], tuner=False, tbw=True),
}


# ---- pure encode/decode helpers (unit-tested) -----------------------------

def bcd_byte(n: int) -> int:
    return ((n // 10) << 4) | (n % 10)


def from_bcd_byte(b: int) -> int:
    return (b >> 4) * 10 + (b & 0x0F)


def freq_to_bcd(hz: int) -> bytes:
    """5 bytes, least-significant pair first (7.093 MHz -> 00 30 09 07 00)."""
    s = f"{int(hz):010d}"
    return bytes(bcd_byte(int(s[i:i + 2])) for i in range(8, -2, -2))


def bcd_to_freq(data: bytes) -> int:
    return sum(from_bcd_byte(b) * 100 ** i for i, b in enumerate(data[:5]))


def level_to_bcd(v: int) -> bytes:
    """0-255 as two BCD bytes, most-significant first (255 -> 02 55)."""
    v = max(0, min(255, int(v)))
    return bytes([bcd_byte(v // 100), bcd_byte(v % 100)])


def bcd_to_level(data: bytes) -> int:
    return from_bcd_byte(data[0]) * 100 + from_bcd_byte(data[1])


def rit_to_bcd(hz: int) -> bytes:
    """RIT/XIT offset: 2 BCD bytes least-significant first, then sign (01 = minus)."""
    a = min(9999, abs(int(hz)))
    return bytes([bcd_byte(a % 100), bcd_byte(a // 100), 1 if hz < 0 else 0])


def bcd_to_rit(data: bytes) -> int:
    v = from_bcd_byte(data[0]) + from_bcd_byte(data[1]) * 100
    return -v if len(data) > 2 and data[2] else v


def build_frame(addr: int, cmd: int, sub: int | None = None, data: bytes = b"") -> bytes:
    body = bytes([cmd]) + (bytes([sub]) if sub is not None else b"") + data
    return b"\xFE\xFE" + bytes([addr, CTRL]) + body + b"\xFD"


def split_frames(buf: bytearray) -> list[bytes]:
    """Pull complete FE FE ... FD frames out of buf (in place)."""
    frames = []
    while True:
        start = buf.find(b"\xFE\xFE")
        if start < 0:
            # keep a trailing lone FE: it may be the first half of a preamble
            keep = buf.endswith(b"\xFE")
            buf.clear()
            if keep:
                buf.append(0xFE)
            return frames
        end = buf.find(b"\xFD", start)
        if end < 0:
            del buf[:start]
            return frames
        frame = bytes(buf[start:end + 1])
        del buf[:end + 1]
        while frame.startswith(b"\xFE\xFE\xFE"):     # tolerate extra preamble bytes
            frame = frame[1:]
        if len(frame) >= 6:
            frames.append(frame)


SCOPE_SPANS = [5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000]   # Hamlib ic705 spectrum_spans
SCOPE_LEVEL_MAX = 160                                                            # data_level_max


def span_label(hz: int) -> str:
    return f"{hz // 1_000_000}M" if hz >= 1_000_000 else f"{hz // 1000}k"


def parse_scope_header(payload: bytes) -> dict | None:
    """Division-1 payload (after 27 00): id, seq, max, mode, 5B freq, 5B freq, out-of-range.
    Centre mode: centre + half-span.  Fixed/scroll modes: low edge + high edge."""
    if len(payload) < 15:
        return None
    mode = payload[3]
    f1, f2 = bcd_to_freq(payload[4:9]), bcd_to_freq(payload[9:14])
    if mode == 0x00:
        lo, hi = f1 - f2, f1 + f2
    else:
        lo, hi = f1, f2
    return {"mode": mode, "lo": lo, "hi": hi, "oor": bool(payload[14]), "extra": payload[15:]}


class ScopeAssembler:
    """Stitches the multi-frame scope sweep (IC-705 over USB: 11 divisions,
    475 points; IC-7610: 689 points). Only the MAIN scope (id 0) is kept:
    a dual-scope radio like the IC-7610 interleaves SUB sweeps (id 1)."""

    def __init__(self, level_max: int = 160):
        self.level_max = level_max
        self.header = None
        self.data = bytearray()
        self.last_div = 0

    def feed(self, payload: bytes) -> dict | None:
        """payload = bytes after '27 00'. Returns a finished sweep or None."""
        if len(payload) < 3 or payload[0] != 0x00:
            return None
        div, maxdiv = from_bcd_byte(payload[1]), from_bcd_byte(payload[2])
        if div == 1:
            self.header = parse_scope_header(payload)
            self.data = bytearray(self.header["extra"]) if self.header else bytearray()
            self.last_div = 1
        elif self.header is not None:
            # a division lost on the wire would shorten the sweep and, once the
            # row is stretched to the display width, slide every peak sideways:
            # drop the whole sweep instead, the next one is ~100 ms away
            if div != self.last_div + 1:
                self.header = None
                self.data = bytearray()
                return None
            self.last_div = div
            self.data.extend(payload[3:])
        if self.header is not None and div == maxdiv:
            sweep = {"lo": self.header["lo"], "hi": self.header["hi"], "mode": self.header["mode"],
                     "oor": self.header["oor"], "data": bytes(self.data), "max": self.level_max}
            self.header = None
            return sweep
        return None


def filter_width_label(mode: str, idx: int) -> str:
    """0x1A 0x03 width index -> Hz label (Icom's documented index scheme)."""
    if mode == "AM":
        hz = 200 * (idx + 1)
    elif mode in ("FM", "WFM"):
        return ""
    elif idx <= 9:
        hz = 50 * (idx + 1)
    else:
        hz = 600 + 100 * (idx - 10)
    return f"{hz / 1000:.1f}k" if hz >= 1000 else f"{hz}"


class IcomCIV(Backend):
    KIND = "icom"

    def __init__(self, cfg: dict):
        cfg.setdefault("model", "IC-7610")
        super().__init__(cfg)
        m = MODELS.get(self.model, MODELS["IC-7610"])
        self.m = m
        self.port = cfg.get("port", "")
        self.baud = int(cfg.get("baud", m["baud"]))
        addr = cfg.get("civ_addr")
        self.addr = int(str(addr), 16) if addr not in (None, "") else m["addr"]
        self.ser: serial.Serial | None = None
        self._buf = bytearray()
        self._meter_idx = 0
        self._fil = 1
        self._scope = ScopeAssembler(m["scope_max"])
        self._scope_restore: list[bytes] = []
        self.scope_seq = 0
        self.att_opts = ["OFF" if a == 0 else f"{a}dB" for a in m["att"]]
        self.modes = list(m["modes"])
        self.meters = ["PO", "ALC", "SWR", "COMP"]
        C = Control
        ctrls = [
            C("mode", "MODE", "choice", self.modes),
            C("filter", "FILTER", "choice", ["FIL1", "FIL2", "FIL3"],
              tip="Click: FIL1/2/3.  Arrows / wheel: width"),
            C("split", "SPLIT", "toggle"),
            C("agc", "AGC", "choice", m["agc"]),
            C("att", "ATT", "choice", self.att_opts),
            C("preamp", "P.AMP", "choice", m["pre"]),
            C("vox", "VOX", "toggle"), C("proc", "COMP", "toggle"),
            C("mon", "MONI", "toggle"), C("mon_level", "MONI LVL", "level"),
            C("af", "AF", "level"), C("rf", "RF GAIN", "level"), C("sql", "SQL", "level"),
            C("nr", "NR", "toggle"), C("nb1", "NB", "toggle"),
            C("notch", "MN", "toggle"), C("bc", "ANF", "toggle"),
            C("power", "PWR", "level", lo=0, hi=m["maxw"], step=1, unit="W"),
            C("ptt", "PTT", "toggle"),
        ]
        if m["data"] is not None:
            ctrls.append(C("data", "DATA", "toggle"))
        if m["rit"]:
            ctrls += [C("rit", "RIT", "toggle"),
                      C("rit_hz", "RIT", "level", lo=-9999, hi=9999, step=10, unit="Hz")]
        if m["xit"]:
            ctrls.append(C("xit", "ΔTX", "toggle"))
        if m["tuner"]:               # the IC-705 has no internal tuner (1C 01 answers NG)
            ctrls.append(C("atu", "ATU", "toggle",
                           tip="Click: tuner in/out.  Hold (or right-click): start a tune cycle (transmits)"))
        if m["ant"]:
            ctrls.append(C("ant", "ANT", "choice", ["ANT1", "ANT2"]))
        if m["rx_ant"]:
            ctrls.append(C("rx_ant", "RX ANT", "toggle"))
        if m["digisel"]:
            ctrls.append(C("psel", "DIGI-SEL", "toggle"))
        if m["ipplus"]:
            ctrls.append(C("nb2", "IP+", "toggle"))
        if m["tbw"]:
            ctrls.append(C("tbw", "TBW", "choice", TBW_OPTS))
        if m.get("scope") and cfg.get("scope", True):
            ctrls += [C("scope_span", "SPAN", "choice", [span_label(x) for x in SCOPE_SPANS]),
                      C("scope_mode", "CTR/FIX", "choice", ["CTR", "FIX"]),
                      C("scope_ref", "REF", "level", lo=-20, hi=20, step=0.5, unit="dB")]
        self.controls = {c.key: c for c in ctrls}

    # ---- serial ------------------------------------------------------------

    def open(self):
        if not self.port:
            raise RuntimeError("no serial port configured")
        # DTR and RTS are held LOW: the IC-705/7300/7610 can be set to key the
        # transmitter from these lines (USB SEND / USB keying menus), and
        # pyserial raises both on open by default. CI-V doesn't use them.
        ser = serial.Serial()
        ser.port, ser.baudrate, ser.timeout, ser.write_timeout = self.port, self.baud, 0.02, 1.0
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except serial.SerialException as exc:
            if "denied" in str(exc).lower() or "PermissionError" in str(exc):
                raise RuntimeError(f"{self.port} is in use by another program "
                                   "(ICOMNodeDisplay / rigctld / wfview?)") from exc
            raise
        self.ser = ser
        self._buf.clear()
        self._scope_restore = []
        if "scope_span" in self.controls:
            self._scope_start()
        self.info = {"Port": f"{self.port} @ {self.baud}", "CI-V address": f"{self.addr:02X}h"}

    def close(self):
        if self.ser:
            for frame in self._scope_restore:      # put the scope back as we found it
                try:
                    self.ser.write(frame)
                except Exception:
                    pass
            self._scope_restore = []
            self.ser.close()
            self.ser = None

    def safe_shutdown(self):
        if self.ser and self.state.get("tx"):
            self._send(0x1C, 0x00, b"\x00")

    def _scope_start(self):
        """Turn on scope CI-V data output (and the scope itself if it was off),
        remembering what to switch back off when we disconnect."""
        try:
            sts = self._cmd(0x27, 0x10)
            dop = self._cmd(0x27, 0x11)
        except RadioTimeout:
            return                                  # radio off: retried on next connect
        if sts is not None and sts[:1] == bytes([0]):
            self._cmd(0x27, 0x10, bytes([1]))
            self._scope_restore.append(build_frame(self.addr, 0x27, 0x10, bytes([0])))
        if dop is not None and dop[:1] != bytes([1]):
            self._cmd(0x27, 0x11, bytes([1]))
            self._scope_restore.insert(0, build_frame(self.addr, 0x27, 0x11, bytes([0])))

    def _scope_frame(self, f: bytes):
        sweep = self._scope.feed(f[6:-1])
        if sweep:
            self.scope_seq += 1
            sweep["seq"] = self.scope_seq
            with self._lock:
                self.state["scope"] = sweep
                self.version += 1

    def _send(self, cmd, sub=None, data=b""):
        frame = build_frame(self.addr, cmd, sub, data)
        self.ser.write(frame)
        self.stats["tx"] += 1
        self._log("TX", frame.hex(" ").upper())

    def _frames(self, deadline):
        while time.monotonic() < deadline:
            frames = split_frames(self._buf)
            if frames:
                return frames
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._buf.extend(chunk)
        return []

    def _handle_unsolicited(self, frame: bytes) -> None:
        cmd, data = frame[4], frame[5:-1]
        if cmd in (0x00, 0x03) and len(data) >= 5:
            self._apply({"freq": bcd_to_freq(data)})
        elif cmd in (0x01, 0x04) and len(data) >= 1:
            self._apply({"mode": MODE_CODES.get(data[0], "?")})

    def _cmd(self, cmd, sub=None, data=b"", sub_len=None) -> bytes | None:
        """Send and wait for the matching reply; returns its payload (after
        cmd and sub bytes), b'' for FB (OK), None for FA (NG)."""
        self._send(cmd, sub, data)
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        while True:
            frames = self._frames(deadline)
            if not frames and time.monotonic() >= deadline:
                raise RadioTimeout(f"{cmd:02X}")
            for f in frames:
                self.stats["rx"] += 1
                if not (len(f) > 6 and f[4] == 0x27 and f[5] == 0x00):     # keep scope data out of DIAG
                    self._log("RX", f.hex(" ").upper())
                to, frm = f[2], f[3]
                if frm == CTRL:            # our own echo (CI-V USB echo on)
                    continue
                if frm != self.addr:
                    continue
                if to == 0x00:             # transceive broadcast
                    self._handle_unsolicited(f)
                    continue
                if f[4] == 0x27 and len(f) > 6 and f[5] == 0x00:   # streamed scope sweep
                    self._scope_frame(f)
                    continue
                self._got_reply()
                c = f[4]
                if c == 0xFB:
                    return b""
                if c == 0xFA:
                    return None
                if c != cmd:
                    continue
                payload = f[5:-1]
                if sub is not None:
                    n = sub_len or 1
                    payload = payload[n:] if len(payload) >= n else b""
                return payload

    # ---- polls -------------------------------------------------------------

    def fast_polls(self):
        return [self._poll_freq, self._poll_meter, self._poll_tx]

    def slow_polls(self):
        m = self.m
        f = lambda sub, key: (lambda: self._poll_func(sub, key))
        lv = lambda sub, key: (lambda: self._poll_lvl(sub, key))
        polls = [self._poll_mode, self._poll_width, self._poll_split, self._poll_sql,
                 self._poll_agc, self._poll_att, self._poll_pre,
                 f(0x22, "nb1"), f(0x40, "nr"), f(0x41, "bc"), f(0x48, "notch"),
                 f(0x44, "proc"), f(0x46, "vox"), f(0x45, "mon"),
                 lv(0x01, "af"), lv(0x02, "rf"), lv(0x03, "sql"), lv(0x15, "mon_level"),
                 self._poll_power]
        if m["data"] is not None:
            polls.append(self._poll_data)
        if m["vfo_b"]:
            polls.append(self._poll_freq_b)
        if m["rit"]:
            polls += [self._poll_rit_hz, lambda: self._poll_bool(0x21, 0x01, "rit")]
        if m["xit"]:
            polls.append(lambda: self._poll_bool(0x21, 0x02, "xit"))
        if m["tuner"]:
            polls.append(self._poll_tuner)
        if m["ant"]:
            polls.append(self._poll_ant)
        if m["digisel"]:
            polls.append(f(0x4E, "psel"))
        if m["ipplus"]:
            polls.append(self._poll_ipplus)
        if m["tbw"]:
            polls.append(self._poll_tbw)
        if m.get("vd_cal"):
            polls += [lambda: self._poll_supply(0x15, "vd", m["vd_cal"]),
                      lambda: self._poll_supply(0x16, "id", m["id_cal"])]
        if "scope_span" in self.controls:
            polls += [self._poll_scope_span, self._poll_scope_mode, self._poll_scope_ref]
        return polls

    def _poll_scope_span(self):
        d = self._cmd(0x27, 0x15, bytes([0]))
        if d and len(d) >= 6:
            self._apply({"scope_span": span_label(bcd_to_freq(d[1:6]) * 2)})

    def _poll_scope_mode(self):
        d = self._cmd(0x27, 0x14, bytes([0]))
        if d and len(d) >= 2:
            self._apply({"scope_mode": "CTR" if d[1] == 0 else "FIX"})

    def _poll_scope_ref(self):
        d = self._cmd(0x27, 0x19, bytes([0]))
        if d and len(d) >= 4:
            v = (from_bcd_byte(d[1]) * 100 + from_bcd_byte(d[2])) / 100
            self._apply({"scope_ref": -v if d[3] else v})

    def _poll_freq(self):
        d = self._cmd(0x03)
        if d and len(d) >= 5:
            self._apply({"freq": bcd_to_freq(d)})

    def _poll_freq_b(self):
        d = self._cmd(0x25, 0x01)
        if d and len(d) >= 5:
            self._apply({"freq_b": bcd_to_freq(d)})

    def _poll_mode(self):
        d = self._cmd(0x04)
        if d and len(d) >= 2:
            self._fil = d[1]
            self._apply({"mode": MODE_CODES.get(d[0], "?"), "fil": d[1]})

    def _poll_data(self):
        d = self._cmd(0x1A, self.m["data"])
        if d and len(d) >= 1:
            self._apply({"data": d[0] != 0})

    def _poll_width(self):
        d = self._cmd(0x1A, 0x03)
        if d:
            idx = from_bcd_byte(d[0])
            w = filter_width_label(self.state.get("mode", ""), idx)
            self._apply({"width_idx": idx, "filter": f"FIL{self._fil} {w}".strip()})

    def _poll_meter(self):
        if not self.state.get("tx"):
            d = self._cmd(0x15, 0x02)
            if d and len(d) >= 2:
                self._apply({"smeter": interp(self.m.get("str_cal", GENERIC_STR_CAL), bcd_to_level(d))})
            return
        sub, key, conv = [(0x11, "po", lambda r: interp(self.m.get("po_cal"), r) if self.m.get("po_cal")
                           else r / 255 * self.m["maxw"]),
                          (0x13, "alc", lambda r: min(1.0, r / 120)),
                          (0x12, "swr", lambda r: interp(SWR_CAL, r)),
                          (0x14, "comp", lambda r: interp(self.m.get("comp_cal", COMP_CAL), r))][self._meter_idx % 4]
        self._meter_idx += 1
        d = self._cmd(0x15, sub)
        if d and len(d) >= 2:
            self._apply({key: conv(bcd_to_level(d))})

    def _poll_supply(self, sub, key, cal):
        """Vd / Id meters: not shown on the deck, but served to programs sharing
        the radio (ICOMNodeDisplay shows them)."""
        d = self._cmd(0x15, sub)
        if d and len(d) >= 2:
            self._apply({key: round(interp(cal, bcd_to_level(d)), 2)})

    def _poll_tx(self):
        d = self._cmd(0x1C, 0x00)
        if d:
            self._apply({"tx": d[0] != 0})

    def _poll_sql(self):
        d = self._cmd(0x15, 0x01)
        if d:
            self._apply({"busy": d[0] != 0})

    def _poll_split(self):
        d = self._cmd(0x0F)
        if d:
            self._apply({"split": d[0] == 0x01})

    def _poll_rit_hz(self):
        d = self._cmd(0x21, 0x00)
        if d and len(d) >= 3:
            self._apply({"rit_hz": bcd_to_rit(d)})

    def _poll_bool(self, cmd, sub, key):
        d = self._cmd(cmd, sub)
        if d:
            self._apply({key: d[0] != 0})

    def _poll_tuner(self):
        d = self._cmd(0x1C, 0x01)
        if d:
            self._apply({"atu": d[0] != 0, "tuning": d[0] == 2})

    def _poll_func(self, sub, key):
        d = self._cmd(0x16, sub)
        if d:
            self._apply({key: d[0] != 0})

    def _poll_lvl(self, sub, key):
        d = self._cmd(0x14, sub)
        if d and len(d) >= 2:
            self._apply({key: round(bcd_to_level(d) * 100 / 255)})

    def _poll_power(self):
        d = self._cmd(0x14, 0x0A)
        if d and len(d) >= 2:
            self._apply({"power": round(bcd_to_level(d) * self.m["maxw"] / 255)})

    def _poll_agc(self):
        d = self._cmd(0x16, 0x12)
        if d and 1 <= d[0] <= len(self.m["agc"]):
            self._apply({"agc": self.m["agc"][d[0] - 1]})

    def _poll_att(self):
        d = self._cmd(0x11)
        if d:
            db = from_bcd_byte(d[0])
            self._apply({"att": "OFF" if db == 0 else f"{db}dB"})

    def _poll_pre(self):
        d = self._cmd(0x16, 0x02)
        if d and d[0] < len(self.m["pre"]):
            self._apply({"preamp": self.m["pre"][d[0]]})

    def _poll_ant(self):
        d = self._cmd(0x12)
        if d:
            vals = {"ant": "ANT2" if d[0] == 1 else "ANT1"}
            if self.m["rx_ant"] and len(d) >= 2:
                vals["rx_ant"] = d[1] != 0
            self._apply(vals)

    def _poll_ipplus(self):
        d = self._cmd(0x1A, 0x07)
        if d:
            self._apply({"nb2": d[0] != 0})

    def _poll_tbw(self):
        d = self._cmd(0x16, 0x58)
        if d and d[0] < 3:
            self._apply({"tbw": TBW_OPTS[d[0]]})

    # ---- writes --------------------------------------------------------------

    def apply_set(self, key, value):
        st = self.state
        fn = {"nb1": 0x22, "nr": 0x40, "bc": 0x41, "notch": 0x48, "proc": 0x44,
              "vox": 0x46, "mon": 0x45, "psel": 0x4E}
        lv = {"af": 0x01, "rf": 0x02, "sql": 0x03, "mon_level": 0x15}
        if key == "freq":
            self._cmd(0x05, None, freq_to_bcd(value))
        elif key == "freq_b":
            self._cmd(0x25, 0x01, freq_to_bcd(value))
        elif key == "mode":
            code = CODE_OF_MODE.get(value)
            if code is not None:
                reply = self._cmd(0x06, None, bytes([code, self._fil]))
                if reply is None and value in ("DV", "WFM"):
                    self._cmd(0x06, None, bytes([code]))   # no selectable filter in these modes on some rigs
        elif key == "data":
            on = bool(value) and st.get("mode") in DATA_CAPABLE
            self._cmd(0x1A, self.m["data"], self._data_bytes(on))
            self._apply({"data": on}, user=True)
        elif key == "rit":
            self._cmd(0x21, 0x01, bytes([int(bool(value))]))
        elif key == "xit":
            self._cmd(0x21, 0x02, bytes([int(bool(value))]))
        elif key == "rit_hz":
            self._cmd(0x21, 0x00, rit_to_bcd(value))
        elif key == "split":
            self._cmd(0x0F, None, bytes([int(bool(value))]))
        elif key == "atu":
            self._cmd(0x1C, 0x01, bytes([int(bool(value))]))
        elif key == "agc":
            self._cmd(0x16, 0x12, bytes([self.m["agc"].index(value) + 1]))
        elif key == "att":
            db = 0 if value == "OFF" else int(value.rstrip("dB"))
            self._cmd(0x11, None, bytes([bcd_byte(db)]))
        elif key == "preamp":
            self._cmd(0x16, 0x02, bytes([self.m["pre"].index(value)]))
        elif key == "ant":
            data = bytes([1 if value == "ANT2" else 0])
            if self.m["rx_ant"]:
                data += bytes([int(bool(st.get("rx_ant")))])
            self._cmd(0x12, None, data)
        elif key == "rx_ant":
            self._cmd(0x12, None, bytes([1 if st.get("ant") == "ANT2" else 0, int(bool(value))]))
        elif key == "nb2":
            self._cmd(0x1A, 0x07, bytes([int(bool(value))]))
        elif key == "tbw":
            self._cmd(0x16, 0x58, bytes([TBW_OPTS.index(value)]))
        elif key in fn:
            self._cmd(0x16, fn[key], bytes([int(bool(value))]))
        elif key in lv:
            self._cmd(0x14, lv[key], level_to_bcd(round(value * 255 / 100)))
        elif key == "power":
            self._cmd(0x14, 0x0A, level_to_bcd(round(value * 255 / self.m["maxw"])))
        elif key == "scope_span":
            hz = SCOPE_SPANS[[span_label(x) for x in SCOPE_SPANS].index(value)]
            self._cmd(0x27, 0x15, bytes([0]) + freq_to_bcd(hz // 2))
        elif key == "scope_mode":
            self._cmd(0x27, 0x14, bytes([0, 0 if value == "CTR" else 1]))
        elif key == "scope_ref":
            v = round(float(value) * 2) / 2
            n = int(round(abs(v) * 100))
            self._cmd(0x27, 0x19, bytes([0, bcd_byte(n // 100), bcd_byte(n % 100), 1 if v < 0 else 0]))
        elif key == "ptt":
            self._cmd(0x1C, 0x00, bytes([int(bool(value))]))
            self._apply({"tx": bool(value)}, user=True)

    def apply_action(self, name, value):
        st = self.state
        if name == "rit_clear":
            self._cmd(0x21, 0x00, rit_to_bcd(0))
            self._apply({"rit_hz": 0}, user=True)
        elif name == "tune":
            self._cmd(0x1C, 0x01, b"\x02")
        elif name == "filter_click":
            self._fil = self._fil % 3 + 1
            code = CODE_OF_MODE.get(st.get("mode"))
            if st.get("data") and self.m["data"] == 0x06:
                self._cmd(0x1A, 0x06, self._data_bytes(True))
            elif code is not None:
                self._cmd(0x06, None, bytes([code, self._fil]))
            self._poll_width()
        elif name == "filter_step":
            idx = max(0, min(49 if st.get("mode") == "AM" else 40, st.get("width_idx", 0) + int(value)))
            self._cmd(0x1A, 0x03, bytes([bcd_byte(idx)]))
            self._poll_width()

    def _data_bytes(self, on: bool) -> bytes:
        """DATA mode payload: 0x1A 0x06 carries data on/off + filter; the
        IC-7200's 0x1A 0x04 is on/off only (Hamlib icom.c)."""
        if self.m["data"] == 0x06:
            return bytes([1 if on else 0, self._fil if on else 0])
        return bytes([1 if on else 0])

    @staticmethod
    def mode_label(st: dict) -> str:
        mode = st.get("mode", "")
        return f"{mode}-D" if st.get("data") else mode
