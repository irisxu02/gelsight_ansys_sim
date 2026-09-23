"""Measure presses the same way in a real recording and in a simulation run.

Without a force sensor, a hand press is characterised by what the image
shows: where the marker grid sits, and how large and how strong the change
from the unloaded frame is. The same measurements applied to a run's rendered
frames turn them into a simulated depth and force for each real press.

- The **patch** is where the colour change (magnitude, smoothed over
  ``BLUR_PX``) exceeds ``PATCH_FRACTION`` of its peak; its radius is that of
  the equal-area disk. It includes the tilted gel just outside true contact,
  so it is a size to compare like with like, not a contact radius.
- The **shading** is the 99th percentile of the signed colour change smoothed
  over ``SHADING_BLUR_PX``, which averages away the bright/dark pairs markers
  leave when they move. It suits blunt tips, whose patch is faint and ragged,
  but it depends on the simulator's photometric response: ``--shading-scale``
  converts it using a real/simulated ratio measured on a tip matched by patch.
"""

import argparse
import json
from pathlib import Path

import numpy as np

from .mini import _cv2, detect_markers, load_recording, order_grid

PATCH_FRACTION = 0.35
BLUR_PX = 3.0
SHADING_BLUR_PX = 8.0
MIN_CHANGE = 15.0  # smoothed change below this is unloaded noise


def unloaded_baseline(frames, count=10):
    """Mean of the first ``count`` frames; recordings start with nothing touching."""
    mean = frames[:count].astype(np.float32).mean(axis=0)
    return np.clip(np.rint(mean), 0, 255).astype(np.uint8)


def grid_geometry(image, rows=11, cols=17):
    """Pitch, extent, center and the equivalent symmetric margins of the marker grid."""
    height, width = image.shape[:2]
    grid = order_grid(detect_markers(image, (width, height)), rows, cols).reshape(
        rows, cols, 2
    )
    x0, x1 = grid[:, 0, 0].mean(), grid[:, -1, 0].mean()
    y0, y1 = grid[0, :, 1].mean(), grid[-1, :, 1].mean()
    return {
        "pitch_px": [
            float(np.diff(grid[..., 0], axis=1).mean()),
            float(np.diff(grid[..., 1], axis=0).mean()),
        ],
        "center_px": [float((x0 + x1) / 2), float((y0 + y1) / 2)],
        "margin_px_yx": [
            float((y0 + height - 1 - y1) / 2),
            float((x0 + width - 1 - x1) / 2),
        ],
    }


def change_maps(frames, baseline):
    """(N, H, W) smoothed colour-change magnitude from the baseline."""
    cv2 = _cv2()
    difference = frames.astype(np.float32) - baseline.astype(np.float32)
    magnitude = np.linalg.norm(difference, axis=3)
    return np.stack([cv2.GaussianBlur(m, (0, 0), BLUR_PX) for m in magnitude])


def patch(change):
    """Center and equal-area radius (px) of the largest patch in one change map."""
    cv2 = _cv2()
    mask = (change > PATCH_FRACTION * change.max()).astype(np.uint8)
    _, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    radius = np.sqrt(stats[largest, cv2.CC_STAT_AREA] / np.pi)
    return [float(v) for v in centroids[largest]], float(radius)


def shading(frame, baseline):
    """99th percentile of the heavily smoothed signed colour change."""
    cv2 = _cv2()
    difference = frame.astype(np.float32) - baseline.astype(np.float32)
    smooth = np.stack(
        [cv2.GaussianBlur(difference[..., c], (0, 0), SHADING_BLUR_PX) for c in range(3)],
        axis=2,
    )
    return float(np.percentile(np.linalg.norm(smooth, axis=2), 99))


def press_events(changes, minimum_frames=5):
    """(start, end) frame ranges where the change stands clear of the unloaded noise.

    At the Mini's 18.7 fps, five frames is 0.27 s: shorter blips are the gel
    settling or the hand brushing it, not presses.
    """
    score = changes.reshape(len(changes), -1).max(axis=1)
    noise = np.median(score[: min(10, len(score))])
    active = np.append(score > max(3 * noise, MIN_CHANGE), False)
    events, start = [], None
    for index, on in enumerate(active):
        if on and start is None:
            start = index
        elif not on and start is not None:
            if index - start >= minimum_frames:
                events.append((start, index - 1))
            start = None
    return events


def measure_recording(path, mm_per_px=None):
    """Grid geometry and every press in a ``gelsight_stream`` recording."""
    recording = load_recording(path)
    frames = recording["frames"]
    baseline = unloaded_baseline(frames)
    changes = change_maps(frames, baseline)
    # The deepest moment of a press is the one that changes the image most in
    # total: the patch grows with depth even where its shading saturates.
    total = changes.reshape(len(changes), -1).sum(axis=1)
    presses = []
    for start, end in press_events(changes):
        peak = start + int(np.argmax(total[start : end + 1]))
        center, radius = patch(changes[peak])
        presses.append(
            {
                "frames": [start, end],
                "t_s": [float(recording["t_s"][start]), float(recording["t_s"][end])],
                "peak_frame": peak,
                "center_px": center,
                "patch_radius_px": radius,
                "shading": shading(frames[peak], baseline),
            }
        )
    return _with_mm(
        {"source": str(path), "grid": grid_geometry(baseline), "presses": presses},
        mm_per_px,
    )


def measure_run(path, mm_per_px=None):
    """Grid geometry, and the patch and shading of every frame, in a simulation run."""
    cv2 = _cv2()
    path = Path(path)
    frames = np.stack(
        [cv2.imread(str(p)) for p in sorted((path / "images").glob("frame_*.png"))]
    )
    unloaded = path / "unloaded_reference.png"
    baseline = cv2.imread(str(unloaded)) if unloaded.exists() else frames[0]
    summary = json.loads((path / "summary.json").read_text())
    changes = change_maps(frames, baseline)
    rows = []
    for index, record in enumerate(summary["frames"]):
        entry = {
            "frame": index,
            "depth_m": record.get("depth_m"),
            "normal_force_n": record.get("normal_force_n"),
            "shading": shading(frames[index], baseline),
        }
        if changes[index].max() > MIN_CHANGE:
            entry["center_px"], entry["patch_radius_px"] = patch(changes[index])
        rows.append(entry)
    return _with_mm(
        {"source": str(path), "grid": grid_geometry(baseline), "frames": rows}, mm_per_px
    )


def depth_for(run, key, value):
    """Depth (m) and force (N) at which a run's ``key`` reaches ``value``.

    ``key`` is ``patch_radius_px`` or ``shading``. Interpolates linearly between
    the bracketing frames of the loaded part of a monotonic press ramp; returns
    None beyond the ramp rather than extrapolating.
    """
    ramp = [f for f in run["frames"] if key in f and (f["depth_m"] or 0) > 0]
    for below, above in zip(ramp, ramp[1:], strict=False):
        v0, v1 = below[key], above[key]
        if v0 <= value <= v1 and v1 > v0:
            w = (value - v0) / (v1 - v0)
            depth = below["depth_m"] + w * (above["depth_m"] - below["depth_m"])
            force = None
            if (
                below["normal_force_n"] is not None
                and above["normal_force_n"] is not None
            ):
                force = below["normal_force_n"] + w * (
                    above["normal_force_n"] - below["normal_force_n"]
                )
            return depth, force
    return None


def depth_for_radius(run, radius_px):
    """``depth_for`` by patch radius."""
    return depth_for(run, "patch_radius_px", radius_px)


def shading_at(run, depth_m):
    """A run's shading at ``depth_m``, interpolated within its ramp."""
    ramp = [f for f in run["frames"] if (f["depth_m"] or 0) > 0]
    depths = [f["depth_m"] for f in ramp]
    if not ramp or not depths[0] <= depth_m <= depths[-1]:
        return None
    return float(np.interp(depth_m, depths, [f["shading"] for f in ramp]))


def _with_mm(report, mm_per_px):
    if mm_per_px:
        report["mm_per_px"] = mm_per_px
        for item in report.get("presses", []) + report.get("frames", []):
            if "patch_radius_px" in item:
                item["patch_radius_mm"] = item["patch_radius_px"] * mm_per_px
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Recording and/or simulation run directories; recordings are matched to runs",
    )
    parser.add_argument(
        "--by",
        choices=("patch", "shading"),
        default="patch",
        help="Match by patch radius (default; sharp tips) or shading strength (blunt tips)",
    )
    parser.add_argument(
        "--shading-scale",
        type=float,
        default=1.0,
        help="Real shading per unit simulated shading, from a tip matched by patch",
    )
    parser.add_argument(
        "--mm-per-px",
        type=float,
        default=0.0634,
        help="Scale for reported mm (default 0.0634, gs_sdk's gsmini ppmm; uncalibrated)",
    )
    parser.add_argument("--json", type=Path, help="Also write every report to this file")
    args = parser.parse_args(argv)

    reports = []
    for path in args.paths:
        if (path / "gs.npz").exists():
            report = measure_recording(path, args.mm_per_px)
            print(f"{path.name}: {len(report['presses'])} presses")
            for item in report["presses"]:
                print(
                    f"  t {item['t_s'][0]:.1f}-{item['t_s'][1]:.1f} s: center"
                    f" ({item['center_px'][0]:.0f}, {item['center_px'][1]:.0f}) px,"
                    f" patch radius {item['patch_radius_px']:.1f} px"
                    f" ({item['patch_radius_mm']:.2f} mm), shading {item['shading']:.1f}"
                )
        else:
            report = measure_run(path, args.mm_per_px)
            print(f"{path.name}: simulation run")
            for frame in report["frames"]:
                if (frame["depth_m"] or 0) > 0:
                    force = frame["normal_force_n"]
                    radius = frame.get("patch_radius_px")
                    print(
                        f"  depth {frame['depth_m'] * 1000:.3f} mm"
                        + (f", force {force:.3f} N" if force is not None else "")
                        + (f": patch radius {radius:.1f} px" if radius else ": no patch")
                        + f", shading {frame['shading']:.1f}"
                    )
        grid = report["grid"]
        print(
            f"  grid pitch {grid['pitch_px'][0]:.2f} x {grid['pitch_px'][1]:.2f} px,"
            f" center ({grid['center_px'][0]:.1f}, {grid['center_px'][1]:.1f})"
        )
        reports.append(report)

    runs = [r for r in reports if "frames" in r]
    recordings = [r for r in reports if "presses" in r]
    for run in runs:
        if not recordings:
            break
        print(f"matched by {args.by} to {Path(run['source']).name}:")
        ratios = []
        for recording in recordings:
            for item in recording["presses"]:
                if args.by == "patch":
                    match = depth_for(run, "patch_radius_px", item["patch_radius_px"])
                else:
                    match = depth_for(
                        run, "shading", item["shading"] / args.shading_scale
                    )
                item.setdefault("matches", {})[run["source"]] = match
                where = f"{Path(recording['source']).name} t {item['t_s'][0]:.1f} s"
                if match is None:
                    print(f"  {where}: outside the simulated ramp")
                    continue
                depth, force = match
                print(
                    f"  {where}: depth {depth * 1000:.2f} mm"
                    + (f", force {force:.2f} N" if force is not None else "")
                )
                simulated = shading_at(run, depth)
                if args.by == "patch" and simulated:
                    ratios.append(item["shading"] / simulated)
        if ratios:
            print(
                f"  real/simulated shading at the matched depths: median"
                f" {np.median(ratios):.2f} (range {min(ratios):.2f}-{max(ratios):.2f});"
                " use it as --shading-scale for --by shading"
            )
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2) + "\n")
    return 0
