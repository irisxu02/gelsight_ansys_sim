"""Finite-element views of the solved bodies, drawn while the run solves.

Every quantity here is already saved: solid_mesh.npz holds the volume grid and
the rigid target, bodies/frame_*.npz holds the nodal solution, and the surface
state holds the contact pressure. This module is the picture of them - the
deformed mesh with its element edges, contoured by contact pressure and by total
displacement, the way a solver's own post-processor shows a result - so nothing
it draws is a new claim about the solve, and switching it off costs only the
pictures.

Color limits grow with the run rather than being fixed in advance, because a run
does not know its peak pressure until it has reached it. They grow in 1-2-5
steps and each frame's limits are recorded next to it, so a scale change is
legible rather than silent.
"""

import itertools

import numpy as np

from .mesh import exterior_faces

# The two views, each with the field it contours and the scale it grows.
PANELS = (
    ("Contact pressure (kPa)", "inferno", "contact_pressure_kpa"),
    ("Total displacement USUM (mm)", "viridis", "displacement_mm"),
)

TARGET_LINE_COLOR = "#8b5cf6"
# Faces of the body that carry no contact quantity are drawn as the body, not as
# a contour of it.
BODY_COLOR = (0.78, 0.80, 0.84, 1.0)
# How far above first touch the rigid target is still drawn. A cylinder's
# surface climbs millimetres within a few millimetres of its lowest line, and
# drawing all of it would scale the view to a body that is mostly nowhere near
# the sensor.
TARGET_HEIGHT_CAP_M = 0.003


def nice_ceiling(value):
    """The next 1, 2 or 5 times a power of ten at or above a positive value."""
    if not np.isfinite(value) or value <= 0:
        return 1.0
    exponent = np.floor(np.log10(value))
    for step in (1.0, 2.0, 5.0, 10.0):
        limit = step * 10.0**exponent
        if value <= limit * (1 + 1e-12):
            return float(limit)
    raise AssertionError("a positive value is at most ten times its own decade")


def grid_shape(points):
    """Rows and columns of a target patch, which is a structured grid raveled.

    Every rigid target here is built by gridding y against x and raveling rows
    of constant y, so the number of distinct x coordinates is the row length.
    """
    columns = np.unique(points[:, 0]).size
    rows, remainder = divmod(len(points), columns)
    return (rows, columns) if not remainder else None


def grid_outline(points, cap, bounds, lines=14):
    """A coarse wireframe of a structured target patch, clipped to the view.

    The target carries thousands of facets and none of them are the subject of
    the picture; what the picture needs from it is where the body is and which
    way it curves. Every few grid lines says that and leaves the gel visible
    underneath. The clip keeps what is over the sensor and below the height cap:
    a cylinder reaches centimetres past the gel and climbs steeply away from it,
    and drawing all of that would scale the view to a body mostly out of contact.
    """
    shape = grid_shape(points)
    if shape is None:
        return []
    rows, columns = shape
    grid = points.reshape(rows, columns, 3)
    lines_of = [
        grid[i] for i in np.unique(np.linspace(0, rows - 1, min(lines, rows)).astype(int))
    ] + [
        grid[:, i]
        for i in np.unique(np.linspace(0, columns - 1, min(lines, columns)).astype(int))
    ]
    (xlo, ylo), (xhi, yhi) = bounds
    segments = []
    for polyline in lines_of:
        keep = (
            (polyline[:, 2] <= cap)
            & (polyline[:, 0] >= xlo)
            & (polyline[:, 0] <= xhi)
            & (polyline[:, 1] >= ylo)
            & (polyline[:, 1] <= yhi)
        )
        inside = np.flatnonzero(keep)
        for run in np.split(inside, np.flatnonzero(np.diff(inside) > 1) + 1):
            if len(run) > 1:
                segments.append(polyline[run])
    return segments


# The twelve edges of a box, as index pairs into the eight corners that
# itertools.product spells out in binary order.
BOX_EDGES = np.array(
    [(a, b) for a in range(8) for b in range(a + 1, 8) if bin(a ^ b).count("1") == 1]
)


def box_edges(points):
    """The twelve edges of a point cloud's bounding box, as line segments."""
    corners = np.array(list(itertools.product(*zip(points.min(axis=0), points.max(axis=0)))))
    return list(corners[BOX_EDGES])


class MeshView:
    """A two-panel finite-element view, reused across the frames of one run."""

    def __init__(self, directory, config, geometry, *, elevation=26.0, azimuth=-62.0):
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
        from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

        self.directory = directory
        self.config = config
        self.scale = config.visualization.mesh_deformation_scale
        self.reference = np.asarray(geometry["gel_reference_m"], dtype=float)
        self.faces = exterior_faces(np.asarray(geometry["gel_hexes"], dtype=np.int64))
        # The contact face carries its own contoured quantity and is drawn from
        # the surface state; the rest of the boundary is drawn as the body. Which
        # nodes those are is a fact the mesh states, not one to rediscover from
        # coordinates - a pad whose face is not the flat top would fool that.
        on_face = np.zeros(len(self.reference), dtype=bool)
        on_face[np.asarray(geometry["surface_nodes"], dtype=np.int64)] = True
        self.body_faces = self.faces[~on_face[self.faces].all(axis=1)]
        self.target = geometry.get("target")
        self.bounds = (
            self.reference[:, :2].min(axis=0),
            self.reference[:, :2].max(axis=0),
        )
        self.limits = dict.fromkeys((key for _, _, key in PANELS), 0.0)
        span = np.ptp(self.reference, axis=0)
        self.figure, axes = plt.subplots(
            1,
            2,
            figsize=(13, 5.6),
            subplot_kw={"projection": "3d"},
            layout="constrained",
        )
        self.surfaces = []
        self.outlines = []
        self.bars = []
        for axis, (label, cmap, _) in zip(axes, PANELS):
            # Draw in a fixed order rather than by computed depth. Matplotlib
            # orders whole artists by one depth value each, and the gel spans
            # the view while the object sits over a part of it, so the object
            # kept being sorted behind the body it stands on. It is a wireframe
            # over a solid; saying so outright is both correct and legible.
            axis.computed_zorder = False
            collection = Poly3DCollection(
                np.zeros((1, 4, 3)), linewidths=0.15, edgecolors="#1f2937", zorder=1
            )
            axis.add_collection3d(collection)
            self.surfaces.append(collection)
            # A collection has to hold something to be added to a 3D axis; the
            # first frame replaces this with the target where the pose puts it.
            lines = Line3DCollection(
                [np.zeros((2, 3))],
                linewidths=0.7,
                colors=TARGET_LINE_COLOR,
                alpha=0.75,
                zorder=3,
            )
            axis.add_collection3d(lines)
            self.outlines.append(lines)
            # The undeformed body, so the deformation is read against something.
            axis.add_collection3d(
                Line3DCollection(
                    [edge * 1000 for edge in box_edges(self.reference)],
                    linewidths=0.6,
                    colors="#9aa3ad",
                    linestyles="dotted",
                    zorder=2,
                )
            )
            axis.set_xlim(-span[0] / 2 * 1000, span[0] / 2 * 1000)
            axis.set_ylim(-span[1] / 2 * 1000, span[1] / 2 * 1000)
            axis.set_zlim(-span[2] * 1000, TARGET_HEIGHT_CAP_M * 1000)
            axis.set_box_aspect((span[0], span[1], span[2] + TARGET_HEIGHT_CAP_M))
            axis.view_init(elev=elevation, azim=azimuth)
            axis.set_xlabel("x (mm)")
            axis.set_ylabel("y (mm)")
            axis.set_zlabel("z (mm)")
            axis.set_title(label)
            bar = self.figure.colorbar(
                ScalarMappable(norm=Normalize(0, 1), cmap=cmap),
                ax=axis,
                shrink=0.72,
                pad=0.13,
            )
            bar.set_label(label)
            self.bars.append(bar)
        self.title = self.figure.suptitle("")

    def close(self):
        import matplotlib.pyplot as plt

        plt.close(self.figure)

    def rescaled(self, key, value):
        """Raise a contour scale to cover a new peak, in 1-2-5 steps.

        A run opens on an unloaded reference frame, where the peak is zero and
        there is no scale to read off it. That frame is drawn against a unit
        scale, which shows it as the empty field it is.
        """
        if value > self.limits[key]:
            self.limits[key] = nice_ceiling(value)
        return self.limits[key] or 1.0

    def colors(self, cmap, values, limit):
        import matplotlib

        return matplotlib.colormaps[cmap](np.clip(values / limit, 0, 1))

    def target_segments(self, metric):
        """The rigid target where this frame's recorded pose has moved it."""
        if self.target is None:
            return []
        offset = np.array(
            [
                metric["x_m"],
                metric["y_m"],
                -metric["depth_m"] - self.config.indenter.clearance_m,
            ]
        )
        return [
            segment * 1000
            for segment in grid_outline(
                self.target + offset, TARGET_HEIGHT_CAP_M, self.bounds
            )
        ]

    def render(self, index, state, metric, volume_displacement):
        """Write one frame's finite-element view and return its contour limits."""
        from PIL import Image

        surface = (state.reference_m + self.scale * state.displacement_m) * 1000
        pressure = np.asarray(state.contact_pressure_pa, dtype=float) / 1000
        displacement = np.asarray(volume_displacement, dtype=float)
        body = (self.reference + self.scale * displacement) * 1000
        magnitude = np.linalg.norm(displacement, axis=1) * 1000
        limits = [
            self.rescaled(PANELS[0][2], float(np.max(pressure, initial=0.0))),
            self.rescaled(PANELS[1][2], float(magnitude.max(initial=0.0))),
        ]
        self.surfaces[0].set_verts(
            np.concatenate((body[self.body_faces], surface[state.quads]))
        )
        self.surfaces[0].set_facecolor(
            np.concatenate(
                (
                    np.tile(BODY_COLOR, (len(self.body_faces), 1)),
                    self.colors(PANELS[0][1], np.maximum(pressure, 0.0), limits[0]),
                )
            )
        )
        self.surfaces[1].set_verts(body[self.faces])
        self.surfaces[1].set_facecolor(
            self.colors(PANELS[1][1], magnitude[self.faces].mean(axis=1), limits[1])
        )

        segments = self.target_segments(metric)
        for lines in self.outlines:
            lines.set_segments(segments)
        for bar, limit in zip(self.bars, limits):
            # A scale is raised a handful of times in a run; redrawing a colorbar
            # that did not move is the one avoidable cost in this loop.
            if bar.mappable.norm.vmax != limit:
                bar.mappable.norm.vmax = limit
                bar.update_normal(bar.mappable)
        self.title.set_text(
            f"{self.config.name} · frame {index} · t = {metric['time_s']:.3f} s · "
            f"normal force {metric['normal_force_n']:.3f} N · "
            f"travel {metric['depth_m'] * 1000:.3f} mm · "
            f"deformation shown at {self.scale:g}x"
        )
        self.figure.canvas.draw()
        folder = self.directory / "mesh"
        folder.mkdir(exist_ok=True)
        Image.fromarray(
            np.asarray(self.figure.canvas.buffer_rgba())[:, :, :3].copy()
        ).save(folder / f"frame_{index:04d}.png")
        return dict(zip((key for _, _, key in PANELS), limits))


def mesh_view(directory, config, model):
    """A view of the bodies a mechanical adapter built, or None if not requested.

    The rigid target is read as the structured patch it is, so the picture can
    draw a few of its grid lines instead of every facet. A deformable object is
    left to the gel panels for now: it moves with the grip and its own contour
    would need its own scale.
    """
    if not config.visualization.save_mesh_frames:
        return None
    geometry = {
        "gel_reference_m": model.mesh.coordinates,
        "gel_hexes": model.mesh.hexes,
        "surface_nodes": model.mesh.surface_nodes,
    }
    points = getattr(model, "target_reference", None)
    if points is not None:
        geometry["target"] = np.asarray(points, dtype=float)
    return MeshView(directory, config, geometry)
