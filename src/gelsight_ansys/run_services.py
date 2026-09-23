"""Shared run lifecycle and frame processing for every mechanical adapter."""

import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import build_report, save_frame, write_json
from .camera import optical_surface
from .metrics import frame_metrics, validate_frame
from .optics import Renderer
from .surface import image_coordinates, project_surface


def create_run(output, config):
    directory = (
        Path(output) / f"{config.name}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def prepare_optics(config, directory, base_directory):
    if config.optics.background_image:
        source = Path(config.optics.background_image)
        if not source.is_absolute():
            source = Path(base_directory or ".") / source
        target = directory / "background.png"
        from PIL import Image

        with Image.open(source) as image:
            image.convert("RGB").save(target)
        config = config.with_optics(background_image="background.png")
    write_json(directory / "config.json", config.to_dict())
    c = config.camera
    write_json(
        directory / "camera.json",
        {
            "sensor_model": config.sensor_model,
            "calibration_status": "nominal rectified model; unit-specific calibration not supplied",
            "image_width_height_px": [c.width_px, c.height_px],
            "reference_fov_width_height_m": [c.fov_width_m, c.fov_height_m],
            "reference_pixel_pitch_m": [
                c.fov_width_m / c.width_px,
                c.fov_height_m / c.height_px,
            ],
            "projection": c.projection,
            "standoff_m": c.standoff_m,
            "intrinsic_matrix": [
                [c.standoff_m * c.width_px / c.fov_width_m, 0, (c.width_px - 1) / 2],
                [0, c.standoff_m * c.height_px / c.fov_height_m, (c.height_px - 1) / 2],
                [0, 0, 1],
            ],
            "lens_distortion": "none; rectified image assumed",
            "image_axes": "column increases with gel x; row increases with negative gel y",
            "field_projection": "orthographic gel-plane force/deformation grids; optical_* fields follow camera rays",
            "marker_grid_rows_cols": config.optics.marker_grid_rows_cols,
            "marker_radius_px": config.optics.marker_radius_px,
            "marker_style": config.optics.marker_style,
            "optical_model": config.optics.model,
            "projection_backend": config.optics.backend,
            "render_mode": config.optics.render_mode,
            "response_gain": config.optics.response_gain,
            "response_smoothing_bins": config.optics.response_smoothing_bins,
            "response_rotation_deg": config.optics.response_rotation_deg,
            "response_interpolation": "periodic cubic"
            if config.optics.model == "taxim"
            else "analytic",
            "optical_calibration": "example-sensor table; not calibrated to this device"
            if config.optics.model == "taxim"
            else "configured analytic lights",
        },
    )
    return config, Renderer(config, directory)


def process_frame(
    directory,
    index,
    config,
    state,
    markers,
    renderer,
    pose,
    gpu,
    body_metrics=None,
    *,
    field_camera=None,
    store_optical_fields=True,
    extra_fields=None,
    extra_metrics=None,
):
    timings = {}
    started = time.perf_counter()
    fields = project_surface(
        state, field_camera or config.camera, backend=config.optics.backend
    )
    timings["surface_projection_s"] = time.perf_counter() - started
    started = time.perf_counter()
    optical = optical_surface(state, config.camera, fields, backend=config.optics.backend)
    if store_optical_fields:
        fields.update(optical)
    timings["camera_projection_s"] = time.perf_counter() - started
    started = time.perf_counter()
    positions = markers.positions(state)
    pixels = image_coordinates(positions, config.camera)
    reference_pixels = image_coordinates(markers.reference_m, config.camera)
    fields["marker_reference_pixel"] = reference_pixels
    fields["marker_flow_pixel"] = pixels - reference_pixels
    rgb = renderer.render({**fields, **optical}, pixels, reference_pixels)
    fields["rgb_difference_int16"] = renderer.difference
    fields.update(extra_fields or {})
    timings["render_s"] = time.perf_counter() - started
    started = time.perf_counter()
    metrics = frame_metrics(state, fields, markers.reference_m, positions, pose, gpu)
    import numpy as np

    metrics["max_marker_image_displacement_px"] = float(
        np.linalg.norm(fields["marker_flow_pixel"], axis=1).max()
    )
    metrics.update(body_metrics or {})
    metrics.update(extra_metrics or {})
    timings["metrics_s"] = time.perf_counter() - started
    started = time.perf_counter()
    save_frame(
        directory,
        index,
        state,
        fields,
        markers.reference_m,
        positions,
        pixels,
        rgb,
        metrics,
    )
    timings["save_frame_s"] = time.perf_counter() - started
    validate_frame(metrics, config)
    metrics["timings"] = timings
    return metrics


class RunLifecycle:
    """Persist failure diagnostics and completion evidence for solve or replay."""

    def __init__(self, directory, summary, *, started=None, previous_elapsed=0.0):
        self.directory, self.summary = directory, summary
        self.started = time.perf_counter() if started is None else started
        self.previous_elapsed = previous_elapsed

    def __enter__(self):
        self.summary["status"] = "running"
        write_json(self.directory / "summary.json", self.summary)
        return self

    def finish(self, config, status="passed"):
        if config.solver.require_gpu and not self.summary["gpu_mechanics_verified"]:
            raise RuntimeError(
                "GPU solver execution was required but not confirmed in sparse statistics"
            )
        self.summary["phase"] = "report"
        write_json(self.directory / "summary.json", self.summary)
        started = time.perf_counter()
        build_report(self.directory, config, self.summary["frames"])
        self.summary["report_elapsed_s"] = time.perf_counter() - started
        self.summary["status"] = status

    def __exit__(self, error_type, error, tb):
        if error is not None:
            self.summary.update(status="failed", error_type=error_type.__name__)
            private = self.directory / "private"
            private.mkdir(exist_ok=True)
            (private / "error.log").write_text(
                "".join(traceback.format_exception(error_type, error, tb)),
                encoding="utf-8",
            )
        self.summary["elapsed_s"] = (
            self.previous_elapsed + time.perf_counter() - self.started
        )
        write_json(self.directory / "summary.json", self.summary)
        return False
