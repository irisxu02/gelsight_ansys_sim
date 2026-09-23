"""Imported rigid surfaces and translating hex volumes using the common gel solve."""

import numpy as np

from .mechanics import AnsysGel
from .mesh import ObjectMesh


class AnsysImported(AnsysGel):
    """Retain the gel contact, continuation, and extraction contracts."""

    def reference_point(self):
        return np.asarray(self.config.imported_mesh.reference_point_m, dtype=float)

    def deformable_mesh(self):
        data = self.config.imported_mesh
        return ObjectMesh(
            np.asarray(data.coordinates_m, dtype=float),
            np.asarray(data.hexes, dtype=np.int64),
            np.asarray(data.faces, dtype=np.int64),
            np.asarray(data.grip_nodes, dtype=np.int64),
        )

    def rigid_target_commands(self, first_element):
        data = self.config.imported_mesh
        x, y, z = self.initial_pilot
        commands = [f"N,{self.pilot},{x:.16g},{y:.16g},{z:.16g}", "TYPE,3"]
        commands += [
            f"N,{self.pilot + 1 + i},{x:.16g},{y:.16g},{z:.16g}"
            for i, (x, y, z) in enumerate(data.coordinates_m)
        ]
        for i, face in enumerate(data.faces):
            commands += [
                "TSHAP,TRIA" if len(face) == 3 else "TSHAP,QUAD",
                f"EN,{first_element + i},"
                + ",".join(str(self.pilot + 1 + n) for n in face),
            ]
        commands += [
            "TSHAP,PILO",
            f"EN,{first_element + len(data.faces)},{self.pilot}",
        ]
        return commands + self.pilot_constraint_commands()

    def additional_geometry(self):
        data = self.config.imported_mesh
        result = {"object_reference_point_m": self.initial_pilot}
        if not self.config.indenter.deformable:
            result.update(
                target_reference_m=np.asarray(data.coordinates_m, dtype=float),
                target_triangles=np.asarray(
                    [f for f in data.faces if len(f) == 3], dtype=np.int64
                ).reshape(-1, 3),
                target_quads=np.asarray(
                    [f for f in data.faces if len(f) == 4], dtype=np.int64
                ).reshape(-1, 4),
            )
        return result
