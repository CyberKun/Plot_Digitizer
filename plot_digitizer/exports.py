"""Headless scientific-format export shared by the desktop app and local API."""

import numpy as np


def export_xrdml(path, two_theta_arr, intensity_arr, dataset_label="Digitized",
                 wavelength_ka1=1.5405980, wavelength_ka2=1.5444260,
                 wavelength_kb=1.3922500,
                 generator_voltage=45.0, generator_current=40.0,
                 counting_time=1.0, y_scale=1.0, y_baseline=0.0,
                 comment="Digitized by PlotDigitizer"):
    """Write HighScore-compatible XRDML v2.2 matching real Empyrean output.
    counting_time: HighScore divides raw counts by this to display cps.
      Set to 1.0 if Y axis is already in cps (default).
      Set to actual dwell time T if Y axis is in raw counts.
    y_scale: multiplied into baseline-corrected intensities before writing.
    y_baseline: subtracted before scaling. Default export preserves the CSV
      intensity scale apart from integer count rounding.
    """
    from datetime import datetime
    import xml.etree.ElementTree as ET
    from xml.dom import minidom

    t = np.asarray(two_theta_arr, dtype=float)
    y = np.asarray(intensity_arr, dtype=float)
    order = np.argsort(t); t = t[order]; y = y[order]
    _, uniq = np.unique(t, return_index=True)
    t = t[uniq]; y = y[uniq]
    n = len(t)
    if n < 2:
        raise ValueError("Need at least 2 data points to write XRDML.")
    start2t = float(t[0])
    end2t   = float(t[-1])
    y = (y - float(y_baseline)) * float(y_scale)
    y_int = np.maximum(0, np.round(y).astype(int))

    NS  = "http://www.xrdml.com/XRDMeasurement/2.2"
    XSI = "http://www.w3.org/2001/XMLSchema-instance"
    LOC = NS + " " + NS + "/XRDMeasurement.xsd"
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def el(parent, tag, text=None, **attrs):
        e = ET.SubElement(parent, tag, **attrs)
        if text is not None:
            e.text = str(text)
        return e

    root = ET.Element("xrdMeasurements",
                      xmlns=NS,
                      **{"xmlns:xsi": XSI, "xsi:schemaLocation": LOC, "status": "Completed"})

    rc = el(root, "comment")
    el(rc, "entry", "Digitized dataset: " + dataset_label)
    el(rc, "entry", "Created by PlotDigitizer")

    sample = el(root, "sample", type="To be analyzed")
    el(sample, "id",         dataset_label[:60])
    el(sample, "name",       dataset_label[:60])
    el(sample, "preparedBy", "PlotDigitizer")

    meas = el(root, "xrdMeasurement",
              measurementType="Scan", status="Completed", sampleMode="Reflection")
    mc = el(meas, "comment"); el(mc, "entry", comment)

    wl = el(meas, "usedWavelength", intended="K-Alpha 1")
    el(wl, "kAlpha1", "%.7f" % wavelength_ka1, unit="Angstrom")
    el(wl, "kAlpha2", "%.7f" % wavelength_ka2, unit="Angstrom")
    el(wl, "kBeta",   "%.7f" % wavelength_kb,  unit="Angstrom")
    el(wl, "ratioKAlpha2KAlpha1", "0.5000")

    ibp = el(meas, "incidentBeamPath")
    el(ibp, "radius", "240.00", unit="mm")
    xrt = el(ibp, "xRayTube", id="0", name="Generic Cu tube")
    el(xrt, "tension",       "%.1f" % generator_voltage, unit="kV")
    el(xrt, "current",       "%.1f" % generator_current, unit="mA")
    el(xrt, "anodeMaterial", "Cu")
    foc = el(xrt, "focus", type="Line")
    el(foc, "length", "12.0", unit="mm")
    el(foc, "width",  "0.4",  unit="mm")
    el(foc, "takeOffAngle", "7.6", unit="deg")

    dbp = el(meas, "diffractedBeamPath")
    el(dbp, "radius", "240.00", unit="mm")
    # Declare a point-detector so HighScore skips PIXcel multi-channel
    # normalisation (which inflates digitized baselines by ~5x)
    det = el(dbp, "detector", id="0", name="Digitized (point)",
             **{"xsi:type": "pointDetectorType"})
    phd = el(det, "phd", unit="%")
    el(phd, "lowerLevel", "10.0")
    el(phd, "upperLevel", "90.0")

    scan = el(meas, "scan",
              appendNumber="0", mode="Continuous", scanAxis="Gonio", status="Completed")
    hdr = el(scan, "header")
    el(hdr, "startTimeStamp", now)
    el(hdr, "endTimeStamp",   now)
    author = el(hdr, "author"); el(author, "name", "PlotDigitizer")
    source = el(hdr, "source")
    el(source, "applicationSoftware",       "PlotDigitizer", version="1.0")
    el(source, "instrumentControlSoftware", "N/A",           version="N/A")
    el(source, "instrumentID", "0")

    dp = el(scan, "dataPoints")
    pos2t = el(dp, "positions", axis="2Theta", unit="deg")
    el(pos2t, "startPosition", "%.8f" % start2t)
    el(pos2t, "endPosition",   "%.8f" % end2t)
    posOm = el(dp, "positions", axis="Omega", unit="deg")
    el(posOm, "startPosition", "%.8f" % (start2t / 2.0))
    el(posOm, "endPosition",   "%.8f" % (end2t   / 2.0))

    ct_val = float(counting_time)
    el(dp, "commonCountingTime", "%.4f" % ct_val, unit="seconds")
    el(dp, "counts", " ".join(str(v) for v in y_int), unit="counts")

    raw    = ET.tostring(root, encoding="unicode", xml_declaration=False)
    pretty = minidom.parseString(raw).toprettyxml(indent="\t", encoding=None)
    lines  = pretty.split("\n")
    lines[0] = '<?xml version="1.0"?>'
    result = "\n".join(l for l in lines if l.strip() != "")
    if hasattr(path, "write"):
        path.write(result + "\n")
    else:
        with open(path, "w", encoding="utf-8-sig") as fh:
            fh.write(result + "\n")
