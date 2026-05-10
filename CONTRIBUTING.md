# Contributing

Thanks for helping test or improve LTDZ Mac Analyzer.

## How to help

You can help by:

- Testing with different LTDZ hardware variants
- Reporting whether your device responds to `8F 76`
- Sharing screenshots and logs
- Improving the GUI
- Improving protocol support
- Improving documentation
- Cleaning up the prototype code

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python ltdz_mac_analyzer.py
```

## Pull requests

Please keep changes focused and include:

- What changed
- Why it changed
- How you tested it
- macOS/Python version used for testing

## Protocol findings

If you discover a different LTDZ protocol variant, please document:

- Baudrate
- Wake/probe command
- Sweep packet bytes
- Response length
- Response format
- Example raw output
