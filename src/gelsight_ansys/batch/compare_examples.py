"""Compare the material examples using complete exported ANSYS metrics."""

import argparse
import json
from pathlib import Path


def build_comparison(root):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = ("sphere_press", "soft_sphere_press", "sphere_slide", "rough_sphere_slide")
    runs = {}
    configs = {}
    for name in names:
        folder = root / name
        summary = json.loads((folder / "summary.json").read_text())
        config = json.loads((folder / "config.json").read_text())
        if summary["status"] != "passed" or len(summary["frames"]) != len(
            config["trajectory"]
        ):
            raise ValueError("Only complete passed runs can be compared")
        runs[name] = summary["frames"]
        configs[name] = config
    for key in ("gel", "material", "camera"):
        if any(configs[name][key] != configs[names[0]][key] for name in names):
            raise ValueError(f"Inconsistent sensor configuration: {key}")
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5), layout="constrained")
    for name, label in (
        ("sphere_press", "Rigid sphere, μ=0.5"),
        ("soft_sphere_press", "50 kPa sphere, μ=0.5"),
    ):
        frames = runs[name]
        axes[0].plot(
            [f["depth_m"] * 1000 for f in frames],
            [f["normal_force_n"] for f in frames],
            "o-",
            label=label,
        )
    axes[0].set(
        xlabel="Commanded grip travel (mm)",
        ylabel="Normal force (N)",
        title="Object compliance: press / release",
    )
    for name, label in (
        ("sphere_slide", "Rigid sphere, μ=0.5"),
        ("rough_sphere_slide", "2 MPa sphere, μ=0.9"),
    ):
        depth = max(f["depth_m"] for f in runs[name])
        frames = [f for f in runs[name] if abs(f["depth_m"] - depth) < 1e-12]
        x = [f["x_m"] * 1000 for f in frames]
        axes[1].plot(x, [f["force_on_gel_n"][0] for f in frames], "o-", label=label)
        axes[2].plot(
            x,
            [f["max_marker_in_plane_displacement_m"] * 1e6 for f in frames],
            "o-",
            label=label,
        )
    axes[1].set(
        xlabel="Lateral grip travel (mm)",
        ylabel="Shear force Fx (N)",
        title="Sliding at fixed commanded depth",
    )
    axes[2].set(
        xlabel="Lateral grip travel (mm)",
        ylabel="Maximum in-plane marker motion (µm)",
        title="Actual material-marker response",
    )
    for axis in axes:
        axis.legend(fontsize=8)
        axis.grid(alpha=0.2)
    fig.suptitle(
        "Uniform gel · provisional materials\nRough-slide changes both stiffness and friction; sphere geometry is faceted for deformable objects",
        fontsize=11,
    )
    target = root / "material_comparison.png"
    fig.savefig(target, dpi=150)
    plt.close(fig)
    print(target)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=Path("docs/examples"))
    args = parser.parse_args(argv)
    build_comparison(args.examples)
    return 0
