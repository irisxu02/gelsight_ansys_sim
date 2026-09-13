"""CUDA shading and material-marker composition using NVIDIA Warp."""

import numpy as np
import warp as wp


@wp.kernel
def render_kernel(
    normals: wp.array(dtype=wp.vec3),
    valid: wp.array(dtype=wp.int32),
    markers: wp.array(dtype=wp.vec2),
    texture_coordinates: wp.array(dtype=wp.vec2),
    material: int,
    lights: wp.array(dtype=wp.vec3),
    colors: wp.array(dtype=wp.vec3),
    background: wp.array(dtype=wp.vec3),
    width: int,
    nlights: int,
    apply_lighting: int,
    nmarkers: int,
    diffuse: float,
    specular: float,
    shininess: float,
    gamma: float,
    rx: float,
    ry: float,
    opacity: float,
    output: wp.array(dtype=wp.vec3),
):
    i = wp.tid()
    value = background[i]
    if valid[i] != 0:
        normal = normals[i]
        base = wp.vec3(0.0, 0.0, 1.0)
        for j in range(nlights * apply_lighting):
            light = lights[j]
            half = wp.normalize(light + base)
            delta = diffuse * (
                wp.max(wp.dot(normal, light), 0.0) - wp.max(wp.dot(base, light), 0.0)
            )
            delta += specular * (
                wp.pow(wp.max(wp.dot(normal, half), 0.0), shininess)
                - wp.pow(wp.max(wp.dot(base, half), 0.0), shininess)
            )
            value += delta * colors[j]
        attenuation = float(1.0)
        x, y = float(i % width), float(i // width)
        if material != 0:
            x, y = texture_coordinates[i][0], texture_coordinates[i][1]
        for j in range(nmarkers):
            p = markers[j]
            d2 = ((x - p[0]) / rx) * ((x - p[0]) / rx) + ((y - p[1]) / ry) * (
                (y - p[1]) / ry
            )
            if material != 0:
                alpha = wp.clamp((1.0 - wp.sqrt(d2)) * wp.min(rx, ry) + 0.5, 0.0, 1.0)
                attenuation *= wp.pow(1.0 - opacity * alpha, gamma)
            elif d2 < 9.0:
                attenuation *= 1.0 - opacity * wp.exp(-2.0 * d2)
        value *= attenuation
    output[i] = wp.vec3(
        wp.pow(wp.clamp(value[0], 0.0, 1.0), 1.0 / gamma),
        wp.pow(wp.clamp(value[1], 0.0, 1.0), 1.0 / gamma),
        wp.pow(wp.clamp(value[2], 0.0, 1.0), 1.0 / gamma),
    )


class CudaRenderer:
    def __init__(self, camera, optics, background, response=None):
        wp.init()
        if not wp.is_cuda_available():
            raise RuntimeError(
                "CUDA rendering was requested but no Warp CUDA device is available; select optics.backend=cpu explicitly"
            )
        self.device_name = "cuda:0"
        self.camera, self.optics = camera, optics
        lights = np.asarray(optics.light_directions, dtype=np.float32)
        lights /= np.linalg.norm(lights, axis=1, keepdims=True)
        self.lights = wp.array(lights, dtype=wp.vec3, device=self.device_name)
        self.colors = wp.array(
            np.asarray(optics.light_colors, dtype=np.float32),
            dtype=wp.vec3,
            device=self.device_name,
        )
        self.background = wp.array(
            np.asarray(background, dtype=np.float32).reshape(-1, 3),
            dtype=wp.vec3,
            device=self.device_name,
        )
        self.response = None
        if response is not None:
            from .taxim_cuda import CudaTaximResponse

            self.response = CudaTaximResponse(response, background, self.device_name)
        self.output = wp.empty(
            camera.width_px * camera.height_px, dtype=wp.vec3, device=self.device_name
        )

    def render(self, normals, valid, markers, texture_coordinates=None):
        c, o = self.camera, self.optics
        normal_array = wp.array(
            np.asarray(normals, dtype=np.float32).reshape(-1, 3),
            dtype=wp.vec3,
            device=self.device_name,
        )
        valid_array = wp.array(
            np.asarray(valid, dtype=np.int32).ravel(),
            dtype=wp.int32,
            device=self.device_name,
        )
        marker_array = wp.array(
            np.asarray(markers, dtype=np.float32),
            dtype=wp.vec2,
            device=self.device_name,
        )
        coordinates = wp.array(
            np.zeros((1, 2), dtype=np.float32)
            if texture_coordinates is None
            else np.asarray(texture_coordinates, dtype=np.float32).reshape(-1, 2),
            dtype=wp.vec2,
            device=self.device_name,
        )
        shaded = (
            self.response.shade(normal_array, valid_array)
            if self.response
            else self.background
        )
        wp.launch(
            render_kernel,
            dim=c.width_px * c.height_px,
            inputs=[
                normal_array,
                valid_array,
                marker_array,
                coordinates,
                int(o.marker_style == "material"),
                self.lights,
                self.colors,
                shaded,
                c.width_px,
                len(o.light_colors),
                int(self.response is None),
                len(markers),
                o.diffuse,
                o.specular,
                o.shininess,
                1.0 if self.response else o.gamma,
                o.marker_radius_px or o.marker_radius_m * c.width_px / c.fov_width_m,
                o.marker_radius_px or o.marker_radius_m * c.height_px / c.fov_height_m,
                o.marker_opacity,
                self.output,
            ],
            device=self.device_name,
        )
        return self.output.numpy().reshape(c.height_px, c.width_px, 3)
