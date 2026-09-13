"""Surface projection, material markers, and conservative force rasterization."""

import numpy as np

from .mesh import nodal_areas_normals


def camera_grid(camera):
    dx, dy = (
        camera.fov_width_m / camera.width_px,
        camera.fov_height_m / camera.height_px,
    )
    xmin = camera.center_x_m - camera.fov_width_m / 2
    ymax = camera.center_y_m + camera.fov_height_m / 2
    x = xmin + (np.arange(camera.width_px) + 0.5) * dx
    y = ymax - (np.arange(camera.height_px) + 0.5) * dy
    return np.meshgrid(x, y), (xmin, ymax, dx, dy)


def barycentric(points, triangle):
    matrix = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
    uv = (points - triangle[0]) @ np.linalg.inv(matrix).T
    return np.column_stack((1 - uv.sum(axis=1), uv))


def clip_polygon(polygon, axis, boundary, keep_greater):
    result = []
    if len(polygon) == 0:
        return result
    previous = np.asarray(polygon[-1])
    previous_inside = (
        (previous[axis] >= boundary) if keep_greater else (previous[axis] <= boundary)
    )
    for current in polygon:
        current = np.asarray(current)
        inside = (
            (current[axis] >= boundary) if keep_greater else (current[axis] <= boundary)
        )
        if inside != previous_inside:
            ratio = (boundary - previous[axis]) / (current[axis] - previous[axis])
            result.append(previous + ratio * (current - previous))
        if inside:
            result.append(current)
        previous, previous_inside = current, inside
    return result


def overlap_centroid(triangle, xmin, xmax, ymin, ymax):
    polygon = triangle
    for axis, boundary, greater in (
        (0, xmin, True),
        (0, xmax, False),
        (1, ymin, True),
        (1, ymax, False),
    ):
        polygon = clip_polygon(polygon, axis, boundary, greater)
        if len(polygon) < 3:
            return 0.0, np.zeros(2)
    polygon = np.asarray(polygon)
    following = np.roll(polygon, -1, axis=0)
    cross = polygon[:, 0] * following[:, 1] - following[:, 0] * polygon[:, 1]
    double_area = cross.sum()
    if abs(double_area) < 1e-25:
        return 0.0, np.zeros(2)
    centroid = ((polygon + following) * cross[:, None]).sum(axis=0) / (3 * double_area)
    return abs(double_area) / 2, centroid


def project_surface(state, camera, backend="cpu"):
    """Project a surface graph; preserve the integral of reconstructed nodal forces.

    Contact pressure is the raw element-average pressure sampled spatially.
    force_density_pa is a separate, conservative reconstruction of equivalent
    nodal loads per projected area. Neither field is globally renormalized.
    """
    if backend == "cuda":
        from .projection_cuda import project_surface_cuda

        return project_surface_cuda(state, camera)
    if backend != "cpu":
        raise ValueError("Projection backend must be cpu or cuda")
    vertices, triangles = state.position_m, state.triangles
    (_, _), (xmin, ymax, dx, dy) = camera_grid(camera)
    h, w = camera.height_px, camera.width_px
    _, normals = nodal_areas_normals(vertices, triangles)
    tri_xy = vertices[triangles, :2]
    edge1 = tri_xy[:, 1] - tri_xy[:, 0]
    edge2 = tri_xy[:, 2] - tri_xy[:, 0]
    projected_area = (edge1[:, 0] * edge2[:, 1] - edge1[:, 1] * edge2[:, 0]) / 2
    if np.any(projected_area <= 1e-20):
        raise ValueError(
            "Surface folds/overhangs are unsupported by the orthographic renderer"
        )
    lump = np.zeros(len(vertices))
    for corner in range(3):
        np.add.at(lump, triangles[:, corner], projected_area / 3)
    nodal_density = state.contact_force_n / lump[:, None]
    result = {
        "position_m": np.zeros((h, w, 3)),
        "displacement_m": np.zeros((h, w, 3)),
        "normals": np.zeros((h, w, 3)),
        "contact_pressure_pa": np.zeros((h, w)),
        "contact_status": np.zeros((h, w), dtype=np.int8),
        "valid_mask": np.zeros((h, w), dtype=bool),
        "pixel_force_n": np.zeros((h, w, 3)),
    }
    result["normals"][..., 2] = 1
    height = np.full((h, w), -np.inf)
    fov_force = np.zeros(3)
    for ti, ids in enumerate(triangles):
        xy = vertices[ids, :2]
        column = (xy[:, 0] - xmin) / dx
        row = (ymax - xy[:, 1]) / dy
        c0, c1 = (
            max(0, int(np.floor(column.min()))),
            min(w - 1, int(np.floor(column.max()))),
        )
        r0, r1 = max(0, int(np.floor(row.min()))), min(h - 1, int(np.floor(row.max())))
        if c1 < c0 or r1 < r0:
            continue
        rr, cc = np.meshgrid(
            np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), indexing="ij"
        )
        rr, cc = rr.ravel(), cc.ravel()
        points = np.column_stack((xmin + (cc + 0.5) * dx, ymax - (rr + 0.5) * dy))
        weights = barycentric(points, xy)
        zz = weights @ vertices[ids, 2]
        inside = (weights >= -1e-10).all(axis=1) & (zz >= height[rr, cc])
        ar, ac, aw = rr[inside], cc[inside], weights[inside]
        result["position_m"][ar, ac] = aw @ vertices[ids]
        result["displacement_m"][ar, ac] = aw @ state.displacement_m[ids]
        result["normals"][ar, ac] = aw @ normals[ids]
        face = ti % len(state.quads)
        result["contact_pressure_pa"][ar, ac] = state.contact_pressure_pa[face]
        result["contact_status"][ar, ac] = int(round(state.contact_status[face]))
        result["valid_mask"][ar, ac] = True
        height[ar, ac] = zz[inside]
        # Exact zero nodal loads have zero integral, including clipped pixels.
        if not np.any(nodal_density[ids]):
            continue
        # A linear field integrates exactly as area * value at polygon centroid.
        inv = np.linalg.inv(np.column_stack((xy[1] - xy[0], xy[2] - xy[0])))
        gradients = np.vstack((-inv.sum(axis=0), inv))
        half_span = np.abs(gradients[:, 0]) * dx / 2 + np.abs(gradients[:, 1]) * dy / 2
        full = (weights - half_span >= -1e-12).all(axis=1)
        possible = (weights + half_span >= -1e-12).all(axis=1)
        result["pixel_force_n"][rr[full], cc[full]] += (
            dx * dy * (weights[full] @ nodal_density[ids])
        )
        for index in np.flatnonzero(possible & ~full):
            r, c = rr[index], cc[index]
            area, centroid = overlap_centroid(
                xy,
                xmin + c * dx,
                xmin + (c + 1) * dx,
                ymax - (r + 1) * dy,
                ymax - r * dy,
            )
            if area:
                value = barycentric(centroid[None], xy)[0] @ nodal_density[ids]
                result["pixel_force_n"][r, c] += area * value
        area, centroid = overlap_centroid(xy, xmin, xmin + w * dx, ymax - h * dy, ymax)
        if area:
            fov_force += area * (
                barycentric(centroid[None], xy)[0] @ nodal_density[ids]
            )
    norm = np.linalg.norm(result["normals"], axis=-1, keepdims=True)
    result["normals"] /= np.maximum(norm, 1e-30)
    result["force_density_pa"] = result["pixel_force_n"] / (dx * dy)
    result["normal_displacement_m"] = -result["displacement_m"][..., 2]
    result["shear_displacement_m"] = result["displacement_m"][..., :2]
    result["slip_mask"] = result["contact_status"] == 2
    result["fov_force_n"] = fov_force
    result["raster_force_error_n"] = (
        result["pixel_force_n"].sum(axis=(0, 1)) - fov_force
    )
    return result


class Markers:
    """Fixed material attachments, independent of contact-mask or force fields."""

    def __init__(self, state, spacing, camera=None, optics=None):
        reference = state.reference_m
        maximum = reference.max(axis=0)
        minimum = reference.min(axis=0)
        x = (
            np.arange(np.ceil(minimum[0] / spacing), np.floor(maximum[0] / spacing) + 1)
            * spacing
        )
        y = (
            np.arange(np.ceil(minimum[1] / spacing), np.floor(maximum[1] / spacing) + 1)
            * spacing
        )
        xx, yy = np.meshgrid(x, y)
        points = np.column_stack((xx.ravel(), yy.ravel()))
        if optics is not None and optics.marker_grid_rows_cols is not None:
            from .camera import pixels_to_reference, reference_marker_pixels

            points = pixels_to_reference(
                reference_marker_pixels(camera, optics), camera
            )[:, :2]
        self.triangle_index = np.full(len(points), -1, dtype=int)
        self.weights = np.zeros((len(points), 3))
        for index, triangle in enumerate(state.triangles):
            weights = barycentric(points, reference[triangle, :2])
            inside = (weights >= -1e-10).all(axis=1) & (self.triangle_index < 0)
            self.triangle_index[inside] = index
            self.weights[inside] = weights[inside]
        if np.any(self.triangle_index < 0):
            raise ValueError("Marker attachment lies outside the reference mesh")
        self.triangles = state.triangles[self.triangle_index]
        self.reference_m = np.einsum(
            "ij,ijk->ik", self.weights, reference[self.triangles]
        )

    def positions(self, state):
        return np.einsum("ij,ijk->ik", self.weights, state.position_m[self.triangles])


def image_coordinates(points, camera):
    points = np.asarray(points)
    if camera.projection == "pinhole":
        depth = camera.standoff_m + points[:, 2]
        if np.any(depth <= 0):
            raise ValueError("Surface point reaches or crosses the camera plane")
        center = np.array([camera.center_x_m, camera.center_y_m])
        xy = center + (points[:, :2] - center) * (camera.standoff_m / depth)[:, None]
        points = np.column_stack((xy, points[:, 2]))
    (_, _), (xmin, ymax, dx, dy) = camera_grid(camera)
    return np.column_stack(
        ((points[:, 0] - xmin) / dx - 0.5, (ymax - points[:, 1]) / dy - 0.5)
    )
