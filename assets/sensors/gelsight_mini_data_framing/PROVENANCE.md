# `gelsight_mini_data_framing` sensor assets

These describe one physical GelSight Mini (R0B 2D8Y-69GR, 11 × 17 marker gel)
**as slip-perception's data collection frames it**: `gs_sdk`'s `resize_crop`
with a 1/25 border, then bilinear to 320 × 240, in BGR.
[Live sensor capture](../../../docs/live-sensor.md) records in that framing, so
a simulation using these assets draws its markers where a trial's `gs.npz` has
them, to within the errors listed below.

| File | What it is |
|---|---|
| `unloaded_reference.png` | Mean of the first 10 frames of the 2026-09-22 recording `20260922-184921`, taken with nothing touching the gel |
| `background.png` | The same frame with its 187 markers inpainted (OpenCV Telea, 9 px dilated dot mask), for `optics.background_image`: the simulator draws its own markers |

Values used by `configs/real_mini_*_press.json`, measured from three
recordings of the unloaded gel that day:

| Quantity | Value | How it was found |
|---|---|---|
| Marker pitch | 14.54–14.60 × 14.58–14.61 px | Detected dot centers, 11 × 17 |
| Symmetric margins (y, x) | 46.4, 42.9 px | Centered grid with the measured extent |
| Grid center | (161.9–166.6, 122.7–123.8) px | Moves between sessions: see below |
| Grid tilt | 0.5–0.6° | Not representable in the simulator's grid |
| Dot radius | 0.22 mm | Real dark core 3.39 px; renders 3.43 px (0.25 mm rendered 3.87 px) |
| Colour rotation | 90° counterclockwise | `optics.response_rotation_deg`, matched by eye to the recorded sphere presses |
| Scale | 0.0634 mm/px | `gs_sdk` `gsmini.yaml` `ppmm`, *not measured on this unit* |
| Camera FOV | 20.288 × 15.216 mm | 320 × 240 px × 0.0634 mm/px |

**Known errors, uncorrected:**

- **Scale is nominal.** The 0.0634 mm/px is the SDK's value for the Mini in
  this framing, used by its own photometric calibration. It has not been
  checked on this unit. A 10 % scale error scales every simulated distance,
  marker pitch and patch size by the same amount.
- **The grid is not centered.** The real grid center sits 2–7 px right of and
  3–4 px below the image center. The simulator always centers its grid, so a
  simulated marker lands up to about 7 px from the real one in absolute
  pixels. Presses are placed relative to the grid, not the image.
- **The gel moves in its holder.** The whole image, gel walls included,
  shifted by about 4 px between recordings made minutes apart.
- **Gel geometry and material are the 11 × 17 preset's**, not measured: a
  22 × 16 mm face, 5 mm thick, 100 kPa Neo-Hookean. The real flat face looks
  closer to 18 × 12.6 mm at the nominal scale.

`calibrated` stays `false`. See the calibration TODO in
[live-sensor.md](../../../docs/live-sensor.md#calibration-todo).
