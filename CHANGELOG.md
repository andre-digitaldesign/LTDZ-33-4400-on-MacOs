# Changelog

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
