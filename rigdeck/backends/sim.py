"""A pretend radio: every control, no hardware. Used for demo mode and for
developing the UI without keying anything real."""
from __future__ import annotations

import math
import random
import time

from .base import Backend, Control, LINK_LIVE

MODES = ["LSB", "USB", "CW", "CW-R", "AM", "FM", "RTTY", "RTTY-R"]
WIDTHS = [1800, 2100, 2400, 2700, 3000, 3600]


class Simulator(Backend):
    KIND = "sim"

    def __init__(self, cfg: dict):
        cfg.setdefault("model", "SIMULATOR")
        super().__init__(cfg)
        self.modes = MODES
        self.meters = ["PO", "ALC", "SWR", "COMP"]
        C = Control
        self.controls = {c.key: c for c in [
            C("mode", "MODE", "choice", MODES), C("data", "DATA", "toggle"),
            C("filter", "FILTER", "choice", ["FIL1", "FIL2", "FIL3"]),
            C("rit", "RIT", "toggle"), C("xit", "XIT", "toggle"),
            C("rit_hz", "RIT", "level", lo=-9999, hi=9999, step=10, unit="Hz"),
            C("split", "SPLIT", "toggle"), C("atu", "ATU", "toggle"),
            C("ant", "ANT", "choice", ["ANT1", "ANT2"]), C("rx_ant", "RX ANT", "toggle"),
            C("agc", "AGC", "choice", ["FAST", "MID", "SLOW"]),
            C("att", "ATT", "choice", ["OFF", "6dB", "12dB", "18dB"]),
            C("preamp", "P.AMP", "choice", ["OFF", "P.AMP1", "P.AMP2"]),
            C("psel", "DIGI-SEL", "toggle"), C("tbw", "TBW", "choice", ["WIDE", "MID", "NAR"]),
            C("vox", "VOX", "toggle"), C("proc", "PROC", "toggle"),
            C("mon", "MON", "toggle"), C("mon_level", "MON LVL", "level"),
            C("af", "AF", "level"), C("rf", "RF GAIN", "level"), C("sql", "SQL", "level"),
            C("nr", "NR", "toggle"), C("nb1", "NB1", "toggle"), C("nb2", "NB2", "toggle"),
            C("notch", "NOTCH", "toggle"), C("bc", "BC", "toggle"),
            C("power", "PWR", "level", lo=5, hi=100, unit="W"),
            C("ptt", "PTT", "toggle"),
        ]}
        f = int(cfg.get("freq", 7140000))
        self.state.update(
            freq=f, freq_b=f + 1000, mode="LSB", data=False, filter="FIL2 2.4k", fil=2, width_idx=2,
            rit=False, xit=False, rit_hz=0, split=False, atu=False, ant="ANT1", rx_ant=False,
            agc="MID", att="OFF", preamp="OFF", psel=False, tbw="MID", vox=False, proc=False,
            mon=False, mon_level=40, af=35, rf=100, sql=0, nr=False, nb1=False, nb2=False,
            notch=False, bc=False, power=50, tx=False, smeter=-30.0, busy=False,
            po=0.0, swr=1.0, alc=0.0, comp=0.0)
        self.info = {"Port": "none (simulated)"}
        self._t0 = time.monotonic()
        self._seed = random.random() * 10
        self._scope_seq = 0
        self._last_sweep = 0.0
        if cfg.get("scope"):
            self.controls["scope_span"] = C("scope_span", "SPAN", "choice",
                                            ["5k", "10k", "20k", "50k", "100k", "200k", "500k", "1M"])
            self.controls["scope_mode"] = C("scope_mode", "CTR/FIX", "choice", ["CTR", "FIX"])
            self.controls["scope_ref"] = C("scope_ref", "REF", "level", lo=-20, hi=20, step=0.5, unit="dB")
            self.state.update(scope_span="100k", scope_mode="CTR", scope_ref=0.0)
            # a few pretend stations: (offset from start freq Hz, strength 0-160, bandwidth Hz)
            self._stations = [(-38000, 95, 2400), (-21500, 70, 2600), (-6000, 120, 2400), (4200, 55, 500),
                              (9000, 60, 150), (17500, 105, 2700), (31000, 80, 2400), (44000, 65, 6000)]
            self._base = f

    def open(self):
        self._set_link(LINK_LIVE)

    def fast_polls(self):
        return [self._tick, self._sweep] if "scope_span" in self.controls else [self._tick]

    def _sweep(self):
        now = time.monotonic()
        if now - self._last_sweep < 0.15:
            return
        self._last_sweep = now
        st = self.state
        span = {"5k": 5000, "10k": 10000, "20k": 20000, "50k": 50000, "100k": 100000, "200k": 200000,
                "500k": 500000, "1M": 1000000}[st.get("scope_span", "100k")]
        centre = st["freq"] if st.get("scope_mode") == "CTR" else (st["freq"] // span) * span + span // 2
        lo, hi = centre - span // 2, centre + span // 2
        t = now - self._t0
        gain = 6 * st.get("scope_ref", 0.0) / 10
        data = bytearray()
        for i in range(475):
            hz = lo + span * i / 474
            v = 18 + random.uniform(0, 14) + gain
            for off, amp, bw in self._stations:
                d = abs(hz - (self._base + off))
                if d < bw / 2:
                    fade = 0.75 + 0.25 * math.sin(t * 0.7 + off)
                    v = max(v, amp * fade * (1 - (d / (bw / 2)) ** 4) + random.uniform(-6, 6) + gain)
            data.append(max(0, min(160, int(v))))
        self._scope_seq += 1
        self._apply({"scope": {"lo": lo, "hi": hi, "mode": 0, "oor": False, "data": bytes(data),
                               "seq": self._scope_seq}})

    def _tick(self):
        time.sleep(0.05)
        t = time.monotonic() - self._t0 + self._seed
        st = self.state
        if st.get("tx"):
            pwr = st.get("power", 50)
            voice = 0.55 + 0.45 * abs(math.sin(t * 3.1) * math.sin(t * 1.3))
            self._apply({"po": pwr * voice, "alc": 0.25 + 0.5 * voice * (1 if st.get("proc") else 0.6),
                         "swr": 1.2 + 0.05 * math.sin(t), "comp": 8 * voice if st.get("proc") else 0.0})
            return
        # QSB-ish signal: slow fade plus syllabic speech envelope, some noise
        fade = 0.5 + 0.5 * math.sin(t * 0.21)
        speech = abs(math.sin(t * 2.3) * math.sin(t * 0.9))
        db = -48 + 55 * fade * (0.4 + 0.6 * speech) + random.uniform(-3, 3)
        if st.get("att") != "OFF":
            db -= int(st["att"].rstrip("dB"))
        if st.get("preamp") != "OFF":
            db += 10
        self._apply({"smeter": max(-54.0, min(60.0, db)), "busy": db > -30, "po": 0.0, "alc": 0.0})

    def apply_set(self, key, value):
        if key == "ptt":
            self._apply({"tx": bool(value)}, user=True)

    def apply_action(self, name, value):
        st = self.state
        if name == "rit_clear":
            self._apply({"rit_hz": 0}, user=True)
        elif name == "filter_click":
            fil = st.get("fil", 2) % 3 + 1
            self._apply({"fil": fil, "width_idx": [4, 2, 0][fil - 1]}, user=True)
        elif name == "filter_step":
            self._apply({"width_idx": max(0, min(len(WIDTHS) - 1, st.get("width_idx", 2) + int(value)))},
                        user=True)
        elif name == "tune":
            self._apply({"atu": True}, user=True)
        idx = st.get("width_idx", 2)
        self._apply({"filter": f"FIL{st.get('fil', 2)} {WIDTHS[idx] / 1000:.1f}k"}, user=True)

    @staticmethod
    def mode_label(st: dict) -> str:
        mode = st.get("mode", "")
        return f"{mode}-D" if st.get("data") else mode
