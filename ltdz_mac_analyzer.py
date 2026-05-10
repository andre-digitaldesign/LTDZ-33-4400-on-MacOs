#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# LTDZ Mac Analyzer
# Copyright (c) 2026 Michael Andre
# Licensed under the MIT License. See LICENSE file for details.
#
# Experimental prototype / vibe-coded test version.
# This software is not calibrated measurement equipment.
# Use at your own risk.
#

"""
LTDZ Mac Analyzer – native Python/Qt GUI for LTDZ 35/33-4400 MHz modules.

Status:
- Functional first prototype for macOS/Linux/Windows.
- Uses the NWT/LTDZ-style serial command family:
  prefix 0x8f + command + ASCII sweep parameters.
- Default command is 'a', because many LTDZ boards use the newer continuous sweep command.
- Includes simulation mode so the UI can be tested without hardware.

Install:
  python3 -m venv .venv
  source .venv/bin/activate
  pip install pyserial PySide6 pyqtgraph numpy

Run:
  python ltdz_mac_analyzer.py

Notes:
- I cannot test your exact LTDZ hardware from here.
- If the sweep returns garbage, try command 'x' instead of 'a', change baudrate,
  or change "Response format" in the UI.
"""

import csv
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import serial
from serial.tools import list_ports

from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg


PREFIX = 0x8F


@dataclass
class SweepConfig:
    start_mhz: float = 35.0
    stop_mhz: float = 4400.0
    points: int = 401
    pause_ms: int = 10
    command: str = "a"
    response_format: str = "auto"  # auto, u8, u16le, s16le
    baudrate: int = 57600
    timeout_s: float = 3.0
    tg_enabled: bool = False


class LTDZDevice:
    """
    Minimal LTDZ/NWT-compatible serial client.

    Command packet assumption:
      0x8f, command byte, start frequency (9 ASCII digits, 10 Hz units),
      step frequency (6 ASCII digits, 10 Hz units),
      step count (4 ASCII digits),
      pause (3 ASCII digits, ms)

    This follows the common NWT/LTDZ command shape visible in compatible firmware:
      frequency fields are parsed as 9 digits, step size as 6 digits,
      step count as 4 digits, pause as 3 digits.

    Some clone/firmware combinations differ. The UI exposes command and response format
    so you can adapt without changing code.
    """

    def __init__(self) -> None:
        self.ser: Optional[serial.Serial] = None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str, baudrate: int, timeout_s: float) -> None:
        self.disconnect()
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=timeout_s,
            write_timeout=timeout_s,
        )
        # Let USB-serial settle.
        time.sleep(0.15)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

    def disconnect(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.close()
            finally:
                self.ser = None

    def _write_packet(self, packet: bytes) -> None:
        if not self.connected:
            raise RuntimeError("Nicht verbunden.")
        assert self.ser is not None
        self.ser.reset_input_buffer()
        self.ser.write(packet)
        self.ser.flush()

    @staticmethod
    def _fmt_fixed(value: int, width: int) -> bytes:
        value = max(0, int(value))
        return f"{value:0{width}d}"[-width:].encode("ascii")

    @staticmethod
    def build_sweep_packet(cfg: SweepConfig) -> Tuple[bytes, np.ndarray]:
        start_hz = int(round(cfg.start_mhz * 1_000_000))
        stop_hz = int(round(cfg.stop_mhz * 1_000_000))
        points = max(2, min(9999, int(cfg.points)))

        if stop_hz <= start_hz:
            raise ValueError("Stop muss größer als Start sein.")

        # Confirmed for this LTDZ:
        #   0x8f 'a' + start(9 ASCII digits, 10 Hz units)
        #   + filler/mode "00"
        #   + step(6 ASCII digits, 10 Hz units)
        #   + points(4 ASCII digits)
        #   + pause(3 ASCII digits)
        # Total: 26 bytes. 101 points returned 404 bytes.
        start_units = int(round(start_hz / 10))
        step_units = int(round(((stop_hz - start_hz) / (points - 1)) / 10))

        if step_units > 999999:
            raise ValueError(
                "Schrittweite zu groß. Mehr Punkte wählen oder kleineren Frequenzbereich nehmen."
            )

        pause_ms = max(0, min(999, int(cfg.pause_ms)))
        cmd = b"a"  # confirmed working
        filler = b"00"

        packet = (
            bytes([PREFIX])
            + cmd
            + LTDZDevice._fmt_fixed(start_units, 9)
            + filler
            + LTDZDevice._fmt_fixed(step_units, 6)
            + LTDZDevice._fmt_fixed(points, 4)
            + LTDZDevice._fmt_fixed(pause_ms, 3)
        )

        freqs_mhz = np.linspace(cfg.start_mhz, cfg.stop_mhz, points)
        return packet, freqs_mhz

    def query_version(self) -> str:
        if not self.connected:
            raise RuntimeError("Nicht verbunden.")
        assert self.ser is not None
        try:
            self.ser.reset_input_buffer()
            self.ser.write(bytes([PREFIX]) + b"v")
            self.ser.flush()
            time.sleep(0.25)
            data = self.ser.read(64)
            if not data:
                return "Keine Antwort – bei LTDZ oft normal"
            return f"{len(data)} Byte: " + " ".join(f"{b:02X}" for b in data)
        except Exception as e:
            return f"Versionsabfrage nicht unterstützt/fehlgeschlagen: {e}"

    def set_tracking_generator(self, enabled: bool) -> None:
        # Best-effort: not all original LTDZ firmwares implement this the same way.
        # Command exists in compatible firmware as 't'. We send one data byte.
        packet = bytes([PREFIX]) + b"t" + (b"1" if enabled else b"0")
        self._write_packet(packet)

    def sweep(self, cfg: SweepConfig) -> Tuple[np.ndarray, np.ndarray, bytes]:
        packet, freqs_mhz = self.build_sweep_packet(cfg)
        self._write_packet(packet)

        assert self.ser is not None
        points = len(freqs_mhz)

        # This LTDZ returned 404 bytes for 101 points: 4 bytes per point.
        # Read up to 4 bytes/point, but decoding also handles 2 bytes/point.
        expected_max = points * 4
        deadline = time.time() + max(0.8, float(cfg.timeout_s))
        chunks = []
        total = 0
        while time.time() < deadline:
            waiting = self.ser.in_waiting
            if waiting:
                part = self.ser.read(min(waiting, expected_max - total))
                chunks.append(part)
                total += len(part)
                if total >= expected_max:
                    break
            else:
                time.sleep(0.01)

        raw = b"".join(chunks)
        values = self.decode_values(raw, points, cfg.response_format)
        return freqs_mhz[: len(values)], values, raw

    @staticmethod
    def decode_values(raw: bytes, points: int, response_format: str) -> np.ndarray:
        if not raw:
            raise RuntimeError("Keine Daten vom LTDZ empfangen.")

        fmt = response_format.lower()

        if fmt == "auto":
            if len(raw) >= points * 4:
                fmt = "u16pair"
            elif len(raw) >= points * 2:
                fmt = "u16le"
            else:
                fmt = "u8"

        if fmt == "u16pair":
            # Confirmed-looking data: 53 00 53 00, 51 00 51 00 ...
            # Use one 16-bit value per 4-byte pair. If both values differ later,
            # average them to stay robust.
            usable = min(len(raw) // 4, points)
            vals = []
            for i in range(usable):
                a = int.from_bytes(raw[i*4:i*4+2], "little", signed=False)
                b = int.from_bytes(raw[i*4+2:i*4+4], "little", signed=False)
                vals.append((a + b) / 2.0)
            arr = np.array(vals, dtype=float)
            return arr - 160.0

        if fmt == "u8":
            arr = np.frombuffer(raw[:points], dtype=np.uint8).astype(float)
            return arr - 160.0

        if fmt == "u16le":
            usable = min(len(raw) // 2, points)
            arr = np.frombuffer(raw[: usable * 2], dtype="<u2").astype(float)
            return arr - 160.0

        if fmt == "s16le":
            usable = min(len(raw) // 2, points)
            arr = np.frombuffer(raw[: usable * 2], dtype="<i2").astype(float)
            return arr / 10.0

        raise ValueError(f"Unbekanntes Response-Format: {response_format}")


class SweepWorker(QtCore.QThread):
    result = QtCore.Signal(object, object, bytes)
    status = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, device: LTDZDevice, cfg: SweepConfig, continuous: bool, simulate: bool) -> None:
        super().__init__()
        self.device = device
        self.cfg = cfg
        self.continuous = continuous
        self.simulate = simulate
        self._running = True

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:
        while self._running:
            try:
                if self.simulate:
                    x = np.linspace(self.cfg.start_mhz, self.cfg.stop_mhz, max(2, self.cfg.points))
                    span = self.cfg.stop_mhz - self.cfg.start_mhz
                    center1 = self.cfg.start_mhz + span * 0.28
                    center2 = self.cfg.start_mhz + span * 0.63
                    noise = np.random.normal(0, 1.5, len(x))
                    y = -85 + noise
                    y += 38 * np.exp(-((x - center1) ** 2) / (2 * (span * 0.015 + 1) ** 2))
                    y += 24 * np.exp(-((x - center2) ** 2) / (2 * (span * 0.035 + 1) ** 2))
                    raw = b"SIM"
                    time.sleep(0.12)
                else:
                    x, y, raw = self.device.sweep(self.cfg)

                self.result.emit(x, y, raw)
                self.status.emit(f"Sweep fertig: {len(y)} Punkte")

            except Exception as e:
                self.error.emit(str(e))
                break

            if not self.continuous:
                break



class DynamicFreqAxis(pg.AxisItem):
    """
    Frequency axis.

    Internal x values are MHz.
    Display unit is based on the visible tick span, not on the original sweep span.
    """

    def __init__(self, orientation: str = "bottom") -> None:
        super().__init__(orientation=orientation)
        self.display_unit = "MHz"
        self.factor = 1.0

    def set_unit_for_span(self, start_mhz: float, stop_mhz: float) -> str:
        span_mhz = abs(float(stop_mhz) - float(start_mhz))

        if span_mhz >= 1000.0:
            self.display_unit = "GHz"
            self.factor = 1.0 / 1000.0
        elif span_mhz >= 1.0:
            self.display_unit = "MHz"
            self.factor = 1.0
        elif span_mhz >= 0.001:
            self.display_unit = "kHz"
            self.factor = 1000.0
        else:
            self.display_unit = "Hz"
            self.factor = 1_000_000.0

        return self.display_unit

    def tickStrings(self, values, scale, spacing):
        vals = [float(v) for v in values if math.isfinite(float(v))]
        if len(vals) >= 2:
            self.set_unit_for_span(min(vals), max(vals))

        out = []
        for v in values:
            shown = float(v) * self.factor
            abs_shown = abs(shown)

            if self.display_unit == "GHz":
                s = f"{shown:.6f}" if abs_shown < 10 else f"{shown:.3f}"
            elif self.display_unit == "MHz":
                s = f"{shown:.6f}" if abs_shown < 10 else f"{shown:.3f}"
            elif self.display_unit == "kHz":
                s = f"{shown:.3f}" if abs_shown < 1000 else f"{shown:.1f}"
            else:
                s = f"{shown:.1f}" if abs_shown < 1000 else f"{shown:.0f}"

            if "." in s:
                s = s.rstrip("0").rstrip(".")
            out.append(s)
        return out


class HelperLiveWorker(QtCore.QThread):
    result = QtCore.Signal(object, object, bytes, str)
    status = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(self, python_exe: str, helper_path: str, port: str, cfg: SweepConfig, continuous: bool) -> None:
        super().__init__()
        self.python_exe = python_exe
        self.helper_path = helper_path
        self.port = port
        self.cfg = cfg
        self.continuous = continuous
        self._running = True
        self.current_proc = None

    def stop(self) -> None:
        self._running = False
        proc = self.current_proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def run_helper_once(self):
        import subprocess
        import tempfile
        from pathlib import Path

        out = Path(tempfile.gettempdir()) / "ltdz_live_capture_raw.bin"
        cmd = [
            self.python_exe,
            self.helper_path,
            self.port,
            str(out),
            str(float(self.cfg.start_mhz)),
            str(float(self.cfg.stop_mhz)),
            str(int(self.cfg.points)),
            str(int(self.cfg.pause_ms)),
        ]

        self.current_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        start_time = time.time()
        timeout_s = 15.0

        while self._running:
            if self.current_proc.poll() is not None:
                break
            if time.time() - start_time > timeout_s:
                try:
                    self.current_proc.terminate()
                    self.current_proc.wait(timeout=1.0)
                except Exception:
                    try:
                        self.current_proc.kill()
                    except Exception:
                        pass
                raise RuntimeError("Helper Timeout")
            time.sleep(0.05)

        if not self._running:
            self.stop()
            return None, "", ""

        stdout, stderr = self.current_proc.communicate(timeout=1.0)
        rc = self.current_proc.returncode
        self.current_proc = None

        log = ""
        if stdout:
            log += stdout
        if stderr:
            log += "\nERR:\n" + stderr

        if rc != 0:
            raise RuntimeError(f"Helper Returncode {rc}\n{log}")

        raw = out.read_bytes() if out.exists() else b""
        return raw, log, str(out)

    def run(self) -> None:
        while self._running:
            try:
                raw, log, _outfile = self.run_helper_once()

                if not self._running:
                    break

                if not raw:
                    self.error.emit("Helper lieferte 0 Bytes.\n" + (log or ""))
                    if not self.continuous:
                        break
                    time.sleep(0.25)
                    continue

                x = np.linspace(float(self.cfg.start_mhz), float(self.cfg.stop_mhz), int(self.cfg.points))
                y = LTDZDevice.decode_values(raw, int(self.cfg.points), self.cfg.response_format or "u16pair")
                self.result.emit(x[:len(y)], y, raw, log or "")
                self.status.emit(f"Live Sweep RX: {len(raw)} Bytes")

            except Exception as e:
                if self._running:
                    self.error.emit(str(e))
                if not self.continuous:
                    break

            if not self.continuous:
                break

            time.sleep(0.2)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LTDZ Mac Analyzer")
        self.resize(1200, 760)
        self.setMinimumSize(760, 520)

        self.device = LTDZDevice()
        self.worker: Optional[SweepWorker] = None
        self.last_x: Optional[np.ndarray] = None
        self.last_y: Optional[np.ndarray] = None
        self.last_raw: bytes = b""
        self.avg_y: Optional[np.ndarray] = None
        self.max_y: Optional[np.ndarray] = None
        self.waterfall_rows = []
        self.waterfall_max_rows = 160
        self.marker_a_index: Optional[int] = None
        self.marker_b_index: Optional[int] = None
        self.marker_a_line = None
        self.marker_b_line = None
        self.current_cursor_index: Optional[int] = None
        self.peak_history = []
        self.peak_history_max = 80
        self.peak_track_curve = None

        self._build_ui()
        self.apply_app_style()
        self.refresh_ports()
        self.apply_darkish_plot()

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QHBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        sidebar_content = QtWidgets.QFrame()
        sidebar_content.setMinimumWidth(340)
        sidebar_content.setMaximumWidth(460)
        sidebar_content.setFrameShape(QtWidgets.QFrame.StyledPanel)
        form = QtWidgets.QFormLayout(sidebar_content)
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignTop)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapAllRows)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setContentsMargins(16, 16, 16, 16)

        title = QtWidgets.QLabel("LTDZ Mac Analyzer")
        title.setObjectName("AppTitle")
        subtitle = QtWidgets.QLabel("Native RF sweep UI · macOS friendly")
        subtitle.setObjectName("AppSubtitle")
        form.addRow(title)
        form.addRow(subtitle)

        self.connection_label = QtWidgets.QLabel("● Nicht verbunden")
        self.connection_label.setObjectName("ConnectionStatus")
        form.addRow(self.connection_label)

        self.auto_setup_btn = QtWidgets.QPushButton("Auto Setup / Verbindung testen")
        form.addRow("", self.auto_setup_btn)

        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.setMinimumWidth(280)
        self.preset_combo.view().setMinimumWidth(340)
        self.preset_combo.addItems([
            "Preset wählen …",
            "Full LTDZ 35–4400 MHz",
            "FM Radio 87.5–108 MHz",
            "Airband 118–137 MHz",
            "2m Amateurfunk 144–146 MHz",
            "433 MHz ISM 430–440 MHz",
            "868 MHz ISM 863–870 MHz",
            "70cm Amateurfunk 430–440 MHz",
            "GSM/LTE grob 700–960 MHz",
            "WiFi 2.4 GHz 2400–2500 MHz",
        ])
        form.addRow("Presets", self.preset_combo)

        self.port_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("Ports aktualisieren")
        self.connect_btn = QtWidgets.QPushButton("Verbinden")
        self.disconnect_btn = QtWidgets.QPushButton("Trennen")
        self.version_btn = QtWidgets.QPushButton("Version lesen")
        self.test_sweep_btn = QtWidgets.QPushButton("Test Sweep 868 MHz")
        self.test_sweep_x_btn = QtWidgets.QPushButton("Test Sweep 868 MHz mit x")
        self.direct_sweep_btn = QtWidgets.QPushButton("DIRECT bestätigter Sweep")

        port_box = QtWidgets.QVBoxLayout()
        port_box.addWidget(self.port_combo)
        port_box.addWidget(self.refresh_btn)
        port_box.addWidget(self.connect_btn)
        port_box.addWidget(self.disconnect_btn)
        port_box.addWidget(self.version_btn)
        port_box.addWidget(self.test_sweep_btn)
        port_box.addWidget(self.test_sweep_x_btn)
        port_box.addWidget(self.direct_sweep_btn)
        form.addRow("Port", port_box)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.setMinimumWidth(280)
        self.baud_combo.addItems(["57600", "115200", "460800"])
        form.addRow("Baud", self.baud_combo)

        self.start_spin = QtWidgets.QDoubleSpinBox()
        self.start_spin.setMinimumWidth(280)
        self.start_spin.setRange(0.001, 6000.0)
        self.start_spin.setDecimals(6)
        self.start_spin.setValue(35.0)
        self.start_spin.setSuffix(" MHz")
        form.addRow("Start", self.start_spin)

        self.stop_spin = QtWidgets.QDoubleSpinBox()
        self.stop_spin.setMinimumWidth(280)
        self.stop_spin.setRange(0.001, 6000.0)
        self.stop_spin.setDecimals(6)
        self.stop_spin.setValue(4400.0)
        self.stop_spin.setSuffix(" MHz")
        form.addRow("Stop", self.stop_spin)

        self.center_spin = QtWidgets.QDoubleSpinBox()
        self.center_spin.setMinimumWidth(280)
        self.center_spin.setRange(0.001, 6000.0)
        self.center_spin.setDecimals(6)
        self.center_spin.setValue((self.start_spin.value() + self.stop_spin.value()) / 2)
        self.center_spin.setSuffix(" MHz")

        self.span_spin = QtWidgets.QDoubleSpinBox()
        self.span_spin.setMinimumWidth(280)
        self.span_spin.setRange(0.001, 6000.0)
        self.span_spin.setDecimals(6)
        self.span_spin.setValue(self.stop_spin.value() - self.start_spin.value())
        self.span_spin.setSuffix(" MHz")

        self.apply_center_span_btn = QtWidgets.QPushButton("Center/Span übernehmen")
        self.reset_zoom_btn = QtWidgets.QPushButton("Zoom auf Bereich zurücksetzen")

        form.addRow("Center", self.center_spin)
        form.addRow("Span", self.span_spin)
        form.addRow("", self.apply_center_span_btn)
        form.addRow("", self.reset_zoom_btn)

        self.points_spin = QtWidgets.QSpinBox()
        self.points_spin.setMinimumWidth(280)
        self.points_spin.setRange(2, 9999)
        self.points_spin.setValue(401)
        form.addRow("Punkte", self.points_spin)

        self.pause_spin = QtWidgets.QSpinBox()
        self.pause_spin.setMinimumWidth(280)
        self.pause_spin.setRange(0, 999)
        self.pause_spin.setValue(10)
        self.pause_spin.setSuffix(" ms")
        form.addRow("Pause/Step", self.pause_spin)

        self.cmd_combo = QtWidgets.QComboBox()
        self.cmd_combo.setMinimumWidth(280)
        self.cmd_combo.addItems(["a", "x", "b"])
        form.addRow("Sweep-Befehl", self.cmd_combo)

        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.setMinimumWidth(280)
        self.format_combo.view().setMinimumWidth(280)
        self.format_combo.addItems(["auto", "u16pair", "u16le", "u8", "s16le"])
        form.addRow("Response", self.format_combo)

        self.cal_offset_spin = QtWidgets.QDoubleSpinBox()
        self.cal_offset_spin.setMinimumWidth(280)
        self.cal_offset_spin.setRange(-200.0, 200.0)
        self.cal_offset_spin.setDecimals(2)
        self.cal_offset_spin.setValue(0.0)
        self.cal_offset_spin.setSuffix(" dB")
        form.addRow("dB Offset", self.cal_offset_spin)

        self.cal_scale_spin = QtWidgets.QDoubleSpinBox()
        self.cal_scale_spin.setMinimumWidth(280)
        self.cal_scale_spin.setRange(0.01, 10.0)
        self.cal_scale_spin.setDecimals(3)
        self.cal_scale_spin.setValue(1.0)
        form.addRow("dB Faktor", self.cal_scale_spin)

        self.avg_check = QtWidgets.QCheckBox("Average glätten")
        self.avg_spin = QtWidgets.QSpinBox()
        self.avg_spin.setMinimumWidth(280)
        self.avg_spin.setRange(2, 50)
        self.avg_spin.setValue(6)
        avg_box = QtWidgets.QHBoxLayout()
        avg_box.addWidget(self.avg_check)
        avg_box.addWidget(self.avg_spin)
        form.addRow("Average", avg_box)

        self.maxhold_check = QtWidgets.QCheckBox("Max Hold anzeigen")
        self.clear_max_btn = QtWidgets.QPushButton("Max Hold löschen")
        max_box = QtWidgets.QVBoxLayout()
        max_box.addWidget(self.maxhold_check)
        max_box.addWidget(self.clear_max_btn)
        form.addRow("Max Hold", max_box)

        self.peaktrack_check = QtWidgets.QCheckBox("Peak Tracking anzeigen")
        self.clear_peaktrack_btn = QtWidgets.QPushButton("Peak Track löschen")
        peaktrack_box = QtWidgets.QVBoxLayout()
        peaktrack_box.addWidget(self.peaktrack_check)
        peaktrack_box.addWidget(self.clear_peaktrack_btn)
        form.addRow("Peak Track", peaktrack_box)

        self.waterfall_check = QtWidgets.QCheckBox("Waterfall anzeigen")
        self.waterfall_check.setChecked(True)
        self.clear_waterfall_btn = QtWidgets.QPushButton("Waterfall löschen")
        wf_box = QtWidgets.QVBoxLayout()
        wf_box.addWidget(self.waterfall_check)
        wf_box.addWidget(self.clear_waterfall_btn)
        form.addRow("Waterfall", wf_box)

        self.autoscale_check = QtWidgets.QCheckBox("Y Autoscale")
        self.autoscale_check.setChecked(True)
        self.ymin_spin = QtWidgets.QDoubleSpinBox()
        self.ymin_spin.setMinimumWidth(280)
        self.ymin_spin.setRange(-200, 200)
        self.ymin_spin.setValue(-120)
        self.ymax_spin = QtWidgets.QDoubleSpinBox()
        self.ymax_spin.setMinimumWidth(280)
        self.ymax_spin.setRange(-200, 200)
        self.ymax_spin.setValue(0)
        y_box = QtWidgets.QVBoxLayout()
        y_box.addWidget(self.autoscale_check)
        y_box.addWidget(self.ymin_spin)
        y_box.addWidget(self.ymax_spin)
        form.addRow("Y Bereich", y_box)

        self.sim_check = QtWidgets.QCheckBox("Simulation ohne Hardware")
        form.addRow("", self.sim_check)

        self.tg_check = QtWidgets.QCheckBox("Tracking Generator an")
        self.tg_btn = QtWidgets.QPushButton("TG setzen")
        tg_box = QtWidgets.QVBoxLayout()
        tg_box.addWidget(self.tg_check)
        tg_box.addWidget(self.tg_btn)
        form.addRow("TG", tg_box)

        self.single_btn = QtWidgets.QPushButton("Single Sweep")
        self.cont_btn = QtWidgets.QPushButton("Continuous Start")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.export_btn = QtWidgets.QPushButton("CSV Export")

        self.set_marker_a_btn = QtWidgets.QPushButton("Marker A setzen")
        self.set_marker_b_btn = QtWidgets.QPushButton("Marker B setzen")
        self.clear_marker_btn = QtWidgets.QPushButton("Marker löschen")
        marker_box = QtWidgets.QVBoxLayout()
        marker_box.addWidget(self.set_marker_a_btn)
        marker_box.addWidget(self.set_marker_b_btn)
        marker_box.addWidget(self.clear_marker_btn)

        form.addRow("", self.single_btn)
        form.addRow("", self.cont_btn)
        form.addRow("", self.stop_btn)
        form.addRow("", self.export_btn)
        form.addRow("Marker", marker_box)

        self.marker_label = QtWidgets.QLabel("Marker: -")
        self.marker_label.setWordWrap(True)
        form.addRow("Info", self.marker_label)

        self.status = QtWidgets.QPlainTextEdit()
        self.status.setReadOnly(True)
        self.status.setMaximumHeight(150)
        form.addRow("Log", self.status)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        self.freq_axis = DynamicFreqAxis("bottom")
        self.plot = pg.PlotWidget(axisItems={"bottom": self.freq_axis})
        self.plot.setLabel("bottom", "Frequenz (MHz)")
        self.plot.setLabel("left", "Pegel", units="dB rel.")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
        self.curve = self.plot.plot([], [], pen=pg.mkPen("#00E5FF", width=2.2))
        self.avg_curve = self.plot.plot([], [], pen=pg.mkPen("#B388FF", width=2, style=QtCore.Qt.DashLine))
        self.max_curve = self.plot.plot([], [], pen=pg.mkPen("#FFD54F", width=2, style=QtCore.Qt.DotLine))
        self.peak_scatter = pg.ScatterPlotItem(size=11, brush=pg.mkBrush("#FF5252"), pen=pg.mkPen("#FFFFFF", width=1))
        self.plot.addItem(self.peak_scatter)

        self.marker_a_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#69F0AE", width=1.5))
        self.marker_b_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#FFAB40", width=1.5))
        self.marker_a_line.hide()
        self.marker_b_line.hide()
        self.plot.addItem(self.marker_a_line)
        self.plot.addItem(self.marker_b_line)
        self.peak_track_curve = self.plot.plot([], [], pen=pg.mkPen("#FF4081", width=1.6, style=QtCore.Qt.DashDotLine))

        self.waterfall_freq_axis = DynamicFreqAxis("bottom")
        self.waterfall_plot = pg.PlotWidget(axisItems={"bottom": self.waterfall_freq_axis})
        self.waterfall_plot.setLabel("bottom", "Frequenz (MHz)")
        self.waterfall_plot.setLabel("left", "Zeit", units="Sweeps")
        self.waterfall_plot.setMouseEnabled(x=True, y=False)
        self.waterfall_plot.enableAutoRange(axis=pg.ViewBox.XAxis, enable=False)
        self.waterfall_img = pg.ImageItem()
        # "Analyzer-like" color map: dark background -> blue/cyan -> yellow/white peaks.
        wf_colors = [
            (0, 6, 10),
            (0, 32, 48),
            (0, 96, 128),
            (0, 220, 255),
            (255, 220, 80),
            (255, 255, 255),
        ]
        wf_pos = np.linspace(0.0, 1.0, len(wf_colors))
        self.waterfall_cmap = pg.ColorMap(wf_pos, wf_colors)
        self.waterfall_img.setLookupTable(self.waterfall_cmap.getLookupTable(0.0, 1.0, 256))
        self.waterfall_plot.addItem(self.waterfall_img)

        splitter.addWidget(self.plot)
        splitter.addWidget(self.waterfall_plot)
        splitter.setSizes([520, 220])
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 2)
        self.plot.setMinimumHeight(220)
        self.waterfall_plot.setMinimumHeight(120)

        sidebar_scroll = QtWidgets.QScrollArea()
        sidebar_scroll.setWidgetResizable(True)
        sidebar_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        sidebar_scroll.setWidget(sidebar_content)
        sidebar_scroll.setMinimumWidth(370)
        sidebar_scroll.setMaximumWidth(500)

        layout.addWidget(sidebar_scroll)
        layout.addWidget(splitter, stretch=1)

        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.connect_device)
        self.disconnect_btn.clicked.connect(self.disconnect_device)
        self.version_btn.clicked.connect(self.read_version)
        self.test_sweep_btn.clicked.connect(self.test_sweep_868)
        self.test_sweep_x_btn.clicked.connect(self.test_sweep_868_x)
        self.direct_sweep_btn.clicked.connect(self.direct_confirmed_sweep)
        self.auto_setup_btn.clicked.connect(self.auto_setup)
        self.single_btn.clicked.connect(lambda: self.start_sweep(False))
        self.cont_btn.clicked.connect(lambda: self.start_sweep(True))
        self.stop_btn.clicked.connect(self.stop_sweep)
        self.export_btn.clicked.connect(self.export_csv)
        self.tg_btn.clicked.connect(self.set_tg)
        self.clear_max_btn.clicked.connect(self.clear_max_hold)
        self.clear_waterfall_btn.clicked.connect(self.clear_waterfall)
        self.clear_peaktrack_btn.clicked.connect(self.clear_peak_tracking)
        self.autoscale_check.stateChanged.connect(self.apply_y_range)
        self.ymin_spin.valueChanged.connect(self.apply_y_range)
        self.ymax_spin.valueChanged.connect(self.apply_y_range)
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)
        self.set_marker_a_btn.clicked.connect(lambda: self.set_marker("A"))
        self.set_marker_b_btn.clicked.connect(lambda: self.set_marker("B"))
        self.clear_marker_btn.clicked.connect(self.clear_markers)
        self.start_spin.valueChanged.connect(self.sync_center_span_from_start_stop)
        self.stop_spin.valueChanged.connect(self.sync_center_span_from_start_stop)
        self.start_spin.valueChanged.connect(self.update_frequency_axis_labels)
        self.stop_spin.valueChanged.connect(self.update_frequency_axis_labels)
        self.apply_center_span_btn.clicked.connect(self.apply_center_span)
        self.reset_zoom_btn.clicked.connect(self.reset_zoom_to_selected_range)
        self.plot.plotItem.vb.sigRangeChanged.connect(lambda *_: self.schedule_frequency_axis_update())
        self.waterfall_plot.plotItem.vb.sigRangeChanged.connect(lambda *_: self.schedule_frequency_axis_update())
        self.plot.scene().sigMouseMoved.connect(self.on_mouse_moved)
        self.normalize_sidebar_controls()
        self.sync_center_span_from_start_stop()
        self.update_frequency_axis_labels()

    def normalize_sidebar_controls(self) -> None:
        widgets = [
            self.port_combo, self.auto_setup_btn, self.refresh_btn, self.connect_btn, self.disconnect_btn,
            self.version_btn, self.test_sweep_btn, self.test_sweep_x_btn, self.direct_sweep_btn, self.baud_combo, self.start_spin, self.stop_spin,
            self.points_spin, self.pause_spin, self.cmd_combo, self.format_combo,
            self.center_spin, self.span_spin, self.apply_center_span_btn, self.reset_zoom_btn,
            self.cal_offset_spin, self.cal_scale_spin,
            self.avg_spin, self.clear_max_btn, self.clear_peaktrack_btn, self.clear_waterfall_btn,
            self.ymin_spin, self.ymax_spin, self.tg_btn, self.single_btn,
            self.cont_btn, self.stop_btn, self.export_btn,
            self.set_marker_a_btn, self.set_marker_b_btn, self.clear_marker_btn,
            self.preset_combo
        ]
        for w in widgets:
            w.setMinimumWidth(280)
            w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def apply_darkish_plot(self) -> None:
        for p in [self.plot, self.waterfall_plot]:
            p.setBackground("#081014")
            p.getAxis("bottom").setTextPen("#D8F3FF")
            p.getAxis("left").setTextPen("#D8F3FF")
            p.getAxis("bottom").setPen("#34515C")
            p.getAxis("left").setPen("#34515C")
            p.showGrid(x=True, y=True, alpha=0.18)

    def apply_app_style(self) -> None:
        self.setStyleSheet("""
        QMainWindow, QWidget {
            background: #0A0F14;
            color: #EAF7FF;
            font-family: "Helvetica Neue", Arial;
            font-size: 13px;
        }
        QFrame {
            background: #101922;
            border: 1px solid #213442;
            border-radius: 14px;
        }
        QScrollArea {
            background: transparent;
            border: none;
        }
        QScrollBar:vertical {
            background: #0A0F14;
            width: 10px;
            margin: 2px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical {
            background: #2E5B73;
            min-height: 32px;
            border-radius: 5px;
        }
        QScrollBar::handle:vertical:hover {
            background: #00A6C8;
        }
        QLabel#AppTitle {
            font-size: 24px;
            font-weight: 800;
            color: #FFFFFF;
            padding-top: 8px;
        }
        QLabel#AppSubtitle {
            color: #8FB6C6;
            padding-bottom: 12px;
        }
        QLabel#ConnectionStatus {
            color: #FFAB40;
            font-size: 15px;
            font-weight: 800;
            padding: 8px 4px 12px 4px;
        }
        QLabel {
            background: transparent;
            border: none;
        }
        QPushButton {
            background: #142635;
            border: 1px solid #2E5B73;
            border-radius: 10px;
            padding: 10px 12px;
            color: #EAF7FF;
            font-weight: 650;
            min-height: 26px;
            min-width: 260px;
        }
        QPushButton:hover {
            background: #19364A;
            border-color: #00E5FF;
        }
        QPushButton:pressed {
            background: #0E1A24;
        }
        QComboBox, QSpinBox, QDoubleSpinBox {
            background: #081014;
            border: 1px solid #2B4655;
            border-radius: 9px;
            padding: 8px 12px;
            color: #EAF7FF;
            min-height: 26px;
            min-width: 260px;
        }
        QComboBox QAbstractItemView {
            background: #081014;
            color: #EAF7FF;
            selection-background-color: #19364A;
            selection-color: #FFFFFF;
            border: 1px solid #2B4655;
            padding: 6px;
            min-width: 180px;
        }
        QComboBox::drop-down {
            width: 28px;
            border-left: 1px solid #2B4655;
        }
        QCheckBox {
            spacing: 8px;
            color: #EAF7FF;
            min-width: 260px;
            padding: 4px 2px;
            background: transparent;
        }
        QPlainTextEdit {
            background: #070B0F;
            border: 1px solid #223746;
            border-radius: 10px;
            color: #BEEBFF;
            font-family: Menlo, Monaco, monospace;
            font-size: 12px;
        }
        """)

    def log(self, msg: str) -> None:
        self.status.appendPlainText(msg)

    def set_connection_status(self, text: str, ok: bool = False) -> None:
        if hasattr(self, "connection_label"):
            self.connection_label.setText(("● " if ok else "● ") + text)
            self.connection_label.setStyleSheet(
                "color: #69F0AE; font-size: 15px; font-weight: 800; padding: 8px 4px 12px 4px;"
                if ok else
                "color: #FFAB40; font-size: 15px; font-weight: 800; padding: 8px 4px 12px 4px;"
            )

    def auto_setup(self) -> None:
        self.log("Auto Setup: konservativer Modus …")
        ports = [self.port_combo.itemData(i) for i in range(self.port_combo.count())]
        ports = [p for p in ports if p]

        preferred = []
        for p in ports:
            name = str(p).lower()
            if "usbserial" in name or "wch" in name or "ch34" in name or "serial" in name:
                preferred.append(p)
        for p in ports:
            if p not in preferred:
                preferred.append(p)

        baudrates = [57600, 115200, 38400, 9600]

        for port in preferred:
            for baud in baudrates:
                try:
                    self.device.connect(port, baud, 0.8)
                    self.baud_combo.setCurrentText(str(baud))
                    self.set_connection_status(f"Port offen: {port} @ {baud}", True)
                    self.log(f"Port geöffnet: {port} @ {baud}")
                    self.log("Jetzt 'Test Sweep 868 MHz' drücken. Wenn RX Bytes kommt, liefert der LTDZ Daten.")
                    return
                except Exception as e:
                    self.log(f"Port/baud nicht offen: {port} @ {baud}: {e}")
                    self.device.disconnect()

        self.set_connection_status("Kein serieller LTDZ-Port geöffnet", False)
        self.log("Auto Setup: kein passender Port gefunden.")

    def test_sweep_868(self) -> None:
        self.log("Test Sweep 868 MHz gestartet …")
        self.start_spin.setValue(863.0)
        self.stop_spin.setValue(870.0)
        self.points_spin.setValue(101)
        self.pause_spin.setValue(20)
        self.cmd_combo.setCurrentText("a")
        self.format_combo.setCurrentText("auto")
        self.update_frequency_axis_labels()

        if not self.device.connected:
            self.connect_device()

        if not self.device.connected:
            self.log("Kein Gerät verbunden. Bitte Port /dev/cu.usbserial-* wählen und Verbinden klicken.")
            return

        self.start_sweep(False)

    def test_sweep_868_x(self) -> None:
        self.log("Test Sweep 868 MHz mit x gestartet …")
        self.start_spin.setValue(863.0)
        self.stop_spin.setValue(870.0)
        self.points_spin.setValue(101)
        self.pause_spin.setValue(20)
        self.cmd_combo.setCurrentText("x")
        self.format_combo.setCurrentText("auto")
        self.update_frequency_axis_labels()

        if not self.device.connected:
            self.connect_device()

        if not self.device.connected:
            self.log("Kein Gerät verbunden. Bitte Port /dev/cu.usbserial-* wählen und Verbinden klicken.")
            return

        self.start_sweep(False)


    def direct_confirmed_sweep(self) -> None:
        self.start_sweep(False)

    def refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        ports = list(list_ports.comports())
        preferred_index = -1
        for p in ports:
            label = f"{p.device} — {p.description}"
            self.port_combo.addItem(label, p.device)
            lname = f"{p.device} {p.description}".lower()
            if preferred_index < 0 and ("usbserial" in lname or "wch" in lname or "ch34" in lname):
                preferred_index = self.port_combo.count() - 1
        if not ports:
            self.port_combo.addItem("Keine seriellen Ports gefunden", "")
        idx = self.port_combo.findText(current)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
        elif preferred_index >= 0:
            self.port_combo.setCurrentIndex(preferred_index)
        self.log("Ports aktualisiert.")

    def config(self) -> SweepConfig:
        return SweepConfig(
            start_mhz=float(self.start_spin.value()),
            stop_mhz=float(self.stop_spin.value()),
            points=int(self.points_spin.value()),
            pause_ms=int(self.pause_spin.value()),
            command=self.cmd_combo.currentText(),
            response_format=self.format_combo.currentText(),
            baudrate=int(self.baud_combo.currentText()),
            timeout_s=3.0,
            tg_enabled=self.tg_check.isChecked(),
        )

    def selected_port(self) -> str:
        return self.port_combo.currentData() or ""

    def connect_device(self) -> None:
        if self.sim_check.isChecked():
            self.set_connection_status("Simulation aktiv", True)
            self.log("Simulation aktiv – keine Hardware-Verbindung nötig.")
            return
        port = self.selected_port()
        if not port:
            self.log("Kein Port ausgewählt.")
            return
        try:
            cfg = self.config()
            self.device.connect(port, cfg.baudrate, cfg.timeout_s)
            self.set_connection_status(f"Verbunden: {port} @ {cfg.baudrate}", True)
            self.log(f"Verbunden: {port} @ {cfg.baudrate}")
        except Exception as e:
            self.log(f"Verbindungsfehler: {e}")

    def disconnect_device(self) -> None:
        self.stop_sweep()
        self.device.disconnect()
        self.set_connection_status("Nicht verbunden", False)
        self.log("Getrennt.")

    def read_version(self) -> None:
        try:
            if self.sim_check.isChecked():
                self.log("Version: SIMULATION")
            else:
                self.log(f"Version raw: {self.device.query_version()}")
        except Exception as e:
            self.log(f"Versionsfehler: {e}")

    def set_tg(self) -> None:
        try:
            if self.sim_check.isChecked():
                self.log(f"TG Simulation: {'an' if self.tg_check.isChecked() else 'aus'}")
                return
            self.device.set_tracking_generator(self.tg_check.isChecked())
            self.log(f"TG gesetzt: {'an' if self.tg_check.isChecked() else 'aus'}")
        except Exception as e:
            self.log(f"TG-Fehler: {e}")

    def ensure_valid_step_width(self, cfg: SweepConfig) -> SweepConfig:
        """
        LTDZ packet has a 6-digit step field in 10 Hz units.
        Max step is therefore about 9.99999 MHz.
        If the selected range is too wide for the current point count,
        automatically increase points instead of throwing an error.
        """
        start_hz = int(round(float(cfg.start_mhz) * 1_000_000))
        stop_hz = int(round(float(cfg.stop_mhz) * 1_000_000))

        if stop_hz <= start_hz:
            return cfg

        max_step_hz = 9_999_990
        span_hz = stop_hz - start_hz
        min_points = int(span_hz // max_step_hz) + 2

        if int(cfg.points) < min_points:
            old_points = int(cfg.points)
            cfg.points = min(9999, max(min_points, old_points))
            self.points_spin.blockSignals(True)
            self.points_spin.setValue(int(cfg.points))
            self.points_spin.blockSignals(False)
            self.log(
                f"Punkte automatisch angepasst: {old_points} → {int(cfg.points)} "
                f"(sonst wäre die Schrittweite zu groß)."
            )

        return cfg

    def start_sweep(self, continuous: bool) -> None:
        if self.worker and self.worker.isRunning():
            self.log("Sweep läuft bereits.")
            return

        port = self.selected_port()
        if not port:
            self.log("Kein Port ausgewählt.")
            return

        cfg = self.config()
        cfg = self.ensure_valid_step_width(cfg)
        cfg.command = "a"
        if cfg.response_format == "auto":
            cfg.response_format = "u16pair"

        try:
            packet, _ = LTDZDevice.build_sweep_packet(cfg)
            self.log(f"TX geplant: {packet.hex(' ')}")
        except Exception as e:
            self.log(f"Konfigurationsfehler: {e}")
            return

        import sys
        from pathlib import Path

        helper = Path(__file__).with_name("ltdz_capture_helper.py")
        if not helper.exists():
            self.log(f"Helper fehlt: {helper}")
            self.log("Bitte ltdz_capture_helper.py in denselben Ordner legen.")
            return

        self.set_connection_status("Live Helper läuft …", True)
        self.worker = HelperLiveWorker(sys.executable, str(helper), port, cfg, continuous)
        self.worker.result.connect(self.update_plot_from_helper)
        self.worker.status.connect(self.log)
        self.worker.error.connect(lambda e: self.log(f"Helper Fehler: {e}"))
        self.worker.finished.connect(lambda: self.log("Sweep gestoppt."))
        self.worker.start()
        self.log("Continuous Live Sweep gestartet." if continuous else "Single Live Sweep gestartet.")

    def stop_sweep(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(8000):
                self.log("Warnung: Sweep-Thread brauchte zu lange und wurde abgebrochen.")
        self.worker = None

    def update_plot_from_helper(self, x: np.ndarray, y: np.ndarray, raw: bytes, helper_log: str) -> None:
        # Keep helper logs short in continuous mode.
        if helper_log:
            lines = [line for line in helper_log.splitlines() if "A SWEEP RX" in line or "FINAL RAW BYTES" in line or "ERROR" in line]
            for line in lines[-3:]:
                self.log("HELPER: " + line)
        self.update_plot(x, y, raw)

    def update_plot(self, x: np.ndarray, y: np.ndarray, raw: bytes) -> None:
        self.last_x = np.array(x)
        y = np.array(y, dtype=float)
        y = (y * float(self.cal_scale_spin.value())) + float(self.cal_offset_spin.value())
        self.last_y = y
        self.last_raw = raw
        if raw:
            self.log(f"RX: {len(raw)} Bytes")
            if self.last_y is not None and len(self.last_y):
                self.log(f"Y Bereich: min={float(np.min(self.last_y)):.2f}, max={float(np.max(self.last_y)):.2f}")

        self.schedule_frequency_axis_update()
        self.curve.setData(self.last_x, self.last_y)
        if len(self.last_x) > 1:
            self.schedule_frequency_axis_update()

        # Average smoothing / exponential moving average
        if self.avg_check.isChecked():
            n = max(2, int(self.avg_spin.value()))
            alpha = 1.0 / float(n)
            if self.avg_y is None or len(self.avg_y) != len(self.last_y):
                self.avg_y = self.last_y.copy()
            else:
                self.avg_y = (1.0 - alpha) * self.avg_y + alpha * self.last_y
            self.avg_curve.setData(self.last_x, self.avg_y)
        else:
            self.avg_curve.setData([], [])

        # Max Hold trace
        if self.maxhold_check.isChecked():
            if self.max_y is None or len(self.max_y) != len(self.last_y):
                self.max_y = self.last_y.copy()
            else:
                self.max_y = np.maximum(self.max_y, self.last_y)
            self.max_curve.setData(self.last_x, self.max_y)
        else:
            self.max_curve.setData([], [])

        # Auto Peak Tracking
        if self.peaktrack_check.isChecked() and len(self.last_y):
            peak_idx = int(np.argmax(self.last_y))
            self.peak_history.append((float(self.last_x[peak_idx]), float(self.last_y[peak_idx])))
            if len(self.peak_history) > self.peak_history_max:
                self.peak_history = self.peak_history[-self.peak_history_max:]
            hx = [p[0] for p in self.peak_history]
            hy = [p[1] for p in self.peak_history]
            self.peak_track_curve.setData(hx, hy)
        else:
            self.peak_track_curve.setData([], [])

        # Waterfall
        if self.waterfall_check.isChecked():
            self.waterfall_rows.append(self.last_y.copy())
            if len(self.waterfall_rows) > self.waterfall_max_rows:
                self.waterfall_rows = self.waterfall_rows[-self.waterfall_max_rows:]
            img = np.array(self.waterfall_rows, dtype=float)
            if self.autoscale_check.isChecked():
                self.waterfall_img.setImage(img.T, autoLevels=True)
            else:
                self.waterfall_img.setImage(
                    img.T,
                    autoLevels=False,
                    levels=(float(self.ymin_spin.value()), float(self.ymax_spin.value())),
                )
            if len(self.last_x) > 1:
                self.waterfall_img.setRect(QtCore.QRectF(
                    float(self.last_x[0]),
                    0,
                    float(self.last_x[-1] - self.last_x[0]),
                    max(1, len(self.waterfall_rows))
                ))

        self.apply_y_range()

        if len(self.last_y):
            idx = int(np.argmax(self.last_y))
            px, py = float(self.last_x[idx]), float(self.last_y[idx])
            self.peak_scatter.setData([px], [py])
            self.update_marker_label()

    def apply_y_range(self) -> None:
        if self.autoscale_check.isChecked():
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        else:
            self.plot.enableAutoRange(axis=pg.ViewBox.YAxis, enable=False)
            ymin = float(self.ymin_spin.value())
            ymax = float(self.ymax_spin.value())
            if ymax > ymin:
                self.plot.setYRange(ymin, ymax, padding=0)

    def clear_max_hold(self) -> None:
        self.max_y = None
        self.max_curve.setData([], [])
        self.log("Max Hold gelöscht.")

    def clear_waterfall(self) -> None:
        self.waterfall_rows = []
        self.waterfall_img.clear()
        self.log("Waterfall gelöscht.")

    def clear_peak_tracking(self) -> None:
        self.peak_history = []
        if self.peak_track_curve:
            self.peak_track_curve.setData([], [])
        self.log("Peak Tracking gelöscht.")

    def update_frequency_axis_from_visible_range(self) -> None:
        try:
            x_range, _y_range = self.plot.viewRange()
            start = float(x_range[0])
            stop = float(x_range[1])
            if stop <= start:
                return

            unit = self.freq_axis.set_unit_for_span(start, stop)
            self.waterfall_freq_axis.set_unit_for_span(start, stop)

            # No pyqtgraph "units=" here, otherwise it adds SI prefixes itself.
            self.plot.setLabel("bottom", f"Frequenz ({unit})")
            self.waterfall_plot.setLabel("bottom", f"Frequenz ({unit})")

            self.plot.getAxis("bottom").update()
            self.waterfall_plot.getAxis("bottom").update()
        except Exception:
            pass

    def schedule_frequency_axis_update(self) -> None:
        QtCore.QTimer.singleShot(0, self.update_frequency_axis_from_visible_range)
        QtCore.QTimer.singleShot(50, self.update_frequency_axis_from_visible_range)

    def update_frequency_axis_labels(self) -> None:
        start = float(self.start_spin.value())
        stop = float(self.stop_spin.value())

        if stop <= start:
            return

        unit = self.freq_axis.set_unit_for_span(start, stop)
        self.waterfall_freq_axis.set_unit_for_span(start, stop)

        self.plot.setLabel("bottom", f"Frequenz ({unit})")
        self.waterfall_plot.setLabel("bottom", f"Frequenz ({unit})")

        self.plot.setXRange(start, stop, padding=0)
        self.waterfall_plot.setXRange(start, stop, padding=0)

    def apply_center_span(self) -> None:
        center = float(self.center_spin.value())
        span = float(self.span_spin.value())
        if span <= 0:
            return
        start = max(0.001, center - span / 2.0)
        stop = center + span / 2.0
        self.start_spin.blockSignals(True)
        self.stop_spin.blockSignals(True)
        self.start_spin.setValue(start)
        self.stop_spin.setValue(stop)
        self.start_spin.blockSignals(False)
        self.stop_spin.blockSignals(False)
        self.clear_waterfall()
        self.clear_max_hold()
        self.clear_markers()
        self.update_frequency_axis_labels()
        self.log(f"Center/Span gesetzt: Center {center:.6f} MHz, Span {span:.6f} MHz")

    def sync_center_span_from_start_stop(self) -> None:
        start = float(self.start_spin.value())
        stop = float(self.stop_spin.value())
        if stop <= start:
            return
        center = (start + stop) / 2.0
        span = stop - start
        if hasattr(self, "center_spin") and hasattr(self, "span_spin"):
            self.center_spin.blockSignals(True)
            self.span_spin.blockSignals(True)
            self.center_spin.setValue(center)
            self.span_spin.setValue(span)
            self.center_spin.blockSignals(False)
            self.span_spin.blockSignals(False)

    def reset_zoom_to_selected_range(self) -> None:
        self.update_frequency_axis_labels()
        self.log("Zoom auf ausgewählten Frequenzbereich zurückgesetzt.")

    def apply_preset(self) -> None:
        name = self.preset_combo.currentText()
        presets = {
            "Full LTDZ 35–4400 MHz": (35.0, 4400.0, 801),
            "FM Radio 87.5–108 MHz": (87.5, 108.0, 401),
            "Airband 118–137 MHz": (118.0, 137.0, 401),
            "2m Amateurfunk 144–146 MHz": (144.0, 146.0, 401),
            "433 MHz ISM 430–440 MHz": (430.0, 440.0, 401),
            "868 MHz ISM 863–870 MHz": (863.0, 870.0, 401),
            "70cm Amateurfunk 430–440 MHz": (430.0, 440.0, 401),
            "GSM/LTE grob 700–960 MHz": (700.0, 960.0, 601),
            "WiFi 2.4 GHz 2400–2500 MHz": (2400.0, 2500.0, 601),
        }
        if name not in presets:
            return
        start, stop, pts = presets[name]
        self.start_spin.setValue(start)
        self.stop_spin.setValue(stop)
        self.points_spin.setValue(pts)
        self.sync_center_span_from_start_stop()
        self.clear_waterfall()
        self.clear_max_hold()
        self.clear_markers()
        self.update_frequency_axis_labels()
        self.log(f"Preset geladen: {name}")

    def nearest_index_to_cursor(self) -> Optional[int]:
        if self.last_x is None or len(self.last_x) == 0:
            return None
        if self.current_cursor_index is not None:
            return int(self.current_cursor_index)
        return int(np.argmax(self.last_y)) if self.last_y is not None and len(self.last_y) else 0

    def set_marker(self, marker: str) -> None:
        idx = self.nearest_index_to_cursor()
        if idx is None:
            self.log("Keine Daten für Marker vorhanden.")
            return
        if marker == "A":
            self.marker_a_index = idx
            self.marker_a_line.setValue(float(self.last_x[idx]))
            self.marker_a_line.show()
            self.log(f"Marker A: {self.last_x[idx]:.6f} MHz")
        else:
            self.marker_b_index = idx
            self.marker_b_line.setValue(float(self.last_x[idx]))
            self.marker_b_line.show()
            self.log(f"Marker B: {self.last_x[idx]:.6f} MHz")
        self.update_marker_label()

    def clear_markers(self) -> None:
        self.marker_a_index = None
        self.marker_b_index = None
        if self.marker_a_line:
            self.marker_a_line.hide()
        if self.marker_b_line:
            self.marker_b_line.hide()
        self.update_marker_label()
        self.log("Marker gelöscht.")

    def update_marker_label(self, cursor_idx: Optional[int] = None) -> None:
        parts = []
        if self.last_x is not None and self.last_y is not None and len(self.last_x):
            peak_idx = int(np.argmax(self.last_y))
            parts.append(f"Peak: {self.last_x[peak_idx]:.6f} MHz / {self.last_y[peak_idx]:.2f} dB rel.")
            if cursor_idx is not None:
                parts.append(f"Cursor: {self.last_x[cursor_idx]:.6f} MHz / {self.last_y[cursor_idx]:.2f} dB rel.")
            if self.marker_a_index is not None and self.marker_a_index < len(self.last_x):
                ia = self.marker_a_index
                parts.append(f"A: {self.last_x[ia]:.6f} MHz / {self.last_y[ia]:.2f} dB")
            if self.marker_b_index is not None and self.marker_b_index < len(self.last_x):
                ib = self.marker_b_index
                parts.append(f"B: {self.last_x[ib]:.6f} MHz / {self.last_y[ib]:.2f} dB")
            if (
                self.marker_a_index is not None and self.marker_b_index is not None
                and self.marker_a_index < len(self.last_x) and self.marker_b_index < len(self.last_x)
            ):
                ia, ib = self.marker_a_index, self.marker_b_index
                df = self.last_x[ib] - self.last_x[ia]
                dy = self.last_y[ib] - self.last_y[ia]
                parts.append(f"Δ B-A: {df:.6f} MHz / {dy:.2f} dB")
        parts.append(f"Raw bytes: {len(self.last_raw)}")
        self.marker_label.setText("\n".join(parts))

    def on_mouse_moved(self, pos) -> None:
        if self.last_x is None or self.last_y is None or len(self.last_x) == 0:
            return
        vb = self.plot.plotItem.vb
        mouse_point = vb.mapSceneToView(pos)
        mx = mouse_point.x()
        idx = int(np.argmin(np.abs(self.last_x - mx)))
        self.current_cursor_index = idx
        self.update_marker_label(cursor_idx=idx)

    def export_csv(self) -> None:
        if self.last_x is None or self.last_y is None:
            self.log("Keine Daten zum Exportieren.")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "CSV speichern",
            str(Path.home() / "ltdz_sweep.csv"),
            "CSV Dateien (*.csv)",
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["frequency_mhz", "level_db_calibrated_relative"])
            writer.writerow([f"# db_offset={float(self.cal_offset_spin.value()):.3f}", f"db_factor={float(self.cal_scale_spin.value()):.6f}"])
            for fx, ly in zip(self.last_x, self.last_y):
                writer.writerow([f"{fx:.9f}", f"{ly:.3f}"])

        self.log(f"CSV gespeichert: {path}")

    def closeEvent(self, event) -> None:
        try:
            self.stop_sweep()
            self.device.disconnect()
        finally:
            event.accept()



def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())