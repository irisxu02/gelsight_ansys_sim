"""Validated object mesh import, independent of MAPDL and source-file location."""

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HEX_FACES = np.array(
    ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
)
UNITS = {"m": 1.0, "mm": 0.001, "cm": 0.01}


def points(value, name):
    a = np.asarray(value, dtype=float)
    if a.ndim != 2 or a.shape[1] != 3 or len(a) == 0 or not np.isfinite(a).all():
        raise ValueError(f"{name} must be nonempty finite N x 3 coordinates")
    return a


def indices(value, count, name, width=None):
    a = np.asarray(value)
    if a.dtype.kind not in "iu" or a.size == 0 or np.any(a < 0) or np.any(a >= count):
        raise ValueError(f"{name} must contain nonempty zero-based integer node indices")
    if width is not None and (a.ndim != 2 or a.shape[1] != width):
        raise ValueError(f"{name} must have {width} nodes per element")
    return a.astype(np.int64)


def face_list(value, count):
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("A nonempty contact face list is required")
    result = []
    for face in value:
        a = indices(face, count, "Contact faces")
        if a.ndim != 1 or len(a) not in (3, 4) or len(np.unique(a)) != len(a):
            raise ValueError("Contact faces require three or four distinct nodes")
        result.append(tuple(int(i) for i in a))
    if len({tuple(sorted(f)) for f in result}) != len(result):
        raise ValueError("Duplicate contact faces")
    return result


def validate_surface(xyz, faces):
    """Reject degeneracy and inconsistent winding; closed components face outward."""
    edges = {}
    for index, face in enumerate(faces):
        p = xyz[list(face)]
        span = np.linalg.norm(np.ptp(p, axis=0))
        normals = np.cross(np.roll(p, -1, axis=0) - p, np.roll(p, -2, axis=0) - p)
        area = np.linalg.norm(normals, axis=1)
        if (
            span == 0
            or np.any(area <= 1e-12 * span**2)
            or np.any(normals @ normals[0] <= 0)
        ):
            raise ValueError("Degenerate, concave, or folded contact face")
        for a, b in zip(face, (*face[1:], face[0])):
            edges.setdefault(tuple(sorted((a, b))), []).append((index, a < b))
    neighbors = [set() for _ in faces]
    boundary = set()
    for records in edges.values():
        if len(records) > 2:
            raise ValueError("Non-manifold contact surface edge")
        if len(records) == 2:
            (a, direction), (b, other) = records
            if direction == other:
                raise ValueError("Inconsistent contact face winding")
            neighbors[a].add(b)
            neighbors[b].add(a)
        else:
            boundary.add(records[0][0])
    remaining = set(range(len(faces)))
    while remaining:
        component, pending = set(), [next(iter(remaining))]
        while pending:
            i = pending.pop()
            if i in component:
                continue
            component.add(i)
            pending.extend(neighbors[i] - component)
        remaining -= component
        if not component & boundary:
            origin = xyz[faces[next(iter(component))][0]]
            volume = 0.0
            for i in component:
                q = xyz[list(faces[i])] - origin
                for j in range(1, len(q) - 1):
                    volume += np.dot(q[0], np.cross(q[j], q[j + 1])) / 6
            if volume <= 0:
                raise ValueError("Closed contact surfaces must have outward face winding")


def validate_hexes(xyz, cells, faces, grip):
    cells = indices(cells, len(xyz), "Hex mesh", 8)
    if np.any(np.diff(np.sort(cells, axis=1), axis=1) == 0):
        raise ValueError("Hex elements must have eight distinct nodes")
    if len(np.unique(np.sort(cells, axis=1), axis=0)) != len(cells):
        raise ValueError("Duplicate hex elements")
    if len(np.unique(cells)) != len(xyz):
        raise ValueError("Volume mesh contains unused nodes")
    signs = np.array(
        (
            (-1, -1, -1),
            (1, -1, -1),
            (1, 1, -1),
            (-1, 1, -1),
            (-1, -1, 1),
            (1, -1, 1),
            (1, 1, 1),
            (-1, 1, 1),
        ),
        dtype=float,
    )
    coordinates = xyz[cells]
    scale = np.linalg.norm(np.ptp(coordinates, axis=1), axis=1) ** 3
    # Check natural-space corners, Gauss points and center, including warped hexes.
    for sample in np.vstack((signs, signs / np.sqrt(3), np.zeros((1, 3)))):
        derivatives = np.empty((8, 3))
        for axis in range(3):
            others = [i for i in range(3) if i != axis]
            derivatives[:, axis] = (
                signs[:, axis]
                * np.prod(1 + signs[:, others] * sample[others], axis=1)
                / 8
            )
        determinant = np.linalg.det(np.einsum("eij,ik->ejk", coordinates, derivatives))
        if np.any(determinant <= 1e-12 * scale) or not np.isfinite(determinant).all():
            raise ValueError("Inverted, degenerate, or excessively distorted hex element")
    occurrences = {}
    for element, cell in enumerate(cells):
        for face in cell[HEX_FACES]:
            occurrences.setdefault(tuple(sorted(face)), []).append((element, tuple(face)))
    neighbors = [set() for _ in cells]
    boundary = {}
    for key, records in occurrences.items():
        if len(records) == 1:
            boundary[key] = records[0][1]
        elif len(records) == 2:
            (a, fa), (b, fb) = records
            reversed_fa = tuple(reversed(fa))
            if not any(fb == reversed_fa[i:] + reversed_fa[:i] for i in range(4)):
                raise ValueError("Hexes overlap or have inconsistent shared faces")
            neighbors[a].add(b)
            neighbors[b].add(a)
        else:
            raise ValueError("Non-manifold volume face")
    seen, pending = set(), [0]
    while pending:
        i = pending.pop()
        if i not in seen:
            seen.add(i)
            pending.extend(neighbors[i] - seen)
    if len(seen) != len(cells):
        raise ValueError("Deformable volume must be connected through shared faces")
    for face in faces:
        original = boundary.get(tuple(sorted(face)))
        if original is None or not any(
            face == original[i:] + original[:i] for i in range(4)
        ):
            raise ValueError("Contact quads must be outward-oriented exterior hex faces")
    grip = indices(grip, len(xyz), "Grip node set")
    if grip.ndim != 1 or len(np.unique(grip)) != len(grip):
        raise ValueError("Grip node set must be a vector of distinct indices")
    exterior = {i for face in boundary.values() for i in face}
    if (
        not set(grip) <= exterior
        or np.linalg.matrix_rank(xyz[grip] - xyz[grip].mean(axis=0)) < 2
    ):
        raise ValueError("Grip requires at least three non-collinear exterior nodes")


@dataclass(frozen=True)
class ImportedMesh:
    """Embedded SI geometry: saved runs never reopen the original mesh file."""

    coordinates_m: tuple
    faces: tuple
    hexes: tuple
    grip_nodes: tuple
    reference_point_m: tuple
    source_name: str
    source_sha256: str
    source_units: str
    transform: dict
    contact_face_set: str | None = None
    grip_node_set: str | None = None

    def validate(self, deformable, clearance):
        xyz = points(self.coordinates_m, "Imported mesh")
        faces = face_list(self.faces, len(xyz))
        validate_surface(xyz, faces)
        if (
            np.asarray(self.reference_point_m).shape != (3,)
            or not np.isfinite(self.reference_point_m).all()
        ):
            raise ValueError("Mesh reference_point_m must be a finite 3-vector")
        if not np.isclose(xyz[:, 2].min(), clearance, rtol=0, atol=1e-9):
            raise ValueError(
                "Transformed mesh minimum z must equal clearance_m (gel top is z=0)"
            )
        if deformable:
            validate_hexes(xyz, self.hexes, faces, self.grip_nodes)
        elif self.hexes or self.grip_nodes:
            raise ValueError(
                "Rigid imports require a surface mesh without volume or grip nodes"
            )
        if (
            self.source_units not in UNITS
            or len(self.source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.source_sha256)
        ):
            raise ValueError("Invalid imported mesh provenance")
        return self


def read_stl(raw):
    # A binary header can start with 'solid'; the record length disambiguates it.
    if len(raw) >= 84 and len(raw) == 84 + 50 * struct.unpack_from("<I", raw, 80)[0]:
        dtype = np.dtype(
            [("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")]
        )
        triangles = np.frombuffer(raw, dtype=dtype, offset=84)["vertices"].astype(float)
    else:
        lines = raw.decode("ascii").splitlines()
        if not lines or not lines[0].strip().lower().startswith("solid"):
            raise ValueError("Invalid ASCII or binary STL")
        triangles, face = [], None
        for line in lines[1:]:
            tokens = line.lower().split()
            if not tokens:
                continue
            if tokens[0] == "facet":
                if face is not None:
                    raise ValueError("Invalid STL facet nesting")
                face = []
            elif tokens[0] == "vertex":
                if face is None or len(tokens) != 4:
                    raise ValueError("Invalid STL vertex")
                face.append([float(v) for v in tokens[1:]])
            elif tokens[0] == "endfacet":
                if face is None or len(face) != 3:
                    raise ValueError("STL facets must have three vertices")
                triangles.append(face)
                face = None
        if face is not None or not triangles:
            raise ValueError("Incomplete or empty STL")
        triangles = np.asarray(triangles)
    vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
    return {"vertices": vertices.tolist(), "faces": inverse.reshape(-1, 3).tolist()}


def read_obj(raw):
    vertices, faces = [], []
    for line in raw.decode("utf-8-sig").splitlines():
        tokens = line.partition("#")[0].split()
        if not tokens:
            continue
        if tokens[0] == "v":
            if len(tokens) != 4:
                raise ValueError("OBJ vertices require exactly three coordinates")
            vertices.append([float(v) for v in tokens[1:]])
        elif tokens[0] == "f":
            if len(tokens) not in (4, 5):
                raise ValueError(
                    "OBJ faces must be triangles or quads; triangulate larger polygons"
                )
            face = [int(t.split("/")[0]) for t in tokens[1:]]
            if any(i == 0 or abs(i) > len(vertices) for i in face):
                raise ValueError("OBJ face index is outside the vertex list")
            faces.append([i - 1 if i > 0 else len(vertices) + i for i in face])
        elif tokens[0] not in ("vn", "vt", "o", "g", "s", "usemtl", "mtllib"):
            raise ValueError(f"Unsupported OBJ record: {tokens[0]}")
    return {"vertices": vertices, "faces": faces}


def import_mesh(geometry, base_directory, deformable):
    """Resolve source geometry once; paths never enter portable run metadata."""
    required = {"shape", "file", "units", "transform", "reference_point_m", "clearance_m"}
    allowed = required | {"contact_face_set", "grip_node_set"}
    if not required <= geometry.keys() or geometry.keys() - allowed:
        raise ValueError(
            "Mesh geometry requires file, units, transform, reference_point_m, and clearance_m"
        )
    if geometry["units"] not in UNITS:
        raise ValueError("Mesh units must be m, mm, or cm")
    path = Path(geometry["file"])
    if not path.is_absolute():
        if base_directory is None:
            raise ValueError("Mesh file references require Config.load(path)")
        path = Path(base_directory) / path
    raw = path.read_bytes()
    if path.suffix.lower() == ".stl":
        data = read_stl(raw)
    elif path.suffix.lower() == ".obj":
        data = read_obj(raw)
    elif path.suffix.lower() == ".json":
        data = json.loads(raw)
        if data.get("config_kind") != "object_mesh" or data.get("schema_version") != 1:
            raise ValueError("JSON meshes require object_mesh schema 1")
        if data.keys() - {
            "config_kind",
            "schema_version",
            "vertices",
            "faces",
            "hexes",
            "face_sets",
            "node_sets",
        }:
            raise ValueError("Unknown JSON mesh field")
    else:
        raise ValueError("Supported mesh files are STL, OBJ, and object_mesh JSON")
    xyz = points(data["vertices"], "Source mesh vertices") * UNITS[geometry["units"]]
    transform = geometry["transform"]
    if set(transform) != {"translation_m", "rotation_xyzw"}:
        raise ValueError("Mesh transform requires translation_m and rotation_xyzw")
    shift, q = (
        np.asarray(transform["translation_m"], dtype=float),
        np.asarray(transform["rotation_xyzw"], dtype=float),
    )
    if (
        shift.shape != (3,)
        or q.shape != (4,)
        or not np.isfinite(shift).all()
        or not np.isfinite(q).all()
        or not np.isclose(np.linalg.norm(q), 1, rtol=0, atol=1e-8)
    ):
        raise ValueError(
            "Transform requires a finite translation and a unit xyzw quaternion"
        )
    x, y, z, w = q / np.linalg.norm(q)
    rotation = np.array(
        (
            (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
        )
    )
    xyz = xyz @ rotation.T + shift
    contact_name, grip_name = (
        geometry.get("contact_face_set"),
        geometry.get("grip_node_set"),
    )
    if any(
        name is not None and (not isinstance(name, str) or not name)
        for name in (contact_name, grip_name)
    ):
        raise ValueError("Mesh boundary set selections must be nonempty names")
    faces = (
        data.get("faces", [])
        if contact_name is None
        else data.get("face_sets", {}).get(contact_name, [])
    )
    faces = face_list(faces, len(xyz))
    cells = data.get("hexes", [])
    grip = data.get("node_sets", {}).get(grip_name, [])
    if deformable and (not contact_name or not grip_name):
        raise ValueError("Deformable imports require contact_face_set and grip_node_set")
    if not deformable and (cells or grip_name is not None):
        raise ValueError(
            "Rigid imports require a surface mesh without hexes or grip selection"
        )
    if not deformable:
        # OBJ groups or selected JSON surfaces can leave unused vertices.
        used = np.unique([i for face in faces for i in face])
        remap = {int(n): i for i, n in enumerate(used)}
        xyz = xyz[used]
        faces = [tuple(remap[i] for i in face) for face in faces]
    mesh = ImportedMesh(
        tuple(map(tuple, xyz.tolist())),
        tuple(faces),
        tuple(map(tuple, cells)),
        tuple(grip),
        tuple(geometry["reference_point_m"]),
        path.name,
        hashlib.sha256(raw).hexdigest(),
        geometry["units"],
        transform,
        contact_name,
        grip_name,
    )
    return mesh.validate(deformable, geometry["clearance_m"])
