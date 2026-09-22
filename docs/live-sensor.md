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
