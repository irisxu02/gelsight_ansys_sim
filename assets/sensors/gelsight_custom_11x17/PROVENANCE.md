# `gelsight_custom_11x17` example background

`background.png` is frame index 0 of a 406-frame raw capture (`gs.npz`,
keys `frames` `(406, 240, 320, 3)` uint8, `t_ns`, `frame_idx`) supplied for this
sensor. It is saved unrotated at the project's native 320 × 240 landscape
orientation, so `optics.background_image` loads it directly with no
transpose (unlike [`data/mini`](../../../src/gelsight_ansys/data/mini/PROVENANCE.md),
whose upstream asset needed one).

Frame 0 was chosen as the unloaded reference: per-frame mean absolute
difference against frame 0 is 0 at frame 0 (trivially), stays near a few
intensity units through the first frames, rises to its captured maximum
around frames 80-340 (a press/contact event), and falls back to roughly its
starting level by frame 400. No independent unloaded-frame confirmation
(e.g. a separate no-contact reference) was supplied, so this is the best
available unloaded frame from the sequence, not a confirmed reference
capture.

**No device-specific photometric calibration or marker-center detection is
derived from these frames.** Automated marker-center detection was attempted
(background-flattened local-minima search) and did not converge on a clean,
countable grid, so no marker pitch, radius, or position was measured from
this capture. `optics.model` remains `taxim` and reuses
[`data/mini/polycalib.npz`](../../../src/gelsight_ansys/data/mini/PROVENANCE.md)
for photometric-stereo shading, exactly as the repo's existing 11 × 17 preset
([`custom_gel_press.json`](../../../configs/custom_gel_press.json), see
[sensor-alignment.md](../../../docs/sensor-alignment.md)) already does. The
marker grid itself (`marker_grid_rows_cols`, `marker_spacing_m`,
`marker_radius_m`, `marker_margin_px`) is the synthetic overlay described in
[`camera.py`](../../../src/gelsight_ansys/camera.py) and
[`surface.py`](../../../src/gelsight_ansys/surface.py); this preset reuses that
same preset's already-declared 11 × 17 pitch on the shared 320 × 240 / 18.6 ×
14.3 mm camera, rather than a value newly measured from `gs.npz`.

`calibrated` remains `false`. This asset supplies an appearance reference
only, matching the disclaimers in `data/mini/PROVENANCE.md` and
`docs/sensor-alignment.md` ("Device calibration requirements").
