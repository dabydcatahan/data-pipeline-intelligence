"""``adf-intelligence`` console entrypoint - shells out to ``streamlit run``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    app_path = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), *sys.argv[1:]]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
