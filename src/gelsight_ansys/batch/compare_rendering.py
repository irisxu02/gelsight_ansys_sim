"""Build raw-versus-subtracted illustrations from a complete raw RGB run."""

import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def scaled_config(config, scale, backend=None):
    """Increase optical sampling while retaining the FOV and material dot layout."""
    rendered = config.with_render_scale(scale)
    if backend is not None:
        rendered = replace(rendered, optics=replace(rendered.optics, backend=backend))
    return rendered.validate()


def replay_rgb(run, config, scale, backend=None):
    """Render saved geometry at new camera rays; never launch a mechanics solve."""
    if config.is_plane:
        raise ValueError(
            "Plane optical replay needs its separate unloaded reference; "
            "use saved RGB comparisons or render at the requested scale during the solve"
        )
    from gelsight_ansys.camera import optical_surface
    from gelsight_ansys.contracts import SurfaceState
    from gelsight_ansys.optics import Renderer
    from gelsight_ansys.surface import Markers, image_coordinates, project_surface

    paths = [run / "states" / f"frame_{i:04d}.npz" for i in range(len(config.trajectory))]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(
                f"High-resolution rendering needs saved state: {path}"
            )
    rendered = scaled_config(config, scale, backend)
    first = SurfaceState.load(paths[0])
    markers = Markers(
        first,
        config.optics.marker_spacing_m,
        camera=config.camera,
        optics=config.optics,
    )
    reference_pixels = image_coordinates(markers.reference_m, rendered.camera)
    with tempfile.TemporaryDirectory(prefix="gelsight-preview-optics-") as temporary:
        # A user-supplied background follows the same FOV at the new pixel resolution.
        if rendered.optics.background_image:
            background = Path(rendered.optics.background_image)
            if not background.is_absolute():
                background = run / background
            target = Path(temporary) / "background.png"
            with Image.open(background) as image:
                image.convert("RGB").resize(
                    (rendered.camera.width_px, rendered.camera.height_px),
                    Image.Resampling.LANCZOS,
                ).save(target)
            rendered = replace(
                rendered, optics=replace(rendered.optics, background_image=str(target))
            )
        renderer = Renderer(rendered, run)
        print(
            f"Optical replay: {rendered.camera.width_px} x {rendered.camera.height_px}; {renderer.device}"
        )
        for path, pose in zip(paths, config.trajectory):
            state = SurfaceState.load(path)
            if not np.isclose(state.time_s, pose.time_s, rtol=0, atol=1e-8):
                raise ValueError(
                    f"Saved state time differs from the trajectory: {path.name}"
                )
            fields = (
                project_surface(state, rendered.camera, backend=rendered.optics.backend)
                if rendered.camera.projection == "orthographic"
                else {}
            )
            fields.update(
                optical_surface(
                    state, rendered.camera, fields, backend=rendered.optics.backend
                )
            )
            fields["normals"] = fields["optical_normals"]
            fields["valid_mask"] = fields["optical_valid_mask"]
            pixels = image_coordinates(markers.positions(state), rendered.camera)
            raw = renderer.render(fields, pixels, reference_pixels)
            yield Image.fromarray(raw), renderer.difference.copy()


def saved_rgb(run, count):
    for i in range(count):
        with Image.open(run / "images" / f"frame_{i:04d}.png") as source:
            raw = source.convert("RGB")
        with np.load(run / "fields" / f"frame_{i:04d}.npz", allow_pickle=False) as fields:
            yield raw, fields["rgb_difference_int16"].copy()


def comparison_panel(raw, difference, metric, index, count, scale=1):
    subtracted = Image.fromarray(
        np.clip(np.rint(128 + difference / 2), 0, 255).astype(np.uint8)
    )
    if raw.size != subtracted.size:
        raise ValueError("RGB and difference image dimensions must match")
    w, h = raw.size
    panel = Image.new("RGB", (2 * w + 36 * scale, h + 78 * scale), "#f4f5f7")
    panel.paste(raw, (12 * scale, 38 * scale))
    panel.paste(subtracted, (w + 24 * scale, 38 * scale))
    draw = ImageDraw.Draw(panel)
    font = ImageFont.load_default(size=12 * scale)
    draw.text((12 * scale, 12 * scale), "Raw tactile RGB", fill="black", font=font)
    draw.text(
        (w + 24 * scale, 12 * scale),
        "Signed difference (zero = gray 128)",
        fill="black",
        font=font,
    )
    draw.text(
        (12 * scale, h + 49 * scale),
        f"Frame {index}/{count - 1} | depth {metric['depth_m'] * 1000:.2f} mm | force {metric['normal_force_n']:.4f} N",
        fill="black",
        font=font,
    )
    return panel


def save_comparison_gif(panels, path, fps):
    """Use the shared bounded-memory GIF writer for long comparisons."""
    from gelsight_ansys.artifacts import save_gif

    save_gif(panels, path, fps)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=Path("docs/examples/sphere_press"))
    parser.add_argument("--output", type=Path, default=Path("docs/rendering"))
    parser.add_argument(
        "--name", default="raw_vs_subtracted", help="Output filename stem"
    )
    parser.add_argument(
        "--render-scale",
        type=int,
        default=1,
        help="Camera resolution multiplier; values above 1 re-render saved states",
    )
    parser.add_argument(
        "--backend", choices=("cpu", "cuda"), help="Optical replay backend"
    )
    args = parser.parse_args()
    if args.render_scale < 1:
        parser.error("--render-scale must be positive")
    if not args.name or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in args.name
    ):
        parser.error("--name must contain only letters, digits, underscores, or hyphens")
    config_data = json.loads((args.run / "config.json").read_text())
    summary = json.loads((args.run / "summary.json").read_text())
    if summary["status"] != "passed" or config_data["optics"]["render_mode"] != "raw":
        raise ValueError("A complete passed raw RGB run is required")
    metrics = summary["frames"]
    if len(metrics) != len(config_data["trajectory"]):
        raise ValueError("Incomplete trajectory")
    if args.render_scale > 1 or args.backend is not None:
        from gelsight_ansys.config import Config

        config = Config.from_dict(config_data)
        frames = replay_rgb(args.run, config, args.render_scale, args.backend)
    else:
        frames = saved_rgb(args.run, len(metrics))
    panels = [
        comparison_panel(raw, difference, metric, i, len(metrics), args.render_scale)
        for i, (metric, (raw, difference)) in enumerate(zip(metrics, frames))
    ]
    args.output.mkdir(parents=True, exist_ok=True)
    peak = max(range(len(metrics)), key=lambda i: metrics[i]["normal_force_n"])
    panels[peak].save(args.output / f"{args.name}.png")
    save_comparison_gif(
        panels, args.output / f"{args.name}.gif", config_data["optics"]["animation_fps"]
    )
    print(
        f"Saved {len(panels)} comparison frames at {panels[0].width} x {panels[0].height} to {args.output}"
    )


if __name__ == "__main__":
    main()
