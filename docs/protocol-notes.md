# Protocol notes

## Yaesu FT-710 (direct CAT)

Verified on the real radio 2026-09-27 (COM5, CP2105 serial 01ABC337,
38400 baud, `ID;` → `ID0800;`). Each command was set, read back, then
restored.

| Function | Read | Set | Notes |
|---|---|---|---|
| VFO / mode / RIT flags | `IF;` (28 bytes) | `FA%09d;` `MD0x;` | Frequency is polled from `IF;`, see quirk 2 |
| VFO-B | `FB;` | `FB%09d;` | |
| Clarifier on/off | `CF000;` → `CF000RT000;` | `CF000<rx><tx>000;` | e.g. `CF00010000;` = RIT on |
| Clarifier offset | `CF001;` → `CF001+0120;` | `CF001±nnnn;` | shared by RIT and XIT |
| Width | `SH0;` → `SH0020;` | `SH00nn;` | SSB table index 20 = 3.0 kHz (Hamlib ftdx101 tables) |
| Narrow | `NA0;` | `NA0n;` | |
| ATT | `RA0;` | `RA0n;` n = 0-3 (OFF/6/12/18 dB) | |
| IPO/AMP | `PA0;` | `PA0n;` n = 0-2 (IPO/AMP1/AMP2) | |
| AGC | `GT0;` | `GT0n;` n = 0-4 | `GT04;` (AUTO) reads back as 6 |
| NB / DNR / DNF / NOTCH / PROC / MON | `NB0;` `NR0;` `BC0;` `BP00;` `PR0;` `ML0;` | `NB0n;` `NR0n;` `BC0n;` `BP0000n;` `PR0n;` `ML000n;` | |
| Split | `ST;` | `STn;` | |
| ATU | `AC;` → `AC001;` | `AC000;`/`AC001;`, tune `AC003;` | tune **not** exercised (transmits) |
| AF / RF / SQL / power / MON level | `AG0;` `RG0;` `SQ0;` `PC;` `ML1;` | same + value | AF/RF 0-255, SQL/MON 0-100, PC 5-100 |
| Meters | `RM1;` S, `RM5;` PO, `RM6;` SWR, `RM4;` ALC, `RM3;` COMP | | 10-char answer with a `000` tail |
| PTT | `TX;` | `TX1;` / `TX0;` | **not** keyed during testing |

Not available over CAT: squelch/busy (`BY;` answers `?;`, and `RI0;` doesn't
change with squelch on SSB), antenna select (single antenna), `RT;`/`XT;`
(the FT-710 uses `CF` instead).

**Quirk 1: a frequency write clears the RIT offset.** Any `FA…;` write sets
the clarifier offset back to 0000. The driver re-sends `CF001±nnnn;` after
every frequency write while RIT or XIT is on, so wheel-tuning keeps your offset.

**Quirk 2: `FA;` includes the RIT offset.** With RIT on at +120 Hz, `FA;`
reads 7.093130 while `IF;` reads the VFO as 7.093010 with the offset in its
own field. The driver polls frequency from `IF;` so tuning doesn't drift by
the offset.

## Yaesu FT-991A (direct CAT)

Bench 2026-09-27 on COM31 (CP2105 serial 0121D76D), 38400 baud, RTS asserted, `ID0670`.
The radio sat at 442.200 MHz C4FM on memory 083, so only commands valid there were
round-tripped: PA0, NB0, ML1, AG0, RG0, SQ0, PC, RT/XT, and the clarifier. All were
set, read back and restored.
* Split: no `ST;` (answers `?`). `FT3;` = TX on VFO-B (reads back `FT1;`), `FT2;` restores (`FT0;`).
* BUSY: `BY;` answers `BY00;`/`BY10;`, so the FT-991A gets a working BUSY light.
* Clarifier: `RT0/1`, `XT0/1`. `RU`/`RD` are RELATIVE (RU0120 then RD0050 = +0070), so the
  driver sends `RC;` first.
* Same two quirks as the FT-710: `FA;` reads VFO + RIT offset, and an `FA` write zeroes the
  offset. An `FA` write also drops the radio out of memory / memory-tune into VFO.
* Refused in C4FM on 70 cm (mode or band limits, still to check on HF SSB): RA0, NR0, BC0,
  NA0, BP00 set, PR01, AC001 (the ATU is HF/6 m only). GT0 and ML0 sets were accepted but not applied.

## Icom IC-705 (direct CI-V)

Verified on the real radio 2026-09-27 (COM4, "IC-705 Serial Port A (CI-V)", 115200,
address A4h). CI-V USB Echo Back is ON on this radio; the driver ignores frames
from E0. Freq/mode/data/width/S-meter/squelch/levels/AGC/ATT/P.AMP/NB/NR/ANF/MN/
TBW/RIT all set, read back and restored (split and PTT not exercised). DV (`06 17`) selects fine with or without a filter byte; in DV the radio NGs `1A 03` (width) and `25 01` (VFO-B), which is harmless. `1C 01` (tuner) answers FA, so there
is no ATU button. Interlocks: P.AMP on turns ATT off; MN turns ANF off.
Calibration: Hamlib ic7300.c IC-705 PO table (10 W) and flrig COMP table.

### IC-705 spectrum scope (`27` commands)

Captured from the real radio: after `27 11 01` (CI-V data output on) it streams
`27 00` sweeps about 6 per second over USB. Each sweep is 11 divisions: division 1 is the header
(scope id, div, max-div, mode, 5-byte freq, 5-byte freq, out-of-range). In centre
mode the two values are the centre and the half-span; in fixed mode they are the edges. Divisions
2-10 carry 50 points and division 11 carries 25 (475 total), each 0-160. Span set/read `27 15 00 <half-span>`,
mode `27 14 00 <0 = centre / 1 = fixed>`, ref `27 19 00 <BCD dB×100, 2 bytes> <sign>`
(e.g. `07 50 01` = -7.5 dB). The driver restores `27 11 00` (and `27 10 00` if it
had to switch the scope on) when it disconnects; restoring was verified.

**IC-7610 (2026-09-30, from Hamlib ic7610.c, not yet seen on the radio):** same `27` commands and
spans; a sweep is 689 points and levels run 0-200. It has MAIN and SUB scopes: the scope id
(first payload byte) is 0 for MAIN, 1 for SUB, and only id 0 sweeps are assembled.

## Icom CI-V (IC-7610 and family)

Command numbers from Hamlib `icom_defs.h` / `ic7610.c`. Tested against
`tests/fake_icom.py` over a com0com pair: 2,838 commands, 0 timeouts. Not yet
tested on the real IC-7610.

* Frequencies: 5 BCD bytes, least-significant first. Levels: 2 BCD bytes 0000-0255.
* RIT/XIT offset `21 00`: 2 BCD bytes least-significant first, then sign (01 = minus).
* VFO-B / split TX frequency uses `25 01` (unselected VFO).
* Data mode `1A 06 <on> <filter>`; filter width index `1A 03` (0-9 = 50-500 Hz, 10-40 = 600-3600 Hz).
* DIGI-SEL `16 4E`, IP+ `1A 07`, SSB TX bandwidth `16 58`, ANT `12 <ant> <rx-ant>`.
* With CI-V Transceive on, knob moves arrive as unsolicited `00` frames and are applied immediately.
* **Framing bug fixed during development:** a serial read that ended on a lone `FE`
  used to discard it, losing the next reply. It is now covered by a unit test.

## Rig Deck as a rigctld server (`rigdeck/share.py`)

Serves Hamlib's NET rigctl client (rig model 2). The client opens with `\chk_vfo`
(answered `0`, so it never sends VFO arguments) and then `\dump_state`, which it parses
line by line; the reply copies the layout of a real rigctld 4.7.1 (captured from
`rigctld -m 1`): protocol 1, rig model, ITU region, RX/TX ranges each ended by a zero line,
tuning steps, filters, max RIT/XIT/IF shift, announces, preamp and attenuator lists, 6 capability
masks, then `key=value` lines ending with `done`. The masks advertise everything; an unsupported
request answers `RPRT -11`, which is what rigctld does too. `ptt_type=0x1` (CAT PTT).
Implemented: `f F m M v V t T s S i I x X j J z Z l L u U _ q \get_powerstat \set_powerstat
\get_dcd`, plain and `+` extended replies, long names (`\get_freq`, `get_freq`). Mode names
map both driver families: CW-U / CW = `CW`, CW-L / CW-R = `CWR`, RTTY-L / RTTY = `RTTY`,
USB+DATA = `PKTUSB`, DV = `DSTAR`, AM-N = `AMN`, FM-N = `FMN`. Levels served: STRENGTH,
RFPOWER, RFPOWER_METER(_WATTS), SWR, ALC, COMP_METER, VD_METER, ID_METER (Icom, polled
`15 15` / `15 16` with Hamlib's tables), AF, RF, SQL, MONITOR_GAIN, PREAMP, ATT, AGC.
When the radio isn't LIVE every get answers `RPRT -6`, so a client can't act on stale data.

## Hamlib (rigctld)

The extended protocol (`+` prefix) is used so every reply ends in `RPRT n`.
Capabilities come from `\dump_caps` (Get/Set level and function lists,
Preamp/Attenuator/AGC lines, mode list). Tested against `rigctld -m 1` (the dummy rig).
