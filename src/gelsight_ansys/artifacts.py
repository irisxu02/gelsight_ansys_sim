"""Dataset exports and a portable HTML/PNG/GIF results viewer."""

import csv
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image


def write_json(path, data):
    """Write a record, tolerating a reader that has it open.

    A run's summary is written after every frame and read while the run is
    going. Windows denies a write to a file another process holds open, so a
    glance at a summary must not end an eight-hour solve; the write is retried
    for a few seconds before it is allowed to fail.
    """
    import time

    text = json.dumps(data, indent=2, allow_nan=False) + "\n"
    deadline = time.monotonic() + 10
    while True:
        try:
            Path(path).write_text(text, encoding="utf-8")
            return
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def save_frame(
    directory, index, state, fields, reference, positions, pixels, rgb, metrics
):
    for folder in ("states", "fields", "images"):
        (directory / folder).mkdir(exist_ok=True)
    state.save(directory / "states" / f"frame_{index:04d}.npz")
    np.savez_compressed(
        directory / "fields" / f"frame_{index:04d}.npz",
        **fields,
        marker_reference_m=reference,
        marker_position_m=positions,
        marker_pixel=pixels,
    )
    Image.fromarray(rgb).save(directory / "images" / f"frame_{index:04d}.png")
    write_json(directory / "fields" / f"frame_{index:04d}.json", metrics)


def animation_durations(count, fps):
    """Illustrative frame timing, with a pause at the unloaded endpoints."""
    durations = [round(1000 / fps)] * count
    durations[0] = durations[-1] = max(700, durations[0])
    return durations


class ImageFiles:
    """A lazy image sequence, keeping only the frame requested by the caller."""

    def __init__(self, paths):
        self.paths = list(paths)

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        with Image.open(self.paths[index]) as source:
            return source.convert("RGB")


def save_gif(images, path, fps, durations=None):
    """Write complete frames with bounded memory and one shared sampled palette."""
    from PIL import GifImagePlugin

    count = len(images)
    if count == 0:
        raise ValueError("A GIF needs at least one image")
    per_frame = max(1, 4_000_000 // count)
    samples = []
    size = images[0].size
    for i in range(count):
        frame = images[i]
        if frame.size != size:
            raise ValueError("All GIF frames must have identical dimensions")
        pixels = np.asarray(frame.convert("RGB")).reshape(-1, 3)
        ids = np.linspace(0, len(pixels) - 1, min(per_frame, len(pixels)), dtype=int)
        samples.append(pixels[ids].copy())
    pixels = np.concatenate(samples)
    width = min(2048, len(pixels))
    padding = (-len(pixels)) % width
    if padding:
        pixels = np.concatenate((pixels, np.repeat(pixels[-1:], padding, axis=0)))
    palette = Image.fromarray(pixels.reshape(-1, width, 3)).quantize(
        colors=256, method=Image.Quantize.MEDIANCUT
    )
    del samples, pixels
    durations = durations or animation_durations(count, fps)
    if len(durations) != count:
        raise ValueError("GIF timing must match the image count")
    with Path(path).open("wb") as stream:
        for i in range(count):
            frame = images[i].quantize(
                palette=palette, dither=Image.Dither.FLOYDSTEINBERG
            )
            if i == 0:
                header, _ = GifImagePlugin.getheader(
                    frame, info={"loop": 0, "optimize": False}
                )
                stream.writelines(header)
            stream.writelines(GifImagePlugin.getdata(
                frame, duration=durations[i], disposal=2
            ))
        stream.write(b";")


def marker_vectors(reference_m, position_m):
    """Actual in-plane motion: mm vectors for axes, micrometres for color."""
    delta = position_m[:, :2] - reference_m[:, :2]
    return delta * 1000, np.linalg.norm(delta, axis=1) * 1e6


def plot_limits(directory, metrics):
    depth_min, depth_max, marker_max = 0.0, 1e-9, 1.0
    for index in range(len(metrics)):
        with np.load(
            directory / "fields" / f"frame_{index:04d}.npz", allow_pickle=False
        ) as fields:
            depth = fields["normal_displacement_m"][fields["valid_mask"]] * 1000
            if depth.size:
                depth_min = min(depth_min, float(depth.min()))
                depth_max = max(depth_max, float(depth.max()))
            _, magnitude = marker_vectors(
                fields["marker_reference_m"], fields["marker_position_m"]
            )
            marker_max = max(marker_max, float(magnitude.max()))
    return {
        "depth_mm": [depth_min, depth_max],
        "pressure_kpa": [
            0.0,
            max(1e-9, max(m["max_pressure_pa"] for m in metrics) / 1000),
        ],
        "marker_in_plane_um": [0.0, marker_max],
    }


def build_process_images(directory, config, metrics, images, limits):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import Normalize

    c = config.camera
    extent = [
        (c.center_x_m - c.fov_width_m / 2) * 1000,
        (c.center_x_m + c.fov_width_m / 2) * 1000,
        (c.center_y_m - c.fov_height_m / 2) * 1000,
        (c.center_y_m + c.fov_height_m / 2) * 1000,
    ]
    with np.load(directory / "fields/frame_0000.npz", allow_pickle=False) as fields:
        reference = fields["marker_reference_m"].copy()
    factor = config.visualization.marker_scale
    fig, axes = plt.subplots(
        2,
        2,
        figsize=(11, 8.5),
        layout="constrained",
        dpi=200 if c.width_px >= 640 else 100,
    )
    rgb = axes[0, 0].imshow(images[0], interpolation="nearest")
    axes[0, 0].set_title(
        "Raw tactile RGB (actual motion)"
        if config.optics.render_mode == "raw"
        else "RGB difference (zero = 128)"
    )
    axes[0, 0].axis("off")
    blank = np.zeros((c.height_px, c.width_px))
    depth_image = axes[0, 1].imshow(
        blank,
        extent=extent,
        interpolation="nearest",
        cmap="coolwarm",
        vmin=limits["depth_mm"][0],
        vmax=limits["depth_mm"][1],
    )
    fig.colorbar(depth_image, ax=axes[0, 1], label="Normal displacement (mm)")
    axes[0, 1].set_title("Solved surface deformation")
    pressure_image = axes[1, 0].imshow(
        blank,
        extent=extent,
        interpolation="nearest",
        cmap="magma",
        vmin=0,
        vmax=limits["pressure_kpa"][1],
    )
    fig.colorbar(pressure_image, ax=axes[1, 0], label="Contact pressure (kPa)")
    axes[1, 0].set_title("ANSYS element contact pressure")
    axes[1, 1].scatter(
        reference[:, 0] * 1000, reference[:, 1] * 1000, s=4, color="#cbd5e1", zorder=1
    )
    zeros = np.zeros(len(reference))
    arrows = axes[1, 1].quiver(
        reference[:, 0] * 1000,
        reference[:, 1] * 1000,
        zeros,
        zeros,
        zeros,
        angles="xy",
        scale_units="xy",
        scale=1 / factor,
        cmap="viridis",
        norm=Normalize(*limits["marker_in_plane_um"]),
        width=0.004,
        headwidth=4,
        zorder=2,
    )
    fig.colorbar(
        arrows, ax=axes[1, 1], label="Actual in-plane marker displacement (µm)"
    )
    key = axes[1, 1].quiverkey(
        arrows,
        0.80,
        0.08,
        config.visualization.marker_key_um / 1000,
        f"{config.visualization.marker_key_um:g} µm actual",
        labelpos="N",
        coordinates="axes",
        color="black",
    )
    key.text.set_bbox({"facecolor": "white", "edgecolor": "none", "alpha": 0.9})
    axes[1, 1].set_xlim(-config.gel.width_m * 500, config.gel.width_m * 500)
    axes[1, 1].set_ylim(-config.gel.length_m * 500, config.gel.length_m * 500)
    axes[1, 1].set_aspect("equal")
    axes[1, 1].set_title(f"Marker motion — arrows enlarged {factor:g}×")
    for axis in (axes[0, 1], axes[1, 0], axes[1, 1]):
        axis.set_xlabel("Gel x (mm)")
        axis.set_ylabel("Gel y (mm)")
    title = fig.suptitle("")
    if any(abs(m["twist_rad"]) > 0 for m in metrics):
        selected = int(np.argmax([abs(m["moment_on_gel_nm"][2]) for m in metrics]))
    elif any(abs(m["x_m"]) + abs(m["y_m"]) > 0 for m in metrics):
        selected = int(
            np.argmax([np.linalg.norm(m["force_on_gel_n"][:2]) for m in metrics])
        )
    else:
        selected = int(np.argmax([m["normal_force_n"] for m in metrics]))
    folder = directory / "panels"
    if config.visualization.save_panel_frames:
        folder.mkdir(exist_ok=True)
    import tempfile

    temporary_panels = tempfile.TemporaryDirectory(prefix="panels-", dir=directory)
    panels = []
    try:
        for index, metric in enumerate(metrics):
            with np.load(
                directory / "fields" / f"frame_{index:04d}.npz", allow_pickle=False
            ) as fields:
                depth = np.where(
                    fields["valid_mask"], fields["normal_displacement_m"] * 1000, np.nan
                )
                pressure = np.where(
                    fields["valid_mask"], fields["contact_pressure_pa"] / 1000, np.nan
                )
                vectors, magnitude = marker_vectors(
                    fields["marker_reference_m"], fields["marker_position_m"]
                )
            rgb.set_data(images[index])
            depth_image.set_data(depth)
            pressure_image.set_data(pressure)
            arrows.set_UVC(vectors[:, 0], vectors[:, 1], magnitude)
            title.set_text(
                f"{config.name} · frame {index}/{len(metrics) - 1}\n"
                f"Grip travel {metric['depth_m'] * 1000:.2f} mm · twist {np.degrees(metric['twist_rad']):.1f}° · "
                f"normal force {metric['normal_force_n']:.3f} N · Mz {metric['moment_on_gel_nm'][2] * 1000:.3f} N mm"
            )
            fig.canvas.draw()
            panel = Image.fromarray(
                np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
            )
            if config.visualization.save_panel_frames:
                panel.save(folder / f"frame_{index:04d}.png")
            temporary_path = Path(temporary_panels.name) / f"frame_{index:04d}.png"
            panel.save(temporary_path)
            panels.append(temporary_path)
            if index == selected:
                panel.save(directory / "preview.png")
        save_gif(
            ImageFiles(panels), directory / "process.gif", config.optics.animation_fps
        )
    finally:
        plt.close(fig)
        temporary_panels.cleanup()
    return selected


def build_report(directory, config, metrics):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    directory = Path(directory)
    rows = []
    for index, metric in enumerate(metrics):
        rows.append(
            {
                "frame": index,
                "time_s": metric["time_s"],
                "depth_m": metric["depth_m"],
                "twist_rad": metric["twist_rad"],
                "x_m": metric["x_m"],
                "y_m": metric["y_m"],
                "max_penetration_m": metric["max_penetration_m"],
                "max_sticking_elastic_slip_m": metric.get(
                    "max_sticking_elastic_slip_m"
                ),
                "max_indenter_deformation_m": metric.get("max_indenter_deformation_m"),
                "indenter_grip_displacement_error_m": metric.get(
                    "indenter_grip_displacement_error_m"
                ),
                "normal_force_n": metric["normal_force_n"],
                "force_x_n": metric["force_on_gel_n"][0],
                "force_y_n": metric["force_on_gel_n"][1],
                "moment_z_nm": metric["moment_on_gel_nm"][2],
                "max_marker_image_displacement_px": metric.get(
                    "max_marker_image_displacement_px"
                ),
                "force_balance_error_n": metric["force_balance_error_n"],
                "max_pressure_pa": metric["max_pressure_pa"],
                "max_marker_displacement_m": metric["max_marker_displacement_m"],
                "max_marker_in_plane_displacement_m": metric[
                    "max_marker_in_plane_displacement_m"
                ],
            }
        )
    with (directory / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    images = ImageFiles(
        directory / "images" / f"frame_{index:04d}.png" for index in range(len(metrics))
    )
    if config.visualization.save_tactile_gif:
        save_gif(images, directory / "tactile.gif", config.optics.animation_fps)
    limits = plot_limits(directory, metrics)
    selected = build_process_images(directory, config, metrics, images, limits)
    frame_folder = "panels" if config.visualization.save_panel_frames else "images"
    # Finite-element views are written frame by frame while the run solves; the
    # report only collects the ones that are there.
    mesh_frames = sorted((directory / "mesh").glob("frame_*.png"))
    if len(mesh_frames) > 1:
        save_gif(
            ImageFiles(mesh_frames), directory / "mesh.gif", config.optics.animation_fps
        )
    write_json(
        directory / "visualization.json",
        {
            "rgb_projection": config.camera.projection,
            "render_mode": config.optics.render_mode,
            "optical_model": config.optics.model,
            "difference_display": "round(128 + (raw - first_unloaded_raw)/2); signed int16 difference also saved",
            "field_projection": "orthographic gel-plane coordinates",
            "marker_scale": config.visualization.marker_scale,
            "marker_key_um": config.visualization.marker_key_um,
            "color_limits": limits,
            "frame_count": len(metrics),
            "frame_viewer_folder": frame_folder,
            "mesh_frame_count": len(mesh_frames),
            "mesh_view_contours": "contact pressure on the deformed contact surface; total nodal displacement on the deformed gel body",
            "mesh_deformation_scale": config.visualization.mesh_deformation_scale,
            "mesh_color_limits": "per frame in summary.json; raised in 1-2-5 steps as the run reaches new peaks",
            "tactile_gif_saved": config.visualization.save_tactile_gif,
            "preview_frame": selected,
            "animation_durations_ms": animation_durations(
                len(metrics), config.optics.animation_fps
            ),
            "marker_color_quantity": "actual in-plane displacement in micrometres",
            "magnification_applies_to": "plot arrows only; saved arrays and RGB use actual motion",
        },
    )
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.5), layout="constrained")
    time = [m["time_s"] for m in metrics]
    axes[0].plot(
        [m["depth_m"] * 1000 for m in metrics],
        [m["normal_force_n"] for m in metrics],
        "o-",
    )
    axes[0].set_xlabel("Prescribed grip travel / depth (mm)")
    axes[0].set_ylabel("Normal force on gel (N)")
    for j, label in enumerate(("Fx", "Fy", "Fz")):
        axes[1].plot(time, [m["force_on_gel_n"][j] for m in metrics], "o-", label=label)
    axes[1].set_xlabel("Load parameter time (s)")
    axes[1].set_ylabel("Contact force (N)")
    axes[1].legend()
    axes[2].plot(
        time, [m["moment_on_gel_nm"][2] * 1000 for m in metrics], "o-", color="#7c3aed"
    )
    axes[2].set_xlabel("Load parameter time (s)")
    axes[2].set_ylabel("Twisting moment Mz (N mm)")
    for axis in axes:
        axis.grid(alpha=0.2)
    fig.savefig(directory / "force_curve.png", dpi=130)
    plt.close(fig)
    title = html.escape(config.name)
    layer = "Uniform gel; silicone coating mechanically homogenized"
    object_description = (
        f"deformable {config.indenter.shape} E={config.indenter.material.young_pa / 1000:g} kPa"
        if config.indenter.deformable
        else "rigid object"
    )
    contact_description = f"friction coefficient {config.indenter.friction:g}"
    if config.is_plane:
        bulk = config.specification.bulk
        object_description = f"finite slab; {bulk['model']}"
        if "young_pa" in bulk:
            object_description += f"; E={bulk['young_pa'] / 1000:g} kPa"
        contact = config.specification.case["contact"]
        contact_description = (
            f"friction: {contact['friction']['model']}; adhesion: {contact['adhesion']['model']}"
        )
    depth_note = "Depth is commanded travel; a soft object absorbs part of it."
    frame_note = "Frame zero is an unloaded reference; later frames are converged ANSYS load steps."
    if config.is_plane:
        frame_note = (
            "Recording starts after the preload; the unloaded reference is "
            "unloaded_reference.png, and every frame is a converged ANSYS substep."
        )
        if config.specification.normal_control == "prescribed_normal_force":
            depth_note = (
                "Depth is the platen travel reached under the commanded normal "
                "force, read back from the solve; it is an outcome, not a setting."
            )
        else:
            depth_note = (
                "Depth is commanded platen travel from first touch, shared "
                "between the gel and the specimen."
            )
    material_note = html.escape(
        f"{layer}; {object_description}; {contact_description}. Provisional material parameters. {depth_note}"
    )
    data = json.dumps(metrics, allow_nan=False).replace("</", "<\\/")
    animation = (
        '<h2>Complete cycle</h2><img src="process.gif" '
        'alt="Tactile RGB, deformation, pressure and scaled marker motion">'
        if not config.visualization.save_panel_frames
        else ""
    )
    mesh_section = (
        '<h2>Finite-element view</h2><p class="note">Deformed mesh with element '
        "edges: contact pressure on the contact surface, total nodal displacement "
        "on the gel body, the rigid object drawn as grid lines. Deformation is "
        f"shown at {config.visualization.mesh_deformation_scale:g}x. Color limits "
        "grow with the run and are recorded per frame in summary.json.</p>"
        '<img src="mesh.gif" alt="Deformed finite-element mesh contoured by '
        'contact pressure and displacement">'
        '<img id="meshframe" alt="Deformed mesh for the selected frame">'
        if len(mesh_frames) > 1
        else ""
    )
    frame_alt = (
        "Tactile RGB, deformation, pressure and scaled marker motion"
        if config.visualization.save_panel_frames
        else "Saved tactile RGB frame"
    )
    tactile_link = (
        ' · <a href="tactile.gif">Tactile RGB GIF</a>'
        if config.visualization.save_tactile_gif
        else ""
    )
    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GelSight simulation</title><style>body{font:16px system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem;background:#111827;color:#e5e7eb}img{max-width:100%;border-radius:10px;background:white}input{width:100%}a{color:#93c5fd}.note{color:#b8c4d6}#metrics{white-space:pre-wrap}.toolbar{position:sticky;top:0;background:#111827;padding:1rem 0}</style>
<h1>"""
        + title
        + "</h1><p>"
        + material_note
        + "</p>"
        + """<p class="note">ANSYS contact · simulated sensor RGB. Arrow magnification is shown on the plot; colors and RGB retain actual motion. Color scales are fixed across the complete cycle.</p>
"""
        + animation
        + '<h2>Saved frames</h2><div class="toolbar"><label>Frame <input id="slider" type="range" min="0" max="'
        + str(len(metrics) - 1)
        + """" value="0" step="1"></label><div id="metrics"></div></div>
"""
        + '<img id="panel" alt="'
        + frame_alt
        + '">'
        + mesh_section
        + '<p><a href="process.gif">Full-process GIF</a>'
        + tactile_link
        + """ · <a href="metrics.csv">Metrics CSV</a> · <a href="summary.json">Run summary</a> · <a href="visualization.json">Plot scales</a></p>
<p class="note">GIF timing is illustrative. """
        + html.escape(frame_note)
        + """</p>
<img src="force_curve.png" alt="Force and twisting-moment curves">
<script>const data="""
        + data
        + ";const frameFolder="
        + json.dumps(frame_folder)
        + """;const slider=document.getElementById('slider');function show(){const i=Number(slider.value),m=data[i];const name='/frame_'+String(i).padStart(4,'0')+'.png';document.getElementById('panel').src=frameFolder+name;const mesh=document.getElementById('meshframe');if(mesh)mesh.src='mesh'+name;document.getElementById('metrics').textContent='Frame '+i+' / '+(data.length-1)+' | depth '+(m.depth_m*1000).toFixed(2)+' mm | twist '+(m.twist_rad*180/Math.PI).toFixed(1)+'° | normal force '+m.normal_force_n.toFixed(4)+' N | state '+m.state_source;}slider.oninput=show;show();</script></html>"""
    )
    (directory / "report.html").write_text(page, encoding="utf-8")
