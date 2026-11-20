"""Entry point used by PyInstaller to build CineCapturePro.exe."""

import sys

from cinecapture.app import main

if __name__ == "__main__":
    sys.exit(main())
