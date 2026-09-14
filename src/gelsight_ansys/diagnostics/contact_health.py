"""Trend contact health across a run's saved frames, without reopening ANSYS.

states/frame_*.npz already carries per-face pressure, penetration, corner status
and elastic slip. Two things in there predict a stalling solve well before the
solver log does: penetration approaching the declared tolerance, and the contact
footprint opening inward from its perimeter.
"""

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

from ..contracts import SurfaceState

ROOT = Path(__file__).resolve().parents[3]

# CONTA174 detection-point status.
OPEN_NEAR, SLIDING, STICKING = 1, 2, 3


def edge_distance(reference, quads):
    """Distance from each face centre to the nearest sensor boundary."""
    centres = reference[quads].mean(axis=1)[:, :2]
    half = np.abs(reference[:, :2]).max(axis=0)
    return np.minimum(half[0] - np.abs(centres[:, 0]), half[1] - np.abs(centres[:, 1]))


def frame_health(state):
    pressure = state.contact_pressure_pa
    penetration = np.abs(state.contact_penetration_m)
    corners = np.asarray(state.contact_integration_status)
    record = {
        "time_s": float(state.time_s),
        "normal_force_n": float(-state.contact_force_n[:, 2].sum()),
        "max_penetration_m": float(penetration.max()),
        "mean_penetration_m": float(penetration.mean()),
        "max_pressure_pa": float(pressure.max()),
        "min_pressure_pa": float(pressure.min()),
    }
    if state.contact_elastic_slip_m.size:
        record["max_elastic_slip_m"] = float(np.abs(state.contact_elastic_slip_m).max())
    if corners.size:
        counts = collections.Counter(corners.astype(int).ravel().tolist())
        record["corner_status"] = {str(k): int(v) for k, v in sorted(counts.items())}
        distance = edge_distance(state.reference_m, state.quads)
        opening = (corners == OPEN_NEAR).any(axis=1)
        record["open_faces"] = int(opening.sum())
        # How far the open front has reached in from the perimeter. A front that
        # advances while load rises is the footprint peeling, not local noise.
        record["open_front_depth_m"] = (
            float(distance[opening].max()) if opening.any() else 0.0
        )
        record["sliding_faces"] = int((corners == SLIDING).any(axis=1).sum())
    return record


def load_frames(run):
    states = sorted((Path(run) / "states").glob("frame_*.npz"))
    if not states:
        raise SystemExit(f"No saved frames in {run}/states")
    return [frame_health(SurfaceState.load(p)) for p in states]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, help="Run directory holding states/")
    parser.add_argument("--json", type=Path, help="Write the per-frame records here")
    parser.add_argument(
        "--tolerance-m",
        type=float,
        help="Declared penetration tolerance, to flag frames that approach it",
    )
    args = parser.parse_args(argv)
    records = load_frames(args.run)
    tolerance = args.tolerance_m
    if tolerance is None:
        config = Path(args.run) / "config.json"
        if config.is_file():
            data = json.loads(config.read_text(encoding="utf-8"))
            tolerance = (data.get("indenter") or {}).get("penetration_tolerance_m")
    header = (
        f"{'t (s)':>7} {'Fz (N)':>9} {'pen (um)':>9} {'pmax kPa':>9} {'pmin kPa':>9} "
        f"{'open':>5} {'front mm':>9} {'slide':>6} {'slip um':>8}"
    )
    print(header)
    for r in records:
        print(
            f"{r['time_s']:7.2f} {r['normal_force_n']:9.3f} "
            f"{r['max_penetration_m'] * 1e6:9.3f} {r['max_pressure_pa'] / 1e3:9.2f} "
            f"{r['min_pressure_pa'] / 1e3:9.2f} {r.get('open_faces', 0):5d} "
            f"{r.get('open_front_depth_m', 0) * 1e3:9.2f} {r.get('sliding_faces', 0):6d} "
            f"{r.get('max_elastic_slip_m', 0) * 1e6:8.2f}"
        )
    first, last = records[0], records[-1]
    summary = {
        "frames": len(records),
        "time_span_s": [first["time_s"], last["time_s"]],
        "force_span_n": [first["normal_force_n"], last["normal_force_n"]],
        "max_penetration_m": max(r["max_penetration_m"] for r in records),
        "open_faces_first": first.get("open_faces", 0),
        "open_faces_last": last.get("open_faces", 0),
        "open_front_depth_last_m": last.get("open_front_depth_m", 0.0),
    }
    if tolerance:
        summary["penetration_tolerance_m"] = tolerance
        summary["penetration_fraction_of_tolerance"] = (
            summary["max_penetration_m"] / tolerance
        )
    print(json.dumps(summary, indent=2))
    warnings = []
    if tolerance and summary["max_penetration_m"] > 0.8 * tolerance:
        warnings.append(
            "Penetration is within 20% of the declared tolerance; augmentation has "
            "little room before it starts re-augmenting instead of converging."
        )
    if summary["open_faces_last"] > max(4, 2 * summary["open_faces_first"]):
        warnings.append(
            f"The contact footprint is opening from its perimeter: "
            f"{summary['open_faces_first']} -> {summary['open_faces_last']} faces with an "
            f"open corner, now {summary['open_front_depth_last_m'] * 1e3:.2f} mm in. A moving "
            "open/close front makes the tangent stiffness discontinuous."
        )
    for line in warnings:
        print(line, file=sys.stderr)
    if args.json:
        args.json.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return 0
