"""Renders the screenshots used by build_user_guide.py (demo radios, off-screen).

    set QT_QPA_PLATFORM=offscreen & set QT_QPA_FONTDIR=C:/Windows/Fonts
    python scripts/guide_screens.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "docs", "guide")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from rigdeck import config  # noqa: E402

config.save = lambda c: None
from rigdeck.ui.dialogs import SetupDialog  # noqa: E402
from rigdeck.ui import main_window as mw  # noqa: E402
from rigdeck.ui.main_window import DeckWindow  # noqa: E402
from rigdeck.ui.theme import DESIGN_W, set_scale  # noqa: E402

SCALE = 1.6          # render bigger than the screen for a sharp printed page


def apply_zoom_unclamped(self, z, full=False):
    set_scale(SCALE)
    self.build_ui()
    self.setFixedSize(round(DESIGN_W * SCALE), round(self.design_h() * SCALE))


DeckWindow.apply_zoom = apply_zoom_unclamped

app = QApplication(sys.argv)
cfg = config.load()
cfg["zoom"] = 1.0
cfg["theme"] = "SHACK"
cfg["radios"] = {"main": {"backend": "sim", "model": "IC-705", "scope": True, "freq": 7140000},
                 "sub": {"backend": "sim", "model": "FT-710", "freq": 7074000}}
cfg["_demo"] = True                  # simulated radios: never open the real share ports
w = DeckWindow(cfg)
w.show()


def step1():
    w.set_focus("main")
    b = w.backends["sub"]
    b.set("mode", "USB")
    w.refresh()
    QTimer.singleShot(20000, step2)       # let the waterfall fill


def step2():
    w.grab().save(os.path.join(OUT, "console.png"))
    w.scope_box.grab().save(os.path.join(OUT, "scope.png"))
    # TX example on SUB
    w.set_focus("sub")
    w.toggle_ptt()
    w.backends["sub"].set("rit", True)
    w.backends["sub"].set("rit_hz", 120)
    QTimer.singleShot(1500, step3)


def step3():
    w.grab().save(os.path.join(OUT, "console_tx.png"))
    w.toggle_ptt()
    w.backends["sub"].set("rit", False)
    d = SetupDialog(w.cfg | {"radios": {"main": {"backend": "icom", "model": "IC-705", "port": "", "baud": 115200},
                                        "sub": {"backend": "yaesu", "model": "FT-710", "port": "COM5",
                                                "baud": 38400}}}, w)
    d.show()
    QTimer.singleShot(600, lambda: (d.grab().save(os.path.join(OUT, "setup.png")), d.close(), w.close()))


QTimer.singleShot(1500, step1)
app.exec()
print("screens written to", OUT)
