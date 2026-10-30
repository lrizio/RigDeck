"""Any Hamlib-supported radio, via rigctld (Hamlib 4.x network daemon).

Either connects to a rigctld that is already running (host:port), or
starts its own hidden rigctld for the configured Hamlib model number,
serial port and baud rate, and kills it again on disconnect.

The control set is built at connect time from rigctld's \\dump_caps, so
a radio only gets the buttons Hamlib says it can actually do.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time

from .base import Backend, Control, RadioTimeout

QUERY_TIMEOUT_S = 1.5
RPRT_RE = re.compile(rb"RPRT -?\d+\r?\n")

FUNC_MAP = {"NB": "nb1", "NB2": "nb2", "NR": "nr", "ANF": "bc", "MN": "notch", "COMP": "proc",
            "VOX": "vox", "MON": "mon", "TUNER": "atu", "RIT": "rit", "XIT": "xit"}
LEVEL_MAP = {"AF": "af", "RF": "rf", "SQL": "sql", "MONITOR_GAIN": "mon_level"}
FUNC_LABELS = {"nb1": "NB", "nb2": "NB2", "nr": "NR", "bc": "ANF", "notch": "NOTCH", "proc": "PROC",
               "vox": "VOX", "mon": "MON", "atu": "ATU", "rit": "RIT", "xit": "XIT"}
AGC_NAMES = {0: "OFF", 1: "SUPERFAST", 2: "FAST", 3: "SLOW", 4: "USER", 5: "MID", 6: "AUTO"}
DISPLAY_MODE = {"CWR": "CW-R", "RTTYR": "RTTY-R", "PKTUSB": "USB", "PKTLSB": "LSB", "PKTFM": "FM",
                "PKTAM": "AM", "PKTFMN": "FMN"}


def find_rigctld() -> str | None:
    found = shutil.which("rigctld")
    if found:
        return found
    for root in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        if not root or not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root), reverse=True):
            if name.lower().startswith("hamlib"):
                exe = os.path.join(root, name, "bin", "rigctld.exe")
                if os.path.exists(exe):
                    return exe
    return None


def list_hamlib_models() -> list[tuple[int, str]]:
    """[(model number, 'Mfg Model')] from `rigctl -l`."""
    rigctld = find_rigctld()
    if not rigctld:
        return []
    rigctl = os.path.join(os.path.dirname(rigctld), "rigctl.exe")
    try:
        out = subprocess.run([rigctl, "-l"], capture_output=True, text=True, timeout=10,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return []
    models = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit() and len(line) > 55:
            models.append((int(parts[0]), f"{line[8:31].strip()} {line[31:55].strip()}"))
    return models


def parse_ext(reply: str) -> tuple[int, list[str]]:
    """Extended-protocol reply -> (RPRT code, value strings)."""
    code, values = -99, []
    lines = [l for l in reply.splitlines() if l.strip()]
    for line in lines[1:]:
        if line.startswith("RPRT"):
            code = int(line.split()[1])
            break
        values.append(line.split(": ", 1)[1] if ": " in line else line.strip())
    if lines and lines[0].startswith("RPRT"):
        code = int(lines[0].split()[1])
    return code, values


class Hamlib(Backend):
    KIND = "hamlib"

    def __init__(self, cfg: dict):
        cfg.setdefault("model", f"Hamlib #{cfg.get('hamlib_model', '?')}")
        super().__init__(cfg)
        self.host = cfg.get("host") or "127.0.0.1"
        self.tcp_port = int(cfg.get("tcp_port") or 4532)
        self.spawn = bool(cfg.get("hamlib_model")) and bool(cfg.get("port"))
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self._rx = b""
        self._funcs: set[str] = set()
        self._levels: set[str] = set()
        self._meter_idx = 0
        self._passband = 0

    # ---- connection ------------------------------------------------------

    def open(self):
        if self.spawn:
            exe = find_rigctld()
            if not exe:
                raise RuntimeError("rigctld.exe not found - install Hamlib")
            args = [exe, "-m", str(self.cfg["hamlib_model"]), "-r", self.cfg["port"],
                    "-t", str(self.tcp_port)]
            if self.cfg.get("baud"):
                args += ["-s", str(self.cfg["baud"])]
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._log("SYS", " ".join(args))
        deadline = time.monotonic() + 8
        while True:
            try:
                self.sock = socket.create_connection((self.host, self.tcp_port), timeout=1)
                break
            except OSError:
                if self.proc and self.proc.poll() is not None:
                    raise RuntimeError(f"rigctld exited (code {self.proc.returncode}) - port busy or wrong model?")
                if time.monotonic() > deadline:
                    raise RuntimeError(f"cannot reach rigctld at {self.host}:{self.tcp_port}")
                time.sleep(0.3)
        self.sock.settimeout(0.05)
        self._rx = b""
        self._load_caps()

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def safe_shutdown(self):
        if self.sock and self.state.get("tx"):
            self._q("T 0")

    def _q(self, cmd: str, raw: bool = False) -> list[str] | None:
        """Run one command; values on success, None on a Hamlib error.
        raw=True returns the reply lines untouched (for dump_caps)."""
        self.sock.sendall(("+" + cmd + "\n").encode())
        self.stats["tx"] += 1
        self._log("TX", cmd)
        deadline = time.monotonic() + QUERY_TIMEOUT_S
        while not RPRT_RE.search(self._rx):
            if time.monotonic() > deadline:
                self._rx = b""
                raise RadioTimeout(cmd)
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("rigctld closed the connection")
                self._rx += chunk
            except socket.timeout:
                pass
        end = RPRT_RE.search(self._rx).end()
        reply, self._rx = self._rx[:end].decode(errors="replace"), self._rx[end:]
        self.stats["rx"] += 1
        self._log("RX", reply.strip().replace("\n", " | "))
        code, values = parse_ext(reply)
        if code == -5:                     # RIG_ETIMEOUT: rigctld up, radio silent
            raise RadioTimeout(cmd)
        self._got_reply()
        if code != 0:
            return None
        return reply.splitlines()[1:-1] if raw else values

    def _load_caps(self):
        text = "\n".join(self._q("\\dump_caps", raw=True) or [])
        get_funcs = set(self._cap_line(text, "Get functions").split())
        set_funcs = set(self._cap_line(text, "Set functions").split())
        get_levels = {t.split("(")[0] for t in self._cap_line(text, "Get level").split()}
        set_levels = {t.split("(")[0] for t in self._cap_line(text, "Set level").split()}
        self._funcs = get_funcs & set_funcs
        self._levels = get_levels
        name = f"{self._cap_line(text, 'Mfg name')} {self._cap_line(text, 'Model name')}".strip()
        if name:
            self.model = name
        self.info = {"rigctld": f"{self.host}:{self.tcp_port}", "Hamlib model": name}
        if self.spawn:
            self.info["Port"] = f"{self.cfg['port']} @ {self.cfg.get('baud', 'default')}"

        C = Control
        ctrls = [C("ptt", "PTT", "toggle"), C("split", "SPLIT", "toggle")]
        modes = [m for m in self._cap_line(text, "Mode list").split() if m]
        base_modes = []
        for m in modes:
            b = DISPLAY_MODE.get(m, m) if m.startswith("PKT") else m
            if not m.startswith("PKT") and b not in base_modes:
                base_modes.append(b)
        self.modes = [DISPLAY_MODE.get(m, m) for m in base_modes]
        self._raw_modes = set(modes)
        ctrls.append(C("mode", "MODE", "choice", self.modes))
        if any(m.startswith("PKT") for m in modes):
            ctrls.append(C("data", "DATA", "toggle"))
        ctrls.append(C("filter", "WIDTH", "choice", tip="Arrows / wheel: passband width"))
        for f, key in FUNC_MAP.items():
            if f in self._funcs:
                ctrls.append(C(key, FUNC_LABELS[key], "toggle"))
        if "rit" in [c.key for c in ctrls] or "xit" in [c.key for c in ctrls]:
            ctrls.append(C("rit_hz", "RIT", "level", lo=-9999, hi=9999, step=10, unit="Hz"))
        for lvl, key in LEVEL_MAP.items():
            if lvl in get_levels and lvl in set_levels:
                ctrls.append(C(key, key.upper(), "level"))
        if "RFPOWER" in get_levels and "RFPOWER" in set_levels:
            ctrls.append(C("power", "PWR", "level", lo=0, hi=100, unit="%"))
        pre = self._cap_line(text, "Preamp").replace("dB", "").split()
        if "PREAMP" in set_levels and pre and pre[0].isdigit():
            ctrls.append(C("preamp", "PRE", "choice", ["OFF"] + [f"{p}dB" for p in pre]))
        att = self._cap_line(text, "Attenuator").replace("dB", "").split()
        if "ATT" in set_levels and att and att[0].isdigit():
            ctrls.append(C("att", "ATT", "choice", ["OFF"] + [f"{a}dB" for a in att]))
        agc = self._cap_line(text, "AGC levels")
        if "AGC" in set_levels and agc:
            self._agc_codes = {}
            for tok in agc.split():
                if "=" in tok:
                    n, name_ = tok.split("=", 1)
                    self._agc_codes[AGC_NAMES.get(int(n), name_)] = int(n)
            if self._agc_codes:
                ctrls.append(C("agc", "AGC", "choice", list(self._agc_codes)))
        self.controls = {c.key: c for c in ctrls}
        self.meters = [m for m, lv in (("PO", "RFPOWER_METER_WATTS"), ("ALC", "ALC"), ("SWR", "SWR"),
                                       ("COMP", "COMP_METER")) if lv in get_levels]
        if "PO" not in self.meters and "RFPOWER_METER" in get_levels:
            self.meters.insert(0, "PO")
        self.version += 1

    @staticmethod
    def _cap_line(text: str, key: str) -> str:
        for line in text.splitlines():
            if line.startswith(key + ":"):
                return line.split(":", 1)[1].strip()
        return ""

    # ---- polls -----------------------------------------------------------

    def _one(self, cmd):
        v = self._q(cmd)
        return v[0] if v else None

    def fast_polls(self):
        return [self._poll_freq, self._poll_meter, self._poll_ptt]

    def slow_polls(self):
        polls = [self._poll_mode, self._poll_split, self._poll_dcd]
        for f, key in FUNC_MAP.items():
            if f in self._funcs:
                polls.append(lambda f=f, key=key: self._poll_func(f, key))
        for lvl, key in LEVEL_MAP.items():
            if key in self.controls:
                polls.append(lambda lvl=lvl, key=key: self._poll_level(lvl, key))
        if "rit_hz" in self.controls:
            polls.append(self._poll_rit)
        for key, lvl in (("preamp", "PREAMP"), ("att", "ATT")):
            if key in self.controls:
                polls.append(lambda key=key, lvl=lvl: self._poll_db(lvl, key))
        if "agc" in self.controls:
            polls.append(self._poll_agc)
        if "power" in self.controls:
            polls.append(self._poll_power)
        return polls

    def _poll_freq(self):
        v = self._one("f")
        if v:
            self._apply({"freq": int(float(v))})

    def _poll_ptt(self):
        v = self._one("t")
        if v is not None:
            self._apply({"tx": v.strip() != "0"})

    def _poll_meter(self):
        if not self.state.get("tx"):
            v = self._one("l STRENGTH")
            if v is not None:
                self._apply({"smeter": float(v)})
            return
        if not self.meters:
            return
        m = self.meters[self._meter_idx % len(self.meters)]
        self._meter_idx += 1
        if m == "PO":
            lv = "RFPOWER_METER_WATTS" if "RFPOWER_METER_WATTS" in self._levels else "RFPOWER_METER"
            v = self._one(f"l {lv}")
            if v is not None:
                self._apply({"po": float(v) * (1 if lv.endswith("WATTS") else 100)})
        else:
            lv = {"ALC": "ALC", "SWR": "SWR", "COMP": "COMP_METER"}[m]
            v = self._one(f"l {lv}")
            if v is not None:
                self._apply({m.lower(): float(v)})

    def _poll_mode(self):
        v = self._q("m")
        if v and len(v) >= 2:
            raw = v[0]
            self._passband = int(float(v[1]))
            self._apply({"mode": DISPLAY_MODE.get(raw, raw), "data": raw.startswith("PKT"),
                         "filter": f"{self._passband / 1000:.1f}k" if self._passband >= 1000
                         else str(self._passband)})

    def _poll_split(self):
        v = self._q("s")
        if v:
            self._apply({"split": v[0].strip() == "1"})
            if v[0].strip() == "1":
                fb = self._one("i")
                if fb:
                    self._apply({"freq_b": int(float(fb))})

    def _poll_dcd(self):
        v = self._one("\\get_dcd")
        self._apply({"busy": (v.strip() == "1") if v is not None else None})

    def _poll_func(self, f, key):
        v = self._one(f"u {f}")
        if v is not None:
            self._apply({key: v.strip() == "1"})

    def _poll_level(self, lvl, key):
        v = self._one(f"l {lvl}")
        if v is not None:
            self._apply({key: round(float(v) * 100)})

    def _poll_rit(self):
        v = self._one("j")
        if v is not None:
            self._apply({"rit_hz": int(float(v))})

    def _poll_db(self, lvl, key):
        v = self._one(f"l {lvl}")
        if v is not None:
            n = int(float(v))
            self._apply({key: "OFF" if n == 0 else f"{n}dB"})

    def _poll_agc(self):
        v = self._one("l AGC")
        if v is not None:
            n = int(float(v))
            names = {c: n_ for n_, c in self._agc_codes.items()}
            self._apply({"agc": names.get(n, str(n))})

    def _poll_power(self):
        v = self._one("l RFPOWER")
        if v is not None:
            self._apply({"power": round(float(v) * 100)})

    # ---- writes -------------------------------------------------------------

    def _raw_mode(self, base: str, data: bool) -> str:
        rev = {v: k for k, v in DISPLAY_MODE.items() if not k.startswith("PKT")}
        raw = rev.get(base, base)
        if data and ("PKT" + raw) in self._raw_modes:
            return "PKT" + raw
        return raw

    def apply_set(self, key, value):
        st = self.state
        rev_func = {v: k for k, v in FUNC_MAP.items()}
        rev_lvl = {v: k for k, v in LEVEL_MAP.items()}
        if key == "freq":
            self._q(f"F {int(value)}")
        elif key == "freq_b":
            self._q(f"I {int(value)}")
        elif key in ("mode", "data"):
            base = value if key == "mode" else st.get("mode", "USB")
            data = value if key == "data" else st.get("data", False)
            self._q(f"M {self._raw_mode(base, bool(data))} -1")
        elif key == "split":
            self._q(f"S {int(bool(value))} VFOB")
        elif key == "ptt":
            self._q(f"T {int(bool(value))}")
            self._apply({"tx": bool(value)}, user=True)
        elif key == "rit_hz":
            if st.get("xit") and not st.get("rit"):
                self._q(f"Z {int(value)}")
            else:
                self._q(f"J {int(value)}")
        elif key in rev_func:
            self._q(f"U {rev_func[key]} {int(bool(value))}")
        elif key in rev_lvl:
            self._q(f"L {rev_lvl[key]} {max(0, min(100, value)) / 100:.3f}")
        elif key == "power":
            self._q(f"L RFPOWER {max(0, min(100, value)) / 100:.3f}")
        elif key in ("preamp", "att"):
            n = 0 if value == "OFF" else int(value.rstrip("dB"))
            self._q(f"L {'PREAMP' if key == 'preamp' else 'ATT'} {n}")
        elif key == "agc":
            self._q(f"L AGC {self._agc_codes[value]}")

    def apply_action(self, name, value):
        if name == "rit_clear":
            self._q("J 0")
            if "xit" in self.controls:
                self._q("Z 0")
            self._apply({"rit_hz": 0}, user=True)
        elif name == "tune":
            self._q("G TUNE")
        elif name == "filter_step":
            pb = max(100, self._passband + 100 * int(value))
            raw = self._raw_mode(self.state.get("mode", "USB"), bool(self.state.get("data")))
            self._q(f"M {raw} {pb}")
            self._poll_mode()

    @staticmethod
    def mode_label(st: dict) -> str:
        mode = st.get("mode", "")
        return f"{mode}-D" if st.get("data") else mode
