"""Share a Rig Deck radio with other programs over rigctld's network protocol.

Rig Deck owns the radio's COM port; this server lets JTDX, WSJT-X, fldigi,
loggers, ICOMNodeDisplay's bridge etc. reach the same radio by choosing
"Hamlib NET rigctl" (rig model 2) at 127.0.0.1:4532 (MAIN) / :4533 (SUB).
No driver or virtual COM port is needed, and the other program never has
to know which radio is behind the port.

Only the commands those programs actually use are implemented (frequency,
mode, PTT, VFO/split, RIT/XIT, levels, meters, functions); anything else
answers RPRT -11 (not available), as rigctld itself does. The \\dump_state
reply follows the layout of a real rigctld 4.7.1 (captured 2026-09-30),
which Hamlib's netrigctl client parses line by line.

Every read is served from the backend's state snapshot and every write goes
through the backend's normal set() queue, so a sharing program is just
another source of button presses: it never touches the serial port itself.
"""
from __future__ import annotations

import re
import socket
import socketserver
import threading
from typing import Callable

from .backends.base import LINK_LIVE

DEFAULT_PORTS = {"main": 4532, "sub": 4533}

# Hamlib error codes (negated in "RPRT -n")
EINVAL, EIO, ENAVAIL = 1, 6, 11

# ---- mode names ----------------------------------------------------------------
# Rig Deck (mode, data) -> Hamlib mode name. Icom and Yaesu spell some modes
# differently (CW-R vs CW-L, RTTY-R vs RTTY-U) but they mean the same thing.
_NAME_OF = {"CW-U": "CW", "CW-L": "CWR", "CW-R": "CWR", "RTTY-L": "RTTY", "RTTY-U": "RTTYR",
            "RTTY-R": "RTTYR", "PSK-R": "PSKR", "AM-N": "AMN", "FM-N": "FMN", "DV": "DSTAR"}
_DATA_NAME = {"USB": "PKTUSB", "LSB": "PKTLSB", "FM": "PKTFM", "FM-N": "PKTFMN", "AM": "PKTAM"}
# Hamlib name -> Rig Deck candidates, first one the radio has wins
_FROM_NAME = {"CW": ["CW-U", "CW"], "CWR": ["CW-L", "CW-R"], "RTTY": ["RTTY-L", "RTTY"],
              "RTTYR": ["RTTY-U", "RTTY-R"], "PSKR": ["PSK-R"], "AMN": ["AM-N"], "FMN": ["FM-N"],
              "DSTAR": ["DV"]}
_FROM_DATA = {v: k for k, v in _DATA_NAME.items()}

_DEFAULT_PB = {"USB": 2400, "LSB": 2400, "PKTUSB": 2400, "PKTLSB": 2400, "CW": 500, "CWR": 500,
               "RTTY": 500, "RTTYR": 500, "PSK": 500, "PSKR": 500, "AM": 6000, "AMN": 3000,
               "PKTAM": 6000, "FM": 15000, "FMN": 9000, "PKTFM": 15000, "PKTFMN": 9000,
               "WFM": 230000, "DSTAR": 6000, "C4FM": 12000}

# Hamlib enum agc_level_e
_AGC_CODE = {"OFF": 0, "FAST": 2, "SLOW": 3, "MID": 5, "AUTO": 6}

# rigctld function name -> Rig Deck control key
_FUNCS = {"NB": "nb1", "NR": "nr", "MN": "notch", "ANF": "bc", "COMP": "proc", "VOX": "vox",
          "MON": "mon", "RIT": "rit", "XIT": "xit", "TUNER": "atu"}
# settable levels: rigctld name -> (Rig Deck key, full scale of Rig Deck's 0..N value)
_PCT_LEVELS = {"AF": "af", "RF": "rf", "SQL": "sql", "MONITOR_GAIN": "mon_level"}

_VFO_WORDS = {"VFOA", "VFOB", "VFOC", "CURRVFO", "VFO", "MAIN", "SUB", "TX", "RX", "MEM",
              "MAINA", "MAINB", "SUBA", "SUBB", "NONE"}

# long command names (and their backslash forms) -> short letters
_LONG = {"set_freq": "F", "get_freq": "f", "set_mode": "M", "get_mode": "m", "set_vfo": "V",
         "get_vfo": "v", "set_ptt": "T", "get_ptt": "t", "set_split_vfo": "S", "get_split_vfo": "s",
         "set_split_freq": "I", "get_split_freq": "i", "set_split_mode": "X", "get_split_mode": "x",
         "set_level": "L", "get_level": "l", "set_func": "U", "get_func": "u", "set_rit": "J",
         "get_rit": "j", "set_xit": "Z", "get_xit": "z", "get_info": "_", "quit": "q"}
# extended-response ("+" prefix) labels for each getter's lines
_LABELS = {"f": ["Frequency"], "m": ["Mode", "Passband"], "v": ["VFO"], "t": ["PTT"],
           "s": ["Split", "TX VFO"], "i": ["TX Frequency"], "x": ["TX Mode", "TX Passband"],
           "j": ["RIT"], "z": ["XIT"], "_": ["Info"], "\\get_powerstat": ["Power Status"],
           "\\get_dcd": ["DCD"], "\\chk_vfo": ["ChkVFO"]}
_NAMES = {v: k for k, v in _LONG.items()}


class RigError(Exception):
    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


def mode_name(mode: str, data: bool) -> str:
    if data and mode in _DATA_NAME:
        return _DATA_NAME[mode]
    return _NAME_OF.get(mode, mode)


def mode_from_name(name: str, modes: list[str], has_data: bool) -> tuple[str, bool] | None:
    name = name.upper()
    if name in _FROM_DATA:
        base = _FROM_DATA[name]
        return (base, True) if base in modes and has_data else None
    for cand in _FROM_NAME.get(name, [name]):
        if cand in modes:
            return cand, False
    return None


def passband_hz(label: str, name: str) -> int:
    """Rough RX width from the backend's filter label ('FIL1 2.4k', 'NAR 500', '2.4k')."""
    m = re.findall(r"(\d+(?:\.\d+)?)(k?)", label or "")
    if m:
        num, k = m[-1]
        hz = float(num) * (1000 if k else 1)
        if hz >= 50:
            return int(hz)
    return _DEFAULT_PB.get(name, 2400)


def _db(label) -> int:
    m = re.search(r"\d+", str(label or ""))
    return int(m.group()) if m and label != "OFF" else 0


class Responder:
    """Turns one rigctld command line into reply text for one slot's backend.
    Kept free of sockets so it can be unit-tested directly."""

    def __init__(self, backend: Callable, ptt: Callable[[bool], bool] | None = None):
        self.backend = backend          # () -> current Backend for this slot (or None)
        self.ptt = ptt                  # (on) -> bool: key/unkey via the deck's TX rules

    # ---- helpers -----------------------------------------------------------

    def _b(self, live=True):
        b = self.backend()
        if b is None:
            raise RigError(EIO)
        if live and b.link != LINK_LIVE:
            raise RigError(EIO)
        return b

    def _st(self):
        return self._b().snapshot()

    def _need(self, b, key):
        if not b.supports(key):
            raise RigError(ENAVAIL)

    def _mode(self):
        st = self._st()
        name = mode_name(st.get("mode", "USB"), bool(st.get("data")))
        return name, passband_hz(st.get("filter", ""), name)

    def _maxw(self, b):
        c = b.controls.get("power")
        return c.hi if c else 100

    # ---- dispatch ----------------------------------------------------------

    def handle(self, line: str) -> str | None:
        """Reply text for one command line (None = close the connection)."""
        line = line.strip()
        if not line:
            return ""
        ext = line[0] in "+;|,"
        if ext:
            line = line[1:].lstrip()
        parts = line.split()
        cmd, args = parts[0], parts[1:]
        if cmd.startswith("\\") and cmd[1:] in _LONG:
            cmd = _LONG[cmd[1:]]
        elif cmd in _LONG:
            cmd = _LONG[cmd]
        if cmd in ("q", "Q"):
            return None
        if args and args[0].upper() in _VFO_WORDS and cmd not in ("V", "S"):
            args = args[1:]                    # "f VFOA" style (clients in VFO mode)
        try:
            values = self._run(cmd, args)
        except RigError as e:
            return self._fmt(cmd, args, None, ext, -e.code)
        except (ValueError, IndexError):
            return self._fmt(cmd, args, None, ext, -EINVAL)
        return self._fmt(cmd, args, values, ext, 0)

    def _fmt(self, cmd, args, values, ext, rc):
        if not ext:
            if values is None or rc:
                return f"RPRT {rc}\n"
            return "".join(f"{v}\n" for v in values)
        name = _NAMES.get(cmd, cmd.lstrip("\\"))
        out = f"{name}:" + ("".join(f" {a}" for a in args) if values is None else "") + "\n"
        if values:
            labels = _LABELS.get(cmd, [args[0] if args else "Value"])
            out += "".join(f"{labels[i] if i < len(labels) else 'Value'}: {v}\n" for i, v in enumerate(values))
        return out + f"RPRT {rc}\n"

    def _run(self, cmd, args):
        """Returns reply values for a getter, None for a setter that worked."""
        if cmd == "\\chk_vfo":
            return ["0"]
        if cmd == "\\dump_state":
            return [dump_state(self.backend())]
        if cmd == "\\get_powerstat":
            b = self.backend()
            return ["1" if b and b.link == LINK_LIVE else "0"]
        if cmd == "\\set_powerstat":
            return None                         # Rig Deck never switches a radio on/off
        if cmd == "_":
            b = self.backend()
            return [f"Rig Deck: {b.model if b else 'no radio'}"]

        if cmd == "f":
            return [str(int(self._st().get("freq") or 0))]
        if cmd == "F":
            self._b().set("freq", int(float(args[0])))
            return None
        if cmd == "m":
            name, pb = self._mode()
            return [name, str(pb)]
        if cmd == "M":
            return self._set_mode(args[0])
        if cmd == "v":
            self._b()
            return ["VFOA"]
        if cmd == "V":
            self._b()
            return None                         # always VFO A; split handles the TX VFO
        if cmd == "t":
            return ["1" if self._st().get("tx") else "0"]
        if cmd == "T":
            return self._set_ptt(args[0] not in ("0",))
        if cmd == "s":
            split = bool(self._st().get("split"))
            return ["1" if split else "0", "VFOB" if split else "VFOA"]
        if cmd == "S":
            b = self._b()
            self._need(b, "split")
            on = args[0] not in ("0",)
            if bool(b.snapshot().get("split")) != on:
                b.set("split", on)
            return None
        if cmd == "i":
            st = self._st()
            return [str(int(st.get("freq_b") or st.get("freq") or 0))]
        if cmd == "I":
            b = self._b()
            b.set("freq_b", int(float(args[0])))
            return None
        if cmd == "x":
            name, pb = self._mode()
            return [name, str(pb)]
        if cmd == "X":
            self._b()
            return None                         # TX mode follows RX mode on these radios
        if cmd in ("j", "z"):
            b = self._b()
            self._need(b, "rit_hz")
            return [str(int(b.snapshot().get("rit_hz") or 0))]
        if cmd in ("J", "Z"):
            b = self._b()
            self._need(b, "rit_hz")
            b.set("rit_hz", int(float(args[0])))
            return None
        if cmd == "l":
            return [self._get_level(args[0].upper())]
        if cmd == "L":
            return self._set_level(args[0].upper(), float(args[1]))
        if cmd == "u":
            b = self._b()
            key = _FUNCS.get(args[0].upper())
            if not key:
                raise RigError(ENAVAIL)
            self._need(b, key)
            return ["1" if b.snapshot().get(key) else "0"]
        if cmd == "U":
            b = self._b()
            key = _FUNCS.get(args[0].upper())
            if not key:
                raise RigError(ENAVAIL)
            self._need(b, key)
            b.set(key, args[1] not in ("0",))
            return None
        if cmd == "\\get_dcd":
            busy = self._st().get("busy")
            if busy is None:
                raise RigError(ENAVAIL)
            return ["1" if busy else "0"]
        raise RigError(ENAVAIL)

    def _set_mode(self, name):
        b = self._b()
        if name.upper() == "NONE":
            return None
        self._need(b, "mode")
        target = mode_from_name(name, b.modes, b.supports("data"))
        if target is None:
            raise RigError(EINVAL)
        mode, data = target
        st = b.snapshot()
        if st.get("mode") != mode:
            b.set("mode", mode)
        if b.supports("data") and bool(st.get("data")) != data:
            b.set("data", data)
        return None

    def _set_ptt(self, on):
        b = self._b()
        self._need(b, "ptt")
        if self.ptt is not None:
            if not self.ptt(on):
                raise RigError(EIO)
        else:
            b.set("ptt", on)
        return None

    def _get_level(self, name):
        b = self._b()
        st = b.snapshot()
        tx = bool(st.get("tx"))
        maxw = self._maxw(b)
        if name == "STRENGTH":
            return str(int(round(st.get("smeter") or -54)))
        if name == "RFPOWER":
            self._need(b, "power")
            return f"{(st.get('power') or 0) / maxw:.6f}"
        if name == "RFPOWER_METER":
            return f"{((st.get('po') or 0) if tx else 0) / maxw:.6f}"
        if name == "RFPOWER_METER_WATTS":
            return f"{(st.get('po') or 0) if tx else 0:.6f}"
        if name == "SWR":
            return f"{(st.get('swr') or 1.0) if tx else 1.0:.6f}"
        if name == "ALC":
            return f"{(st.get('alc') or 0) if tx else 0:.6f}"
        if name == "COMP_METER":
            return f"{(st.get('comp') or 0) if tx else 0:.6f}"
        if name in ("VD_METER", "ID_METER"):
            v = st.get("vd" if name == "VD_METER" else "id")
            if v is None:
                raise RigError(ENAVAIL)
            return f"{v:.6f}"
        if name in _PCT_LEVELS:
            key = _PCT_LEVELS[name]
            self._need(b, key)
            return f"{(st.get(key) or 0) / 100:.6f}"
        if name == "PREAMP":
            self._need(b, "preamp")
            opts = b.controls["preamp"].options
            v = st.get("preamp")
            return str(opts.index(v) * 10 if v in opts else 0)
        if name == "ATT":
            self._need(b, "att")
            return str(_db(st.get("att")))
        if name == "AGC":
            self._need(b, "agc")
            return str(_AGC_CODE.get(st.get("agc"), 6))
        raise RigError(ENAVAIL)

    def _set_level(self, name, value):
        b = self._b()
        if name == "RFPOWER":
            self._need(b, "power")
            c = b.controls["power"]
            b.set("power", max(c.lo, min(c.hi, round(value * self._maxw(b)))))
            return None
        if name in _PCT_LEVELS:
            key = _PCT_LEVELS[name]
            self._need(b, key)
            b.set(key, max(0, min(100, round(value * 100))))
            return None
        raise RigError(ENAVAIL)


def dump_state(b) -> str:
    """rigctld's \\dump_state reply (protocol 1), shaped like rigctld 4.7.1's.
    Frequency ranges, modes and power come from the backend; the capability
    masks say "everything" so clients ask, and unsupported requests are
    answered RPRT -11, exactly as a real rigctld does for a rig without them."""
    maxw = b.controls["power"].hi if b is not None and "power" in b.controls else 100
    mw = int(maxw * 1000)
    modes = "0x7ffffffff"      # every Hamlib mode bit up to PKTFMN: the backend refuses what it lacks
    lines = [
        "1",                                   # protocol version
        "2",                                   # rig model (NET rigctl)
        "0",                                   # ITU region (deprecated)
        f"30000.000000 1500000000.000000 {modes} -1 -1 0x3 0x1",
        "0 0 0 0 0 0 0",
        f"1800000.000000 1500000000.000000 {modes} {min(1000, mw)} {mw} 0x3 0x1",
        "0 0 0 0 0 0 0",
        f"{modes} 1",
        "0 0",
        "0xc 2400", "0x2 500", "0x10 500", "0x1 6000", "0x20 15000", "0x40 230000",
        "0 0",
        "9999", "9999", "0", "0",              # max RIT, max XIT, max IF shift, announces
        "10 20 ", "6 12 18 20 ",               # preamp / attenuator dB lists
        "0xffffffffffffffff", "0xffffffffffffffff",      # get / set func
        "0xffffffffffffffff", "0xffffffffffffffff",      # get / set level
        "0", "0",                              # get / set parm
        "vfo_ops=0x0",
        "ptt_type=0x1",                        # RIG_PTT_RIG: key over CAT
        "targetable_vfo=0x0",
        "has_set_vfo=1", "has_get_vfo=1", "has_set_freq=1", "has_get_freq=1",
        "has_set_conf=0", "has_get_conf=0", "has_power2mW=0", "has_mW2power=0",
        "has_get_ant=0", "has_set_ant=0",
        "timeout=1000",
        "rig_model=2",
        "rigctld_version=Rig Deck share server",
        "agc_levels=0=OFF 2=FAST 5=MEDIUM 3=SLOW 6=AUTO",
        "done",
    ]
    return "\n".join(lines)


class _Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.server.share.clients += 1

    def finish(self):
        self.server.share.clients -= 1
        super().finish()

    def handle(self):
        r = self.server.share.responder
        self.request.settimeout(None)
        while True:
            try:
                raw = self.rfile.readline(4096)
            except OSError:
                return
            if not raw:
                return
            # one line may carry several commands separated by ';' in some clients: not
            # supported by netrigctl, so treat the whole line as one command
            reply = r.handle(raw.decode("ascii", errors="replace"))
            if reply is None:
                return
            try:
                self.wfile.write(reply.encode("ascii", errors="replace"))
                self.wfile.flush()
            except OSError:
                return
            self.server.share.requests += 1


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False      # on Windows reuse would let two servers share the port


class ShareServer:
    """One listening port for one deck slot."""

    def __init__(self, slot: str, port: int, backend: Callable, ptt: Callable | None = None,
                 host: str = "127.0.0.1"):
        self.slot, self.port, self.host = slot, port, host
        self.responder = Responder(backend, ptt)
        self.clients = 0
        self.requests = 0
        self.error = ""
        self._srv: _Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        srv = _Server((self.host, self.port), _Handler, bind_and_activate=False)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                srv.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            srv.server_bind()
            srv.server_activate()
        except OSError as exc:
            srv.server_close()               # start() is retried, so don't leak the socket
            self.error = f"port {self.port} in use" if getattr(exc, "winerror", None) in (10048, 10013) \
                or exc.errno in (98, 10048) else str(exc)
            return False
        srv.share = self
        self._srv = srv
        self.error = ""
        self._thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.25},
                                        name=f"share-{self.slot}", daemon=True)
        self._thread.start()
        return True

    @property
    def running(self) -> bool:
        return self._srv is not None

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None
        self.clients = 0

