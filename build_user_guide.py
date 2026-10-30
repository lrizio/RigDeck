#!/usr/bin/env python3
"""Generates build/Rig Deck User Guide.pdf -- the end-user guide for Rig Deck.

Same structure and styling as the other HamProjects guides (Yaesu Radio
Controller / FT-710 Settings Manager build_user_guide.py). Screenshots come
from docs/guide/, rendered by scripts/guide_screens.py.

Usage (from this project's root):
    python build_user_guide.py

Requires: pip install reportlab pillow (build-time only).
"""
from __future__ import annotations

import os

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, ListFlowable, ListItem, PageBreak,
                                Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

from rigdeck.config import VERSION

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(ROOT, "build", "Rig Deck User Guide.pdf")
SHOTS = os.path.join(ROOT, "docs", "guide")

ACCENT = colors.HexColor("#1565c0")
CALLOUT_BG = colors.HexColor("#fff3e0")
CALLOUT_BORDER = colors.HexColor("#e0a030")
NOTE_BG = colors.HexColor("#e8f0fe")
NOTE_BORDER = colors.HexColor("#8ab4f8")
TABLE_HEADER_BG = colors.HexColor("#eeeeee")
MUTED = colors.HexColor("#555555")
CONTENT_W = 7.2 * inch

styles = getSampleStyleSheet()
style_title = ParagraphStyle("GuideTitle", parent=styles["Title"], fontSize=26, leading=30, spaceAfter=2)
style_subtitle = ParagraphStyle("GuideSubtitle", parent=styles["Normal"], fontSize=11, leading=14,
                                textColor=MUTED, alignment=TA_CENTER, spaceAfter=14)
style_body = ParagraphStyle("GuideBody", parent=styles["Normal"], fontSize=10, leading=13, spaceAfter=5)
style_h2 = ParagraphStyle("GuideH2", parent=styles["Heading2"], fontSize=12.5, leading=14, textColor=ACCENT,
                          spaceBefore=8, spaceAfter=3)
style_h3 = ParagraphStyle("GuideH3", parent=styles["Heading3"], fontSize=10.5, leading=13,
                          textColor=colors.HexColor("#333333"), spaceBefore=5, spaceAfter=2)
style_bullet = ParagraphStyle("GuideBullet", parent=styles["Normal"], fontSize=9.5, leading=12)
style_note = ParagraphStyle("GuideNote", parent=styles["Normal"], fontSize=9.5, leading=13)
style_cell = ParagraphStyle("GuideCell", parent=styles["Normal"], fontSize=9, leading=11.5)
style_head = ParagraphStyle("GuideHead", parent=style_cell, fontName="Helvetica-Bold")
style_caption = ParagraphStyle("GuideCaption", parent=styles["Normal"], fontSize=8.5, leading=11,
                               textColor=MUTED, alignment=TA_CENTER, spaceBefore=2, spaceAfter=8)
style_footer = ParagraphStyle("GuideFooter", parent=styles["Normal"], fontSize=8, leading=11, textColor=MUTED)


def P(text, style=style_body):
    return Paragraph(text, style)


def bullets(items):
    return ListFlowable([ListItem(Paragraph(i, style_bullet), spaceAfter=2.5) for i in items],
                        bulletType="bullet", bulletFontSize=7, leftIndent=14, bulletOffsetY=1)


def numbered(items):
    return ListFlowable([ListItem(Paragraph(i, style_bullet), spaceAfter=4) for i in items],
                        bulletType="1", leftIndent=18)


def box(text, bg=NOTE_BG, border=NOTE_BORDER):
    return Table([[Paragraph(text, style_note)]], colWidths=[CONTENT_W],
                 style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("BOX", (0, 0), (-1, -1), 1, border),
                                   ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                   ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))


def warn(text):
    return box(text, CALLOUT_BG, CALLOUT_BORDER)


def table(rows, widths):
    data = [[Paragraph(c, style_head) for c in rows[0]]] + \
           [[Paragraph(c, style_cell) for c in r] for r in rows[1:]]
    return Table(data, colWidths=[w * inch for w in widths], repeatRows=1,
                 style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                                   ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                   ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))


def img(name, width):
    path = os.path.join(SHOTS, name)
    w, h = PILImage.open(path).size
    return Image(path, width=width, height=width * h / w)


def shot(name, width=CONTENT_W, caption=None):
    img_ = img(name, width)
    parts = [img_]
    if caption:
        parts.append(Paragraph(caption, style_caption))
    return KeepTogether(parts)


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.65 * inch, 0.3 * inch, "Rig Deck - User Guide")
    canvas.drawRightString(LETTER[0] - 0.65 * inch, 0.3 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build():
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    doc = SimpleDocTemplate(OUT_PATH, pagesize=LETTER, title="Rig Deck - User Guide", author="Rig Deck",
                            topMargin=0.45 * inch, bottomMargin=0.55 * inch,
                            leftMargin=0.65 * inch, rightMargin=0.65 * inch)
    f = []
    f.append(P("Rig Deck", style_title))
    f.append(P(f"User Guide | Version {VERSION} | Two-radio control console for Windows", style_subtitle))
    f.append(Table([[""]], colWidths=[CONTENT_W], rowHeights=[1],
                   style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1, colors.HexColor("#cccccc"))])))
    f.append(Spacer(1, 8))
    f.append(P(
        "Rig Deck puts two radios side by side in one window. It is set up for an <b>Icom IC-705</b> "
        "on the left (MAIN) and a <b>Yaesu FT-710</b> on the right (SUB), and can also run a "
        "<b>Yaesu FT-991A</b>, 17 Icom and 7 Yaesu models in all, or any radio Hamlib supports. Each radio gets its "
        "own frequency readout, S-meter and status lines; one shared set of buttons controls whichever "
        "radio you select. The IC-705 and IC-7610 also get a live spectrum scope and waterfall."))
    f.append(shot("console.png", caption="The Rig Deck console (demo radios shown). MAIN is selected, so "
                                           "the buttons act on the IC-705."))

    # ---------------------------------------------------------------------
    f.append(P("What You Need", style_h2))
    f.append(bullets([
        "A Windows 10 or 11 PC.",
        "One or two radios connected by USB. Supported directly (no extra software):",
    ]))
    f.append(table([
        ["Radio", "Connection", "Notes"],
        ["Icom IC-705", "USB, \"IC-705 Serial Port A (CI-V)\"", "Found automatically. 115200 baud, CI-V address A4."],
        ["Yaesu FT-710", "USB, CP2105 <b>Enhanced</b> COM port", "38400 baud; radio menu CAT-1 RATE must match."],
        ["Yaesu FT-991A", "USB, CP2105 <b>Enhanced</b> COM port", "38400 baud; radio menu 031 CAT RATE must match."],
        ["Other Icom: IC-7300, 7300MK2, 7610, 7760, 7850/7851, 7800, 7700, 7600, 7410, 7200, 7100, 7000, "
         "9700, 9100, 905", "USB CI-V port",
         "Same Icom driver; address and baud filled in per model. Not yet tried on those radios."],
        ["Other Yaesu: FTDX10, FTDX101D, FTDX101MP, FT-991, FT-891", "USB, CP2105 <b>Enhanced</b> COM port",
         "Same Yaesu driver, per-model details from Hamlib. Not yet tried on those radios. "
         "Older Yaesus (FT-950, FTDX1200/3000/5000, FT-450D) go through Hamlib."],
        ["Any other radio", "Hamlib rigctld", "Needs Hamlib installed (it is on this PC). The buttons shown are "
                                              "the ones Hamlib says the radio supports."],
    ], [1.6, 2.2, 3.4]))
    f.append(Spacer(1, 4))
    f.append(box("Only one program can use a radio's COM port at a time. Close ICOMNodeDisplay, "
                 "Yaesu Radio Controller, the Settings Manager, WSJT-X/flrig or anything else talking to "
                 "the same radio before starting Rig Deck, or that side will show <b>ERROR ... in use by "
                 "another program</b>."))

    # ---------------------------------------------------------------------
    f.append(P("Starting Rig Deck", style_h2))
    f.append(bullets([
        "Double-click the <b>Rig Deck</b> shortcut on the Desktop (it runs <i>build\\Rig_Deck.exe</i>).",
        "Each radio panel's title has a small LED: <font color='#2e9e5b'><b>green</b></font> = connected, "
        "<font color='#d08a00'><b>amber</b></font> = port open but the radio isn't answering (usually switched "
        "off), <font color='#c62828'><b>red</b></font> = error (message shown next to it). The top bar sums it "
        "up, e.g. <b>MAIN LIVE &bull; SUB LIVE</b>.",
        "Rig Deck keeps retrying on its own. Switch a radio on and it connects within a few seconds.",
        "To try the console without radios, run <b>Rig_Deck.exe --demo</b>: both sides become simulated "
        "radios and your real settings are left alone.",
    ]))

    # ---------------------------------------------------------------------
    f.append(P("First-Time Setup", style_h2))
    f.append(P("Click <b>SETUP</b> in the top bar. Choose the radio for each side, then click <b>Save</b>; "
               "the console reconnects straight away."))
    f.append(shot("setup.png", width=5.6 * inch, caption="Setup: only the fields that apply to the chosen "
                                                         "connection are shown."))
    f.append(table([
        ["Field", "What to enter"],
        ["Connection", "<b>Icom CI-V (direct)</b>, <b>Yaesu CAT (direct)</b>, <b>Hamlib rigctld (any radio)</b>, "
                       "<b>Simulator</b>, or <b>Not used</b> (that side stays empty)."],
        ["Icom model / Yaesu model", "Pick the exact radio from the list. Buttons the model doesn't have "
                                     "are greyed out on the deck."],
        ["Hamlib radio", "Type part of the name or number to search Hamlib's list (e.g. <i>TS-590</i>)."],
        ["Serial port", "The radio's COM port. For Icom radios you can leave it blank: Rig Deck picks the port "
                        "named after the radio. For Yaesu radios use the <b>Enhanced</b> port of the pair "
                        "(this PC: FT-710 = COM5, FT-991A = COM31)."],
        ["Baud", "Follows the radio automatically (Yaesu 38400; Icom 115200, or 19200 for older models such as "
                 "the IC-7600/7410/7200/7100) unless you change it. It must match the radio's CAT/CI-V rate "
                 "setting."],
        ["CI-V address", "Icom only. Leave blank for the model's default (IC-705 = A4)."],
        ["rigctld host : port", "Hamlib only. With a serial port set, Rig Deck starts its own hidden rigctld on "
                                "this TCP port. Leave the serial port blank to join a rigctld that is already "
                                "running."],
        ["Callsign", "Shown in the title bar."],
        ["PTT time-out", "Transmit is released automatically after this many seconds (default 180)."],
    ], [1.7, 5.5]))

    # ---------------------------------------------------------------------
    f.append(CondPageBreak(3 * inch))
    f.append(P("A Tour of the Screen", style_h2))
    f.append(P("Radio panels (MAIN left, SUB right)", style_h3))
    f.append(shot("panel.png", width=4.2 * inch))
    f.append(bullets([
        "<b>Title</b>: side, radio model and connection LED. The selected radio's panel has a coloured border.",
        "<b>Mode</b> (top left), <b>INFO</b> (connection details) and the <b>BUSY</b> lamp (lit green when "
        "the squelch is open; it shows <b>TX</b> in red while transmitting). The FT-710 does not report "
        "squelch over CAT, so its BUSY lamp stays grey.",
        "<b>Frequency</b>: up to 9 digits (e.g. 434.300.000). Scroll the mouse wheel over any digit to tune "
        "that digit; a small bar under the digit shows which one you are on. Double-click to type a frequency.",
        "<b>Status lines</b>: antenna, AGC, preamp, filter, noise functions, RIT/XIT, ATT, notch and "
        "VFO/MEM/SPLIT. Lit text = on; dim = off; &mdash; = the radio does not have it.",
        "<b>Meter</b>: S-meter while receiving, power output while transmitting.",
    ]))
    f.append(P("Centre panel", style_h3))
    f.append(Table([[img("center.png", 2.4 * inch),
                     bullets([
                         "<b>POWER</b> of the selected radio, and both radios' frequencies.",
                         "<b>SIMPLEX</b> or <b>SPLIT TX</b> with the transmit frequency, and XIT status.",
                         "The <b>TX meter</b> (ALC, PO, SWR or COMP, chosen with the METER key).",
                         "<b>&#9660; ENT &#9650;</b>: &#9660;/&#9650; tune the selected radio in 1 kHz steps, or "
                         "adjust the item picked with PWR, RF GAIN, SQL, AF or REF (its name then shows "
                         "on the ENT key). ENT goes back to tuning; when already tuning, ENT opens "
                         "frequency entry.",
                     ])]], colWidths=[2.6 * inch, 4.6 * inch],
                   style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    f.append(P("Scope (IC-705, IC-7610)", style_h3))
    f.append(shot("scope.png"))
    f.append(bullets([
        "The radio's own spectrum scope, sent over the USB cable: the live trace on top, a scrolling "
        "waterfall below (contrast follows the noise floor, so only real signals light up). The shaded band is your receive passband; the coloured line is the radio's "
        "frequency, and a second line marks the other radio when it is in view.",
        "<b>Click</b> the scope to tune the radio there; <b>scroll</b> over it to tune in 100 Hz steps.",
        "With an IC-705 and an IC-7610 both on the deck, the scope shows the <b>selected</b> radio and "
        "switches when you press MAIN / SUB (or Tab). The IC-7610 shows its MAIN-receiver scope.",
        "<b>SPAN &lsaquo; &rsaquo;</b> 5 kHz to 1 MHz &bull; <b>CTR / FIX</b> centre or fixed-edge mode "
        "&bull; <b>REF &lsaquo; &rsaquo;</b> reference level -20 to +20 dB (raise it if the trace is flat).",
        "Rig Deck switches the radio's scope data output on while connected and back off when it closes. "
        "The FT-710 and FT-991A cannot send scope data over CAT, so they have no scope: when one of them is "
        "selected, the scope panel is blanked (\"has no spectrum scope\") instead of showing the other radio's.",
    ]))
    f.append(P("Band scale", style_h3))
    f.append(shot("band.png"))
    f.append(P("Follows the selected radio. Blue = inside the amateur band, red = outside (Region 3 / VK "
               "limits). The triangles mark MAIN (above) and SUB (below). Click to move the selected radio "
               "there; scroll to tune it in 1 kHz steps."))

    # ---------------------------------------------------------------------
    f.append(CondPageBreak(4 * inch))
    f.append(P("The Button Deck", style_h2))
    f.append(shot("deck.png"))
    f.append(box("The deck always works on the <b>selected radio</b>: click <b>MAIN</b> or <b>SUB</b>, click "
                 "anywhere on a radio panel, or press <b>Tab</b>. Key labels change to that radio's own names "
                 "(e.g. COMP on the Icom, PROC on the Yaesu). Keys for things the radio does not have are "
                 "greyed out."))
    f.append(Spacer(1, 4))
    f.append(P("How the keys work", style_h3))
    f.append(bullets([
        "<b>Click</b> a key to switch it on/off or step through its choices. Keys with a small <b>LED</b> "
        "show on/off at a glance.",
        "Keys with <b>&lsaquo; &rsaquo;</b> arrows: click the middle to switch, click an arrow (or scroll the "
        "wheel over the key) to adjust.",
        "<b>Right-click</b> AGC, ATT, PRE AMP, ANT, TX FILTER or SPAN for a list to pick from.",
        "The small text under a key shows its current value.",
    ]))
    f.append(table([
        ["Key", "What it does", "Icom / Yaesu names"],
        ["MAIN / SUB", "Select which radio the deck controls.", ""],
        ["PWR", "Select transmit power for &#9660;/&#9650; (or scroll over the key).", ""],
        ["METER", "Choose the centre TX meter: PO, ALC, SWR, COMP.", ""],
        ["&lsaquo; RIT &rsaquo;", "RIT on/off; arrows move the offset &plusmn;10 Hz.", "Yaesu: clarifier"],
        ["CLR", "Clear the RIT/XIT offset.", ""],
        ["&lsaquo; XIT &rsaquo;", "XIT on/off; arrows move the offset.", "Icom: &Delta;TX"],
        ["ATU", "Tuner in/out. <b>Press and hold</b> (or right-click) to start a tune cycle (transmits).", "Not on the IC-705"],
        ["&lsaquo; SPLIT &rsaquo;", "Split on/off (starts 1 kHz up if VFO-B is far away); arrows move the TX "
                                    "frequency &plusmn;1 kHz.", ""],
        ["PTT", "Transmit on the selected radio; click again to stop.", "See Transmitting"],
        ["ANT / RX ANT", "Antenna select / receive antenna.", "IC-7610 only"],
        ["AGC", "Step through AGC speeds.", ""],
        ["ATT", "Attenuator.", "IC-705 20 dB; FT-710 6/12/18 dB; FT-991A 12 dB"],
        ["PRE AMP", "Preamp.", "Icom P.AMP1/2; Yaesu IPO/AMP1/AMP2"],
        ["PSEL", "Preselector.", "IC-7610 DIGI-SEL"],
        ["&lsaquo; RX FILTER &rsaquo;", "Click: Icom FIL1/2/3, Yaesu NARROW. Arrows: filter width.", ""],
        ["TX FILTER", "SSB transmit bandwidth.", "Icom TBW WIDE/MID/NAR"],
        ["M &gt; S", "Copy MAIN's frequency and mode to SUB.", ""],
        ["M / S", "Swap MAIN and SUB frequencies and modes.", ""],
        ["TRACK", "SUB follows MAIN's tuning (same step, any direction).", ""],
        ["MODE", "Pick a mode from the list.", "IC-705 includes DV and WFM; FT-991A includes C4FM"],
        ["DATA", "Data mode for SSB/AM/FM (USB-D, DATA-U&hellip;).", ""],
        ["VOX / PROC", "VOX; speech processor.", "Icom COMP, Yaesu PROC"],
        ["&lsaquo; MON &rsaquo;", "Transmit monitor on/off; arrows set its level.", "Icom MONI"],
        ["&lsaquo; AF &rsaquo;", "Audio gain (arrows or &#9660;/&#9650; after clicking).", ""],
        ["RF GAIN / SQL", "Select for &#9660;/&#9650; (or scroll over the key).", ""],
        ["MUTE", "Mute the selected radio's audio; click again (or close Rig Deck) to restore.", ""],
        ["NR / NB1 / NB2", "Noise reduction / noise blanker.", "Yaesu DNR; Icom NB2 = IP+ (IC-7300/7610)"],
        ["NOTCH / BC", "Manual notch / auto notch.", "Icom MN/ANF; Yaesu NOTCH/DNF"],
    ], [1.3, 3.6, 2.3]))

    # ---------------------------------------------------------------------
    f.append(P("Transmitting", style_h2))
    f.append(bullets([
        "Click <b>PTT</b> to transmit on the selected radio; the key turns red and shows <b>TX</b>, the panel "
        "meter shows power and the centre meter shows the chosen TX meter. Click again to stop.",
        "Only one radio can transmit: keying one releases the other.",
        "PTT is released automatically after the time-out set in SETUP (default 180 s), and when Rig Deck "
        "closes or loses the radio.",
        "The ATU tune cycle (hold ATU) also transmits.",
        "The Icom driver holds the USB port's DTR and RTS lines low, so the radio's USB SEND/keying settings "
        "can never key it by accident.",
    ]))
    f.append(warn("<b>Test PTT into a dummy load first.</b> Every receive control has been checked on the "
                  "real radios, but PTT, the tune cycle and VOX were deliberately never keyed during "
                  "development. Try each radio's PTT once into a dummy load before using it on air."))

    # ---------------------------------------------------------------------
    f.append(CondPageBreak(3.2 * inch))
    f.append(P("Sharing a Radio with Other Programs", style_h2))
    f.append(P("Only one program can open a radio's COM port. Rig Deck holds it and shares each radio "
               "over the network, the same way Hamlib's rigctld does, so JTDX, WSJT-X, fldigi, loggers and "
               "ICOMNodeDisplay can use the radio while Rig Deck is running. Nothing extra to install."))
    f.append(table([
        ["In the other program", "Set"],
        ["Rig / radio", "<b>Hamlib NET rigctl</b>"],
        ["Network server", "<b>127.0.0.1:4532</b> for the MAIN radio, <b>127.0.0.1:4533</b> for SUB"],
        ["PTT method", "<b>CAT</b>"],
    ], [2.2, 5.0]))
    f.append(bullets([
        "The top bar shows <b>SHARED :4532 :4533</b>; the number in brackets is "
        "how many programs are connected, e.g. <b>:4532(2)</b>.",
        "The other program's settings stay the same when you change radios in Rig Deck: it simply "
        "talks to whatever radio is on MAIN (or SUB).",
        "PTT from the other program follows Rig Deck's rules: only one radio transmits at a time, and "
        "the PTT time-out applies.",
        "<b>ICOMNodeDisplay can be started before or after Rig Deck.</b> Started after, it notices Rig "
        "Deck and uses it instead of its own rigctld. Started before, Rig Deck closes ICOMNodeDisplay's "
        "rigctld to get the radio's COM port (the radio shows ERROR for a few seconds, then LIVE) and the "
        "display carries on through Rig Deck. Only a rigctld on that radio's own COM port is ever closed.",
        "A program set to open the radio's COM port directly (not Hamlib NET rigctl) will always clash "
        "with Rig Deck: only one program can have a COM port open.",
        "<b>:4532 IN USE</b> means another program already has the port. Close it, or change the ports "
        "in SETUP. Sharing can be switched off there too.",
    ]))
    f.append(P("<b>Update check.</b> About four seconds after start, Rig Deck asks GitHub whether a newer release exists "
               "(one small request; nothing is downloaded or installed). If there is one, an amber <b>UPDATE AVAILABLE</b> "
               "button appears in the top bar and opens the download page. Untick the box in SETUP to switch this off."))

    f.append(P("Setting up FreeDV", style_h3))
    f.append(P("In FreeDV open <b>Tools &rarr; PTT Config</b>, tick <b>Use Hamlib PTT</b>, choose rig model "
               "<b>Hamlib NET rigctl</b> and type <b>127.0.0.1:4532</b> (MAIN) as the serial device / port. "
               "Leave the serial-port PTT options unticked. Audio is unchanged: FreeDV still uses the "
               "radio's USB sound card."))

    f.append(CondPageBreak(4.5 * inch))
    f.append(P("Setting up JTDX", style_h3))
    f.append(P("Start <b>Rig Deck</b> and wait until the top bar shows <b>MAIN LIVE</b> and "
               "<b>SHARED :4532</b>, then start JTDX. ICOMNodeDisplay, if you use it, can be started at "
               "any point."))
    f.append(P("<b>In JTDX, open File &rarr; Settings &rarr; Radio and set:</b>"))
    f.append(table([
        ["JTDX setting", "Value"],
        ["Rig", "<b>Hamlib NET rigctl</b>"],
        ["Network Server", "<b>127.0.0.1:4532</b> for the MAIN radio (<b>127.0.0.1:4533</b> for SUB)"],
        ["PTT Method", "<b>CAT</b>"],
        ["Mode", "<b>Data/Pkt</b> (the radio switches to USB-D / DATA-U), or <b>None</b> to set the mode "
                 "on the radio yourself"],
        ["Split Operation", "<b>Fake It</b> (works on every radio; <b>Rig</b> also works on the IC-705 and "
                            "FT-710)"],
    ], [2.2, 5.0]))
    f.append(Spacer(1, 4))
    f.append(P("Leave the <b>Audio</b> tab as it is: audio still goes through the radio's own USB sound card. "
               "Rig Deck only handles the control side."))
    f.append(P("<b>Check it, then click OK:</b>"))
    f.append(numbered([
        "Click <b>Test CAT</b>. It turns green and JTDX shows the same frequency as Rig Deck. Change band in "
        "JTDX and Rig Deck's readout follows.",
        "With a <b>dummy load</b> connected, click <b>Test PTT</b>. Rig Deck's PTT key turns red and shows TX. "
        "Click it again to stop.",
        "On the IC-705, check that DATA MOD (the data-mode audio source) is set to USB, so the DATA mode "
        "JTDX selects takes its audio from the PC.",
    ]))
    f.append(P("<b>If JTDX misbehaves:</b>"))
    f.append(bullets([
        "<b>Rig control error</b>: Rig Deck isn't running, or that radio isn't LIVE. Rig Deck tells JTDX a "
        "disconnected radio is \"powered off\" on purpose, so JTDX never works from stale readings.",
        "<b>Test CAT fails and Rig Deck shows :4532 IN USE</b>: another program has the port. Close it, "
        "or end a left-over <i>rigctld.exe</i> in Task Manager; Rig Deck picks the port up by itself.",
        "<b>Transmit stops after 3 minutes</b> (e.g. a long Tune): that's Rig Deck's PTT time-out, which applies "
        "to JTDX too. Raise it in SETUP.",
    ]))

    # ---------------------------------------------------------------------
    f.append(P("Window, Themes and Tools", style_h2))
    f.append(bullets([
        "<b>&times;0.8 &hellip; &times;2</b> resize the whole console; <b>FULL</b> (or F11) fills the screen, "
        "Esc returns. Drag the grip in the bottom-right corner to <b>resize</b> the window freely: the console rescales to fit, and the size is remembered (a zoom button resets it). Drag the top bar to move the window; <b>&ndash;</b> minimises.",
        "<b>THEME</b> cycles <b>Shack</b> (the standard HamProjects look), Flight Deck, Amber Night, Phosphor "
        "and Daylight. Zoom, window size and theme are remembered.",
        "<b>GUIDE</b> opens this User Guide in your PDF viewer. The guide is built into Rig Deck, so it is "
        "always the one that matches your version.",
        "<b>DIAG</b> shows the live command traffic to and from both radios, with counts of commands, "
        "replies and timeouts. Useful when a radio is not responding.",
        "<b>INFO</b> on a radio panel shows its port, radio ID, link state and supported controls.",
    ]))

    # ---------------------------------------------------------------------
    f.append(P("Radio-Specific Notes", style_h2))
    f.append(P("Icom IC-705", style_h3))
    f.append(bullets([
        "No internal tuner, so ATU is greyed out. Turning P.AMP on switches ATT off, and MN switches ANF "
        "off; that is the radio's own behaviour.",
        "In DV the radio has no adjustable filter, so RX FILTER does nothing there.",
        "If the IC-705 stops answering (amber LED) while Windows still shows its COM port, plug its USB "
        "cable into a <b>different USB socket</b>; a power cycle alone did not fix it when this happened.",
    ]))
    f.append(P("Yaesu FT-710", style_h3))
    f.append(bullets([
        "Single antenna connector, so ANT/RX ANT are greyed. No squelch status over CAT, so BUSY stays grey.",
        "The radio clears the RIT offset whenever its frequency is set over CAT; Rig Deck puts the offset "
        "back automatically, so tuning with RIT on keeps your offset.",
    ]))
    f.append(P("Yaesu FT-991A", style_h3))
    f.append(bullets([
        "Covers HF, 6 m, 2 m and 70 cm, with C4FM. BUSY works on this radio.",
        "Some functions only exist in some modes/bands: on 70 cm C4FM the radio refuses ATT, DNR, DNF, "
        "NARROW, NOTCH, PROC and the ATU (the ATU is HF/6 m only).",
        "Tuning the FT-991A from Rig Deck takes it out of memory mode into VFO, just as typing a frequency "
        "on the radio does.",
    ]))

    # ---------------------------------------------------------------------
    f.append(P("Troubleshooting", style_h2))
    f.append(table([
        ["Symptom", "What to check"],
        ["ERROR: ... in use by another program", "Another program has the radio's COM port. Close it and "
                                                 "Rig Deck connects within 5 seconds."],
        ["ERROR: no serial port configured", "Set the Serial port in SETUP (Icom radios: make sure the USB "
                                             "cable is plugged in so the port can be found automatically)."],
        ["NO REPLY (amber LED)", "Radio switched off, wrong port (use the Yaesu <b>Enhanced</b> port), or baud "
                                 "not matching the radio's CAT/CI-V rate. IC-705: try another USB socket."],
        ["A key is greyed out", "The selected radio does not have that function, or is not connected. "
                                "Check you have the right radio selected (MAIN/SUB)."],
        ["A key does nothing", "The radio may refuse it in the current mode or band (see Radio-Specific "
                               "Notes). DIAG shows <b>?;</b> (Yaesu) or <b>FA</b> (Icom) for a refused command."],
        ["JTDX / WSJT-X can't connect", "Rig Deck must be running with the radio LIVE. Rig: Hamlib NET "
                                        "rigctl, server 127.0.0.1:4532 (MAIN) or :4533 (SUB), PTT: CAT. "
                                        "The top bar must show SHARED, not IN USE."],
        ["Scope trace is flat", "No signal or no antenna; raise REF. The scope needs the IC-705 or IC-7610 connected."],
    ], [2.2, 5.0]))
    f.append(Spacer(1, 6))
    f.append(P("Settings are stored in <b>%APPDATA%\\RigDeck\\config.json</b>. The band limits used to "
               "colour the band scale are in the same file (<i>band_plan</i>) if you want to change them.",
               style_note))
    doc.build(f, onFirstPage=footer, onLaterPages=footer)
    print("wrote", OUT_PATH)


if __name__ == "__main__":
    build()
