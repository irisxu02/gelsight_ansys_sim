# `gelsight_custom_11x17` example sensor

## Background

This sensor uses the same optics as the GelSight Mini, so `background.png` is
the stock marker-free Mini background from
[`data/mini`](../../../src/gelsight_ansys/data/mini/PROVENANCE.md), stored here
already rotated into the project's native 320 × 240 landscape orientation. It
is pixel-identical to the background the Mini preset renders with (the
`TaximResponse` background after its 90 degree clockwise rotation), so
`optics.background_image` loads it with no transpose.

An earlier version of this asset was frame 0 of the supplied capture below.
That frame has the real dots photographed into it, which doubled the synthetic
markers wherever the two lattices disagreed, so it is no longer used as a
background.

## Marker lattice

The lattice is fitted to a 406-frame raw capture of the sensor (`gs.npz`, keys
`frames` `(406, 240, 320, 3)` uint8, `t_ns`, `frame_idx`). Frame 0 is taken as
unloaded: per-frame mean absolute difference against it stays near a few
intensity units through the first frames, rises during a press around frames
80-340, and falls back by frame 400. No separate no-contact reference was
supplied.

Dots were found in frame 0 by subtracting a Gaussian-blurred (sigma 8 px)
grey image, thresholding the darkening at 12 intensity units, and taking the
darkening-weighted centroid of each connected region of 10-120 px. That gives
exactly 187 dots in 11 rows of 17, plus a few edge artifacts outside the pad,
which were discarded. A least-squares uniform lattice through them has:

| Quantity | Fitted |
|---|---|
| First / last column center | x = 47.4 / 280.6 px |
| First / last row center | y = 51.5 / 197.9 px |
| Pitch | 14.57 px (x), 14.64 px (y) |
| Dot radius | about 3.4 px (equal-area), 0.2 mm |

In config terms this is `marker_margin_px` `[46.3, 42.9]` with
`marker_offset_px` `[5.2, 4.5]` (rows, columns). The fitted lattice lies
1.7 px mean and 4.8 px worst case from the detected centers, against 7.9 px
mean and 14.2 px worst for the previous, unmeasured 10 px margins. The
residual is the capture's slight perspective: dots spread a little wider
toward the bottom right than a uniform lattice allows.

`marker_spacing_m` (0.85 mm) records the pitch for reference only; with
`marker_grid_rows_cols` set, the lattice is placed from pixels.

## Calibration status

No device-specific photometric calibration is derived from the capture.
`optics.model` remains `taxim` and reuses
[`data/mini/polycalib.npz`](../../../src/gelsight_ansys/data/mini/PROVENANCE.md).
The lattice is a pixel fit on the nominal camera, not a calibrated camera
model, and `calibrated` remains `false`, matching the disclaimers in
`data/mini/PROVENANCE.md` and `docs/sensor-alignment.md` ("Device calibration
requirements").
