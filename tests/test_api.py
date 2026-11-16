"""Real HTTP checks for the headless local API."""
import base64
import copy
import csv
import http.client
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import cv2
import numpy as np
from plot_digitizer import api

ROOT = Path(__file__).resolve().parents[1]


def plot_image():
    image = np.full((220, 340, 3), 255, np.uint8)
    cv2.line(image, (40, 30), (40, 190), (0, 0, 0), 2)
    cv2.line(image, (40, 190), (300, 190), (0, 0, 0), 2)
    xs = np.arange(45, 295)
    points = np.column_stack((xs, 110 + 25 * np.sin(xs / 22))).astype(np.int32)
    cv2.polylines(image, [points], False, (30, 70, 210), 2)
    return image


def reviewed_calibration():
    return {"reviewed": True, "axes": {
        "x": {"scale": "linear", "points": [
            {"pixel": [40, 190], "value": 5}, {"pixel": [300, 190], "value": 45}]},
        "y": {"scale": "linear", "points": [
            {"pixel": [40, 190], "value": 0}, {"pixel": [40, 30], "value": 100}]}}}


class LocalAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = api.create_server(port=0, allowed_origins=["http://localhost:5173"])
        cls.thread = threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        cls.thread.start()
        success, data = cv2.imencode(".png", plot_image())
        assert success
        cls.payload = {"image_base64": base64.b64encode(data).decode("ascii")}

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, path="/v1/extract", payload=None, *, method="POST", headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        body = raw if raw is not None else json.dumps(self.payload if payload is None else payload).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
        try:
            connection.request(method, path, body=body if method == "POST" else None, headers=headers)
            response = connection.getresponse()
            data, response_headers = response.read(), dict(response.getheaders())
            parsed = json.loads(data) if data and "application/json" in response_headers.get("Content-Type", "") else data
            return response.status, parsed, response_headers
        finally:
            connection.close()

    def test_health_and_openapi(self):
        status, health, _ = self.request("/health", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["api_version"], "v1")
        status, schema, _ = self.request("/openapi.json", method="GET")
        self.assertEqual(status, 200)
        self.assertIn("/v1/extract", schema["paths"])
        self.assertIn("/v1/calibration", schema["paths"])

    def test_extraction_preserves_pixels_and_diagnostics(self):
        status, result, _ = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(result["coordinate_system"], "image_pixels")
        self.assertEqual(result["source_image"]["width"], 340)
        self.assertEqual(result["source_image"]["height"], 220)
        self.assertEqual(len(result["source_image"]["sha256"]), 64)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIsNone(result["candidates"][0]["data_points"])
        self.assertGreater(result["candidates"][0]["coverage"], .8)
        self.assertIn("threshold_trials", result)
        self.assertIn("overlap_regions", result)
        self.assertTrue(result["review_required"])

    def test_reviewed_calibration_returns_correct_values(self):
        status, result, _ = self.request(payload=self.payload | {"calibration": reviewed_calibration()})
        self.assertEqual(status, 200)
        self.assertEqual(result["coordinate_system"], "axis_values")
        curve = result["candidates"][0]
        self.assertEqual(len(curve["points"]), len(curve["data_points"]))
        for pixel, real in zip(curve["points"][::30], curve["data_points"][::30]):
            self.assertAlmostEqual(real[0], 5 + 40 * (pixel[0] - 40) / 260)
            self.assertAlmostEqual(real[1], 100 * (190 - pixel[1]) / 160)

    def test_decreasing_log_axis_retains_small_values(self):
        calibration = reviewed_calibration()
        calibration["axes"]["x"]["scale"] = "log"
        calibration["axes"]["x"]["points"][0]["value"] = 1e-19
        calibration["axes"]["x"]["points"][1]["value"] = 1e-20
        status, result, _ = self.request(payload=self.payload | {"calibration": calibration})
        self.assertEqual(status, 200)
        values = result["candidates"][0]["data_points"]
        self.assertTrue(1e-20 < values[-1][0] < values[0][0] < 1e-19)

    def test_ocr_proposals_are_never_applied_automatically(self):
        proposal = {"ready": True, "review_required": True, "axes": reviewed_calibration()["axes"]}
        with patch.object(api, "suggest_axis_calibration", return_value=proposal) as recognizer:
            status, result, _ = self.request(payload=self.payload | {"suggest_calibration": True})
        self.assertEqual(status, 200)
        recognizer.assert_called_once()
        self.assertEqual(result["calibration_proposal"], proposal)
        self.assertIsNone(result["calibration"])
        self.assertIsNone(result["candidates"][0]["data_points"])

    def test_partial_ocr_results(self):
        proposal = {"ready": False, "review_required": True, "engine": None, "axes": {"x": {}, "y": {}}}
        with patch.object(api, "suggest_axis_calibration", return_value=proposal):
            status, result, _ = self.request("/v1/calibration")
        self.assertEqual(status, 200)
        self.assertFalse(result["calibration_proposal"]["ready"])
        self.assertTrue(result["review_required"])

    def test_csv_distinguishes_pixels_and_values(self):
        for calibrated in (False, True):
            payload = self.payload | {"format": "csv", "candidate_ids": [0]}
            if calibrated:
                payload["calibration"] = reviewed_calibration()
            status, output, headers = self.request(payload=payload)
            self.assertEqual(status, 200)
            self.assertIn("text/csv", headers["Content-Type"])
            rows = list(csv.DictReader(io.StringIO(output.decode())))
            self.assertGreater(len(rows), 100)
            self.assertEqual(rows[0]["candidate_id"], "0")
            self.assertEqual(bool(rows[0]["x"]), calibrated)
            self.assertTrue(rows[0]["pixel_x"])

    def test_selection_and_roi(self):
        status, result, _ = self.request(payload=self.payload | {"roi": [45, 35, 295, 185], "candidate_ids": []})
        self.assertEqual(status, 200)
        self.assertEqual(result["roi"], [45, 35, 295, 185])
        self.assertEqual(result["candidates"], [])
        for selection in ([True], [-1], [99], [0, 0], "0"):
            self.assertEqual(self.request(payload=self.payload | {"candidate_ids": selection})[0], 422)

    def test_invalid_calibration_fails_before_extraction(self):
        for reviewed in (None, False, "false"):
            calibration = reviewed_calibration()
            calibration["reviewed"] = reviewed
            with patch.object(api, "extract_plot_curves") as extract:
                self.assertEqual(self.request(payload=self.payload | {"calibration": calibration})[0], 422)
                extract.assert_not_called()
        calibration = reviewed_calibration()
        calibration["axes"]["x"]["points"][1]["pixel"] = [40, 190]
        self.assertEqual(self.request(payload=self.payload | {"calibration": calibration})[0], 422)

    def test_invalid_json_options_and_media_types(self):
        self.assertEqual(self.request(raw=b"{")[0], 400)
        self.assertEqual(self.request(raw=b'{"image_base64":NaN}')[0], 400)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request(payload={"image_base64": "!!!"})[0], 422)
        self.assertEqual(self.request(payload=self.payload | {"image_url": "http://example.com/image.png"})[0], 422)
        self.assertEqual(self.request(payload=self.payload | {"roi": [0, 0, 1e300, 100]})[0], 422)
        self.assertEqual(self.request(payload=self.payload | {"roi": [0, 0, 10**400, 100]})[0], 422)

    def test_size_check_precedes_body_read(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.putrequest("POST", "/v1/extract")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(api.MAX_BODY_BYTES + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            self.assertEqual(json.loads(response.read())["error"]["code"], "body_too_large")
        finally:
            connection.close()

    def test_host_origin_and_opt_in_cors(self):
        self.assertEqual(self.request("/health", method="GET", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "http://localhost:5174"})[0], 403)
        status, _, headers = self.request(headers={"Origin": "http://localhost:5173"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        status, _, headers = self.request(method="OPTIONS", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "http://localhost:5173")

    def test_busy_server_returns_retry_after_and_recovers(self):
        slots = self.server.processing_slots
        slots.acquire()
        slots.acquire()
        try:
            status, error, headers = self.request()
            self.assertEqual(status, 503)
            self.assertEqual(error["error"]["code"], "busy")
            self.assertEqual(headers["Retry-After"], "1")
        finally:
            slots.release()
            slots.release()
        self.assertEqual(self.request()[0], 200)

    def test_errors_hide_internal_details_and_routes_are_checked(self):
        with patch.object(api, "extract_plot_curves", side_effect=RuntimeError("private/internal/path")):
            status, error, _ = self.request()
        self.assertEqual(status, 500)
        self.assertNotIn("private", json.dumps(error))
        self.assertEqual(self.request("/missing", method="GET")[0], 404)
        self.assertEqual(self.request("/v1/extract", method="GET")[0], 405)

    def test_standard_library_client(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "plot.png"
            source.write_bytes(base64.b64decode(self.payload["image_base64"]))
            output = folder / "result.json"
            command = [sys.executable, str(ROOT / "scripts/api_example.py"), str(source), "--url",
                       "http://127.0.0.1:" + str(self.server.server_port), "--output", str(output)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(len(json.loads(output.read_bytes())["candidates"]), 1)

    def test_api_imports_without_gui_libraries(self):
        code = """
import importlib.abc
import sys
class NoGUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('PySide6', 'matplotlib', 'tkinter'):
            raise RuntimeError('GUI import is forbidden: ' + fullname)
sys.meta_path.insert(0, NoGUI())
from plot_digitizer.api import create_server, process_request
assert callable(create_server)
assert callable(process_request)
"""
        # Match the existing dependency-boundary checks: cold native-wheel imports
        # on hosted macOS runners can exceed 20 seconds. Socket behavior is tested
        # separately by the real HTTP cases above; this process checks imports only.
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_xrdml_has_counts_angles_and_settings(self):
        payload = self.payload | {"calibration": reviewed_calibration(), "candidate_ids": [0], "format": "xrdml",
                                  "xrdml": {"confirmed_pxrd": True, "dataset_label": "API PXRD", "counting_time": 2}}
        status, output, headers = self.request(payload=payload)
        self.assertEqual(status, 200)
        self.assertIn("application/xml", headers["Content-Type"])
        root = ET.fromstring(output)
        ns = {"x": "http://www.xrdml.com/XRDMeasurement/2.2"}
        self.assertEqual(root.find("x:sample/x:name", ns).text, "API PXRD")
        counts = root.find(".//x:counts", ns).text.split()
        self.assertGreater(len(counts), 100)
        self.assertTrue(all(int(value) >= 0 for value in counts))
        self.assertEqual(float(root.find(".//x:commonCountingTime", ns).text), 2)
        self.assertIn("reconstructed from a plot image", output.decode("utf-8-sig"))
        payload["calibration"]["reviewed"] = False
        self.assertEqual(self.request(payload=payload)[0], 422)

    def test_xrdml_rejects_unconfirmed_nonuniform_or_multiple_traces(self):
        _, result, _ = self.request(payload=self.payload | {"calibration": reviewed_calibration()})
        with self.assertRaises(api.APIError):
            api.xrdml_result(result, {})
        irregular = copy.deepcopy(result)
        del irregular["candidates"][0]["data_points"][20]
        with self.assertRaisesRegex(api.APIError, "evenly spaced"):
            api.xrdml_result(irregular, {"confirmed_pxrd": True})
        multiple = copy.deepcopy(result)
        multiple["candidates"].append(copy.deepcopy(multiple["candidates"][0]))
        with self.assertRaisesRegex(api.APIError, "exactly one"):
            api.xrdml_result(multiple, {"confirmed_pxrd": True})


class ImageAndBindingTests(unittest.TestCase):
    def test_image_limits_precede_opencv_allocation(self):
        header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 100000, 100000) + b"\x08\x02\x00\x00\x00" + b"\0" * 4
        with patch.object(api.cv2, "imdecode") as decoder:
            with self.assertRaises(api.APIError) as raised:
                api.decode_image(base64.b64encode(header).decode())
            self.assertEqual(raised.exception.status, 413)
            decoder.assert_not_called()

    def test_png_and_jpeg_dimensions(self):
        for suffix in (".png", ".jpg"):
            success, data = cv2.imencode(suffix, plot_image())
            self.assertTrue(success)
            image, metadata = api.decode_image(base64.b64encode(data).decode())
            self.assertEqual(image.shape, (220, 340, 3))
            self.assertEqual(metadata["height"], 220)
        with self.assertRaises(api.APIError):
            api.decode_image(base64.b64encode(b"not an image").decode())

    def test_nonlocal_hosts_and_origins_are_rejected(self):
        for host in ("0.0.0.0", "192.168.1.2", "example.com"):
            with self.assertRaises(ValueError):
                api.create_server(host, 0)
        for origin in ("https://example.com", "http://localhost:5173/", "null", "http://user@localhost:5173"):
            with self.assertRaises(ValueError):
                api.create_server(port=0, allowed_origins=[origin])


if __name__ == "__main__":
    unittest.main()
