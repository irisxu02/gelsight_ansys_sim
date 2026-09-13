"""Deterministic hexahedral gel mesh with a material surface topology."""

from dataclasses import dataclass

import numpy as np


@dataclass
class Mesh:
    coordinates: np.ndarray
    hexes: np.ndarray
    surface_nodes: np.ndarray
    surface_quads: np.ndarray
    triangles: np.ndarray
    bottom_nodes: np.ndarray
    material_ids: np.ndarray


def biased_axis(half_extent, elements, bias):
    """Smooth symmetric grading; positive bias refines the central contact zone."""
    q = np.linspace(-1.0, 1.0, elements + 1)
    return half_extent * (np.sinh(bias * q) / np.sinh(bias) if bias else q)


def contact_axis(half_extent, elements, core_half_extent, size):
    """Uniform contact-zone cells joined to geometric outer transitions."""
    inner = int(np.ceil(core_half_extent / size - 1e-12))
    outer = (elements - 2 * inner) // 2
    if elements % 2 or outer < 1 or not 0 < core_half_extent < half_extent:
        raise ValueError("Invalid contact-zone mesh sizing")
    step = core_half_extent / inner
    span = half_extent - core_half_extent
    powers = np.arange(1, outer + 1)
    lo, hi = 0.0, 2.0
    while np.sum(step * hi**powers) < span:
        hi *= 2
    for _ in range(80):
        ratio = (lo + hi) / 2
        if np.sum(step * ratio**powers) < span:
            lo = ratio
        else:
            hi = ratio
    positive = np.r_[
        np.linspace(0, core_half_extent, inner + 1),
        core_half_extent + np.cumsum(step * ((lo + hi) / 2) ** powers),
    ]
    positive[-1] = half_extent
    return np.r_[-positive[:0:-1], positive]


def structured_mesh(gel):
    nx, ny, nz = gel.elements
    if gel.contact_element_size_m is None:
        x = biased_axis(gel.width_m / 2, nx, gel.in_plane_bias)
        y = biased_axis(gel.length_m / 2, ny, gel.in_plane_bias)
    else:
        x = contact_axis(
            gel.width_m / 2,
            nx,
            gel.refinement_half_extents_m[0],
            gel.contact_element_size_m,
        )
        y = contact_axis(
            gel.length_m / 2,
            ny,
            gel.refinement_half_extents_m[1],
            gel.contact_element_size_m,
        )
    q = np.linspace(1.0, 0.0, nz + 1)
    bias = gel.through_thickness_bias
    q = np.expm1(bias * q) / np.expm1(bias) if bias else q
    z = -gel.thickness_m * q
    material_ids = np.ones(nx * ny * nz, dtype=int)
    zz, yy, xx = np.meshgrid(z, y, x, indexing="ij")
    coordinates = np.column_stack((xx.ravel(), yy.ravel(), zz.ravel()))
    node = np.arange(len(coordinates)).reshape(nz + 1, ny + 1, nx + 1)
    hexes = np.stack(
        (
            node[:-1, :-1, :-1],
            node[:-1, :-1, 1:],
            node[:-1, 1:, 1:],
            node[:-1, 1:, :-1],
            node[1:, :-1, :-1],
            node[1:, :-1, 1:],
            node[1:, 1:, 1:],
            node[1:, 1:, :-1],
        ),
        axis=-1,
    ).reshape(-1, 8)
    top = np.arange((nx + 1) * (ny + 1)).reshape(ny + 1, nx + 1)
    quads = np.stack(
        (top[:-1, :-1], top[:-1, 1:], top[1:, 1:], top[1:, :-1]), axis=-1
    ).reshape(-1, 4)
    triangles = np.concatenate((quads[:, [0, 1, 2]], quads[:, [0, 2, 3]]))
    surface_nodes = node[-1].ravel()
    return Mesh(
        coordinates,
        hexes,
        surface_nodes,
        quads,
        triangles,
        node[0].ravel(),
        material_ids,
    )


def nodal_areas_normals(vertices, triangles):
    cross = np.cross(
        vertices[triangles[:, 1]] - vertices[triangles[:, 0]],
        vertices[triangles[:, 2]] - vertices[triangles[:, 0]],
    )
    area = np.linalg.norm(cross, axis=1) / 2
    normals = np.zeros_like(vertices)
    areas = np.zeros(len(vertices))
    for corner in range(3):
        np.add.at(normals, triangles[:, corner], cross)
        np.add.at(areas, triangles[:, corner], area / 3)
    norm = np.linalg.norm(normals, axis=1)
    if np.any(norm <= 0) or np.any(areas <= 0):
        raise ValueError("Degenerate surface triangles")
    return areas, normals / norm[:, None]


@dataclass
class ObjectMesh:
    coordinates: np.ndarray
    hexes: np.ndarray
    surface_quads: np.ndarray
    grip_nodes: np.ndarray


def sphere_mesh(indenter, center):
    """Conforming hex sphere obtained by smoothly mapping a Cartesian cube.

    All boundary nodes lie on the sphere; quad targets retain mesh faceting.
    Node/element indices are local and zero based, as for the gel mesh.
    """
    from .config import Gel

    n = indenter.sphere_elements_per_axis
    cube = structured_mesh(
        Gel(width_m=2, length_m=2, thickness_m=2, elements=(n, n, n))
    )
    q = cube.coordinates.copy()
    q[:, 2] += 1
    xyz = np.empty_like(q)
    for axis in range(3):
        other = [i for i in range(3) if i != axis]
        a, b = q[:, other[0]] ** 2, q[:, other[1]] ** 2
        xyz[:, axis] = q[:, axis] * np.sqrt(1 - a / 2 - b / 2 + a * b / 3)
    xyz *= indenter.radius_m
    # Boundary faces oriented outwards, obtained from each hex's six local faces.
    faces = cube.hexes[
        :,
        np.array(
            (
                (0, 3, 2, 1),
                (4, 5, 6, 7),
                (0, 1, 5, 4),
                (1, 2, 6, 5),
                (2, 3, 7, 6),
                (3, 0, 4, 7),
            )
        ),
    ].reshape(-1, 4)
    _, inverse, counts = np.unique(
        np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    boundary = faces[counts[inverse] == 1]
    boundary_nodes = np.unique(boundary)
    grip = boundary_nodes[
        xyz[boundary_nodes, 2] >= indenter.grip_height_fraction * indenter.radius_m
    ]
    return ObjectMesh(xyz + np.asarray(center), cube.hexes, boundary, grip)
