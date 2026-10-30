"""Yaesu "new CAT" radios over their own CAT port (the "Enhanced" CP2105 port).

FT-710 and FT-991A are bench-tested; FTDX10, FTDX101D/MP, FT-991 and
FT-891 are described from Hamlib's newcat.c and per-model files (see
the class docstrings) and have not been on the bench.

FT-710 notes:

Command formats were confirmed against a real FT-710 on 2026-09-27 by
read / set / read-back / restore (see docs/protocol-notes.md), with
calibration tables taken from Hamlib's ft710.h. Transmit-related
commands (TX1;, AC003;, VX1;) were deliberately NOT exercised in that
check; their formats come from the FT-710 CAT manual and Hamlib's newcat
driver.
"""
from __future__ import annotations

import time

import serial

from .base import Backend, Control, RadioTimeout, interp

QUERY_TIMEOUT_S = 0.35
# Small gap after each set command so a burst of deck-button presses is
# paced out rather than blasted at the radio (Hamlib paces newcat writes too).
POST_WRITE_S = 0.03

# Radio IDs returned by "ID;" (Hamlib newcat.c nc_rigid_t)
YAESU_IDS = {"0800": "FT-710", "0670": "FT-991A", "0570": "FT-991", "0761": "FTDX10",
             "0681": "FTDX101D", "0682": "FTDX101MP", "0135": "FT-891"}

# MD / IF mode digit -> (base mode, data flag)
CODE_TO_MODE = {
    "1": ("LSB", False), "2": ("USB", False), "3": ("CW-U", False), "4": ("FM", False),
    "5": ("AM", False), "6": ("RTTY-L", False), "7": ("CW-L", False), "8": ("LSB", True),
    "9": ("RTTY-U", False), "A": ("FM", True), "B": ("FM-N", False), "C": ("USB", True),
    "D": ("AM-N", False), "E": ("PSK", False), "F": ("FM-N", True),
}
MODE_TO_CODE = {v: k for k, v in CODE_TO_MODE.items()}
MODES = ["LSB", "USB", "CW-U", "CW-L", "AM", "AM-N", "FM", "FM-N", "RTTY-L", "RTTY-U", "PSK"]
DATA_LABELS = {"LSB": "DATA-L", "USB": "DATA-U", "FM": "DATA-FM", "FM-N": "DATA-FMN"}

# SH width index tables (Hamlib ftdx101_*_widths, shared by the FT-710)
SSB_WIDTHS = [0, 300, 400, 600, 850, 1100, 1200, 1500, 1650, 1800, 1950, 2100, 2200, 2300,
              2400, 2500, 2600, 2700, 2800, 2900, 3000, 3200, 3500, 4000]
CW_WIDTHS = [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 800, 1200, 1400, 1700,
             2000, 2400, 3000, 3200, 3500, 4000]

STR_CAL = [(0, -54), (12, -48), (27, -42), (40, -36), (55, -30), (65, -24), (80, -18),
           (95, -12), (112, -6), (130, 0), (150, 10), (172, 20), (190, 30), (220, 40),
           (240, 50), (255, 60)]
PO_CAL = [(0, 0.0), (27, 5.0), (94, 25.0), (147, 50.0), (176, 75.0), (205, 100.0), (255, 125.0)]
SWR_CAL = [(0, 1.0), (26, 1.2), (52, 1.5), (89, 2.0), (126, 3.0), (173, 4.0), (236, 5.0), (255, 25.0)]
COMP_CAL = [(0, 0.0), (40, 2.5), (60, 5.0), (85, 7.5), (135, 10.0), (150, 12.5), (175, 15.0),
            (195, 17.5), (220, 20.0)]

AGC_SET = ["OFF", "FAST", "MID", "SLOW", "AUTO"]          # GT0n set values 0-4
AGC_READ = {0: "OFF", 1: "FAST", 2: "MID", 3: "SLOW", 4: "AUTO", 5: "AUTO", 6: "AUTO"}
ATT_OPTS = ["OFF", "6dB", "12dB", "18dB"]                 # RA0n, n = 0-3 (bench-confirmed)
PRE_OPTS = ["IPO", "AMP1", "AMP2"]                        # PA0n, n = 0-2 (bench-confirmed)


def widths_for(mode: str) -> list[int] | None:
    if mode in ("LSB", "USB"):
        return SSB_WIDTHS
    if mode in ("CW-U", "CW-L", "RTTY-L", "RTTY-U", "PSK"):
        return CW_WIDTHS
    return None          # AM/FM widths are fixed


def fmt_width(hz: int) -> str:
    return f"{hz / 1000:.1f}k" if hz >= 1000 else f"{hz}"


def parse_if(frame: str, code_map: dict | None = None) -> dict | None:
    """28-byte IF; reply, e.g. IF000007093000+000000100000; (same layout on
    the FT-710 and FT-991A; only the mode-digit table differs)."""
    if len(frame) != 28 or not frame.startswith("IF") or frame[14] not in "+-":
        return None
    try:
        hz = int(frame[5:14])
        off = int(frame[15:19]) * (-1 if frame[14] == "-" else 1)
    except ValueError:
        return None
    mode, data = (code_map or CODE_TO_MODE).get(frame[21], ("?", False))
    vfo = {"0": "VFO", "1": "MEM", "2": "M-TUNE", "3": "QMB", "5": "PMS"}.get(frame[22], "VFO")
    return {"freq": hz, "rit_hz": off, "rit": frame[19] == "1", "xit": frame[20] == "1",
            "mode": mode, "data": data, "vfo": vfo}


class YaesuFT710(Backend):
    KIND = "yaesu"
    MODEL = "FT-710"
    RADIO_ID = "0800"
    CODE_TO_MODE = CODE_TO_MODE
    MODES = MODES
    SSB_WIDTHS = SSB_WIDTHS
    CW_WIDTHS = CW_WIDTHS
    ATT_OPTS = ATT_OPTS
    PO_CAL = PO_CAL
    TUNE_CMD = "AC003"          # FT-710 tuning start (P3=3)
    SH_SET = "SH00{:02d}"       # width index write; every model's reply ends in the 2-digit index
    MAX_W = 100
    STR_CAL = STR_CAL
    PRE_OPTS = PRE_OPTS
    SPLIT = "ST"                # "ST" = ST0/ST1;  "FT" = TX-on-VFO-B via FT2/FT3 (read FT0/FT1)
    CLAR = "CF"                 # "CF" = CF command;  "RT" = RT/XT + RC/RU/RD;  None = no CAT clarifier
    HAS_BUSY = False            # BY; reports squelch-open
    CAT_RATE_MENU = "CAT-1 RATE"

    def __init__(self, cfg: dict):
        cfg.setdefault("model", self.MODEL)
        super().__init__(cfg)
        self.MODE_TO_CODE = {v: k for k, v in self.CODE_TO_MODE.items()}
        self.port = cfg.get("port", "")
        self.baud = int(cfg.get("baud", 38400))
        self.ser: serial.Serial | None = None
        self._buf = ""
        self._meter_idx = 0
        self.modes = self.MODES
        self.meters = ["PO", "ALC", "SWR", "COMP"]
        C = Control
        self.controls = {c.key: c for c in [
            C("mode", "MODE", "choice", self.MODES),
            C("data", "DATA", "toggle"),
            C("filter", "WIDTH", "choice", tip="Click: NARROW on/off.  Arrows / wheel: width"),
            C("split", "SPLIT", "toggle"),
            C("atu", "ATU", "toggle", tip="Click: tuner in/out.  Hold (or right-click): start a tune cycle (transmits)"),
            C("agc", "AGC", "choice", AGC_SET),
            C("att", "ATT", "choice", self.ATT_OPTS),
            C("preamp", "IPO/AMP", "choice", self.PRE_OPTS),
            C("vox", "VOX", "toggle"), C("proc", "PROC", "toggle"),
            C("mon", "MON", "toggle"), C("mon_level", "MON LVL", "level", 0, 100),
            C("af", "AF", "level"), C("rf", "RF GAIN", "level"), C("sql", "SQL", "level"),
            C("nr", "DNR", "toggle"), C("nb1", "NB", "toggle"),
            C("notch", "NOTCH", "toggle"), C("bc", "DNF", "toggle"),
            C("power", "PWR", "level", lo=5, hi=self.MAX_W, step=1, unit="W"),
            C("ptt", "PTT", "toggle"),
        ]}
        if self.CLAR:
            for c in (C("rit", "RIT", "toggle"), C("xit", "XIT", "toggle"),
                      C("rit_hz", "CLAR", "level", lo=-9990, hi=9990, step=10, unit="Hz")):
                self.controls[c.key] = c
        self.state.update(busy=None)

    # ---- serial ----------------------------------------------------------

    def open(self) -> None:
        if not self.port:
            raise RuntimeError("no serial port configured")
        self.ser = serial.Serial(self.port, self.baud, timeout=0.02, write_timeout=1.0)
        self.ser.rts = True          # the FT-991A only answers CAT with RTS asserted (menu 033 CAT RTS)
        self._buf = ""
        self.info = {"Port": f"{self.port} @ {self.baud}"}
        try:
            reply = self._query("ID")
            rid = reply[2:6] if reply else "?"
            self.info["Radio ID"] = f"{rid} ({YAESU_IDS.get(rid, 'unknown')})"
            if rid != self.RADIO_ID:
                self._log("ERR", f"expected {self.MODEL} (ID{self.RADIO_ID}), radio says ID{rid}")
        except RadioTimeout:
            self._got_timeout()

    def close(self) -> None:
        if self.ser:
            self.ser.close()
            self.ser = None

    def safe_shutdown(self) -> None:
        if self.ser and self.state.get("tx"):
            self._write("TX0")

    def _send(self, cmd: str) -> None:
        self.ser.write((cmd + ";").encode("ascii"))
        self.stats["tx"] += 1
        self._log("TX", cmd + ";")

    def _write(self, cmd: str) -> None:
        """A set command: the radio sends nothing back on success."""
        self._send(cmd)
        time.sleep(POST_WRITE_S)

    def _read_frame(self, deadline: float) -> str | None:
        while time.monotonic() < deadline:
            i = self._buf.find(";")
            if i >= 0:
                frame, self._buf = self._buf[:i + 1], self._buf[i + 1:]
                return frame
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                self._buf += chunk.decode("ascii", errors="replace")
        return None

    def _query(self, cmd: str, prefix: str | None = None) -> str | None:
        """Send a read command and return its reply frame. None = the radio
        answered '?;' (command not valid right now). Raises RadioTimeout."""
        prefix = prefix or cmd[:2]
        self._send(cmd)
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        while True:
            frame = self._read_frame(deadline)
            if frame is None:
                raise RadioTimeout(cmd)
            self.stats["rx"] += 1
            self._log("RX", frame)
            if frame == "?;":
                self._got_reply()
                return None
            if frame.startswith(prefix):
                self._got_reply()
                return frame

    def _num(self, cmd: str, start: int, count: int) -> int | None:
        r = self._query(cmd)
        if r and len(r) >= start + count and r[start:start + count].lstrip("+-").isdigit():
            return int(r[start:start + count])
        return None

    # ---- polls -------------------------------------------------------------

    def fast_polls(self):
        # Frequency comes from IF;, not FA;: bench-found 2026-09-27 that with
        # RIT on, FA; reads back VFO + clarifier offset, while IF; carries the
        # plain VFO frequency (what the radio's own display shows) plus the
        # offset separately. Tuning from an FA; reading would drift by the offset.
        return [self._poll_if, self._poll_meter, self._poll_tx]

    def slow_polls(self):
        flag = lambda cmd, key, pos=3: (lambda: self._poll_flag(cmd, key, pos))
        level = lambda cmd, key, s, n, scale: (lambda: self._poll_level(cmd, key, s, n, scale))
        return [
            self._poll_fb, self._poll_width, flag("NA0", "narrow"),
            self._poll_split, self._poll_ac, self._poll_agc,
            self._poll_att, self._poll_pre, flag("NB0", "nb1"), flag("NR0", "nr"),
            flag("BC0", "bc"), flag("BP00", "notch", 6), flag("PR0", "proc"),
            flag("VX", "vox", 2), flag("ML0", "mon", 5),
            level("ML1", "mon_level", 3, 3, 100), level("AG0", "af", 3, 3, 255),
            level("RG0", "rf", 3, 3, 255), level("SQ0", "sql", 3, 3, 100), self._poll_pc,
        ] + ([self._poll_busy] if self.HAS_BUSY else [])

    def _poll_fa(self):
        v = self._num("FA", 2, 9)
        if v is not None:
            self._apply({"freq": v})

    def _poll_fb(self):
        v = self._num("FB", 2, 9)
        if v is not None:
            self._apply({"freq_b": v})

    def _poll_tx(self):
        r = self._query("TX")
        if r and len(r) >= 4:
            self._apply({"tx": r[2] != "0"})

    def _poll_meter(self):
        if not self.state.get("tx"):
            v = self._num("RM1", 3, 3)
            if v is not None:
                self._apply({"smeter": interp(self.STR_CAL, v)})
            return
        code, key, conv = [("5", "po", lambda r: interp(self.PO_CAL, r)),
                           ("4", "alc", lambda r: r / 255),
                           ("6", "swr", lambda r: interp(SWR_CAL, r)),
                           ("3", "comp", lambda r: interp(COMP_CAL, r))][self._meter_idx % 4]
        self._meter_idx += 1
        v = self._num("RM" + code, 3, 3)
        if v is not None:
            self._apply({key: conv(v)})

    def _poll_if(self):
        r = self._query("IF")
        p = parse_if(r, self.CODE_TO_MODE) if r else None
        if p:
            self._apply(p)

    def _poll_flag(self, cmd, key, pos):
        r = self._query(cmd)
        if r and len(r) > pos:
            self._apply({key: r[pos] not in "0"})

    def _poll_split(self):
        # ST; answers ST0/ST1 (ST2 = split +5 kHz); FT; answers FT0/FT1 = TX VFO.
        r = self._query(self.SPLIT)
        if r and len(r) >= 4:
            self._apply({"split": r[2] != "0"})

    def _poll_busy(self):
        r = self._query("BY")
        if r and len(r) >= 4:
            self._apply({"busy": r[2] == "1"})

    def _poll_level(self, cmd, key, start, n, scale):
        v = self._num(cmd, start, n)
        if v is not None:
            self._apply({key: round(v * 100 / scale)})

    def _poll_width(self):
        # Reply is SH0nn; / SH00nn; / SH01nn; depending on model: the index is
        # always the last two digits (Hamlib tells the formats apart by length).
        r = self._query("SH0")
        if not r or len(r) < 5 or not r[-3:-1].isdigit():
            return
        idx = int(r[-3:-1])
        self._apply({"width_idx": idx})
        self._update_filter_label()

    def _widths(self, mode: str):
        if mode in ("LSB", "USB"):
            return self.SSB_WIDTHS
        if mode in ("CW-U", "CW-L", "CW", "CW-R", "RTTY-L", "RTTY-U", "PSK"):
            return self.CW_WIDTHS
        return None          # AM/FM/C4FM widths are fixed

    def _update_filter_label(self):
        st = self.state
        table = self._widths(st.get("mode", ""))
        idx = st.get("width_idx", 0)
        if table is None:
            label = "FIXED"
        elif idx == 0 or idx >= len(table):
            label = "DEFAULT"
        else:
            label = fmt_width(table[idx])
        if st.get("narrow"):
            label = "NAR " + label
        self._apply({"filter": label})

    def _poll_ac(self):
        r = self._query("AC")
        if r and len(r) >= 6:
            self._apply({"atu": r[4] == "1", "tuning": r[4] == "3"})

    def _poll_agc(self):
        v = self._num("GT0", 3, 1)
        if v is not None:
            self._apply({"agc": AGC_READ.get(v, "?")})

    def _poll_att(self):
        v = self._num("RA0", 3, 1)
        if v is not None and v < len(self.ATT_OPTS):
            self._apply({"att": self.ATT_OPTS[v]})

    def _poll_pre(self):
        v = self._num("PA0", 3, 1)
        if v is not None and v < len(self.PRE_OPTS):
            self._apply({"preamp": self.PRE_OPTS[v]})

    def _poll_pc(self):
        v = self._num("PC", 2, 3)
        if v is not None:
            self._apply({"power": v})

    # ---- writes ------------------------------------------------------------

    FA_CLEARS_CLAR = True       # FT-710: an FA; write zeroes the clarifier offset (bench-found)

    def _clar(self, rit: bool, xit: bool):
        if self.CLAR == "CF":
            self._write(f"CF000{int(rit)}{int(xit)}000")
        elif self.CLAR == "RT":
            self._write(f"RT{int(rit)}")
            self._write(f"XT{int(xit)}")

    def _set_offset(self, hz: int):
        v = max(-9990, min(9990, int(hz)))
        if self.CLAR == "CF":
            self._write(f"CF001{'+' if v >= 0 else '-'}{abs(v):04d}")
        elif self.CLAR == "RT":
            # RU/RD are RELATIVE (FT-991A bench: RU0120 then RD0050 -> +0070),
            # so clear first to make the offset absolute (Hamlib newcat_set_rit does the same).
            self._write("RC")
            if v > 0:
                self._write(f"RU{v:04d}")
            elif v < 0:
                self._write(f"RD{-v:04d}")

    def apply_set(self, key, value):
        st = self.state
        pct = lambda v, full: max(0, min(full, round(v * full / 100)))
        if key == "freq":
            self._write(f"FA{int(value):09d}")
            # Bench-found 2026-09-27: on the FT-710 any FA; write zeroes the
            # clarifier offset. Put it back so tuning with RIT/XIT on keeps it.
            if self.CLAR and self.FA_CLEARS_CLAR and (st.get("rit") or st.get("xit")) and st.get("rit_hz"):
                self._set_offset(st["rit_hz"])
        elif key == "freq_b":
            self._write(f"FB{int(value):09d}")
        elif key in ("mode", "data"):
            mode = value if key == "mode" else st.get("mode", "USB")
            data = value if key == "data" else st.get("data", False)
            code = self.MODE_TO_CODE.get((mode, bool(data))) or self.MODE_TO_CODE.get((mode, False))
            if code:
                self._write(f"MD0{code}")
                self._apply({"data": bool(data) and (mode, True) in self.MODE_TO_CODE}, user=True)
                self._update_filter_label()
        elif key == "rit":
            self._clar(bool(value), bool(st.get("xit")))
        elif key == "xit":
            self._clar(bool(st.get("rit")), bool(value))
        elif key == "rit_hz":
            self._set_offset(value)
        elif key == "split":
            if self.SPLIT == "FT":
                self._write("FT3" if value else "FT2")
            else:
                self._write(f"ST{int(bool(value))}")
        elif key == "atu":
            self._write("AC001" if value else "AC000")
        elif key == "agc":
            self._write(f"GT0{AGC_SET.index(value)}")
        elif key == "att":
            self._write(f"RA0{self.ATT_OPTS.index(value)}")
        elif key == "preamp":
            self._write(f"PA0{self.PRE_OPTS.index(value)}")
        elif key == "nb1":
            self._write(f"NB0{int(bool(value))}")
        elif key == "nr":
            self._write(f"NR0{int(bool(value))}")
        elif key == "bc":
            self._write(f"BC0{int(bool(value))}")
        elif key == "notch":
            self._write(f"BP00{int(bool(value)):03d}")
        elif key == "proc":
            self._write(f"PR0{int(bool(value))}")
        elif key == "vox":
            self._write(f"VX{int(bool(value))}")
        elif key == "mon":
            self._write(f"ML0{int(bool(value)):03d}")
        elif key == "mon_level":
            self._write(f"ML1{pct(value, 100):03d}")
        elif key == "af":
            self._write(f"AG0{pct(value, 255):03d}")
        elif key == "rf":
            self._write(f"RG0{pct(value, 255):03d}")
        elif key == "sql":
            self._write(f"SQ0{pct(value, 100):03d}")
        elif key == "power":
            self._write(f"PC{max(5, min(self.MAX_W, int(value))):03d}")
        elif key == "ptt":
            self._write("TX1" if value else "TX0")
            self._apply({"tx": bool(value)}, user=True)

    def apply_action(self, name, value):
        st = self.state
        if name == "rit_clear":
            self._set_offset(0)
            self._apply({"rit_hz": 0}, user=True)
        elif name == "tune":
            self._write(self.TUNE_CMD)
        elif name == "filter_click":
            self._write(f"NA0{int(not st.get('narrow'))}")
            self._apply({"narrow": not st.get("narrow")}, user=True)
            self._update_filter_label()
        elif name == "filter_step":
            table = self._widths(st.get("mode", ""))
            if table:
                idx = max(1, min(len(table) - 1, st.get("width_idx", 0) + int(value)))
                self._write(self.SH_SET.format(idx))
                self._apply({"width_idx": idx}, user=True)
                self._update_filter_label()

    # ---- display helpers ---------------------------------------------------

    @staticmethod
    def mode_label(st: dict) -> str:
        mode = st.get("mode", "")
        return DATA_LABELS.get(mode, mode) if st.get("data") else mode


# ---- FT-991A -------------------------------------------------------------------

FT991_CODE_TO_MODE = {
    "1": ("LSB", False), "2": ("USB", False), "3": ("CW", False), "4": ("FM", False),
    "5": ("AM", False), "6": ("RTTY-L", False), "7": ("CW-R", False), "8": ("LSB", True),
    "9": ("RTTY-U", False), "A": ("FM", True), "B": ("FM-N", False), "C": ("USB", True),
    "D": ("AM-N", False), "E": ("C4FM", False),
}
# Hamlib ft991.c width tables (shared with the FT-891)
FT991_SSB_WIDTHS = [0, 200, 400, 600, 850, 1100, 1350, 1500, 1650, 1800, 1950, 2100, 2200, 2300,
                    2400, 2500, 2600, 2700, 2800, 2900, 3000, 3200]
FT991_CW_WIDTHS = [0, 50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 800, 1200, 1400, 1700,
                   2000, 2400, 3000]
FT991_PO_CAL = [(0, 0.0), (10, 0.8), (50, 8.0), (100, 26.0), (150, 54.0), (200, 92.0), (250, 140.0)]
FT891_PO_CAL = FT991_PO_CAL          # Hamlib ft891.h: identical table
# Hamlib ftdx101.h
FTDX101_STR_CAL = [(0, -60), (17, -54), (25, -48), (34, -42), (51, -36), (68, -30), (85, -24),
                   (102, -18), (119, -12), (136, -6), (160, 0), (255, 60)]
FTDX101D_PO_CAL = [(0, 0.0), (38, 5.0), (94, 25.0), (147, 50.0), (176, 75.0), (205, 100.0)]
FTDX101MP_PO_CAL = [(0, 0.0), (30, 5.0), (69, 20.0), (98, 40.0), (119, 60.0), (139, 80.0), (160, 100.0),
                    (173, 120.0), (185, 140.0), (198, 160.0), (210, 180.0), (225, 200.0), (255, 210.0)]


class YaesuFT991A(YaesuFT710):
    """FT-991A: same "new CAT" family and IF; layout as the FT-710; the
    differences below come from Hamlib (ft991.c / newcat.c) and the
    FT991A_Display_Control bench notes (RTS, RM answer, AC002).
    Bench 2026-09-27: no ST; command ('?'), split is "TX on VFO-B" (FT3; sets
    it, FT2; clears it, FT; reads back FT1/FT0); BY; reports squelch-open."""
    MODEL = "FT-991A"
    RADIO_ID = "0670"
    CODE_TO_MODE = FT991_CODE_TO_MODE
    MODES = ["LSB", "USB", "CW", "CW-R", "AM", "AM-N", "FM", "FM-N", "RTTY-L", "RTTY-U", "C4FM"]
    SSB_WIDTHS = FT991_SSB_WIDTHS
    CW_WIDTHS = FT991_CW_WIDTHS
    ATT_OPTS = ["OFF", "12dB"]
    PO_CAL = FT991_PO_CAL
    TUNE_CMD = "AC002"          # FT-991A tuner start (P3=2)
    SH_SET = "SH0{:02d}"
    FA_CLEARS_CLAR = True       # bench 2026-09-27: same as the FT-710 (FA; write zeroed the offset)
    SPLIT = "FT"
    CLAR = "RT"
    HAS_BUSY = True
    CAT_RATE_MENU = "menu 031 CAT RATE"


class YaesuFT991(YaesuFT991A):
    """FT-991 (non-A): Hamlib drives it with the same ft991.c as the 991A."""
    MODEL = "FT-991"
    RADIO_ID = "0570"


class YaesuFT891(YaesuFT991A):
    """FT-891 (Hamlib ft891.c): FT-991 width tables and mode digits, HF/6m
    only (no C4FM), one preamp step, split is ST; (not FT;), and there is
    no CAT RIT/XIT switch (newcat has no RT/XT/CF for it), so no clarifier
    controls. Width writes carry an "on" digit: SH01nn;."""
    MODEL = "FT-891"
    RADIO_ID = "0135"
    CODE_TO_MODE = {k: v for k, v in FT991_CODE_TO_MODE.items() if k != "E"}
    MODES = ["LSB", "USB", "CW", "CW-R", "AM", "FM", "FM-N", "RTTY-L", "RTTY-U"]
    PO_CAL = FT891_PO_CAL
    PRE_OPTS = ["IPO", "AMP"]
    SH_SET = "SH01{:02d}"
    SPLIT = "ST"
    CLAR = None
    FA_CLEARS_CLAR = False
    CAT_RATE_MENU = "CAT RATE menu"


class YaesuFTDX10(YaesuFT710):
    """FTDX10 (Hamlib ftdx10.c): the FT-710 is derived from it, and newcat
    treats the two alike (CF clarifier, ST split, SH00nn widths, same width
    tables and PSK mode digit). Only the ID and tuner-start code differ."""
    MODEL = "FTDX10"
    RADIO_ID = "0761"
    TUNE_CMD = "AC002"
    CAT_RATE_MENU = "CAT RATE menu"


class YaesuFTDX101D(YaesuFT710):
    """FTDX101D (Hamlib ftdx101.c): FT-710 width tables, mode digits and
    SH00nn widths, ST split; clarifier is RT/XT + RC/RU/RD like the FT-991A.
    Only MAIN is controlled."""
    MODEL = "FTDX101D"
    RADIO_ID = "0681"
    TUNE_CMD = "AC002"
    CLAR = "RT"
    STR_CAL = FTDX101_STR_CAL
    PO_CAL = FTDX101D_PO_CAL
    CAT_RATE_MENU = "CAT RATE menu"


class YaesuFTDX101MP(YaesuFTDX101D):
    """FTDX101MP: the 200 W version of the FTDX101D."""
    MODEL = "FTDX101MP"
    RADIO_ID = "0682"
    MAX_W = 200
    PO_CAL = FTDX101MP_PO_CAL


# Model name -> driver class, in the order the Setup dialog lists them.
MODELS = {c.MODEL: c for c in (YaesuFT710, YaesuFTDX10, YaesuFTDX101D, YaesuFTDX101MP,
                               YaesuFT991A, YaesuFT991, YaesuFT891)}
