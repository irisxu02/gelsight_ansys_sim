"""Rectified camera projection and material coordinates for deforming dot textures.

Optical sampling is separate from the orthographic, conservative force grid.
Perspective-correct triangle interpolation gives the exact material attachment
at each camera ray, without approximating an inverse displacement field.
"""

import numpy as np

from .mesh import nodal_areas_normals
from .surface import barycentric, image_coordinates


def reference_marker_pixels(camera, optics):
    rows, cols = optics.marker_grid_rows_cols
    my, mx = optics.marker_margin_px
    yy = (
        np.linspace(my, camera.height_px - 1 - my, rows)
        if rows > 1
        else [(camera.height_px - 1) / 2]
    )
    xx = (
        np.linspace(mx, camera.width_px - 1 - mx, cols)
        if cols > 1
        else [(camera.width_px - 1) / 2]
    )
    xx, yy = np.meshgrid(xx, yy)
    return np.column_stack((xx.ravel(), yy.ravel()))


def pixels_to_reference(pixels, camera):
    pixels = np.asarray(pixels)
    x = (
        camera.center_x_m
        + ((pixels[:, 0] + 0.5) / camera.width_px - 0.5) * camera.fov_width_m
    )
    y = (
        camera.center_y_m
        + (0.5 - (pixels[:, 1] + 0.5) / camera.height_px) * camera.fov_height_m
    )
    return np.column_stack((x, y, np.zeros_like(x)))


def optical_surface(state, camera, fields, backend="cpu"):
    if camera.projection == "orthographic":
        return {
            "optical_position_m": fields["position_m"],
            "optical_reference_m": fields["position_m"] - fields["displacement_m"],
            "optical_normals": fields["normals"],
            "optical_valid_mask": fields["valid_mask"],
        }
    if backend == "cuda":
        from .projection_cuda import optical_surface_cuda

        return optical_surface_cuda(state, camera)
    if backend != "cpu":
        raise ValueError("Projection backend must be cpu or cuda")
    vertices = state.position_m
    uv = image_coordinates(vertices, camera)
    depths = vertices[:, 2] + camera.standoff_m
    _, normals = nodal_areas_normals(vertices, state.triangles)
    h, w = camera.height_px, camera.width_px
    result = {
        k: np.zeros((h, w, 3))
        for k in ("optical_position_m", "optical_reference_m", "optical_normals")
    }
    result["optical_normals"][..., 2] = 1
    result["optical_valid_mask"] = np.zeros((h, w), dtype=bool)
    nearest = np.full((h, w), np.inf)
    for ids in state.triangles:
        tri = uv[ids]
        c0, r0 = np.maximum(np.ceil(tri.min(axis=0)).astype(int), [0, 0])
        c1, r1 = np.minimum(np.floor(tri.max(axis=0)).astype(int), [w - 1, h - 1])
        if c1 < c0 or r1 < r0:
            continue
        matrix = np.column_stack((tri[1] - tri[0], tri[2] - tri[0]))
        if abs(np.linalg.det(matrix)) < 1e-12:
            continue
        rr, cc = np.meshgrid(
            np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), indexing="ij"
        )
        rr, cc = rr.ravel(), cc.ravel()
        screen_weights = barycentric(np.column_stack((cc, rr)), tri)
        weights = screen_weights / depths[ids]
        inv_depth = weights.sum(axis=1)
        depth = np.divide(
            1.0, inv_depth, out=np.full_like(inv_depth, np.inf), where=inv_depth > 0
        )
        inside = (screen_weights >= -1e-10).all(axis=1) & (depth <= nearest[rr, cc])
        rr, cc = rr[inside], cc[inside]
        weights = weights[inside] * depth[inside, None]
        result["optical_position_m"][rr, cc] = weights @ vertices[ids]
        result["optical_reference_m"][rr, cc] = weights @ state.reference_m[ids]
        result["optical_normals"][rr, cc] = weights @ normals[ids]
        result["optical_valid_mask"][rr, cc] = True
        nearest[rr, cc] = depth[inside]
    norms = np.linalg.norm(result["optical_normals"], axis=-1, keepdims=True)
    result["optical_normals"] /= np.maximum(norms, 1e-30)
    return result
