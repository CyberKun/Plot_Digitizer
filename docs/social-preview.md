# Social preview assets

[`images/social-preview.png`](images/social-preview.png) is the repository's
1280 × 640 social card. It shows the bundled PXRD figure next to a plot of
the reviewed extracted CSV, with the workflow and supported outputs.

The source figure is `examples/patent_plots/us8198452b2-fig1.png`. Its provenance
and separate reuse basis are documented in the [example note](../examples/README.md).
The numerical curve comes from `examples/pxrd-extracted.csv`; reviewed axis
coordinates and extraction settings are in `examples/pxrd-extraction.json`.
The illustration does not establish general accuracy or software endorsement.

## Regenerate

The renderer needs Pillow, which is included with the desktop Matplotlib
dependency. For an asset-only environment, install it separately:

```text
python -m pip install Pillow
python scripts/render_social_preview.py
```

Run from the repository root. The script leaves the input figure, CSV and
calibration/settings record unchanged. It draws the extracted curve from the
recorded samples and leaves gaps where observed columns are missing.
The renderer checks common Windows, Linux and macOS font locations. Supply
`--font /path/to/font.ttf` for a specific font. The generated PNG must remain
below GitHub's 1 MB upload limit.

## Set the repository preview

On GitHub, open **Settings → General → Social preview → Edit → Upload an image**
and choose `docs/images/social-preview.png`. Merely committing the file does
not select it as the social preview. Verify that the saved image appears in
settings; existing external link previews may take time to refresh.

The repository About text is tracked in
[`github-description.txt`](github-description.txt). Keep it aligned with the
README and current functionality.
