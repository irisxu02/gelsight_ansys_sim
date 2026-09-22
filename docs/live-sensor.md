# Live GelSight Mini capture

[Documentation](README.md) · [Sensor alignment](sensor-alignment.md) · [Dataset](dataset.md)

`scripts/gelsight_stream.py` views and records a USB GelSight Mini in the
simulator's image convention - 320 × 240, landscape, RGB - so a recording can
be set beside a run's `images/frame_XXXX.png` without further conversion. With
`--sim-run` the window shows that run next to the live sensor, and both signed
differences on one scale.

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
# Live view, framed on the marker grid of the run it will be compared with
python scripts/gelsight_stream.py --crop grid --sim-run outputs/<run> --label sphere3mm_press

# Headless: 10 s to outputs/gelsight_mini/<timestamp>_press/
python scripts/gelsight_stream.py --no-display --duration 10 --crop grid --label press
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
| `g` | Overlay the simulator's unloaded marker centers (grid framing) |
| `+` / `-` | Double or halve the difference gain |
| `,` / `.` / `p` | Previous, next, or play the sim frames |
| `q` / `Esc` | Quit; a running recording is finished and saved |

Differences are drawn as `128 + gain × (frame − reference)`, gray at zero. The
default gain 0.5 is the one `compare_rendering` uses for its subtracted panels.
The sim panel's reference is the run's `unloaded_reference.png` for plane runs,
else its frame 0 ([dataset](dataset.md#run-files-and-coordinates)).

## Framing

The Mini offers one mode, 3280 × 2464 MJPEG. Frames are decoded at 1/2, 1/4 or
1/8 size with libjpeg's DCT scaling, never coarser than the output needs, then
mapped to 320 × 240:

| `--crop` | Output |
|---|---|
| `full` (default) | The whole sensor, area-resized. This is how `gs.npz` was captured. |
| `gsrobotics` | The GelSight SDK's `resize_crop_mini`: a 1/7 border off each side, then trimmed to 4:3. |
| `x0,y0,x1,y1` | A box in raw sensor pixels. |
| `grid:ROWSxCOLS[:MARGIN]` | Marker-grid framing, below. Bare `grid` reads rows, columns and margin from `--sim-run`'s `config.json`, else uses `11x17:10`. |

**Marker-grid framing** makes a pixel in the recording the same material point
as that pixel in the simulation. On opening, it finds the markers in five
averaged unloaded frames, orders them into the declared grid, and builds a
per-pixel map. The map interpolates the detected centers bicubically and
extrapolates linearly past the outer markers. Each unloaded marker then lands
exactly on the center the simulator draws (`camera.reference_marker_pixels`),
and the lens distortion is removed along with the tilt.

On the tested unit, detected markers in the remapped image sit 0.13 px RMS
(0.46 px maximum) from the simulated centers. That is about the detector's own
0.10 px error on a rendered sim frame. A single homography would leave 1.7 px
RMS, which is recorded as `grid.homography_rms_residual_px` as a measure of the
lens distortion. The framing fails, rather than guessing, if it does not find
exactly `ROWS × COLS` markers.

This framing matches marker positions only. It does not calibrate pixel pitch
in millimetres, photometric response, or force. The simulator's
`fov_width_m`/`fov_height_m` must still describe the real marker pitch for the
two images to have the same scale ([calibration requirements](sensor-alignment.md)).

## Recording layout

```text
outputs/gelsight_mini/<YYYYmmdd-HHMMSS>_<label>/
  frames.npz      frames (N, H, W, 3) uint8 RGB; t_ns, t_s, frame_idx (N,)
  reference.png   unloaded reference, RGB (when one was captured)
  meta.json       device, framing, decode reduction, camera controls, timing
  raw/frame_XXXXXX.jpg   untouched camera JPEGs, with --save-raw
```

`t_ns` is `time.monotonic_ns()` when each frame arrived and `t_s` is relative
to the first frame. `frame_idx` counts frames received since the camera
opened, so `meta.json`'s `dropped_frames` counts gaps in it. The tested unit
delivers about 18.7 fps, not the 25 fps it advertises, even when frames are
grabbed without decoding. Use the timestamps, not a nominal rate. Recording
happens on the capture thread, so a slow display does not drop recorded frames.

```python
from gelsight_ansys.sensor.mini import load_recording

rec = load_recording("outputs/gelsight_mini/20260922-181812_press")
rec["frames"], rec["t_s"], rec["reference"], rec["meta"]["grid"]
```

`frames.npz` reuses the key names of the supplied `gs.npz` capture, but the
channel order differs. `gs.npz` frames are **BGR**: they match live frames to
12 intensity levels in BGR order and 30 levels in RGB order. Its frame 0 was
copied into `assets/sensors/gelsight_custom_11x17/background.png` as if it were
RGB, so that preset's background has red and blue swapped. Recordings made
here state `"channel_order": "RGB"` in `meta.json`.
