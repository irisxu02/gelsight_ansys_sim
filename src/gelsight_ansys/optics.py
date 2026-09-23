"""Synthetic configurable-light rendering with a NumPy reference implementation."""

from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image


def shade(normals, optics):
    directions = np.asarray(optics.light_directions, dtype=float)
    directions /= np.linalg.norm(directions, axis=1, keepdims=True)
    colors = np.asarray(optics.light_colors, dtype=float)
    half = directions + [0, 0, 1]
    half /= np.maximum(np.linalg.norm(half, axis=1, keepdims=True), 1e-15)
    diffuse = np.maximum(normals @ directions.T, 0)
    specular = np.maximum(normals @ half.T, 0) ** optics.shininess
    return (
        optics.ambient
        + (optics.diffuse * diffuse + optics.specular * specular) @ colors
    )


def render_cpu(
    normals,
    valid,
    markers,
    background,
    camera,
    optics,
    texture_coordinates=None,
    shaded=None,
):
    baseline = shade(np.array([0.0, 0.0, 1.0]), optics)
    linear = (
        (background + shade(normals, optics) - baseline)
        if shaded is None
        else shaded.copy()
    )
    linear[~valid] = background[~valid]
    yy, xx = np.indices(valid.shape)
    rx = optics.marker_radius_px or optics.marker_radius_m / (
        camera.fov_width_m / camera.width_px
    )
    ry = optics.marker_radius_px or optics.marker_radius_m / (
        camera.fov_height_m / camera.height_px
    )
    material = optics.marker_style == "material"
    if material:
        xx, yy = texture_coordinates[..., 0], texture_coordinates[..., 1]
    attenuation = np.ones(valid.shape)
    for x, y in markers:
        d2 = ((xx - x) / rx) ** 2 + ((yy - y) / ry) ** 2
        if material:
            # Antialiased opaque disks in the material's reference texture.
            alpha = np.clip((1 - np.sqrt(d2)) * min(rx, ry) + 0.5, 0, 1)
            attenuation *= (1 - optics.marker_opacity * alpha) ** optics.gamma
        else:
            attenuation *= 1 - optics.marker_opacity * np.exp(-2 * d2) * (d2 < 9)
    linear *= np.where(valid, attenuation, 1)[..., None]
    return np.clip(linear, 0, 1) ** (1 / optics.gamma)


class Renderer:
    def __init__(self, config, base_directory=None):
        self.camera, self.optics = config.camera, config.optics
        h, w = self.camera.height_px, self.camera.width_px
        self.background = np.broadcast_to(
            shade(np.array([0.0, 0.0, 1.0]), self.optics), (h, w, 3)
        ).copy()
        self.response = None
        if self.optics.model == "taxim":
            from .taxim import TaximResponse

            self.response = TaximResponse(
                self.camera,
                self.optics.response_gain,
                self.optics.response_smoothing_bins,
                self.optics.response_rotation_deg,
            )
            self.background = self.response.background.copy()
        if self.optics.background_image:
            path = Path(self.optics.background_image)
            if base_directory is not None and not path.is_absolute():
                path = Path(base_directory) / path
            with Image.open(path) as image:
                image = np.asarray(image.convert("RGB"), dtype=float) / 255
            if image.shape != (h, w, 3):
                raise ValueError(
                    "Background image must match configured camera dimensions"
                )
            self.background = image if self.response else image**self.optics.gamma
        self.cuda = None
        if self.optics.backend == "cuda":
            from .optics_cuda import CudaRenderer

            self.cuda = CudaRenderer(
                self.camera, self.optics, self.background, self.response
            )
        self.device = self.cuda.device_name if self.cuda else "cpu"
        self.reference_rgb = None
        self.raw_rgb = None
        self.difference = None

    def render(self, fields, marker_pixels, reference_pixels=None):
        from .surface import image_coordinates

        normals = fields.get("optical_normals", fields["normals"])
        valid = fields.get("optical_valid_mask", fields["valid_mask"])
        texture_coordinates = None
        if self.optics.marker_style == "material":
            if (
                self.camera.projection == "pinhole"
                and "optical_reference_m" not in fields
            ):
                raise ValueError(
                    "Pinhole rendering requires ray-sampled optical fields"
                )
            reference = fields.get("optical_reference_m")
            if reference is None:
                reference = fields["position_m"] - fields["displacement_m"]
            texture_coordinates = image_coordinates(
                reference.reshape(-1, 3), self.camera
            ).reshape(*valid.shape, 2)
            marker_pixels = (
                reference_pixels if reference_pixels is not None else marker_pixels
            )
        if self.cuda is None:
            image = render_cpu(
                normals,
                valid,
                marker_pixels,
                self.background,
                self.camera,
                replace(self.optics, gamma=1.0) if self.response else self.optics,
                texture_coordinates,
                self.response.shade(normals, valid, self.background)
                if self.response
                else None,
            )
        else:
            image = self.cuda.render(normals, valid, marker_pixels, texture_coordinates)
        self.raw_rgb = np.rint(image * 255).astype(np.uint8)
        if self.reference_rgb is None:
            self.reference_rgb = self.raw_rgb.copy()
        self.difference = self.raw_rgb.astype(np.int16) - self.reference_rgb.astype(
            np.int16
        )
        if self.optics.render_mode == "subtracted":
            # Signed differences retain both brightening and darkening. Zero is 128.
            return np.clip(np.rint(128 + self.difference / 2), 0, 255).astype(np.uint8)
        return self.raw_rgb
