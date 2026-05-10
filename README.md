# LTDZ Mac Analyzer

Experimental native macOS GUI for LTDZ 33/35–4400 MHz spectrum analyzer modules.

This project exists because many cheap LTDZ spectrum analyzer boards are Windows-first and the available software often needs Wine/CrossOver. This tool is a Python/PySide6 GUI with a helper process for reliable serial communication on macOS.

## Project maturity

This project is currently a quick experimental prototype.

It was built in a rapid, vibe-coded way to get a working native macOS workflow for one LTDZ device. Not every function has been fully tested yet, and parts of the code still need cleanup, refactoring, and validation with more hardware variants.

At the moment, it should be treated as:

- experimental
- uncalibrated
- tested mainly on one specific LTDZ device
- useful for exploration and community testing
- not ready as polished measurement software

Contributions, bug reports, protocol findings, cleanup PRs, and tests with other LTDZ variants are very welcome.

## Authorship

Created by Michael Andre as an experimental macOS prototype for LTDZ spectrum analyzer modules.

Parts of the code were rapidly prototyped with AI assistance and need further cleanup, testing, and validation.

## Current status

Confirmed working on one LTDZ device with:

- macOS
- USB serial port like `/dev/cu.usbserial-2120`
- Baudrate: `57600`
- Wake/probe command: `8F 76`
- Wake response: `77`
- Sweep command: `a`
- Confirmed sweep packet length: `26 bytes`
- Confirmed response: `404 bytes` for `101` points
- Response decoding: `u16pair`

The software is not calibrated measurement equipment. Levels are currently relative dB values, not guaranteed absolute dBm.

## Files

Put these two files in the same folder:

```text
ltdz_mac_analyzer.py
ltdz_capture_helper.py
```

The GUI calls the helper script for hardware communication. This is intentional: on macOS, the helper process gave more reliable serial behavior than keeping the LTDZ port open inside the Qt GUI.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
source .venv/bin/activate
python ltdz_mac_analyzer.py
```

## Basic usage

1. Connect the LTDZ via USB.
2. Start the GUI.
3. Select the USB serial port, for example:

```text
/dev/cu.usbserial-2120
```

4. Choose a preset or set Start/Stop manually.
5. Use:

- `Single Sweep` for one measurement
- `Continuous Start` for repeated live sweeps
- `Stop` to stop live sweeping

Recommended first test:

```text
Preset: 868 MHz ISM 863–870 MHz
Points: 101 or 201
Pause/Step: 10–20 ms
Response: auto or u16pair
Y Autoscale: enabled
```

## Known working protocol notes

The tested device responds to:

```text
TX: 8F 76
RX: 77
```

A working 868 MHz test sweep used:

```text
X prep packet:
8F 78 30 38 36 33 30 30 30 30 30 30 30 30 30 37 30 30 30 30 31 30 31

A sweep packet:
8F 61 30 38 36 33 30 30 30 30 30 30 30 30 30 37 30 30 30 30 31 30 31 30 32 30
```

For 101 points, this returned 404 bytes:

```text
101 points × 4 bytes = 404 bytes
```

The repeated pattern looks like two little-endian 16-bit values per point, often duplicated:

```text
59 00 59 00
5B 00 5B 00
...
```

The current decoder averages the two 16-bit values and maps them to relative dB.

## Limitations

- Experimental.
- macOS-focused.
- Not calibrated.
- Currently depends on helper process for stable LTDZ communication.
- Different LTDZ clone firmware may use different packets.
- Full 35–4400 MHz sweeps may need many points because the packet format only allows a limited step width.
- Continuous sweep speed depends heavily on point count and pause/step.

## Testing other LTDZ devices

Please test and report:

- macOS version
- Mac model / CPU
- Python version
- PySide6 version
- LTDZ model / seller link / board photo
- USB serial chip if known
- Selected serial port
- Frequency range
- Points
- Pause/Step
- Response format
- Whether `8F 76` returns `77`
- Whether the sweep returns bytes
- Screenshot or terminal output

## Safety and legal note

This tool is for receiving/analyzing RF signals with LTDZ-compatible spectrum analyzer modules. Observe local laws and regulations. Do not transmit on frequencies where you are not licensed to do so.

## License

MIT License. See `LICENSE`.
