"""Local HTTP interface to Autopilot. Run with python -m plot_digitizer.api."""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
import math
import socket
import struct
import sys
import threading
import time
from urllib.parse import urlsplit

import cv2
import numpy as np

from .axis_calibration import calibration_coefficients, suggest_axis_calibration
from .digitizer_core import detect_plot_roi, extract_plot_curves
from .exports import export_xrdml

API_VERSION = "v1"
MAX_BODY_BYTES = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_SIDE = 16_000


class APIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


def _invalid(message):
    raise APIError(422, "invalid_request", message)


def _dimensions(data):
    """Inspect PNG/JPEG dimensions before allocating a decoded image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 33 or data[8:16] != b"\x00\x00\x00\rIHDR":
            _invalid("The PNG header is incomplete.")
        return struct.unpack(">II", data[16:24])
    if data.startswith(b"\xff\xd8"):
        offset, dimensions = 2, None
        while offset < len(data):
            if data[offset] != 255:
                _invalid("The JPEG header is invalid.")
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xDA, 0xD9):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            length = int.from_bytes(data[offset:offset + 2], "big")
            if length < 2 or offset + length > len(data):
                _invalid("The JPEG header is incomplete.")
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                if dimensions is not None or length < 8:
                    _invalid("The JPEG frame header is invalid.")
                height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                dimensions = width, height
            offset += length
        if dimensions:
            return dimensions
        _invalid("The JPEG frame dimensions are missing.")
    raise APIError(415, "unsupported_image", "Upload a PNG or JPEG image as base64.")


def decode_image(encoded):
    if not isinstance(encoded, str) or not encoded:
        _invalid("image_base64 must contain a PNG or JPEG encoded as base64.")
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise APIError(413, "image_too_large", "The encoded image exceeds 12 MiB.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        _invalid("image_base64 is not valid base64; send file bytes without a data-URL prefix.")
    if len(data) > MAX_IMAGE_BYTES:
        raise APIError(413, "image_too_large", "The image exceeds 12 MiB.")
    width, height = _dimensions(data)
    if max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS:
        raise APIError(413, "image_too_large", "Images must be at most 16 megapixels and 16000 pixels on each side.")
    if min(width, height) < 8:
        _invalid("The image must be at least 8 pixels wide and high.")
    try:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    except cv2.error:
        _invalid("The image could not be decoded.")
    if image is None or image.shape[:2] != (height, width):
        _invalid("The decoded image does not match its header.")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), {
        "width": width, "height": height, "sha256": hashlib.sha256(data).hexdigest(),
    }


def _number(value):
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        _invalid("Coordinates and tick values must be finite numbers.")
    return float(value)


def _roi(value, image):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        _invalid("roi must be [left, top, right, bottom] in source-image pixels.")
    left, top, right, bottom = (_number(v) for v in value)
    height, width = image.shape[:2]
    if not (0 <= left < right <= width and 0 <= top < bottom <= height
            and round(right) - round(left) >= 8 and round(bottom) - round(top) >= 8):
        _invalid("roi must lie inside the image and span at least 8 pixels on each axis.")
    return tuple(round(v) for v in (left, top, right, bottom))


def _calibration(value, image):
    if value is None:
        return None, None
    if not isinstance(value, dict) or value.get("reviewed") is not True:
        _invalid("Set calibration.reviewed to true only after reviewing both axis calibrations.")
    axes = value.get("axes")
    if not isinstance(axes, dict) or set(axes) != {"x", "y"}:
        _invalid("calibration.axes must contain x and y axes.")
    cleaned, coefficients = {}, {}
    height, width = image.shape[:2]
    for axis in ("x", "y"):
        entry = axes[axis]
        if not isinstance(entry, dict) or entry.get("scale", "linear") not in ("linear", "log"):
            _invalid("Each axis scale must be linear or log.")
        points = entry.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 64:
            _invalid("Each axis needs 2 to 64 reviewed tick points.")
        rows = []
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("pixel"), list) or len(point["pixel"]) != 2:
                _invalid("Each tick needs pixel: [x, y] and a numeric value.")
            px, py = map(_number, point["pixel"])
            if not (0 <= px < width and 0 <= py < height):
                _invalid("Calibration ticks must lie inside the source image.")
            rows.append({"pixel": [px, py], "value": _number(point.get("value"))})
        scale = entry.get("scale", "linear")
        try:
            coefficients[axis] = calibration_coefficients(rows, axis, scale)
        except ValueError as error:
            _invalid(str(error))
        cleaned[axis] = {"scale": scale, "points": rows}
    return {"reviewed": True, "axes": cleaned}, coefficients


def process_request(payload, *, calibration_only=False):
    """Stateless adapter shared by the HTTP handler and embedded integrations."""
    if not isinstance(payload, dict):
        _invalid("Send a JSON object.")
    allowed = {"image_base64", "roi"}
    if not calibration_only:
        allowed |= {"calibration", "suggest_calibration", "candidate_ids", "format", "xrdml"}
    if set(payload) - allowed:
        _invalid("Unknown request fields: " + ", ".join(sorted(set(payload) - allowed)))
    image, source = decode_image(payload.get("image_base64"))
    roi = _roi(payload.get("roi"), image)
    if calibration_only:
        box = roi or detect_plot_roi(image) or (0, 0, source["width"], source["height"])
        return {"api_version": API_VERSION, "source_image": source, "roi": box,
                "calibration_proposal": suggest_axis_calibration(image, box), "review_required": True}
    if payload.get("format", "json") not in ("json", "csv", "xrdml"):
        _invalid("format must be json, csv or xrdml.")
    suggest = payload.get("suggest_calibration", False)
    if not isinstance(suggest, bool):
        _invalid("suggest_calibration must be true or false.")
    calibration, coefficients = _calibration(payload.get("calibration"), image)
    result = extract_plot_curves(image, roi=roi)
    proposal = suggest_axis_calibration(image, result["roi"]) if suggest else None
    for index, candidate in enumerate(result["candidates"]):
        candidate["id"] = index
        candidate["data_points"] = None
        if coefficients:
            values = np.asarray(candidate["points"], dtype=float)
            for col, axis in enumerate(("x", "y")):
                values[:, col] = np.polyval(coefficients[axis], values[:, col])
                if calibration["axes"][axis]["scale"] == "log":
                    with np.errstate(over="ignore", under="ignore"):
                        values[:, col] = np.power(10.0, values[:, col])
                    if np.any(values[:, col] <= 0):
                        _invalid("The log calibration produces unrepresentable values; check the ticks.")
            if not np.all(np.isfinite(values)):
                _invalid("The calibration produces unrepresentable values; check the ticks.")
            candidate["data_points"] = values.tolist()
    selected = payload.get("candidate_ids")
    if selected is not None:
        if (not isinstance(selected, list) or any(type(i) is not int for i in selected)
                or len(set(selected)) != len(selected)
                or any(i < 0 or i >= len(result["candidates"]) for i in selected)):
            _invalid("candidate_ids must be unique curve IDs from this extraction, starting at zero.")
        result["candidates"] = [c for c in result["candidates"] if c["id"] in selected]
    result.update(api_version=API_VERSION, source_image=source, calibration=calibration,
                  calibration_proposal=proposal, coordinate_system="axis_values" if calibration else "image_pixels",
                  review_required=True)
    return result


def csv_result(result):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["candidate_id", "pixel_x", "pixel_y", "x", "y"])
    for candidate in result["candidates"]:
        for index, pixel in enumerate(candidate["points"]):
            real = candidate["data_points"][index] if candidate["data_points"] is not None else ("", "")
            writer.writerow([candidate["id"], *pixel, *real])
    return stream.getvalue().encode("utf-8")


def xrdml_result(result, options):
    if not result["calibration"]:
        _invalid("XRDML requires reviewed axis calibration.")
    if not isinstance(options, dict) or options.get("confirmed_pxrd") is not True:
        _invalid("Set xrdml.confirmed_pxrd to true for PXRD with a 2Theta axis in degrees and counts/cps intensities.")
    curves = result["candidates"]
    if len(curves) != 1:
        _invalid("Select exactly one candidate_id for each XRDML file.")
    allowed = {"confirmed_pxrd", "dataset_label", "wavelength_ka1", "wavelength_ka2", "wavelength_kb",
               "generator_voltage", "generator_current", "counting_time", "y_scale", "y_baseline"}
    if set(options) - allowed:
        _invalid("Unknown XRDML options: " + ", ".join(sorted(set(options) - allowed)))
    settings = {key: value for key, value in options.items() if key != "confirmed_pxrd"}
    for key, value in settings.items():
        if key == "dataset_label":
            if (not isinstance(value, str) or len(value) > 200
                    or any((ord(char) < 32 and char not in "\t\n\r") or 0xD800 <= ord(char) <= 0xDFFF
                           or ord(char) in (0xFFFE, 0xFFFF) for char in value)):
                _invalid("dataset_label must be text with at most 200 characters.")
        else:
            settings[key] = _number(value)
            if key != "y_baseline" and settings[key] <= 0:
                _invalid("XRDML wavelengths, tube settings, counting time and y_scale must be positive.")
    points = np.asarray(curves[0]["data_points"], dtype=float)
    points = points[np.argsort(points[:, 0])]
    if len(points) < 2 or np.any(points[:, 0] < 0) or np.any(points[:, 0] > 180):
        _invalid("XRDML needs at least two 2Theta values between 0 and 180 degrees.")
    steps = np.diff(points[:, 0])
    if np.any(steps <= 0) or not np.allclose(steps, steps[0], rtol=1e-6, atol=1e-9):
        _invalid("This XRDML writer requires evenly spaced angles. Use JSON/CSV to preserve gaps or nonuniform sampling.")
    counts = np.maximum(0, np.round((points[:, 1] - settings.get("y_baseline", 0)) * settings.get("y_scale", 1)))
    if not np.all(np.isfinite(counts)) or np.any(counts >= np.iinfo(np.int64).max):
        _invalid("XRDML counts exceed the supported integer range.")
    output = io.StringIO()
    export_xrdml(output, points[:, 0], points[:, 1],
                 comment="Digitized by PlotDigitizer local API; reconstructed from a plot image, not a raw measurement.",
                 **settings)
    return output.getvalue().encode("utf-8-sig")


def openapi_schema():
    point = {"type": "object", "required": ["pixel", "value"], "properties": {
        "pixel": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}},
        "value": {"type": "number"}}}
    axis = {"type": "object", "required": ["points"], "properties": {
        "scale": {"type": "string", "enum": ["linear", "log"], "default": "linear"},
        "points": {"type": "array", "minItems": 2, "maxItems": 64, "items": point}}}
    image = {"image_base64": {"type": "string", "description": "Base64 PNG/JPEG bytes; no URL or filesystem path."},
             "roi": {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number"}}}
    extra = {"suggest_calibration": {"type": "boolean", "default": False},
             "format": {"type": "string", "enum": ["json", "csv", "xrdml"], "default": "json"},
             "candidate_ids": {"type": "array", "uniqueItems": True, "items": {"type": "integer", "minimum": 0}},
             "xrdml": {"type": "object", "description": "PXRD export settings; confirmed_pxrd: true is required.",
                       "properties": {"confirmed_pxrd": {"type": "boolean", "const": True}}},
             "calibration": {"type": "object", "required": ["reviewed", "axes"], "properties": {
                 "reviewed": {"type": "boolean", "const": True},
                 "axes": {"type": "object", "required": ["x", "y"], "properties": {"x": axis, "y": axis}}}}}
    paths = {"/health": {"get": {"summary": "Check that the local API is ready", "responses": {"200": {"description": "Service status"}}}}}
    for path, properties, summary in (
        ("/v1/extract", image | extra, "Detect curves and optionally apply reviewed calibration"),
        ("/v1/calibration", image, "Propose editable axis calibration using installed local OCR"),
    ):
        content = {"application/json": {"schema": {"type": "object"}}}
        if path.endswith("extract"):
            content |= {"text/csv": {"schema": {"type": "string"}}, "application/xml": {"schema": {"type": "string"}}}
        paths[path] = {"post": {"summary": summary, "requestBody": {"required": True, "content": {
            "application/json": {"schema": {"type": "object", "required": ["image_base64"],
                                           "additionalProperties": False, "properties": properties}}}},
            "responses": {"200": {"description": "Reviewable extraction/calibration result", "content": content},
                          "400": {"description": "Invalid JSON"}, "413": {"description": "Request/image exceeds limits"},
                          "415": {"description": "Unsupported media type"}, "422": {"description": "Invalid plot/options"},
                          "503": {"description": "Processing slots busy; retry later"}}}}
    return {"openapi": "3.1.0", "info": {"title": "PlotDigitizer local API", "version": "1.0.0"}, "paths": paths}


def _json_bytes(value):
    def convert(item):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError("Not JSON serializable")
    return json.dumps(value, allow_nan=False, default=convert, separators=(",", ":")).encode("utf-8")


def _reject_json_constant(value):
    raise ValueError("Nonfinite JSON number: " + value)


class APIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PlotDigitizerAPI/1"

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def _check_client(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in self.server.allowed_hosts:
            raise APIError(403, "invalid_host", "Use the local API host and port.")
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] not in self.server.allowed_origins):
            raise APIError(403, "origin_not_allowed", "This browser origin has not been enabled.")

    def _drain_rejected_body(self):
        # Unread uploads can cause a TCP reset on Windows and discard errors.
        if getattr(self, "_body_read", False):
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or len(lengths[0]) > 12 or not lengths[0].isascii() or not lengths[0].isdigit():
            return
        remaining = int(lengths[0])
        if not 0 < remaining <= MAX_BODY_BYTES:
            return
        deadline = time.monotonic() + .25
        try:
            while remaining and time.monotonic() < deadline:
                self.connection.settimeout(max(.001, deadline - time.monotonic()))
                block = self.rfile.read(min(remaining, 65536))
                if not block:
                    break
                remaining -= len(block)
        except OSError:
            pass
        finally:
            self.connection.settimeout(15)
            self._body_read = True

    def _send(self, status, value, content_type="application/json; charset=utf-8", *, preflight=False):
        if status >= 400:
            self._drain_rejected_body()
        body = value if isinstance(value, bytes) else _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        origin = self.headers.get("Origin")
        if origin in self.server.allowed_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if preflight:
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if status == 503:
            self.send_header("Retry-After", "1")
        if status == 405:
            self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()
        self.close_connection = True
        if self.command != "HEAD":
            self.wfile.write(body)

    def _dispatch(self):
        try:
            self._check_client()
            path = urlsplit(self.path).path
            if self.command == "OPTIONS":
                method = self.headers.get("Access-Control-Request-Method", "POST")
                headers = self.headers.get("Access-Control-Request-Headers", "content-type")
                if method not in ("GET", "POST") or any(h.strip().lower() != "content-type" for h in headers.split(",")):
                    raise APIError(403, "preflight_not_allowed", "Only GET/POST with Content-Type are supported.")
                return self._send(204, b"", preflight=True)
            if self.command == "GET" and path == "/health":
                return self._send(200, {"status": "ok", "service": "PlotDigitizer", "api_version": API_VERSION,
                                        "max_body_bytes": MAX_BODY_BYTES, "max_image_pixels": MAX_IMAGE_PIXELS})
            if self.command == "GET" and path == "/openapi.json":
                return self._send(200, openapi_schema())
            if path not in ("/health", "/openapi.json", "/v1/extract", "/v1/calibration"):
                raise APIError(404, "not_found", "Unknown API endpoint. See /openapi.json.")
            if self.command != "POST" or path not in ("/v1/extract", "/v1/calibration"):
                raise APIError(405, "method_not_allowed", "Use GET for health/schema and POST for processing.")
            if self.headers.get_content_type() != "application/json":
                raise APIError(415, "unsupported_media_type", "Send Content-Type: application/json.")
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get("Transfer-Encoding") or len(lengths) != 1 or len(lengths[0]) > 12
                    or not lengths[0].isascii() or not lengths[0].isdigit()):
                raise APIError(411, "length_required", "Send one Content-Length header; chunked bodies are not supported.")
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY_BYTES:
                raise APIError(413, "body_too_large", "Send a nonempty JSON body of at most 16 MiB.")
            body = self.rfile.read(length)
            self._body_read = True
            if len(body) != length:
                raise APIError(400, "incomplete_body", "The JSON body was incomplete.")
            if not self.server.processing_slots.acquire(blocking=False):
                raise APIError(503, "busy", "The API is processing other plots. Retry shortly.")
            try:
                try:
                    payload = json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
                except (ValueError, UnicodeDecodeError, RecursionError):
                    raise APIError(400, "invalid_json", "Send a valid UTF-8 JSON object.") from None
                result = process_request(payload, calibration_only=path.endswith("calibration"))
                if payload.get("format") == "csv":
                    return self._send(200, csv_result(result), "text/csv; charset=utf-8")
                if payload.get("format") == "xrdml":
                    return self._send(200, xrdml_result(result, payload.get("xrdml")), "application/xml; charset=utf-8")
                return self._send(200, result)
            finally:
                self.server.processing_slots.release()
        except APIError as error:
            self._send(error.status, {"error": {"code": error.code, "message": str(error)}})
        except (socket.timeout, TimeoutError):
            self._send(408, {"error": {"code": "request_timeout", "message": "The request timed out."}})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except ValueError as error:
            self._send(422, {"error": {"code": "invalid_plot", "message": str(error)}})
        except Exception as error:
            self.log_error("Processing failed (%s)", type(error).__name__)
            self._send(500, {"error": {"code": "processing_failed", "message": "The plot could not be processed."}})

    do_GET = do_POST = do_OPTIONS = do_HEAD = do_PUT = do_DELETE = _dispatch


class LocalAPIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(host="127.0.0.1", port=8765, *, allowed_origins=()):
    """Create a loopback-only server; port=0 selects a free port for embedding."""
    host = "127.0.0.1" if host == "localhost" else host
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("Use a loopback IP address, such as 127.0.0.1 or ::1.") from None
    if not address.is_loopback or not 0 <= port <= 65535:
        raise ValueError("The API must use a loopback address and a valid port.")
    origins = set(allowed_origins)
    for origin in origins:
        parsed = urlsplit(origin)
        try:
            local = parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            local = False
        if (not local or parsed.scheme not in ("http", "https") or parsed.path or parsed.query or parsed.fragment
                or parsed.username is not None or parsed.password is not None):
            raise ValueError("Browser origins must be exact local HTTP(S) origins without a trailing slash.")
        _ = parsed.port
    server_class = LocalAPIServer
    if address.version == 6:
        class IPv6LocalAPIServer(LocalAPIServer):
            address_family = socket.AF_INET6
        server_class = IPv6LocalAPIServer
    server = server_class((host, port), APIHandler)
    authority = ("[" + str(address) + "]") if address.version == 6 else str(address)
    server.allowed_hosts = {authority + ":" + str(server.server_port), "localhost:" + str(server.server_port)}
    server.allowed_origins = origins | {"http://" + h for h in server.allowed_hosts}
    server.processing_slots = threading.BoundedSemaphore(2)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the PlotDigitizer local HTTP API without opening the GUI.")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback address (default: 127.0.0.1)")
    parser.add_argument("--port", default=8765, type=int, help="Listening port (default: 8765)")
    parser.add_argument("--allow-origin", action="append", default=[], help="Allow one exact local browser origin; repeat for more")
    args = parser.parse_args(argv)
    try:
        server = create_server(args.host, args.port, allowed_origins=args.allow_origin)
    except (ValueError, OSError) as error:
        print("Could not start PlotDigitizer API: " + str(error), file=sys.stderr)
        return 1
    authority = next(h for h in server.allowed_hosts if not h.startswith("localhost:"))
    print("PlotDigitizer API: http://" + authority + " (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
