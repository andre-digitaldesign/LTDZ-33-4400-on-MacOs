#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
LTDZ Sweep Probe V3

Gezielter Test für das echte NWT/LTDZ-Kommandoformat:
- Firmware reagiert auf 0x8f 'v' mit 119 / 0x77 -> 57600 Baud stimmt.
- Sweep-Pakete haben wahrscheinlich:
    x: 23 Bytes
    a: 26 Bytes
  und zwei bislang fehlende Füll-/Mode-Bytes zwischen Startfrequenz und Step.

Run:
  source .venv/bin/activate
  python ltdz_sweep_probe_v3.py /dev/cu.usbserial-2120
"""

import argparse
import time
import serial

PREFIX = 0x8F


def hx(data: bytes, max_len=180) -> str:
    if not data:
        return "<keine Daten>"
    s = " ".join(f"{b:02X}" for b in data[:max_len])
    if len(data) > max_len:
        s += f" ... ({len(data)} Bytes total)"
    return s


def read_for(ser, seconds=4.0) -> bytes:
    deadline = time.time() + seconds
    chunks = []
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            chunks.append(ser.read(n))
        else:
            time.sleep(0.02)
    return b"".join(chunks)


def fields(start_mhz=863.0, stop_mhz=870.0, points=101, pause_ms=20):
    start_units = int(round(start_mhz * 1_000_000 / 10))
    step_units = int(round(((stop_mhz - start_mhz) * 1_000_000 / (points - 1)) / 10))
    return (
        f"{start_units:09d}"[-9:].encode("ascii"),
        f"{step_units:06d}"[-6:].encode("ascii"),
        f"{points:04d}"[-4:].encode("ascii"),
        f"{pause_ms:03d}"[-3:].encode("ascii"),
    )


def pkt(cmd: str, filler: bytes, include_pause: bool, start_mhz=863.0, stop_mhz=870.0, points=101, pause_ms=20) -> bytes:
    start, step, count, pause = fields(start_mhz, stop_mhz, points, pause_ms)
    p = bytes([PREFIX]) + cmd.encode("ascii")[:1] + start + filler + step + count
    if include_pause:
        p += pause
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=57600)
    args = ap.parse_args()

    fillers = [
        b"00", b"01", b"10", b"11", b"99",
        b"  ", b"\x00\x00", b"\x01\x00", b"\x00\x01",
    ]

    tests = []

    # Expected by firmware style:
    # x/b: length 23 => no pause
    # a:   length 26 => with pause
    for filler in fillers:
        tests.append((f"x len23 filler={repr(filler)}", pkt("x", filler, False)))
        tests.append((f"a len26 filler={repr(filler)}", pkt("a", filler, True)))
        tests.append((f"b len23 filler={repr(filler)}", pkt("b", filler, False)))

    # Smaller point counts, in case response transfer/timeout is the issue.
    for points in [5, 10, 20, 50]:
        for filler in [b"00", b"01", b"\x00\x00"]:
            tests.append((f"x small points={points} filler={repr(filler)}", pkt("x", filler, False, points=points)))
            tests.append((f"a small points={points} filler={repr(filler)}", pkt("a", filler, True, points=points, pause_ms=10)))

    with serial.Serial(args.port, baudrate=args.baud, timeout=0.2, write_timeout=0.5) as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print(f"Port offen: {args.port} @ {args.baud}")

        ser.write(bytes([PREFIX]) + b"v")
        ser.flush()
        ver = read_for(ser, 0.8)
        print(f"Version probe RX {len(ver)} Bytes: {hx(ver)}")

        for name, packet in tests:
            ser.reset_input_buffer()
            print(f"\n=== {name} ===")
            print(f"LEN {len(packet)} TX:", hx(packet))
            ser.write(packet)
            ser.flush()
            rx = read_for(ser, 4.0)
            print(f"RX {len(rx)} Bytes:", hx(rx))
            if len(rx) >= 8:
                print("\n>>> MÖGLICHER SWEEP GEFUNDEN:", name)
                print(">>> Diese Zeile + RX-Länge bitte an Fred schicken.")
                return

    print("\nKein Sweep gefunden. Dann brauchen wir Mitschnitt der Originalsoftware.")


if __name__ == "__main__":
    main()
