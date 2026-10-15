"""PyInstaller entry point for the Python service bundled beside Electron."""

from pathlib import Path
import sys


if not getattr(sys, "frozen", False):
    source_root = Path(__file__).resolve().parents[1] / "src"
    if source_root.is_dir():
        sys.path.insert(0, str(source_root))

from scansci_html.electron_backend import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
