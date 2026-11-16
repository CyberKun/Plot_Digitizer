# GitHub plot-digitizer review and positioning

Reviewed 16 November 2026. This compares the linked projects' published workflows
and export/integration descriptions. It is not an accuracy benchmark or an
exhaustive catalogue of every feature in their source code.

| Project | Published workflow | Integration/exports described | Relevance to PlotDigitizer |
| --- | --- | --- | --- |
| [WebPlotDigitizer](https://github.com/automeris-io/WebPlotDigitizer), [documentation](https://automeris.io/docs/digitize/) | Many axis/chart types, manual extraction and automatic algorithms. Documented auto setup includes masking, color/distance and algorithm parameters. | CSV/copy-paste. The frontend can run locally; the repository distinguishes its proprietary cloud AI Assist service. | Show which setup choices Autopilot makes and the four-action workflow. |
| [Engauge Digitizer maintained fork](https://github.com/jasonnicholson/engauge-digitizer) | Established desktop graph digitization; this fork emphasizes maintained Linux/Windows builds and documentation. | Portable desktop distributions and the traditional digitization workflow. | Portable/offline operation is already established. Make the spectrum workflow and scientific handoff visible. |
| [dilawar/PlotDigitizer](https://github.com/dilawar/PlotDigitizer/blob/main/README.md) | Python CLI for repeated similar plots. Users crop the image and supply calibration points; pixel locations can be supplied for batch use. | CSV, optional result plots and batch CLI. | Explain how image analysis reduces per-image setup while retaining calibration review. |
| [valentinognev/PlotDigitizer](https://github.com/valentinognev/PlotDigitizer) | Human-guided editing, axis placement, point tools, OpenCV tracing and resampling. | FastAPI REST backend, CSV, JSON and saved projects. | Local APIs already exist. Expose Autopilot results and calibrated scientific exports directly. |
| [LinePilot](https://github.com/RayanA07/LinePilot) | Manual, enhanced-click and OCR calibration with color-based path recovery. | Desktop, CLI, batch, Python API, CSV/Excel and overlays; its own benchmark. | Automatic calibration and programmatic use are established. Focus on spectrum behavior, review and XRDML integration. |

## Recommended message

**Scientific figures to reviewed curve data, ready for your analysis software.**

Lead with **Open Image → Auto-calibration → Auto Extraction → Export**. Explain
that Autopilot detects the plot area, proposes axis ticks, tries contrast/grid
handling and upward-peak/downward-dip settings, and presents curve candidates.
Review stays visible; manual recovery lives in one expandable section.

For PXRD users, show an actual XRDML handoff alongside CSV/JSON. For Raman/IR,
show decreasing/log axes and downward dips. For developers, show one local HTTP
request with source pixels, reviewed axis values and diagnostic metadata. The
portable Windows package removes Python setup from the first trial.

Demonstrate these features with original/extracted figures and clear launch
steps. Comparative robustness or speed rankings need the same corpus, calibration
protocol and metrics across tools. Our bundled examples illustrate behavior;
they do not establish a universal ranking.

## Formats

Current desktop and API outputs: CSV, JSON, and XRDML for diffraction workflows.
API XRDML requires one reviewed PXRD curve and uniform angle sampling.
JSON/CSV retain original sample positions and floating-point values.
See [API details](API.md#xrdml-for-diffraction-software).

SPA is a prospective spectroscopy format, not an implemented export. Advertise
it after a writer is implemented and round-tripped with representative files
in the intended analysis software. XRDML support does not imply SPA support.

The repository About copy is in [github-description.txt](github-description.txt).
