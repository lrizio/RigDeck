"""Rig Deck - a two-radio control console.

    python main.py            normal start
    python main.py --demo     both sides simulated (no radios needed)
"""
from __future__ import annotations

import ctypes
import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from rigdeck import config
from rigdeck.ui.main_window import DeckWindow


def resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main() -> int:
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HamProjects.RigDeck")
    app = QApplication(sys.argv)
    app.setApplicationName("Rig Deck")
    icon = QIcon(resource("icon.ico")) if os.path.exists(resource("icon.ico")) else None
    if icon:
        app.setWindowIcon(icon)
    cfg = config.load()
    if "--demo" in sys.argv:
        cfg["radios"] = {"main": {"backend": "sim", "model": "IC-705 (SIM)", "scope": True},
                         "sub": {"backend": "sim", "model": "FT-710 (SIM)"}}
        cfg["_demo"] = True
    win = DeckWindow(cfg, icon)
    if cfg.get("_demo"):
        win.cfg = dict(win.cfg)
        config.save = lambda _cfg: None        # demo mode never overwrites the real settings
    win.show()
    if "--shot" in sys.argv:                   # dev aid: save a PNG of the window and quit
        from PySide6.QtCore import QTimer
        path = sys.argv[sys.argv.index("--shot") + 1]
        QTimer.singleShot(3500, lambda: (win.grab().save(path), win.close()))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
