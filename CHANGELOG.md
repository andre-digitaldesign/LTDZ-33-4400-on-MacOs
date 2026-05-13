# Changelog

## v0.1.1 experimental

### Fixed

- Added the app version to the main window title.
- Let simulation sweeps run without requiring a serial port selection.
- Kept the selected sweep command when building sweep packets instead of always forcing `a`.
- Fall back to the built-in direct serial sweep worker when the optional helper script is not present.

## v0.1.0 experimental

Initial public test version.

### Added

- Native macOS Python/PySide6 GUI
- Serial port selection
- Presets
- Start/Stop frequency selection
- Center/Span controls
- Single sweep
- Continuous sweep
- Waterfall display
- Average smoothing
- Max Hold
- Peak tracking
- CSV export
- Zoom-aware frequency labels
- Helper process for reliable serial communication
- Confirmed LTDZ 26-byte `a` sweep packet
- `u16pair` response decoding

### Known working device behavior

- Baudrate: `57600`
- Wake: `8F 76`
- Wake response: `77`
- 101-point sweep returns 404 bytes
