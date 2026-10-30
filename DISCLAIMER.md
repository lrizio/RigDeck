# Disclaimer

Rig Deck is a free, non-commercial hobby project written by an amateur radio operator for other
amateurs. By downloading, installing or using it you accept the terms below. If you do not accept them,
do not use the software.

## No warranty

Rig Deck and its documentation are provided **"as is" and "as available"**, without warranty of
any kind, express or implied, including fitness for a particular purpose, accuracy, reliability,
availability or non-infringement. The full terms are in the [MIT License](LICENSE).

## Limitation of liability

To the maximum extent permitted by law, the author and contributors are not liable for any direct,
indirect, incidental, special, consequential or other loss or damage arising from the use of, or
inability to use, Rig Deck. This includes loss of data, damage to radios, amplifiers, antennas or other equipment, unintended transmissions, interference,
licence infringements, injury or property damage.
You use it entirely at your own risk. Nothing here excludes any right that cannot be excluded under the
consumer law that applies to you.

## Controlling a radio

Rig Deck sends commands to your radio over USB/serial and can **transmit** (PTT, TUNE/ATU, VOX) and change any setting
the radio exposes. Check the power, mode, frequency, antenna and band before you key a transmitter. Never transmit into
a missing or unsuitable antenna, and never run an amplifier without understanding its limits. A **PTT time-out**
(default 180 s) releases PTT automatically, but it is a safety net, not a guarantee. You are solely responsible for
everything your station transmits and for operating within your licence and the regulations of your country.

Rig Deck must not be used for safety-of-life purposes. Only the IC-705, FT-710 and FT-991A have been bench-tested by the
author, and then only on the receive side; the other models listed are implemented from published documentation
(Hamlib sources) and have **never** been tried on a real radio. Try new functions at low power into a dummy load first.
Radio settings it changes may not be restored automatically: note down anything you care about first.

## Sharing with other programs

When sharing is on (the default), Rig Deck listens for rigctld-protocol connections on `127.0.0.1` (this PC only) so
programs such as JTDX can use the radios. Any program on your PC that connects can read and change the radio and key
PTT. Switch sharing off in SETUP if you do not want this. Rig Deck may also close a `rigctld.exe` that is holding the
COM port of a shared radio, so that it can take the port over.

## Third-party software

Rig Deck is built with open-source libraries (PySide6/Qt and pyserial) and can start Hamlib's `rigctld` if you
have it installed. Their licences apply to them. Rig Deck is **not affiliated with or endorsed by** any radio
manufacturer, Hamlib, or any other program it can work with. All product names and trademarks (Icom, Yaesu,
IC-705, FT-710 and others) belong to their owners and are used only to identify compatible hardware.

## Network use and privacy

* Rig Deck does not collect telemetry, analytics or personal data and has no server of its own. Its only
  internet use is an optional update check: shortly after it starts it makes one small request to GitHub's public
  "latest release" record for this project (GitHub sees your IP address, as for any web request) and shows a notice if a
  newer version exists. Nothing is downloaded or installed. Untick the box in **SETUP** to turn it off. The author
  receives nothing from your copy.
* The only other network use is the optional local share ports described above. Settings are stored in
  `%APPDATA%\RigDeck\config.json`.

## Unsigned software

The Windows program is not code-signed, so Windows SmartScreen may warn you the first time you run it. Only
download Rig Deck from this project's official GitHub page. If you prefer, you can run it directly from the source code.

## Changes

These terms may be updated with new releases. The version included with the release you downloaded applies
to that release.
