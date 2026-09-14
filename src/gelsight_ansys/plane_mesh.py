"""Hexahedral slab/gel grids with a bounded through-thickness growth ratio."""

import numpy as np

from .mesh import ObjectMesh, structured_mesh, tensor_mesh


def depth_axis(thickness, size, refined_depth, growth=1.3):
    """Distances into a body from its contact face, with a uniform fine skin."""
    if not 0 < refined_depth <= thickness or size <= 0 or growth <= 1:
        raise ValueError("Invalid contact-layer mesh parameters")
    n = int(np.ceil(refined_depth / size - 1e-12))
    fine = np.linspace(0, refined_depth, n + 1)
    step = refined_depth / n
    remaining = thickness - refined_depth
    if remaining < 1e-14:
        return fine
    outer = 1
    while step * np.sum(growth ** np.arange(1, outer + 1)) < remaining:
        outer += 1
    # If only a short tail remains, split it without exceeding the growth bound.
    if remaining < outer * step:
        return np.linspace(0, thickness, int(np.ceil(thickness / step)) + 1)
    lo, hi = 1.0, growth
    powers = np.arange(1, outer + 1)
    for _ in range(70):
        ratio = (lo + hi) / 2
        if step * np.sum(ratio**powers) < remaining:
            lo = ratio
        else:
            hi = ratio
    result = np.r_[fine, refined_depth + np.cumsum(step * ((lo + hi) / 2) ** powers)]
    result[-1] = thickness
    return result


def gel_mesh(case, element_size=None):
    data, rules = case.suite["sensor"]["gel"], case.suite["discretization"]
    if element_size is None and rules.get("gel_mesh", "uniform") == "uniform":
        from .config import Gel

        return structured_mesh(Gel(**data))
    size = element_size or rules["common_contact_surface_max_edge_m"]
    x = np.linspace(
        -data["width_m"] / 2,
        data["width_m"] / 2,
        int(np.ceil(data["width_m"] / size)) + 1,
    )
    y = np.linspace(
        -data["length_m"] / 2,
        data["length_m"] / 2,
        int(np.ceil(data["length_m"] / size)) + 1,
    )
    z = -depth_axis(
        data["thickness_m"],
        size,
        rules["refined_depth_into_each_deformable_body_m"],
        rules["maximum_element_growth_ratio"],
    )[::-1]
    return tensor_mesh(x, y, z)


def slab_mesh(
    case, clearance, element_size=None, *, object_mode="simplified", object_size=None
):
    specimen, rules = case.suite["specimen"], case.suite["discretization"]
    if object_mode == "matched":
        size = element_size or rules["common_contact_surface_max_edge_m"]
    else:
        size = object_size or max(
            element_size or 0,
            rules.get("object_mesh", {}).get("deformable_max_edge_m", 0.0005),
        )
    x = np.linspace(
        -specimen["width_m"] / 2,
        specimen["width_m"] / 2,
        int(np.ceil(specimen["width_m"] / size)) + 1,
    )
    y = np.linspace(
        -specimen["length_m"] / 2,
        specimen["length_m"] / 2,
        int(np.ceil(specimen["length_m"] / size)) + 1,
    )
    z = clearance + depth_axis(
        specimen["thickness_m"],
        size,
        rules["refined_depth_into_each_deformable_body_m"],
        rules["maximum_element_growth_ratio"],
    )
    mesh = tensor_mesh(x, y, z)
    # A target's bottom face normal points towards the gel (-z).
    bottom_quads = mesh.bottom_nodes[mesh.surface_quads[:, [0, 3, 2, 1]]]
    return ObjectMesh(mesh.coordinates, mesh.hexes, bottom_quads, mesh.surface_nodes)


def texture_edge(case, object_mode="simplified"):
    """Edge length of the rigid target that carries a surface height field.

    A textured target is geometry, not a body: it adds no degrees of freedom,
    so its cost is contact search and storage rather than equations. It still
    has to resolve the shape it represents, which is what the case's
    minimum_elements_per_shortest_wavelength asks for, and it should not be
    coarser than the sensing surface it is searched against. The matched mode
    keeps the contact-matched grid it exists to compare with.
    """
    rules = case.suite["discretization"]
    if object_mode == "matched":
        return rules["common_contact_surface_max_edge_m"]
    return rules.get("object_mesh", {}).get(
        "rigid_texture_max_edge_m", rules["common_contact_surface_max_edge_m"]
    )


def textured_target(case, clearance, element_size=None, *, object_mode="simplified"):
    specimen = case.suite["specimen"]
    if object_mode == "simplified" and not case.case["surface_geometry"].get("modes"):
        hx, hy = specimen["width_m"] / 2, specimen["length_m"] / 2
        # One exact plane facet removes artificial internal target boundaries.
        points = np.array(
            [
                [-hx, -hy, clearance],
                [-hx, hy, clearance],
                [hx, hy, clearance],
                [hx, -hy, clearance],
            ]
        )
        return points, np.array([[0, 1, 2, 3]])
    size = element_size or texture_edge(case, object_mode)
    x = np.linspace(
        -specimen["width_m"] / 2,
        specimen["width_m"] / 2,
        int(np.ceil(specimen["width_m"] / size)) + 1,
    )
    y = np.linspace(
        -specimen["length_m"] / 2,
        specimen["length_m"] / 2,
        int(np.ceil(specimen["length_m"] / size)) + 1,
    )
    yy, xx = np.meshgrid(y, x, indexing="ij")
    height = case.surface_height(xx, yy)
    # Positive texture height protrudes towards the sensor.
    peak = sum(
        mode["amplitude_m"] for mode in case.case["surface_geometry"].get("modes", [])
    )
    points = np.column_stack(
        (xx.ravel(), yy.ravel(), (clearance + peak - height).ravel())
    )
    node = np.arange(len(points)).reshape(len(y), len(x))
    faces = np.stack(
        (node[:-1, :-1], node[1:, :-1], node[1:, 1:], node[:-1, 1:]), axis=-1
    ).reshape(-1, 4)
    return points, faces
