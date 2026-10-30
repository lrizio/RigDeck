"""Take a radio back from a stand-alone rigctld.

If ICOMNodeDisplay (or a rigctld started by hand) was running before Rig Deck,
its rigctld holds the radio's COM port, so Rig Deck cannot reach the radio.
Rather than make the operator close things in the right order, Rig Deck
closes that rigctld and takes its place: it answers the same protocol on the
same TCP port, so the programs that were using rigctld simply reconnect to
Rig Deck.

Only a process named rigctld.exe that was started on the serial port Rig Deck
is waiting for is ever closed; anything else holding the port (wfview, JTDX
with a direct COM port...) is left alone and the radio stays at ERROR.

(The share port is no use as a trigger: rigctld listens on every address, and
Windows still lets Rig Deck bind 127.0.0.1:4532 next to it.)
"""
from __future__ import annotations

import os
import re
import subprocess

_LIST = ("Get-CimInstance Win32_Process -Filter \"Name='rigctld.exe'\" | "
         "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def norm_port(name: str) -> str:
    """'\\\\.\\com4' / 'com4' -> 'COM4'."""
    name = (name or "").strip().strip('"')
    if name.startswith("\\\\.\\"):
        name = name[4:]
    return name.upper()


def rigctld_serial_port(cmdline: str) -> str:
    """The serial port a rigctld command line names (-r COM4 / --rig-file=COM4)."""
    m = re.search(r"(?:\s-r\s*|\s--rig-file[=\s]\s*)(\"[^\"]+\"|\S+)", cmdline or "")
    return norm_port(m.group(1)) if m else ""


def port_is_busy(error: str) -> bool:
    """Does a backend's open() error mean another program holds the COM port?"""
    e = (error or "").lower()
    return "in use" in e or "denied" in e or "permissionerror" in e


def rigctlds() -> list[tuple[int, str]]:
    """(pid, command line) of every running rigctld."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _LIST],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        pid, _, cmdline = line.strip().partition("|")
        if pid.isdigit():
            found.append((int(pid), cmdline))
    return found


def holders(procs: list[tuple[int, str]], serial_port: str) -> list[int]:
    """PIDs of the rigctlds that were started on `serial_port`."""
    want = norm_port(serial_port)
    return [pid for pid, cmdline in procs if want and rigctld_serial_port(cmdline) == want]


def take_over(serial_port: str) -> str:
    """Close any rigctld holding `serial_port`. Returns a line for the log
    ("" when nothing was closed)."""
    pids = holders(rigctlds(), serial_port)
    closed = []
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20, creationflags=_NO_WINDOW)
            closed.append(str(pid))
        except (OSError, subprocess.SubprocessError):
            pass
    if not closed:
        return ""
    return f"closed rigctld (PID {', '.join(closed)}) that held {norm_port(serial_port)}"
