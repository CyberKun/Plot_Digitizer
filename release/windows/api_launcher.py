"""Start the bundled local API using only the package's private dependencies."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "runtime_deps/py314"), str(ROOT)]

from plot_digitizer.api import main

if __name__ == "__main__":
    raise SystemExit(main())
