"""A scripted fake IC-7610 CI-V responder for testing without the radio.

Run it on one end of a com0com pair and point Rig Deck's Icom backend at
the other end:

    python tests/fake_icom.py COM20          (Rig Deck on COM19)

It keeps its own state, answers reads, applies writes (replying FB),
answers FA to anything it does not know, and every few seconds pretends
the VFO knob moved by broadcasting a CI-V transceive frequency frame.
"""
from __future__ import annotations

import sys
import time

import serial

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
from rigdeck.backends.icom import (bcd_to_freq, bcd_to_level, freq_to_bcd, level_to_bcd,  # noqa: E402
                                   split_frames)

ADDR = 0x98


class FakeIC7610:
    def __init__(self):
        self.freq = 14074000
        self.freq_b = 14080000
        self.mode, self.fil = 0x01, 2
        self.data = 0
        self.width = 34               # 3.0 kHz
        self.levels = {0x01: 80, 0x02: 255, 0x03: 0, 0x0A: 128, 0x15: 100}
        self.funcs = {0x02: 0, 0x12: 2, 0x22: 0, 0x40: 0, 0x41: 0, 0x44: 0, 0x45: 0,
                      0x46: 0, 0x48: 0, 0x4E: 0, 0x58: 1}
        self.att = 0x00
        self.ant = [0, 0]
        self.split = 0
        self.rit = [0, 0, 0]          # BCD lo, BCD hi, sign
        self.rit_on = self.xit_on = 0
        self.tuner = 0
        self.ptt = 0
        self.ipplus = 0
        self.t0 = time.monotonic()

    def handle(self, cmd: int, d: bytes) -> bytes | None:
        """Return reply body (cmd + data) or None for FA."""
        ok = b"\xFB"
        if cmd == 0x03:
            return bytes([0x03]) + freq_to_bcd(self.freq)
        if cmd == 0x05:
            self.freq = bcd_to_freq(d)
            return ok
        if cmd == 0x04:
            return bytes([0x04, self.mode, self.fil])
        if cmd == 0x06:
            self.mode = d[0]
            if len(d) > 1:
                self.fil = d[1]
            return ok
        if cmd == 0x0F:
            if d:
                self.split = d[0]
                return ok
            return bytes([0x0F, self.split])
        if cmd == 0x11:
            if d:
                self.att = d[0]
                return ok
            return bytes([0x11, self.att])
        if cmd == 0x12:
            if d:
                self.ant = [d[0], d[1] if len(d) > 1 else 0]
                return ok
            return bytes([0x12] + self.ant)
        if cmd == 0x14:
            sub = d[0]
            if len(d) >= 3:
                self.levels[sub] = bcd_to_level(d[1:3])
                return ok
            return bytes([0x14, sub]) + level_to_bcd(self.levels.get(sub, 0))
        if cmd == 0x15:
            sub = d[0]
            t = time.monotonic() - self.t0
            if sub == 0x01:
                return bytes([0x15, 0x01, 1 if int(t) % 6 < 3 else 0])
            if sub == 0x02:
                return bytes([0x15, 0x02]) + level_to_bcd(int(90 + 60 * abs(((t * 0.7) % 2) - 1)))
            if sub in (0x11, 0x12, 0x13, 0x14):
                return bytes([0x15, sub]) + level_to_bcd(150 if self.ptt else 0)
            return None
        if cmd == 0x16:
            sub = d[0]
            if len(d) >= 2:
                self.funcs[sub] = d[1]
                return ok
            if sub in self.funcs:
                return bytes([0x16, sub, self.funcs[sub]])
            return None
        if cmd == 0x1A:
            sub = d[0]
            if sub == 0x03:
                if len(d) >= 2:
                    self.width = (d[1] >> 4) * 10 + (d[1] & 15)
                    return ok
                return bytes([0x1A, 0x03, ((self.width // 10) << 4) | (self.width % 10)])
            if sub == 0x06:
                if len(d) >= 3:
                    self.data = d[1]
                    if d[2]:
                        self.fil = d[2]
                    return ok
                return bytes([0x1A, 0x06, self.data, self.fil if self.data else 0])
            if sub == 0x07:
                if len(d) >= 2:
                    self.ipplus = d[1]
                    return ok
                return bytes([0x1A, 0x07, self.ipplus])
            return None
        if cmd == 0x1C:
            sub = d[0]
            if sub == 0x00:
                if len(d) >= 2:
                    self.ptt = d[1]
                    return ok
                return bytes([0x1C, 0x00, self.ptt])
            if sub == 0x01:
                if len(d) >= 2:
                    self.tuner = d[1]
                    return ok
                return bytes([0x1C, 0x01, self.tuner])
            return None
        if cmd == 0x21:
            sub = d[0]
            if sub == 0x00:
                if len(d) >= 4:
                    self.rit = list(d[1:4])
                    return ok
                return bytes([0x21, 0x00] + self.rit)
            if sub in (0x01, 0x02):
                attr = "rit_on" if sub == 0x01 else "xit_on"
                if len(d) >= 2:
                    setattr(self, attr, d[1])
                    return ok
                return bytes([0x21, sub, getattr(self, attr)])
            return None
        if cmd == 0x25 and d and d[0] == 0x01:
            if len(d) >= 6:
                self.freq_b = bcd_to_freq(d[1:6])
                return ok
            return bytes([0x25, 0x01]) + freq_to_bcd(self.freq_b)
        return None


def main(port: str):
    rig = FakeIC7610()
    ser = serial.Serial(port, 115200, timeout=0.01)
    buf = bytearray()
    last_bcast = time.monotonic()
    print(f"fake IC-7610 (CI-V {ADDR:02X}h) on {port}")
    while True:
        buf.extend(ser.read(ser.in_waiting or 1))
        for f in split_frames(buf):
            to, frm, cmd, data = f[2], f[3], f[4], f[5:-1]
            if to != ADDR:
                continue
            body = rig.handle(cmd, data)
            reply = b"\xFE\xFE" + bytes([frm, ADDR]) + (body if body is not None else b"\xFA") + b"\xFD"
            ser.write(reply)
        if time.monotonic() - last_bcast > 4:
            last_bcast = time.monotonic()
            rig.freq += 100
            ser.write(b"\xFE\xFE\x00" + bytes([ADDR, 0x00]) + freq_to_bcd(rig.freq) + b"\xFD")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "COM20")
