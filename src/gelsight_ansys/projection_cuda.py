"""Double-precision CUDA surface projection with conservative pixel integrals.

One thread owns each pixel. Sorted triangle candidates preserve the CPU tie
order and avoid atomic accumulation races at contact-element boundaries.
"""

import numpy as np
import warp as wp

from .mesh import nodal_areas_normals
from .surface import camera_grid, image_coordinates

Polygon = wp.types.matrix(shape=(8, 2), dtype=wp.float64)


@wp.func
def weights_at(p: wp.vec2d, a: wp.vec2d, b: wp.vec2d, c: wp.vec2d):
    u = b - a
    v = c - a
    q = p - a
    det = u[0] * v[1] - u[1] * v[0]
    w1 = (q[0] * v[1] - q[1] * v[0]) / det
    w2 = (u[0] * q[1] - u[1] * q[0]) / det
    return wp.vec3d(wp.float64(1.0) - w1 - w2, w1, w2)


@wp.func
def clip(poly: Polygon, count: int, axis: int, bound: wp.float64, greater: int):
    out = Polygon()
    size = int(0)
    if count > 0:
        previous = wp.vec2d(poly[count - 1, 0], poly[count - 1, 1])
        previous_inside = previous[axis] >= bound
        if greater == 0:
            previous_inside = previous[axis] <= bound
        for i in range(count):
            current = wp.vec2d(poly[i, 0], poly[i, 1])
            inside = current[axis] >= bound
            if greater == 0:
                inside = current[axis] <= bound
            if inside != previous_inside:
                ratio = (bound - previous[axis]) / (current[axis] - previous[axis])
                point = previous + ratio * (current - previous)
                out[size, 0] = point[0]
                out[size, 1] = point[1]
                size += 1
            if inside:
                out[size, 0] = current[0]
                out[size, 1] = current[1]
                size += 1
            previous = current
            previous_inside = inside
    return out, size


@wp.func
def overlap(
    a: wp.vec2d,
    b: wp.vec2d,
    c: wp.vec2d,
    xmin: wp.float64,
    xmax: wp.float64,
    ymin: wp.float64,
    ymax: wp.float64,
):
    poly = Polygon()
    poly[0, 0] = a[0]
    poly[0, 1] = a[1]
    poly[1, 0] = b[0]
    poly[1, 1] = b[1]
    poly[2, 0] = c[0]
    poly[2, 1] = c[1]
    poly, n = clip(poly, 3, 0, xmin, 1)
    poly, n = clip(poly, n, 0, xmax, 0)
    poly, n = clip(poly, n, 1, ymin, 1)
    poly, n = clip(poly, n, 1, ymax, 0)
    result = wp.vec3d()
    if n >= 3:
        area2 = wp.float64(0.0)
        cx = wp.float64(0.0)
        cy = wp.float64(0.0)
        for i in range(n):
            j = (i + 1) % n
            cross = poly[i, 0] * poly[j, 1] - poly[j, 0] * poly[i, 1]
            area2 += cross
            cx += (poly[i, 0] + poly[j, 0]) * cross
            cy += (poly[i, 1] + poly[j, 1]) * cross
        if wp.abs(area2) >= wp.float64(1.0e-25):
            result = wp.vec3d(
                wp.abs(area2) / wp.float64(2.0),
                cx / (wp.float64(3.0) * area2),
                cy / (wp.float64(3.0) * area2),
            )
    return result


@wp.func
def xy(v: wp.vec3d):
    return wp.vec2d(v[0], v[1])


@wp.func
def interpolate(w: wp.vec3d, a: wp.vec3d, b: wp.vec3d, c: wp.vec3d):
    return w[0] * a + w[1] * b + w[2] * c


@wp.kernel
def surface_pixels(
    starts: wp.array(dtype=wp.int32),
    candidates: wp.array(dtype=wp.int32),
    triangles: wp.array(dtype=wp.vec3i),
    vertices: wp.array(dtype=wp.vec3d),
    displacement: wp.array(dtype=wp.vec3d),
    normals: wp.array(dtype=wp.vec3d),
    density: wp.array(dtype=wp.vec3d),
    pressure: wp.array(dtype=wp.float64),
    status: wp.array(dtype=wp.int32),
    nfaces: int,
    width: int,
    xmin: wp.float64,
    ymax: wp.float64,
    dx: wp.float64,
    dy: wp.float64,
    positions_out: wp.array(dtype=wp.vec3d),
    displacement_out: wp.array(dtype=wp.vec3d),
    normals_out: wp.array(dtype=wp.vec3d),
    pressure_out: wp.array(dtype=wp.float64),
    status_out: wp.array(dtype=wp.int32),
    valid_out: wp.array(dtype=wp.int32),
    force_out: wp.array(dtype=wp.vec3d),
):
    pixel = wp.tid()
    row = pixel // width
    col = pixel % width
    left = xmin + wp.float64(col) * dx
    top = ymax - wp.float64(row) * dy
    p = wp.vec2d(left + dx / wp.float64(2.0), top - dy / wp.float64(2.0))
    height = wp.float64(-1.0e300)
    force = wp.vec3d()
    pos = wp.vec3d()
    disp = wp.vec3d()
    normal = wp.vec3d(wp.float64(0.0), wp.float64(0.0), wp.float64(1.0))
    pres = wp.float64(0.0)
    stat = int(0)
    valid = int(0)
    for slot in range(starts[pixel], starts[pixel + 1]):
        ti = candidates[slot]
        ids = triangles[ti]
        a = xy(vertices[ids[0]])
        b = xy(vertices[ids[1]])
        c = xy(vertices[ids[2]])
        w = weights_at(p, a, b, c)
        point = interpolate(w, vertices[ids[0]], vertices[ids[1]], vertices[ids[2]])
        if (
            wp.min(w[0], wp.min(w[1], w[2])) >= wp.float64(-1.0e-10)
            and point[2] >= height
        ):
            height = point[2]
            pos = point
            disp = interpolate(
                w, displacement[ids[0]], displacement[ids[1]], displacement[ids[2]]
            )
            normal = interpolate(w, normals[ids[0]], normals[ids[1]], normals[ids[2]])
            pres = pressure[ti % nfaces]
            stat = status[ti % nfaces]
            valid = 1
        e1 = b - a
        e2 = c - a
        det = e1[0] * e2[1] - e1[1] * e2[0]
        g1 = wp.vec2d(e2[1] / det, -e2[0] / det)
        g2 = wp.vec2d(-e1[1] / det, e1[0] / det)
        g0 = -g1 - g2
        half = wp.vec3d(
            (wp.abs(g0[0]) * dx + wp.abs(g0[1]) * dy) / wp.float64(2.0),
            (wp.abs(g1[0]) * dx + wp.abs(g1[1]) * dy) / wp.float64(2.0),
            (wp.abs(g2[0]) * dx + wp.abs(g2[1]) * dy) / wp.float64(2.0),
        )
        lo = w - half
        hi = w + half
        if wp.min(lo[0], wp.min(lo[1], lo[2])) >= wp.float64(-1.0e-12):
            force += (
                dx
                * dy
                * interpolate(w, density[ids[0]], density[ids[1]], density[ids[2]])
            )
        elif wp.min(hi[0], wp.min(hi[1], hi[2])) >= wp.float64(-1.0e-12):
            part = overlap(a, b, c, left, left + dx, top - dy, top)
            if part[0] > wp.float64(0.0):
                wc = weights_at(wp.vec2d(part[1], part[2]), a, b, c)
                force += part[0] * interpolate(
                    wc, density[ids[0]], density[ids[1]], density[ids[2]]
                )
    positions_out[pixel] = pos
    displacement_out[pixel] = disp
    normals_out[pixel] = normal / wp.max(wp.length(normal), wp.float64(1.0e-30))
    pressure_out[pixel] = pres
    status_out[pixel] = stat
    valid_out[pixel] = valid
    force_out[pixel] = force


@wp.kernel
def fov_integrals(
    triangles: wp.array(dtype=wp.vec3i),
    vertices: wp.array(dtype=wp.vec3d),
    density: wp.array(dtype=wp.vec3d),
    xmin: wp.float64,
    xmax: wp.float64,
    ymin: wp.float64,
    ymax: wp.float64,
    result: wp.array(dtype=wp.vec3d),
):
    ti = wp.tid()
    ids = triangles[ti]
    a = xy(vertices[ids[0]])
    b = xy(vertices[ids[1]])
    c = xy(vertices[ids[2]])
    part = overlap(a, b, c, xmin, xmax, ymin, ymax)
    force = wp.vec3d()
    if part[0] > wp.float64(0.0):
        w = weights_at(wp.vec2d(part[1], part[2]), a, b, c)
        force = part[0] * interpolate(
            w, density[ids[0]], density[ids[1]], density[ids[2]]
        )
    result[ti] = force


@wp.kernel
def camera_pixels(
    starts: wp.array(dtype=wp.int32),
    candidates: wp.array(dtype=wp.int32),
    triangles: wp.array(dtype=wp.vec3i),
    uv: wp.array(dtype=wp.vec2d),
    depths: wp.array(dtype=wp.float64),
    vertices: wp.array(dtype=wp.vec3d),
    reference: wp.array(dtype=wp.vec3d),
    normals: wp.array(dtype=wp.vec3d),
    width: int,
    positions_out: wp.array(dtype=wp.vec3d),
    reference_out: wp.array(dtype=wp.vec3d),
    normals_out: wp.array(dtype=wp.vec3d),
    valid_out: wp.array(dtype=wp.int32),
):
    pixel = wp.tid()
    p = wp.vec2d(wp.float64(pixel % width), wp.float64(pixel // width))
    nearest = wp.float64(1.0e300)
    pos = wp.vec3d()
    ref = wp.vec3d()
    normal = wp.vec3d(wp.float64(0.0), wp.float64(0.0), wp.float64(1.0))
    valid = int(0)
    for slot in range(starts[pixel], starts[pixel + 1]):
        ids = triangles[candidates[slot]]
        a = uv[ids[0]]
        b = uv[ids[1]]
        c = uv[ids[2]]
        e1 = b - a
        e2 = c - a
        det = e1[0] * e2[1] - e1[1] * e2[0]
        if wp.abs(det) >= wp.float64(1.0e-12):
            w = weights_at(p, a, b, c)
            if wp.min(w[0], wp.min(w[1], w[2])) >= wp.float64(-1.0e-10):
                corrected = wp.vec3d(
                    w[0] / depths[ids[0]], w[1] / depths[ids[1]], w[2] / depths[ids[2]]
                )
                inv_depth = corrected[0] + corrected[1] + corrected[2]
                if inv_depth > wp.float64(0.0):
                    depth = wp.float64(1.0) / inv_depth
                    if depth <= nearest:
                        nearest = depth
                        corrected *= depth
                        pos = interpolate(
                            corrected,
                            vertices[ids[0]],
                            vertices[ids[1]],
                            vertices[ids[2]],
                        )
                        ref = interpolate(
                            corrected,
                            reference[ids[0]],
                            reference[ids[1]],
                            reference[ids[2]],
                        )
                        normal = interpolate(
                            corrected, normals[ids[0]], normals[ids[1]], normals[ids[2]]
                        )
                        valid = 1
    positions_out[pixel] = pos
    reference_out[pixel] = ref
    normals_out[pixel] = normal / wp.max(wp.length(normal), wp.float64(1.0e-30))
    valid_out[pixel] = valid


def candidates_by_pixel(screen_triangles, h, w, centers=False):
    """Stable CSR triangle lists; the inexpensive bounding boxes stay on CPU."""
    lower = (
        np.ceil(screen_triangles.min(axis=1))
        if centers
        else np.floor(screen_triangles.min(axis=1))
    )
    upper = np.floor(screen_triangles.max(axis=1))
    lower = np.maximum(lower.astype(np.int64), [0, 0])
    upper = np.minimum(upper.astype(np.int64), [w - 1, h - 1])
    pixels, triangles = [], []
    for ti, ((c0, r0), (c1, r1)) in enumerate(zip(lower, upper)):
        if c1 < c0 or r1 < r0:
            continue
        chunk = (np.arange(r0, r1 + 1)[:, None] * w + np.arange(c0, c1 + 1)).ravel()
        pixels.append(chunk)
        triangles.append(np.full(len(chunk), ti, dtype=np.int32))
    if not pixels:
        return np.zeros(h * w + 1, dtype=np.int32), np.empty(0, dtype=np.int32)
    pixels = np.concatenate(pixels)
    order = np.argsort(pixels, kind="stable")
    starts = np.concatenate(([0], np.cumsum(np.bincount(pixels, minlength=h * w))))
    if starts[-1] > np.iinfo(np.int32).max:
        raise ValueError("Projection candidate list exceeds int32 capacity")
    return starts.astype(np.int32), np.concatenate(triangles)[order]


def device_array(value, dtype):
    return wp.array(np.ascontiguousarray(value), dtype=dtype, device="cuda:0")


def require_cuda():
    wp.init()
    if not wp.is_cuda_available():
        raise RuntimeError("CUDA projection requested but no CUDA device is available")


def project_surface_cuda(state, camera):
    require_cuda()
    vertices, triangles = state.position_m, state.triangles
    (_, _), (xmin, ymax, dx, dy) = camera_grid(camera)
    h, w = camera.height_px, camera.width_px
    _, normals = nodal_areas_normals(vertices, triangles)
    xy_tri = vertices[triangles, :2]
    e1, e2 = xy_tri[:, 1] - xy_tri[:, 0], xy_tri[:, 2] - xy_tri[:, 0]
    areas = (e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2
    if np.any(areas <= 1e-20):
        raise ValueError(
            "Surface folds/overhangs are unsupported by the orthographic renderer"
        )
    lump = np.zeros(len(vertices))
    for corner in range(3):
        np.add.at(lump, triangles[:, corner], areas / 3)
    density = state.contact_force_n / lump[:, None]
    screen = np.empty_like(xy_tri)
    screen[..., 0] = (xy_tri[..., 0] - xmin) / dx
    screen[..., 1] = (ymax - xy_tri[..., 1]) / dy
    starts, candidates = candidates_by_pixel(screen, h, w)
    tri = device_array(triangles, wp.vec3i)
    xyz = device_array(vertices, wp.vec3d)
    dens = device_array(density, wp.vec3d)
    vec = [wp.empty(h * w, dtype=wp.vec3d, device="cuda:0") for _ in range(4)]
    pres = wp.empty(h * w, dtype=wp.float64, device="cuda:0")
    stat = wp.empty(h * w, dtype=wp.int32, device="cuda:0")
    valid = wp.empty(h * w, dtype=wp.int32, device="cuda:0")
    wp.launch(
        surface_pixels,
        dim=h * w,
        inputs=[
            device_array(starts, wp.int32),
            device_array(candidates, wp.int32),
            tri,
            xyz,
            device_array(state.displacement_m, wp.vec3d),
            device_array(normals, wp.vec3d),
            dens,
            device_array(state.contact_pressure_pa, wp.float64),
            device_array(np.rint(state.contact_status).astype(np.int32), wp.int32),
            len(state.quads),
            w,
            xmin,
            ymax,
            dx,
            dy,
            vec[0],
            vec[1],
            vec[2],
            pres,
            stat,
            valid,
            vec[3],
        ],
        device="cuda:0",
    )
    integral = wp.empty(len(triangles), dtype=wp.vec3d, device="cuda:0")
    wp.launch(
        fov_integrals,
        dim=len(triangles),
        inputs=[tri, xyz, dens, xmin, xmin + w * dx, ymax - h * dy, ymax, integral],
        device="cuda:0",
    )
    result = {
        key: array.numpy().reshape(h, w, 3)
        for key, array in zip(
            ("position_m", "displacement_m", "normals", "pixel_force_n"), vec
        )
    }
    result.update(
        contact_pressure_pa=pres.numpy().reshape(h, w),
        contact_status=stat.numpy().reshape(h, w).astype(np.int8),
        valid_mask=valid.numpy().reshape(h, w).astype(bool),
    )
    result["force_density_pa"] = result["pixel_force_n"] / (dx * dy)
    result["normal_displacement_m"] = -result["displacement_m"][..., 2]
    result["shear_displacement_m"] = result["displacement_m"][..., :2]
    result["slip_mask"] = result["contact_status"] == 2
    result["fov_force_n"] = integral.numpy().sum(axis=0)
    result["raster_force_error_n"] = (
        result["pixel_force_n"].sum(axis=(0, 1)) - result["fov_force_n"]
    )
    return result


def optical_surface_cuda(state, camera):
    require_cuda()
    vertices, triangles = state.position_m, state.triangles
    uv = image_coordinates(vertices, camera)
    depths = vertices[:, 2] + camera.standoff_m
    _, normals = nodal_areas_normals(vertices, triangles)
    h, w = camera.height_px, camera.width_px
    starts, candidates = candidates_by_pixel(uv[triangles], h, w, centers=True)
    outputs = [wp.empty(h * w, dtype=wp.vec3d, device="cuda:0") for _ in range(3)]
    valid = wp.empty(h * w, dtype=wp.int32, device="cuda:0")
    wp.launch(
        camera_pixels,
        dim=h * w,
        inputs=[
            device_array(starts, wp.int32),
            device_array(candidates, wp.int32),
            device_array(triangles, wp.vec3i),
            device_array(uv, wp.vec2d),
            device_array(depths, wp.float64),
            device_array(vertices, wp.vec3d),
            device_array(state.reference_m, wp.vec3d),
            device_array(normals, wp.vec3d),
            w,
            *outputs,
            valid,
        ],
        device="cuda:0",
    )
    result = {
        key: array.numpy().reshape(h, w, 3)
        for key, array in zip(
            ("optical_position_m", "optical_reference_m", "optical_normals"), outputs
        )
    }
    result["optical_valid_mask"] = valid.numpy().reshape(h, w).astype(bool)
    return result
