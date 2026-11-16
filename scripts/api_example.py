"""Submit a plot to a running local API using Python's standard library."""
import argparse
import base64
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, help="JSON containing reviewed: true and axes")
    parser.add_argument("--suggest-calibration", action="store_true")
    parser.add_argument("--format", choices=("json", "csv", "xrdml"), default="json")
    parser.add_argument("--candidate-id", type=int, action="append", help="Keep a zero-based curve ID; repeat to select more")
    parser.add_argument("--confirm-pxrd", action="store_true", help="Confirm 2Theta degrees and counts/cps for XRDML")
    args = parser.parse_args(argv)
    try:
        payload = {"image_base64": base64.b64encode(args.image.read_bytes()).decode("ascii"),
                   "suggest_calibration": args.suggest_calibration, "format": args.format}
        if args.calibration:
            payload["calibration"] = json.loads(args.calibration.read_text(encoding="utf-8"))
        if args.candidate_id is not None:
            payload["candidate_ids"] = args.candidate_id
        if args.confirm_pxrd:
            payload["xrdml"] = {"confirmed_pxrd": True}
        request = Request(args.url.rstrip("/") + "/v1/extract", data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=120) as response:
            result = response.read()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(result)
        if args.format == "json":
            data = json.loads(result)
            print(f"Saved {len(data['candidates'])} candidates ({data['coordinate_system']}) to {args.output}")
        else:
            print("Saved " + args.format.upper() + " to " + str(args.output))
    except HTTPError as error:
        print("API rejected the request: " + error.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except (OSError, ValueError, URLError) as error:
        print("Could not digitize the image: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
