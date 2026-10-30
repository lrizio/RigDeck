"""Protocol encode/decode tests.  Run:  python -m pytest tests  (or python tests/test_protocols.py)"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rigdeck.backends import icom, yaesu  # noqa: E402
from rigdeck.backends.base import interp  # noqa: E402
from rigdeck.backends.hamlib import parse_ext  # noqa: E402
from rigdeck.ui.main_window import parse_freq  # noqa: E402


class IcomTests(unittest.TestCase):
    def test_freq_bcd_roundtrip(self):
        self.assertEqual(icom.freq_to_bcd(7093000), bytes([0x00, 0x30, 0x09, 0x07, 0x00]))
        self.assertEqual(icom.freq_to_bcd(14074000), bytes([0x00, 0x40, 0x07, 0x14, 0x00]))
        for hz in (135700, 1840000, 7093000, 50313000, 144174000):
            self.assertEqual(icom.bcd_to_freq(icom.freq_to_bcd(hz)), hz)

    def test_level_bcd(self):
        self.assertEqual(icom.level_to_bcd(255), bytes([0x02, 0x55]))
        self.assertEqual(icom.level_to_bcd(120), bytes([0x01, 0x20]))
        self.assertEqual(icom.bcd_to_level(bytes([0x02, 0x41])), 241)
        self.assertEqual(icom.level_to_bcd(999), bytes([0x02, 0x55]))   # clamped

    def test_rit_bcd(self):
        self.assertEqual(icom.rit_to_bcd(150), bytes([0x50, 0x01, 0x00]))
        self.assertEqual(icom.rit_to_bcd(-1230), bytes([0x30, 0x12, 0x01]))
        self.assertEqual(icom.bcd_to_rit(bytes([0x30, 0x12, 0x01])), -1230)

    def test_frame(self):
        self.assertEqual(icom.build_frame(0x98, 0x03), bytes.fromhex("FEFE98E003FD"))
        self.assertEqual(icom.build_frame(0x98, 0x14, 0x0A, bytes([0x01, 0x28])),
                         bytes.fromhex("FEFE98E0140A0128FD"))

    def test_split_frames_keeps_partial_preamble(self):
        # Regression: a read ending on a lone FE used to discard it and lose the next reply.
        buf = bytearray(b"\xFE")
        self.assertEqual(icom.split_frames(buf), [])
        buf.extend(bytes.fromhex("FEE09803003009070 0FD".replace(" ", "")))
        frames = icom.split_frames(buf)
        self.assertEqual(len(frames), 1)
        self.assertEqual(icom.bcd_to_freq(frames[0][5:-1]), 7093000)

    def test_split_frames_multiple_and_garbage(self):
        buf = bytearray(b"\x00\x11" + bytes.fromhex("FEFEE098FBFD") + bytes.fromhex("FEFE0098000030090700FD")
                        + bytes.fromhex("FEFE98"))
        frames = icom.split_frames(buf)
        self.assertEqual([f[4] for f in frames], [0xFB, 0x00])
        self.assertEqual(bytes(buf), bytes.fromhex("FEFE98"))            # partial kept for next read

    def test_filter_width_labels(self):
        self.assertEqual(icom.filter_width_label("USB", 34), "3.0k")
        self.assertEqual(icom.filter_width_label("CW", 9), "500")
        self.assertEqual(icom.filter_width_label("AM", 29), "6.0k")


class IcomScopeTests(unittest.TestCase):
    def test_assemble_real_ic705_sweep(self):
        # Division layout captured from the real IC-705 over USB, 2026-09-27:
        # header (centre 7.140 MHz, half-span 50 kHz) + 9 x 50 + 25 points.
        a = icom.ScopeAssembler()
        header = bytes.fromhex("00 01 11 00 00 00 14 07 00 00 00 05 00 00 00".replace(" ", ""))
        self.assertIsNone(a.feed(header))
        sweep = None
        for div in range(2, 12):
            n = 25 if div == 11 else 50
            payload = bytes([0x00, icom.bcd_byte(div), 0x11]) + bytes([div] * n)
            sweep = a.feed(payload)
            if div < 11:
                self.assertIsNone(sweep)
        self.assertEqual((sweep["lo"], sweep["hi"], sweep["mode"]), (7_090_000, 7_190_000, 0))
        self.assertEqual(len(sweep["data"]), 475)
        self.assertEqual(sweep["data"][-1], 11)

    def test_dropped_division_discards_sweep(self):
        # a lost middle division must not yield a short sweep (it would be stretched
        # on screen and slide every peak sideways), and the next sweep must be clean
        a = icom.ScopeAssembler()
        header = bytes.fromhex("00 01 11 00 00 00 14 07 00 00 00 05 00 00 00".replace(" ", ""))

        def run(skip=None):
            out = [a.feed(header)]
            for div in range(2, 12):
                if div == skip:
                    continue
                n = 25 if div == 11 else 50
                out.append(a.feed(bytes([0x00, icom.bcd_byte(div), 0x11]) + bytes([div] * n)))
            return [r for r in out if r]
        self.assertEqual(run(skip=6), [])
        good = run()
        self.assertEqual(len(good), 1)
        self.assertEqual(len(good[0]["data"]), 475)

    def test_fixed_mode_header(self):
        h = icom.parse_scope_header(bytes([0, 1, 0x11, 0x01]) + icom.freq_to_bcd(7_000_000)
                                    + icom.freq_to_bcd(7_300_000) + bytes([0]))
        self.assertEqual((h["lo"], h["hi"]), (7_000_000, 7_300_000))

    def test_ic7610_sweep_main_only(self):
        # Synthetic IC-7610 sweep: header + 13 x 50 + 39 = 689 points (Hamlib ic7610.c),
        # with a SUB-scope (id 1) sweep interleaved that must be ignored.
        def frames(scope_id):
            hdr = bytes([scope_id, 0x01, 0x15, 0x00]) + icom.freq_to_bcd(14_074_000) \
                + icom.freq_to_bcd(25_000) + b"\x00"
            out = [hdr]
            for div in range(2, 16):
                n = 39 if div == 15 else 50
                out.append(bytes([scope_id, icom.bcd_byte(div), 0x15]) + bytes([200 if scope_id == 0 else 7] * n))
            return out
        asm = icom.ScopeAssembler(200)
        done = []
        for main, sub in zip(frames(0), frames(1)):
            for f in (main, sub):
                r = asm.feed(f)
                if r:
                    done.append(r)
        self.assertEqual(len(done), 1)
        self.assertEqual(len(done[0]["data"]), 689)
        self.assertEqual((done[0]["lo"], done[0]["hi"], done[0]["max"]), (14_049_000, 14_099_000, 200))
        self.assertEqual(set(done[0]["data"]), {200})
        b = icom.IcomCIV({"model": "IC-7610", "port": "COM99"})
        self.assertIn("scope_span", b.controls)
        self.assertEqual(b._scope.level_max, 200)

    def test_span_labels(self):
        self.assertEqual([icom.span_label(x) for x in icom.SCOPE_SPANS][-2:], ["500k", "1M"])


class YaesuTests(unittest.TestCase):
    def test_parse_if_real_frames(self):
        # Frames captured from the real FT-710, 2026-09-27
        p = yaesu.parse_if("IF000007093000+000000100000;")
        self.assertEqual((p["freq"], p["mode"], p["data"], p["rit"], p["xit"], p["rit_hz"]),
                         (7093000, "LSB", False, False, False, 0))
        p = yaesu.parse_if("IF000007093010+012010100000;")
        self.assertEqual((p["freq"], p["rit"], p["xit"], p["rit_hz"]), (7093010, True, False, 120))
        p = yaesu.parse_if("IF000007093000+000011100000;")
        self.assertTrue(p["rit"] and p["xit"])

    def test_parse_if_rejects_bad(self):
        self.assertIsNone(yaesu.parse_if("IF000007093000+00000010000;"))
        self.assertIsNone(yaesu.parse_if("FA007093000;"))

    def test_mode_codes_roundtrip(self):
        for code, (mode, data) in yaesu.CODE_TO_MODE.items():
            self.assertEqual(yaesu.MODE_TO_CODE[(mode, data)], code)
        self.assertEqual(yaesu.MODE_TO_CODE[("USB", True)], "C")
        self.assertEqual(yaesu.MODE_TO_CODE[("LSB", True)], "8")

    def test_widths(self):
        self.assertEqual(yaesu.SSB_WIDTHS[20], 3000)          # radio reported SH0020 at 3.0k
        self.assertEqual(yaesu.fmt_width(yaesu.SSB_WIDTHS[18]), "2.8k")
        self.assertIsNone(yaesu.widths_for("FM"))

    def test_smeter_cal(self):
        self.assertEqual(interp(yaesu.STR_CAL, 130), 0)       # S9
        self.assertEqual(interp(yaesu.STR_CAL, 0), -54)
        self.assertAlmostEqual(interp(yaesu.STR_CAL, 76), -19.6, places=1)


def _yaesu_rig(model, replies=None):
    """A Yaesu backend with its serial I/O replaced: writes are recorded,
    queries answered from `replies` (cmd -> reply frame)."""
    b = yaesu.MODELS[model]({"port": "COM99"})
    b.sent = []
    b._write = lambda cmd: b.sent.append(cmd)
    b._query = lambda cmd, prefix=None: (replies or {}).get(cmd)
    return b


def _icom_rig(model, replies=None):
    b = icom.IcomCIV({"model": model, "port": "COM99"})
    b.sent = []

    def cmd(c, sub=None, data=b"", sub_len=None):
        b.sent.append((c, sub, bytes(data)))
        return (replies or {}).get((c, sub), b"")
    b._cmd = cmd
    return b


class ModelTableTests(unittest.TestCase):
    """Every model in both tables builds, and the per-model command
    differences taken from Hamlib come out on the wire as intended."""

    def test_every_model_builds(self):
        for name in icom.MODELS:
            b = icom.IcomCIV({"model": name, "port": "COM99"})
            self.assertEqual(b.model, name)
            self.assertTrue(b.slow_polls() and b.fast_polls(), name)
            self.assertEqual(b.controls["power"].hi, icom.MODELS[name]["maxw"], name)
        for name, cls in yaesu.MODELS.items():
            b = cls({"port": "COM99"})
            self.assertEqual(b.model, name)
            self.assertTrue(b.slow_polls() and b.fast_polls(), name)
            for (mode, data), code in b.MODE_TO_CODE.items():
                self.assertEqual(b.CODE_TO_MODE[code], (mode, data))
            for m in b.MODES:
                self.assertIn((m, False), b.MODE_TO_CODE, f"{name} {m}")

    def test_icom_feature_flags(self):
        b = _icom_rig("IC-7200")
        for key in ("rit", "xit", "rit_hz"):
            self.assertNotIn(key, b.controls)
        self.assertNotIn("FM", b.modes)
        self.assertNotIn("data", _icom_rig("IC-7000").controls)
        self.assertNotIn("xit", _icom_rig("IC-9700").controls)
        self.assertIn("rit", _icom_rig("IC-9700").controls)
        self.assertEqual(_icom_rig("IC-7851").controls["att"].options[-1], "21dB")
        self.assertEqual(icom.MODELS["IC-7300MK2"]["addr"], 0xB6)

    def test_icom_data_mode_subcommand(self):
        b = _icom_rig("IC-7200")
        b.state["mode"] = "USB"
        b.apply_set("data", True)
        self.assertEqual(b.sent[-1], (0x1A, 0x04, b"\x01"))       # IC-7200: 1A 04, on/off only
        b = _icom_rig("IC-7610")
        b.state["mode"] = "USB"
        b.apply_set("data", True)
        self.assertEqual(b.sent[-1], (0x1A, 0x06, b"\x01\x01"))   # 1A 06, on + FIL1

    def test_icom_agc_codes(self):
        b = _icom_rig("IC-7410", {(0x16, 0x12): b"\x01"})        # IC-7410: 1 = SLOW
        b._poll_agc()
        self.assertEqual(b.state["agc"], "SLOW")
        b.apply_set("agc", "FAST")
        self.assertEqual(b.sent[-1], (0x16, 0x12, b"\x03"))

    def test_yaesu_split_commands(self):
        for model, on, off in (("FT-710", "ST1", "ST0"), ("FTDX101D", "ST1", "ST0"),
                               ("FT-891", "ST1", "ST0"), ("FT-991", "FT3", "FT2")):
            b = _yaesu_rig(model)
            b.apply_set("split", True)
            b.apply_set("split", False)
            self.assertEqual(b.sent, [on, off], model)

    def test_yaesu_clarifier_styles(self):
        b = _yaesu_rig("FTDX10")
        b.apply_set("rit_hz", -120)
        self.assertEqual(b.sent, ["CF001-0120"])
        b = _yaesu_rig("FTDX101MP")
        b.apply_set("rit_hz", -120)
        self.assertEqual(b.sent, ["RC", "RD0120"])
        self.assertNotIn("rit", _yaesu_rig("FT-891").controls)

    def test_yaesu_width_formats(self):
        for model, reply, write in (("FT-710", "SH0020;", "SH0021"), ("FT-991A", "SH020;", "SH021"),
                                    ("FT-891", "SH0120;", "SH0121")):
            b = _yaesu_rig(model, {"SH0": reply})
            b.state["mode"] = "USB"
            b._poll_width()
            self.assertEqual(b.state["width_idx"], 20, model)
            b.apply_action("filter_step", 1)
            self.assertEqual(b.sent[-1], write, model)

    def test_yaesu_per_model_limits(self):
        self.assertEqual(_yaesu_rig("FTDX101MP").controls["power"].hi, 200)
        self.assertEqual(_yaesu_rig("FT-891").controls["preamp"].options, ["IPO", "AMP"])
        self.assertNotIn("C4FM", _yaesu_rig("FT-891").MODES)
        self.assertEqual(_yaesu_rig("FTDX10").TUNE_CMD, "AC002")
        self.assertEqual(_yaesu_rig("FT-710").TUNE_CMD, "AC003")


class ShareServerTests(unittest.TestCase):
    """The rigctld-protocol replies Rig Deck gives sharing programs (JTDX etc.)."""

    def _resp(self, backend, ptt=None):
        from rigdeck.backends.base import LINK_LIVE
        from rigdeck.share import Responder
        backend.link = LINK_LIVE
        return Responder(lambda: backend, ptt)

    def test_mode_names_both_families(self):
        from rigdeck.share import mode_from_name, mode_name
        self.assertEqual(mode_name("USB", True), "PKTUSB")
        self.assertEqual(mode_name("CW-L", False), "CWR")          # Yaesu FT-710
        self.assertEqual(mode_name("CW-R", False), "CWR")          # Icom
        self.assertEqual(mode_name("RTTY-U", False), "RTTYR")
        self.assertEqual(mode_name("DV", False), "DSTAR")
        ft710 = yaesu.MODELS["FT-710"].MODES
        self.assertEqual(mode_from_name("CW", ft710, True), ("CW-U", False))
        self.assertEqual(mode_from_name("PKTUSB", ft710, True), ("USB", True))
        self.assertIsNone(mode_from_name("PKTUSB", ft710, False))
        self.assertIsNone(mode_from_name("DSTAR", ft710, True))
        self.assertEqual(mode_from_name("cwr", icom.MODELS["IC-705"]["modes"], True), ("CW-R", False))

    def test_get_set_roundtrip(self):
        b = _yaesu_rig("FT-710")
        b.state.update(freq=14074000, mode="USB", data=True, filter="2.4k", tx=False, split=False)
        r = self._resp(b)
        self.assertEqual(r.handle("f\n"), "14074000\n")
        self.assertEqual(r.handle("m"), "PKTUSB\n2400\n")
        self.assertEqual(r.handle("F 7074000"), "RPRT 0\n")
        self.assertEqual(r.handle("f"), "7074000\n")               # optimistic state, no radio round trip
        self.assertEqual(r.handle("+f"), "get_freq:\nFrequency: 7074000\nRPRT 0\n")
        self.assertEqual(r.handle("M CW 0"), "RPRT 0\n")
        self.assertEqual((b.state["mode"], b.state["data"]), ("CW-U", False))
        self.assertEqual(r.handle("M DSTAR 0"), "RPRT -1\n")       # FT-710 has no D-STAR
        self.assertEqual(r.handle("l FOO"), "RPRT -11\n")
        self.assertEqual(r.handle("\\get_foo"), "RPRT -11\n")
        self.assertEqual(r.handle("s"), "0\nVFOA\n")
        self.assertEqual(r.handle("f VFOA"), "7074000\n")          # VFO-mode clients
        self.assertIsNone(r.handle("q"))

    def test_ptt_goes_through_deck_rules(self):
        calls = []
        b = _yaesu_rig("FT-710")
        r = self._resp(b, ptt=lambda on: calls.append(on) or True)
        self.assertEqual(r.handle("T 1"), "RPRT 0\n")
        self.assertEqual(calls, [True])
        r = self._resp(b, ptt=lambda on: False)                   # deck refused (radio not live)
        self.assertEqual(r.handle("T 1"), "RPRT -6\n")

    def test_not_live_is_an_error(self):
        from rigdeck.share import Responder
        b = _yaesu_rig("FT-710")
        b.state["freq"] = 14074000
        r = Responder(lambda: b)                                   # link OFFLINE
        self.assertEqual(r.handle("f"), "RPRT -6\n")
        self.assertEqual(r.handle("\\get_powerstat"), "0\n")
        self.assertEqual(r.handle("\\chk_vfo"), "0\n")

    def test_dump_state_shape(self):
        from rigdeck.share import dump_state
        lines = dump_state(icom.IcomCIV({"model": "IC-705", "port": "COM99"})).split("\n")
        self.assertEqual(lines[:3], ["1", "2", "0"])
        self.assertIn("1800000.000000 1500000000.000000 0x7ffffffff 1000 10000 0x3 0x1", lines)
        self.assertEqual(lines[-1], "done")
        self.assertIn("ptt_type=0x1", lines)


class TakeoverTests(unittest.TestCase):
    """Which process Rig Deck may close to get its radio's COM port back."""

    def test_rigctld_serial_port(self):
        from rigdeck.takeover import rigctld_serial_port
        exe = r'"C:\Program Files\hamlib-w64-4.7.1\bin\rigctld.exe"'
        self.assertEqual(rigctld_serial_port(exe + " -m 3085 -r COM4 -s 115200 -W 3"), "COM4")
        self.assertEqual(rigctld_serial_port(exe + " -m 3085 -r com4"), "COM4")
        self.assertEqual(rigctld_serial_port(exe + r" --rig-file=\\.\COM12 -m 1"), "COM12")
        self.assertEqual(rigctld_serial_port(exe + " -m 1"), "")

    def test_only_a_rigctld_on_our_port_is_closed(self):
        from rigdeck.takeover import holders, port_is_busy
        procs = [(100, "rigctld.exe -m 3085 -r COM4 -s 115200"), (200, "rigctld.exe -m 1036 -r COM5 -t 4541"),
                 (300, "rigctld.exe -m 1"), (400, "")]                           # 400: command line unreadable
        self.assertEqual(holders(procs, "COM4"), [100])
        self.assertEqual(holders(procs, "com5"), [200])
        self.assertEqual(holders(procs, "COM9"), [])
        self.assertEqual(holders(procs, ""), [])
        self.assertTrue(port_is_busy("COM4 is in use by another program (ICOMNodeDisplay / rigctld / wfview?)"))
        self.assertTrue(port_is_busy("could not open port 'COM5': PermissionError(13, 'Access is denied.')"))
        self.assertFalse(port_is_busy("could not open port 'COM5': FileNotFoundError(2, ...)"))
        self.assertFalse(port_is_busy("no serial port configured"))


class HamlibTests(unittest.TestCase):
    def test_parse_ext(self):
        self.assertEqual(parse_ext("get_freq:\nFrequency: 145000000\nRPRT 0\n"), (0, ["145000000"]))
        self.assertEqual(parse_ext("get_mode:\nMode: FM\nPassband: 15000\nRPRT 0\n"), (0, ["FM", "15000"]))
        self.assertEqual(parse_ext("get_level: STRENGTH\n-52\nRPRT 0\n"), (0, ["-52"]))
        self.assertEqual(parse_ext("RPRT -11\n")[0], -11)


class FreqEntryTests(unittest.TestCase):
    def test_parse_freq(self):
        self.assertEqual(parse_freq("7.074"), 7074000)
        self.assertEqual(parse_freq("14074"), 14074000)
        self.assertEqual(parse_freq("14074000"), 14074000)
        self.assertEqual(parse_freq("14.074.500"), 14074500)
        self.assertEqual(parse_freq("7074.5"), 7074500)
        self.assertEqual(parse_freq("3.5 MHz"), 3500000)
        self.assertEqual(parse_freq("7150 kHz"), 7150000)
        self.assertIsNone(parse_freq("abc"))


if __name__ == "__main__":
    unittest.main()
