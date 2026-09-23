# Live GelSight Mini capture

[Documentation](README.md) · [Sensor alignment](sensor-alignment.md) · [Dataset](dataset.md)

`scripts/gelsight_stream.py` views and records a USB GelSight Mini. Its images
are made exactly as slip-perception's real-world data collection makes them,
and its recordings are saved in that collection's `gs.npz` format. A marker at
pixel (u, v) in a recording made here is therefore at (u, v) in a trial
recorded by `run_trials.sh`. With `--sim-run`, the window shows a finished
simulation run beside the live sensor, with both signed differences on one
scale.

## Setup

```bash
pip install -e .[sensor]          # OpenCV, with its Qt window backend
python scripts/gelsight_stream.py --list
```

`--list` prints every GelSight capture node (`/dev/videoN`) and its current V4L2
controls. The user needs to be in the `video` group. `v4l2-ctl` (package
`v4l-utils`) is optional; with it, each recording saves the camera controls and
`--control` can set them.

## View and record

```bash
# Live view beside a simulation run
python scripts/gelsight_stream.py --sim-run outputs/<run> --label sphere3mm_press

# Headless: 10 s to outputs/gelsight_mini/<timestamp>_press/gs.npz
python scripts/gelsight_stream.py --no-display --duration 10 --label press
```

Start with nothing touching the gel. One second after opening, the mean of ten
frames becomes the **unloaded reference** for the difference panel; press `b`
to take it again.

| Key | Action |
|---|---|
| `space` / `r` | Start or stop recording |
| `b` | Capture a new unloaded reference (also saved into a running recording) |
| `s` | Snapshot the live frame (and its difference) to `outputs/gelsight_mini/snapshots/` |
| `d` | Show or hide the difference row |
| `g` | Overlay a sim preset's unloaded marker centers (`grid` framing only) |
| `+` / `-` | Double or halve the difference gain |
| `,` / `.` / `p` | Previous, next, or play the sim frames |
| `q` / `Esc` | Quit; a running recording is finished and saved |

Differences are drawn as `128 + gain × (frame − reference)`, gray at zero. The
default gain 0.5 is the one `compare_rendering` uses for its subtracted panels.
The sim panel's reference is the run's `unloaded_reference.png` for plane runs,
else its frame 0 ([dataset](dataset.md#run-files-and-coordinates)).

## Framing

The Mini offers one mode, 3280 × 2464 MJPEG. slip-perception's `run_trials.sh`
records through `gs_sdk/examples/fast_stream_device.py`, whose
`gs_sdk.gs_device.FastCamera` decodes the full frame to BGR and calls
`resize_crop(frame, 320, 240)`. That function takes a **1/25** border off each
side, trims rows or columns from the start to reach 4:3, and applies
`cv2.resize` with its default bilinear interpolation. (Its comment still says
1/7, but the code uses 1/25.) The default `--crop gs_sdk` is a line-for-line
port of it:

| `--crop` | Raw box kept (x0, y0, x1, y1) | Source |
|---|---|---|
| `gs_sdk` (default) | 131, 102, 3149, 2366 | `gs_sdk` fork's `resize_crop`: the data collection |
| `gsrobotics` | 468, 354, 2812, 2112 | 1/7 border: upstream GelSight SDK and `gs-marker-utils/src/stream_gelsight_utils.py` |
| `full` | 0, 0, 3280, 2464 | The whole sensor |
| `x0,y0,x1,y1` | as given | A box in raw sensor pixels |
| `grid:ROWSxCOLS[:MARGIN]` | — | Remap into a *sim preset's* pixel space (below) |

Each frame is decoded at full resolution (two decoder threads keep up with the
camera) and cropped with exactly the SDK's slicing and `cv2.resize`. Frames
stay BGR from the decoder to the file.

On the tested unit, recorded frames are **identical** to `resize_crop` applied
to the same camera JPEGs. The only possible difference from a trial is the
JPEG decoder: `FastCamera` decodes with ffmpeg, while this tool uses OpenCV.
The two differ by 0.8 intensity levels on average after the crop, which moves
marker centers by at most 0.19 px. The marker pitch measured in a recording
(14.55 × 14.63 px) matches that of the `gs.npz` trial capture
(14.56 × 14.58 px). That capture's markers sit about (3.1, −0.9) px from
today's. The shift is uniform, which suggests the gel sat differently in the
sensor rather than a processing difference.

**`grid` framing** does something else. It maps the detected unloaded markers
onto the grid a simulator preset draws (`camera.reference_marker_pixels`),
removing lens distortion, for visual comparison with a sim run whose camera
has not been matched to the sensor. The markers land within 0.13 px RMS of the
preset's centers. The result is *not* the data collection's pixel space, so do
not use it for recordings meant to sit beside trial data. The map is built
from five averaged unloaded frames, and the framing fails if it does not find
exactly `ROWS × COLS` markers. A single homography would leave 1.7 px RMS,
recorded as `grid.homography_rms_residual_px`.

Neither framing calibrates millimetres per pixel, photometric response, or
force ([calibration requirements](sensor-alignment.md)).

## Recording layout

```text
outputs/gelsight_mini/<YYYYmmdd-HHMMSS>_<label>/
  gs.npz          frames (N, 240, 320, 3) uint8 BGR; frame_idx, timestamps, t_ns,
                  device_frame_idx (N,) int64
  reference.png   unloaded reference (when one was captured)
  meta.json       device, crop box, camera controls, timing
  raw/frame_XXXXXX.jpg   untouched camera JPEGs, with --save-raw
```

`gs.npz` has the keys and meanings `fast_stream_device.py` writes for a trial:

- `frame_idx` counts recorded frames from 0.
- `timestamps` and `t_ns` are the same `time.perf_counter_ns()` values.

It adds `device_frame_idx`, the camera's own frame count. `meta.json`'s
`dropped_frames` counts the gaps in it. Timestamps are taken when each camera
frame arrives, before decoding; `FastCamera` takes them after its decode.

The tested unit delivers about 18.7 fps, not the 25 fps it advertises, so use
the timestamps rather than a nominal rate. Frames are recorded before the
display sees them, so a slow display does not drop recorded frames.

```python
from gelsight_ansys.sensor.mini import load_recording

rec = load_recording("outputs/gelsight_mini/20260922-184126_press")
rec["frames"], rec["t_ns"], rec["t_s"], rec["reference"], rec["meta"]
```

Trial `gs.npz` frames are BGR. The `gs.npz` supplied with this repository
matches live frames to 12 intensity levels in BGR order and 30 levels in RGB
order. Its frame 0 was copied into
`assets/sensors/gelsight_custom_11x17/background.png` as if it were RGB, so that
preset's background has red and blue swapped.

## Matching a simulation to hand presses

Without a force sensor, the image itself characterises a press.
`scripts/analyze_presses.py` measures each press the same way in a recording
and in a simulation run's frames, then interpolates the simulated depth and
force that reproduce it:

- A **press** is a run of at least 5 frames (0.27 s) whose smoothed change
  from the recording's first 10 frames stands clear of their noise. Its
  **peak** is its frame with the largest total change.
- The **patch** is where the colour change (smoothed over 3 px) exceeds 35 %
  of its peak. Its radius is that of the equal-area disk. It includes the
  tilted gel just outside true contact, so it is a size for comparing like
  with like, not a contact radius. `--by patch` (default) matches on it.
- The **shading** is the 99th percentile of the signed colour change smoothed
  over 8 px, which averages away the bright/dark pairs that moving markers
  leave. `--by shading` matches on it. It suits blunt tips, whose patch is
  faint and ragged, but it depends on the simulator's photometric response,
  which is not calibrated. `--shading-scale` corrects for that with the
  real/simulated shading ratio. A `--by patch` match prints that ratio for its
  tip.

```bash
# A sharp tip: match by patch; note the printed shading ratio.
python scripts/analyze_presses.py outputs/gelsight_mini/<recording> outputs/<sphere run>
# A blunt tip: match by shading, scaled by that ratio.
python scripts/analyze_presses.py outputs/gelsight_mini/<recording> outputs/<blunt run> \
    --by shading --shading-scale 0.69
```

### Sensor preset in the data framing

`configs/real_mini_*_press.json` render into the data collection's pixel
space. They keep the 11 × 17 tapered pad of `custom_gel_press` and change only
the camera and appearance (see
[provenance](../assets/sensors/gelsight_mini_data_framing/PROVENANCE.md)):

| Setting | Value |
|---|---|
| Camera FOV | 20.288 × 15.216 mm: 320 × 240 px at `gs_sdk`'s 0.0634 mm/px |
| Marker grid | 11 × 17, margins 46.4 (y) and 42.9 (x) px, measured from the unloaded gel |
| Background | The real unloaded frame with its markers inpainted |

A simulated unloaded frame has a marker pitch of 14.57 × 14.62 px, against
14.54 × 14.61 px measured on the sensor. Its grid is centered, whereas the real
one sits about 7 px right of and 4 px below the image center. Each press is
therefore placed at the real press's offset *from the grid center*.

### 2026-09-22 hand presses

Recordings `outputs/gelsight_mini/20260922-184921` and `-185013` are presses
by hand with a 10 mm-radius spherical tip. `-185500` is presses with the large
hemisphere tip, `assets/meshes/hemisphere-large.stl`. The STL's contacting cap
is exactly a 50 mm-radius sphere; 19.35 mm is the dome's height. The config
therefore uses an analytic rigid sphere of that radius. The STL indenter
itself fails the contact/backing force balance on the tapered pad (24 % at
0.15 mm), although it balances on the straight pad (see the TODO below).

| Tip | Matched by | Real press | Simulated depth | Simulated force |
|---|---|---|---|---|
| 10 mm sphere (4 presses) | patch | radius 3.53–4.06 mm | 0.78–0.99 mm, median 0.89 | 0.93–1.51 N, median 1.21 |
| 50 mm hemisphere (4 presses) | shading, × 1 / 0.69 | shading 8.5–11.6 | 0.24–0.32 mm, median 0.29 | 0.45–0.80 N, median 0.61 |

The hemisphere's patch is too faint and ragged to size. The simulated patch
radius grows 42.7, 50.9, 52.4 px over 0.20–0.30 mm, then jumps to 68.7 px. Its
presses are therefore matched by shading instead. The 0.69 ratio comes from the
sphere presses matched by patch (range 0.58–0.88): the simulator shades about
1.45 times more strongly than this sensor. The hemisphere presses also move the
whole gel sideways, leaving marker ghosts across the field and the gel walls,
which the simulated normal press does not reproduce.

Solved ramps (`outputs/real_mini_*_press/`), for later fitting:

| 10 mm sphere depth (mm) | 0.3 | 0.6 | 0.9 | 1.2 | 1.5 |
|---|---|---|---|---|---|
| Force (N) | 0.145 | 0.531 | 1.187 | 2.213 | 3.653 |

| 50 mm hemisphere depth (mm) | 0.05 | 0.10 | 0.15 | 0.20 | 0.25 | 0.30 | 0.35 | 0.40 | 0.45 |
|---|---|---|---|---|---|---|---|---|---|
| Force (N) | 0.021 | 0.075 | 0.164 | 0.294 | 0.470 | 0.691 | 0.962 | 1.297 | 1.707 |

These depths and forces are what the *simulated gel* needs to reproduce each
press's image. They inherit every uncalibrated assumption below and are not
measurements of the hand presses. Side-by-side renders are in
`outputs/gelsight_mini/compare_sphere10.png` and `compare_hemisphere50.png`.

## Calibration TODO

Do this when a robot and force/torque sensor are available. Until then,
`calibrated` stays `false` and the numbers above are approximate.

1. **Scale (mm/px).** Press a precision ball or flat-ended pin of known size
   at a known robot position, then move it by known steps. The marker or patch
   motion per mm gives the real scale in place of `gs_sdk`'s nominal 0.0634,
   and fixes `camera.fov_*_m` and the marker pitch in mm.
2. **Grid position.** The simulator always centers its marker grid, but the
   real one is off by 2–7 px and moves about 4 px when the gel shifts in its
   holder. Either add a marker-grid offset to the optics config, or record an
   unloaded frame at the start of every trial and compare relative to it.
3. **Gel stiffness and geometry.** Robot depth against F/T normal force for
   the 10 mm sphere, over 0–1.5 mm, fits `material.young_pa` (currently a
   nominal 100 kPa), using the solved ramps above to fit against. Measure the real
   pad's thickness and face size too: the preset assumes a 22 × 16 mm face,
   5 mm thick, but the visible face is closer to 18 × 12.6 mm.
4. **Photometric response.** The simulator shades with the stock MMintLab
   Mini table (`data/mini/polycalib.npz`). Its colour wheel is rotated about
   60–90° from this sensor's, and more saturated (`response_gain` 2.0). Run
   `gs_sdk`'s ball-press calibration (`calibration/`) on this unit and use the
   fitted table; then set `response_gain` against a recorded press. The real
   shading is 0.69 times the simulated (see above). Once the response matches,
   `--by shading` needs no `--shading-scale`.
5. **Markers.** Dot radius (0.25 mm in the preset; the real dark core is about
   0.22 mm) and the 0.5° grid tilt.
6. **Force axis and position.** With the F/T sensor, record the tangential
   force and robot pose for each press, so a press's force and position come
   from measurement rather than from matching patch size.
7. **STL indenter on the tapered pad.** Find why a `shape: mesh` indenter
   fails force balance on `top_width_m`/`taper_height_m` pads when an analytic
   sphere does not.
