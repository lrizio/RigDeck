"""Settings file (%APPDATA%\\RigDeck\\config.json) and backend factory."""
from __future__ import annotations

import copy
import json
import os

import serial.tools.list_ports

from .backends.base import Backend
from .backends.hamlib import Hamlib
from .backends.icom import MODELS as ICOM_MODELS, IcomCIV
from .backends.sim import Simulator
from .backends.yaesu import MODELS as YAESU_MODELS, YaesuFT710

APP_NAME = "RIG DECK"
VERSION = "0.3.1"

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RigDeck")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

BACKENDS = {
    "icom": "Icom CI-V (direct)",
    "yaesu": "Yaesu CAT (direct)",
    "hamlib": "Hamlib rigctld (any radio)",
    "sim": "Simulator",
    "none": "Not used",
}

# Amateur allocations used to colour the band scale (Region 3 / VK).
# Anything outside these draws red. Edit band_plan in config.json to suit.
DEFAULT_BAND_PLAN = [
    [1800000, 1875000], [3500000, 3800000], [5351500, 5366500], [7000000, 7300000],
    [10100000, 10150000], [14000000, 14350000], [18068000, 18168000], [21000000, 21450000],
    [24890000, 24990000], [28000000, 29700000], [50000000, 54000000], [144000000, 148000000],
    [420000000, 450000000],
]

DEFAULTS = {
    "radios": {
        "main": {"backend": "icom", "model": "IC-705", "port": "", "baud": 115200, "civ_addr": ""},
        "sub": {"backend": "yaesu", "model": "FT-710", "port": "COM5", "baud": 38400},
    },
    "zoom": 1.0,
    "theme": "SHACK",
    "callsign": "",
    "tot_s": 180,
    "band_plan": DEFAULT_BAND_PLAN,
    # Share each radio with other programs over rigctld's network protocol
    # (JTDX/WSJT-X: rig "Hamlib NET rigctl", 127.0.0.1:4532 for MAIN, :4533 for SUB).
    "share": {"enabled": True, "main_port": 4532, "sub_port": 4533},
}


def share_cfg(cfg: dict) -> dict:
    sh = dict(DEFAULTS["share"])
    sh.update(cfg.get("share") or {})
    return sh


def load() -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        for k, v in saved.items():
            if k == "radios":
                for slot in ("main", "sub"):
                    if isinstance(v.get(slot), dict):
                        cfg["radios"][slot] = v[slot]
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    os.replace(tmp, CONFIG_PATH)


def guess_icom_port(model: str) -> str:
    """Icom radios name their USB serial device after themselves
    ("IC-7610 12001234" serial number, "IC-705 Serial Port A (CI-V)")."""
    for p in serial.tools.list_ports.comports():
        text = f"{p.description} {p.serial_number or ''}"
        if model in text and "Port B" not in text:
            return p.device
    return ""


def make_backend(slot_cfg: dict, slot: str) -> Backend | None:
    cfg = dict(slot_cfg)
    kind = cfg.get("backend", "none")
    if kind == "icom":
        if not cfg.get("port"):
            cfg["port"] = guess_icom_port(cfg.get("model", "IC-7610"))
        return IcomCIV(cfg)
    if kind == "yaesu":
        return YAESU_MODELS.get(cfg.get("model"), YaesuFT710)(cfg)
    if kind == "hamlib":
        cfg.setdefault("tcp_port", 4541 if slot == "main" else 4542)
        return Hamlib(cfg)
    if kind == "sim":
        cfg.setdefault("freq", 7140000 if slot == "main" else 7141000)
        return Simulator(cfg)
    return None


__all__ = ["load", "save", "make_backend", "share_cfg", "BACKENDS", "ICOM_MODELS", "YAESU_MODELS", "APP_NAME", "VERSION"]
