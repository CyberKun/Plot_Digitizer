"""Exercise the packaged interpreter, dependency imports, GUI and extraction."""

import argparse
import base64
import json
import os
from pathlib import Path
import sys
import subprocess
import threading
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DEPS = ROOT / "runtime_deps/py314"
sys.path[:0] = [str(DEPS), str(ROOT)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plot_digitizer.qt_runtime import configure_qt_runtime
configure_qt_runtime(application_root=ROOT)

import cv2
import matplotlib
import numpy as np
import PySide6
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

from plot_digitizer.gui import MainWindow
from plot_digitizer.digitizer_core import extract_plot_curves
from plot_digitizer.axis_calibration import calibration_coefficients


from plot_digitizer.api import create_server


def verify_api():
    launcher = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "api_launcher.py"), "--help"],
                              capture_output=True, text=True, timeout=30)
    if launcher.returncode or "--port" not in launcher.stdout:
        raise RuntimeError("The bundled API launcher failed: " + launcher.stdout + launcher.stderr)
    server = create_server(port=0)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
    worker.start()
    try:
        address = "http://127.0.0.1:" + str(server.server_port)
        with urlopen(address + "/health", timeout=10) as response:
            if json.load(response)["status"] != "ok":
                raise RuntimeError("The bundled API is not ready.")
        encoded = base64.b64encode((ROOT / "examples/patent_plots/us8198452b2-fig1.png").read_bytes()).decode("ascii")
        request = Request(address + "/v1/extract", data=json.dumps({"image_base64": encoded}).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        if len(result["candidates"]) != 1 or result["coordinate_system"] != "image_pixels":
            raise RuntimeError("The bundled API extraction failed.")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def main(with_api=False):
    metadata = json.loads((ROOT / "release-info.json").read_text(encoding="utf8"))
    expected_python = ROOT / "python314/python.exe"
    if not expected_python.samefile(sys.executable):
        raise RuntimeError("Verification must use the bundled Python interpreter.")
    if sys.version.split()[0] != metadata["python"]["version"]:
        raise RuntimeError("The bundled Python version does not match its manifest.")
    for module in (cv2, matplotlib, np, PySide6):
        if not Path(module.__file__).resolve().is_relative_to(DEPS.resolve()):
            raise RuntimeError("A dependency was imported outside the packaged runtime.")
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families():
        raise RuntimeError("No Qt fonts are available.")
    window = MainWindow(standalone=True)
    window.show()
    app.processEvents()
    if window.reference_group.isVisible() or not window.auto_calibration_button.text():
        raise RuntimeError("The standalone window is incomplete.")
    image = cv2.cvtColor(cv2.imread(str(ROOT / "examples/patent_plots/us8198452b2-fig1.png")), cv2.COLOR_BGR2RGB)
    result = extract_plot_curves(image)
    if len(result["candidates"]) != 1 or result["preset"] != "normal":
        raise RuntimeError("Packaged PXRD extraction failed.")
    coefficients = calibration_coefficients([
        {"pixel": [0, 0], "value": 1e-20}, {"pixel": [100, 0], "value": 2e-20}], "x")
    if not np.isclose(np.polyval(coefficients, 100), 2e-20, rtol=1e-12, atol=0):
        raise RuntimeError("Small scientific values lost calibration precision.")
    if with_api:
        verify_api()
    QTimer.singleShot(20, window.close)
    QTimer.singleShot(40, app.quit)
    app.exec()
    print(json.dumps({"verified": True, "python": sys.version.split()[0], "numpy": np.__version__,
                      "opencv": cv2.__version__, "qt": PySide6.__version__,
                      "curve_count": len(result["candidates"]), "runtime_isolated": True, "api_verified": with_api}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-api", action="store_true", help="Also verify the HTTP service during release checks")
    raise SystemExit(main(parser.parse_args().with_api))
