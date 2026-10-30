"""SETUP (which radio on which port), DIAG (live CAT traffic) and INFO."""
from __future__ import annotations

import time

import serial.tools.list_ports
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QCompleter)

from ..backends.hamlib import list_hamlib_models
from ..config import BACKENDS, ICOM_MODELS, YAESU_MODELS, share_cfg
BAUDS = ["4800", "9600", "19200", "38400", "57600", "115200"]


def port_items() -> list[tuple[str, str]]:
    ports = sorted(serial.tools.list_ports.comports(), key=lambda p: int(p.device[3:]) if p.device[3:].isdigit() else 0)
    return [(p.device, f"{p.device}  -  {p.description}") for p in ports]


class RadioBox(QGroupBox):
    _hamlib_models: list[tuple[int, str]] | None = None

    def __init__(self, title: str, cfg: dict, parent=None):
        super().__init__(title, parent)
        self.cfg = dict(cfg)
        form = QFormLayout(self)
        self.form = form

        self.kind = QComboBox()
        for k, label in BACKENDS.items():
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(cfg.get("backend", "none"))))
        form.addRow("Connection", self.kind)

        self.icom_model = QComboBox()
        self.icom_model.addItems(list(ICOM_MODELS))
        self.icom_model.setCurrentText(cfg.get("model", "IC-705") if cfg.get("backend") == "icom" else "IC-705")
        form.addRow("Icom model", self.icom_model)

        self.yaesu_model = QComboBox()
        self.yaesu_model.addItems(list(YAESU_MODELS))
        self.yaesu_model.setCurrentText(cfg.get("model", "FT-710") if cfg.get("backend") == "yaesu" else "FT-710")
        form.addRow("Yaesu model", self.yaesu_model)

        self.hl_model = QComboBox()
        self.hl_model.setEditable(True)
        self.hl_model.setToolTip("Type to search Hamlib's radio list, e.g. 'TS-590' or '7610'")
        form.addRow("Hamlib radio", self.hl_model)

        self.port = QComboBox()
        self.port.setEditable(True)
        # long device names ("Silicon Labs Dual CP2105 ...") stretched the dialog past half the screen
        self.port.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.port.setMinimumContentsLength(30)
        self.port.view().setMinimumWidth(460)
        for dev, label in port_items():
            self.port.addItem(label, dev)
        cur = cfg.get("port", "")
        i = self.port.findData(cur)
        if i >= 0:
            self.port.setCurrentIndex(i)
        else:
            self.port.setEditText(cur)
        form.addRow("Serial port", self.port)

        self.baud = QComboBox()
        self.baud.addItems(BAUDS)
        self.baud.setCurrentText(str(cfg.get("baud", 38400)))
        form.addRow("Baud", self.baud)

        self.civ = QLineEdit(str(cfg.get("civ_addr", "")))
        self.civ.setPlaceholderText("blank = model default")
        form.addRow("CI-V address (hex)", self.civ)

        self.rig_host = QLineEdit(cfg.get("host", "") or "")
        self.rig_host.setPlaceholderText("127.0.0.1")
        self.rig_tcp = QSpinBox()
        self.rig_tcp.setRange(1024, 65535)
        self.rig_tcp.setValue(int(cfg.get("tcp_port") or 4532))
        h = QHBoxLayout()
        h.addWidget(self.rig_host, 3)
        h.addWidget(self.rig_tcp, 1)
        self.rig_row = h
        form.addRow("rigctld host : port", h)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("dim")
        form.addRow(self.hint)

        self.kind.currentIndexChanged.connect(self._sync)
        self.icom_model.currentTextChanged.connect(self._icom_model_changed)
        self._last_icom_model = self.icom_model.currentText()
        self.yaesu_model.currentTextChanged.connect(self._sync)
        self._sync()

    def _load_hamlib(self):
        if RadioBox._hamlib_models is None:
            RadioBox._hamlib_models = list_hamlib_models()
        if self.hl_model.count() == 0:
            for num, name in RadioBox._hamlib_models:
                self.hl_model.addItem(f"{num}  {name}", num)
            comp = QCompleter([self.hl_model.itemText(i) for i in range(self.hl_model.count())])
            from PySide6.QtCore import Qt
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            self.hl_model.setCompleter(comp)
            num = self.cfg.get("hamlib_model")
            i = self.hl_model.findData(int(num)) if num else -1
            if i >= 0:
                self.hl_model.setCurrentIndex(i)

    def _icom_model_changed(self, model: str):
        # Older Icoms top out at 19200 baud: follow the model's default unless it was customised.
        old = ICOM_MODELS.get(self._last_icom_model, {}).get("baud")
        new = ICOM_MODELS.get(model, {}).get("baud")
        if new and self.baud.currentText() == str(old):
            self.baud.setCurrentText(str(new))
        self._last_icom_model = model
        self._sync()

    def _sync(self):
        # Only show the fields that mean something for this connection type
        # (greyed-out-but-visible dropdowns looked like broken ones).
        k = self.kind.currentData()
        # switching radio type: move the baud to the new type's default unless it was customised
        defaults = {"icom": str(ICOM_MODELS.get(self.icom_model.currentText(), {}).get("baud", 115200)),
                    "yaesu": "38400"}
        last = getattr(self, "_last_kind", k)
        if last != k and self.baud.currentText() == defaults.get(last, self.baud.currentText()) and k in defaults:
            self.baud.setCurrentText(defaults[k])
        self._last_kind = k
        serial = k in ("icom", "yaesu", "hamlib")
        for widget, show in ((self.icom_model, k == "icom"), (self.yaesu_model, k == "yaesu"),
                             (self.hl_model, k == "hamlib"), (self.port, serial), (self.baud, serial),
                             (self.civ, k == "icom"), (self.rig_row, k == "hamlib")):
            self.form.setRowVisible(widget, show)
        if k == "icom":
            addr = ICOM_MODELS.get(self.icom_model.currentText(), {}).get("addr")
            self.civ.setPlaceholderText(f"blank = {self.icom_model.currentText()} default ({addr:02X})"
                                        if addr else "blank = model default")
        if k == "hamlib":
            self._load_hamlib()
        self.hint.setText({
            "icom": "Use the radio's USB port. Set the radio's CI-V USB baud to match (or Auto). "
                    "Leave the port blank to pick the port named after the radio automatically.",
            "yaesu": (f"{self.yaesu_model.currentText()}: use the Enhanced COM port of the CP2105 pair; "
                      + YAESU_MODELS[self.yaesu_model.currentText()].CAT_RATE_MENU
                      + " must match the baud here."),
            "hamlib": "Any radio Hamlib supports. With a serial port set, Rig Deck starts its own rigctld on the "
                      "TCP port shown. Leave the serial port blank to join a rigctld that is already running.",
            "sim": "A pretend radio for trying the console out.",
            "none": "This side of the deck stays empty.",
        }.get(k, ""))
        # re-fit the dialog once the new hint/rows have laid out (no-op until it's shown)
        QTimer.singleShot(0, lambda: getattr(self.window(), "fit_wrapped", lambda: None)())

    def result(self) -> dict:
        k = self.kind.currentData()
        txt = self.port.currentText().strip()
        i = self.port.findText(txt)
        port = self.port.itemData(i) if i >= 0 else (txt.split()[0] if txt else "")
        out = {"backend": k, "port": port or "", "baud": int(self.baud.currentText())}
        if k == "icom":
            out["model"] = self.icom_model.currentText()
            out["civ_addr"] = self.civ.text().strip()
        elif k == "yaesu":
            out["model"] = self.yaesu_model.currentText()
        elif k == "hamlib":
            num = self.hl_model.currentData()
            if num is None:
                txt = self.hl_model.currentText().split()
                num = int(txt[0]) if txt and txt[0].isdigit() else None
            out["hamlib_model"] = num
            out["host"] = self.rig_host.text().strip() or "127.0.0.1"
            out["tcp_port"] = self.rig_tcp.value()
            if not port or out["host"] not in ("127.0.0.1", "localhost"):
                out["port"] = ""
        return out


class SetupDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rig Deck setup")
        self.cfg = cfg
        v = QVBoxLayout(self)
        h = QHBoxLayout()
        self.main = RadioBox("MAIN radio (left panel)", cfg["radios"]["main"])
        self.sub = RadioBox("SUB radio (right panel)", cfg["radios"]["sub"])
        h.addWidget(self.main)
        h.addWidget(self.sub)
        h.setAlignment(self.main, Qt.AlignmentFlag.AlignTop)
        h.setAlignment(self.sub, Qt.AlignmentFlag.AlignTop)
        v.addLayout(h)
        f = QFormLayout()
        self.call = QLineEdit(cfg.get("callsign", ""))
        f.addRow("Callsign (shown in the title bar)", self.call)
        self.tot = QSpinBox()
        self.tot.setRange(10, 900)
        self.tot.setSuffix(" s")
        self.tot.setValue(int(cfg.get("tot_s", 180)))
        f.addRow("PTT time-out (releases PTT after)", self.tot)
        self.upd_chk = QCheckBox("Check GitHub for a newer Rig Deck at start-up (one small request, nothing is installed)")
        self.upd_chk.setChecked(bool(cfg.get("check_updates", True)))
        f.addRow(self.upd_chk)
        v.addLayout(f)

        sh = share_cfg(cfg)
        box = QGroupBox("Share the radios with other programs (JTDX, WSJT-X, fldigi, loggers, ICOMNodeDisplay)")
        g = QFormLayout(box)
        self.share_on = QCheckBox("Share over the network (rigctld protocol, this PC only)")
        self.share_on.setChecked(bool(sh["enabled"]))
        g.addRow(self.share_on)
        self.share_main = QSpinBox()
        self.share_sub = QSpinBox()
        for w, val in ((self.share_main, sh["main_port"]), (self.share_sub, sh["sub_port"])):
            w.setRange(1024, 65535)
            w.setValue(int(val))
        hp = QHBoxLayout()
        hp.addWidget(QLabel("MAIN"))
        hp.addWidget(self.share_main)
        hp.addSpacing(16)
        hp.addWidget(QLabel("SUB"))
        hp.addWidget(self.share_sub)
        hp.addStretch(1)
        g.addRow("TCP ports", hp)
        tip = QLabel("In the other program choose rig <b>Hamlib NET rigctl</b>, network server "
                     "<b>127.0.0.1:4532</b> for MAIN (or :4533 for SUB), PTT method <b>CAT</b>. "
                     "It keeps working whichever radio Rig Deck is set to. Rig Deck must be running.")
        tip.setWordWrap(True)
        tip.setObjectName("dim")
        g.addRow(tip)
        self.share_on.toggled.connect(lambda on: (self.share_main.setEnabled(on), self.share_sub.setEnabled(on)))
        self.share_on.toggled.emit(self.share_on.isChecked())
        v.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def showEvent(self, e):
        super().showEvent(e)
        self.fit_wrapped()

    def fit_wrapped(self):
        """Word-wrapped hints report too small a height at the zoomed-in sizes (x1.25 and up),
        so the dialog came up short and clipped them. Once real widths are known, pin each
        hint to the height it needs at that width, and let the dialog grow (or shrink) to fit.
        Re-run when a hint's text changes (RadioBox._sync)."""
        if not self.isVisible():
            return
        labels = [lbl for lbl in self.findChildren(QLabel) if lbl.wordWrap()]
        for lbl in labels:
            lbl.setMinimumHeight(0)
        for _ in range(2):                  # a second pass settles widths changed by the first
            for lbl in labels:
                if lbl.isVisible() and lbl.width() > 0:
                    lbl.setMinimumHeight(lbl.heightForWidth(lbl.width()))
            self.adjustSize()

    def result_cfg(self) -> dict:
        cfg = dict(self.cfg)
        cfg["radios"] = {"main": self.main.result(), "sub": self.sub.result()}
        cfg["callsign"] = self.call.text().strip().upper()
        cfg["tot_s"] = self.tot.value()
        cfg["check_updates"] = self.upd_chk.isChecked()
        cfg["share"] = {"enabled": self.share_on.isChecked(), "main_port": self.share_main.value(),
                        "sub_port": self.share_sub.value()}
        return cfg


class DiagDialog(QDialog):
    """Live CAT traffic and link statistics for both radios."""

    def __init__(self, get_backends, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rig Deck diagnostics")
        self.resize(900, 560)
        self.get_backends = get_backends
        v = QVBoxLayout(self)
        self.stats = QLabel()
        self.stats.setWordWrap(True)
        v.addWidget(self.stats)
        h = QHBoxLayout()
        self.pause = QCheckBox("Pause")
        self.show_main = QCheckBox("MAIN")
        self.show_sub = QCheckBox("SUB")
        self.show_main.setChecked(True)
        self.show_sub.setChecked(True)
        for w in (self.show_main, self.show_sub, self.pause):
            h.addWidget(w)
        h.addStretch(1)
        clear = QPushButton("Copy")
        clear.clicked.connect(lambda: (self.log.selectAll(), self.log.copy()))
        h.addWidget(clear)
        v.addLayout(h)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setStyleSheet("font-family: Consolas, monospace; font-size: 12px;")
        v.addWidget(self.log, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(300)
        self.refresh()

    def refresh(self):
        backends = self.get_backends()
        lines, stats = [], []
        for slot, b in backends.items():
            if b is None:
                stats.append(f"<b>{slot.upper()}</b>: not used")
                continue
            s = b.stats
            stats.append(f"<b>{slot.upper()}</b> {b.model} &mdash; {b.link}"
                         f"{(' (' + b.error + ')') if b.error else ''} &mdash; sent {s['tx']}, "
                         f"replies {s['rx']}, timeouts {s['timeouts']}, errors {s['errors']}")
            if (slot == "main" and self.show_main.isChecked()) or (slot == "sub" and self.show_sub.isChecked()):
                for t, d, text in list(b.log)[-250:]:
                    lines.append((t, f"{time.strftime('%H:%M:%S', time.localtime(t))}.{int(t * 1000) % 1000:03d} "
                                     f"{slot.upper():4s} {d:3s} {text}"))
        self.stats.setText("<br>".join(stats))
        if not self.pause.isChecked():
            lines.sort()
            self.log.setPlainText("\n".join(l for _, l in lines[-400:]))
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
