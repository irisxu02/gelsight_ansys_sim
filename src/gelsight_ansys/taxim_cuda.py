"""CUDA evaluation of the example sensor's angular RGB response table."""

import numpy as np
import warp as wp


@wp.func
def cubic_weight(t: float, k: int):
    result = float(0.0)
    if k == 0:
        result = -0.5 * t + t * t - 0.5 * t * t * t
    elif k == 1:
        result = 1.0 - 2.5 * t * t + 1.5 * t * t * t
    elif k == 2:
        result = 0.5 * t + 2.0 * t * t - 1.5 * t * t * t
    else:
        result = -0.5 * t * t + 0.5 * t * t * t
    return result


@wp.kernel
def response_kernel(
    normals: wp.array(dtype=wp.vec3),
    valid: wp.array(dtype=wp.int32),
    table: wp.array4d(dtype=float),
    features: wp.array2d(dtype=float),
    flat: wp.array(dtype=wp.vec3),
    background: wp.array(dtype=wp.vec3),
    gain: float,
    rotation_cos: float,
    rotation_sin: float,
    output: wp.array(dtype=wp.vec3),
):
    p = wp.tid()
    rgb = background[p]
    if valid[p] != 0:
        m = normals[p]
        # taxim.rotate_normals: turn the in-plane part by -rotation.
        n = wp.vec3(
            rotation_cos * m[0] + rotation_sin * m[1],
            -rotation_sin * m[0] + rotation_cos * m[1],
            m[2],
        )
        slope = wp.sqrt(n[0] * n[0] + n[1] * n[1])
        mag = wp.atan2(slope, wp.max(n[2], 1.0e-12))
        direction = float(0.0)
        if slope > 1.0e-7:
            direction = wp.atan2(-n[0], -n[1])
        u = wp.clamp(mag * (119.0 / (0.5 * 3.141592653589793)), 0.0, 124.0)
        v = wp.clamp(
            (direction + 3.141592653589793) * (119.0 / (2.0 * 3.141592653589793)),
            0.0,
            124.0,
        )
        i, j = int(wp.floor(u)), int(wp.floor(v))
        a, b = u - float(i), v - float(j)
        for c in range(3):
            value = float(0.0)
            for x in range(4):
                ix = wp.clamp(i + x - 1, 0, 124)
                wx = cubic_weight(a, x)
                for y in range(4):
                    iy = (j + y - 1 + 119) % 119
                    weight = wx * cubic_weight(b, y)
                    for k in range(6):
                        value += weight * table[ix, iy, c, k] * features[p, k]
            rgb[c] = wp.clamp(rgb[c] + gain * (value - flat[p][c]) / 255.0, 0.0, 1.0)
    output[p] = rgb


class CudaTaximResponse:
    def __init__(self, response, background, device):
        self.device = device
        self.gain = response.gain
        angle = np.radians(getattr(response, "rotation_deg", 0.0))
        self.rotation = (float(np.cos(angle)), float(np.sin(angle)))
        self.table = wp.array(
            np.asarray(response.table, dtype=np.float32), dtype=float, device=device
        )
        self.features = wp.array(
            np.asarray(response.features, dtype=np.float32).reshape(-1, 6),
            dtype=float,
            device=device,
        )
        self.flat = wp.array(
            np.asarray(response.flat, dtype=np.float32).reshape(-1, 3),
            dtype=wp.vec3,
            device=device,
        )
        self.background = wp.array(
            np.asarray(background, dtype=np.float32).reshape(-1, 3),
            dtype=wp.vec3,
            device=device,
        )
        self.output = wp.empty(len(self.flat), dtype=wp.vec3, device=device)

    def shade(self, normals, valid):
        wp.launch(
            response_kernel,
            dim=len(self.output),
            inputs=[
                normals,
                valid,
                self.table,
                self.features,
                self.flat,
                self.background,
                self.gain,
                self.rotation[0],
                self.rotation[1],
                self.output,
            ],
            device=self.device,
        )
        return self.output
