#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# LTDZ Capture Helper
# Copyright (c) 2026 Michael Andre
# Licensed under the MIT License. See LICENSE file for details.
#
# Experimental prototype / vibe-coded test version.
# This software is not calibrated measurement equipment.
# Use at your own risk.
#

"""
LTDZ capture helper v5 - dynamic range.

Usage:
  python ltdz_capture_helper_v5.py <port> <outfile> <start_mhz> <stop_mhz> <points> <pause_ms>
"""

import sys
import time
from pathlib import Path
import serial

if len(sys.argv) < 7:
    print("usage: ltdz_capture_helper_v5.py <port> <outfile> <start_mhz> <stop_mhz> <points> <pause_ms>", file=sys.stderr, flush=True)
    raise SystemExit(2)

port = sys.argv[1]
outfile = Path(sys.argv[2])
start_mhz = float(sys.argv[3])
stop_mhz = float(sys.argv[4])
points = max(2, min(9999, int(float(sys.argv[5]))))
pause_ms = max(0, min(999, int(float(sys.argv[6]))))

PREFIX = 0x8F

def fmt_fixed(value: int, width: int) -> bytes:
    return f"{max(0, int(value)):0{width}d}"[-width:].encode("ascii")

def build_packets():
    start_hz = int(round(start_mhz * 1_000_000))
    stop_hz = int(round(stop_mhz * 1_000_000))
    start_units = int(round(start_hz / 10))
    step_units = int(round(((stop_hz - start_hz) / (points - 1)) / 10))

    if step_units > 999999:
        print("ERROR: step_units too large. Use more points or smaller span.", flush=True)
        raise SystemExit(3)

    # x-prep has confirmed len 23:
    x_packet = (
        bytes([PREFIX]) + b"x"
        + fmt_fixed(start_units, 9)
        + b"00"
        + fmt_fixed(step_units, 6)
        + fmt_fixed(points, 4)
    )

    # a-sweep has confirmed len 26:
    a_packet = (
        bytes([PREFIX]) + b"a"
        + fmt_fixed(start_units, 9)
        + b"00"
        + fmt_fixed(step_units, 6)
        + fmt_fixed(points, 4)
        + fmt_fixed(pause_ms, 3)
    )

    return x_packet, a_packet

wake = bytes([PREFIX]) + b"v"
x_packet, a_packet = build_packets()

def read_for(ser, seconds):
    deadline = time.time() + seconds
    chunks = []
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            chunks.append(ser.read(n))
        else:
            time.sleep(0.01)
    return b"".join(chunks)

def txrx(ser, name, packet, seconds):
    ser.reset_input_buffer()
    print(f"{name} TX:", packet.hex(" ").upper(), flush=True)
    ser.write(packet)
    ser.flush()
    rx = read_for(ser, seconds)
    print(f"{name} RX: {len(rx)} Bytes " + (rx[:120].hex(" ").upper() if rx else ""), flush=True)
    return rx

raw = b""

with serial.Serial(port, baudrate=57600, timeout=0.2, write_timeout=0.5) as ser:
    time.sleep(0.15)
    ser.reset_input_buffer()
    ser.reset_output_buffer()

    txrx(ser, "WAKE", wake, 0.5)
    txrx(ser, "X PREP", x_packet, 0.8)

    # Read roughly enough for 4 bytes/point. More points need a little longer.
    read_seconds = max(1.2, min(8.0, 0.8 + points * max(1, pause_ms) / 1000.0 + points / 600.0))
    raw = txrx(ser, "A SWEEP", a_packet, read_seconds)

outfile.write_bytes(raw)
print("OUTFILE:", str(outfile), flush=True)
print("FINAL RAW BYTES:", len(raw), flush=True)
print(f"RANGE: {start_mhz} {stop_mhz} POINTS: {points} PAUSE: {pause_ms}", flush=True)