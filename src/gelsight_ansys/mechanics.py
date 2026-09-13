"""Sphere and flat-target finite-strain mechanics and result extraction."""

import time

import numpy as np

from .ansys.materials import material_commands
from .ansys.session import AnsysSession, gpu_statistics, validate_solve
from .contracts import SurfaceState
from .mesh import sphere_mesh, structured_mesh


class AnsysGel(AnsysSession):
    def __init__(self, config, directory, executable=None, restart=None):
        super().__init__(
            config, directory, structured_mesh(config.gel), executable, restart
        )

    def reference_point(self):
        ind = self.config.indenter
        return np.array(
            [0.0, 0.0, ind.clearance_m + (ind.radius_m if ind.shape == "sphere" else 0)]
        )

    def deformable_mesh(self):
        return sphere_mesh(self.config.indenter, self.initial_pilot)

    def additional_geometry(self):
        return {}

    def rigid_target_commands(self, first_element):
        ind = self.config.indenter
        center_z = self.initial_pilot[2]
        commands = [f"N,{self.pilot},0,0,{center_z:.16g}", "TYPE,3"]
        if ind.shape == "sphere":
            commands += ["TSHAP,SPHE", f"EN,{first_element},{self.pilot}"]
        else:
            for i, (x, y) in enumerate(
                (
                    (-ind.half_width_m, -ind.half_length_m),
                    (-ind.half_width_m, ind.half_length_m),
                    (ind.half_width_m, ind.half_length_m),
                    (ind.half_width_m, -ind.half_length_m),
                )
            ):
                commands.append(
                    f"N,{self.pilot + 1 + i},{x:.16g},{y:.16g},{center_z:.16g}"
                )
            commands += [
                "TSHAP,QUAD",
                f"EN,{first_element},"
                + ",".join(str(self.pilot + i + 1) for i in range(4)),
            ]
        commands += [
            "TSHAP,PILO",
            f"EN,{first_element + 1},{self.pilot}",
            f"D,{self.pilot},ALL,0",
        ]
        return commands

    def build(self):
        c, mesh = self.config, self.mesh
        mat, ind = c.material, c.indenter
        nsolid, nface = self.contact_start, len(mesh.surface_quads)
        self.pilot = len(mesh.coordinates) + 1
        self.initial_pilot = self.reference_point()
        commands = [
            "/PREP7",
            "ET,1,SOLID185",
            f"KEYOPT,1,6,{int(mat.formulation == 'mixed_up')}",
        ]
        commands += material_commands(mat, 1)
        commands += ["TYPE,1", "MAT,1", "SECNUM,0"]
        commands += [
            f"N,{i + 1},{x:.16g},{y:.16g},{z:.16g}"
            for i, (x, y, z) in enumerate(mesh.coordinates)
        ]
        for i, elem in enumerate(mesh.hexes):
            commands.append(f"EN,{i + 1}," + ",".join(str(int(n) + 1) for n in elem))
        ftoln = (
            -ind.penetration_tolerance_m
            if ind.penetration_tolerance_m is not None
            else ind.penetration_tolerance
        )
        commands += [
            "SECNUM,0",
            "ET,2,CONTA174",
            "KEYOPT,2,2,0",
            "KEYOPT,2,4,0",
            "KEYOPT,2,10,2",
            "KEYOPT,2,11,0",
            "KEYOPT,2,12,0",
            "KEYOPT,2,18,0",
            "ET,3,TARGE170",
            f"MP,MU,3,{ind.friction:.16g}",
            f"R,1,{ind.radius_m:.16g},0,{ind.stiffness_factor:.16g},{ftoln:.16g},0,{-max(ind.radius_m, 2 * ind.clearance_m):.16g}",
            f"RMODIF,1,12,{ind.tangential_stiffness_factor:.16g}",
            "REAL,1",
            "TYPE,2",
            "MAT,3",
        ]
        if ind.elastic_slip_tolerance_m is not None:
            commands.append(f"RMODIF,1,23,{-ind.elastic_slip_tolerance_m:.16g}")
        for i, quad in enumerate(mesh.surface_quads):
            commands.append(
                f"EN,{nsolid + i + 1},"
                + ",".join(str(int(mesh.surface_nodes[n]) + 1) for n in quad)
            )
        if ind.deformable:
            self.object_mesh = self.deformable_mesh()
            obj = self.object_mesh
            self.object_offset = len(mesh.coordinates)
            commands += [
                "ET,5,SOLID185",
                f"KEYOPT,5,6,{int(ind.material.formulation == 'mixed_up')}",
            ]
            commands += material_commands(ind.material, 4)
            commands += ["TYPE,5", "MAT,4"]
            commands += [
                f"N,{self.object_offset + i + 1},{x:.16g},{y:.16g},{z:.16g}"
                for i, (x, y, z) in enumerate(obj.coordinates)
            ]
            start = nsolid + nface
            for i, elem in enumerate(obj.hexes):
                commands.append(
                    f"EN,{start + i + 1},"
                    + ",".join(str(self.object_offset + int(n) + 1) for n in elem)
                )
            commands += ["TYPE,3", "REAL,1", "MAT,3", "TSHAP,QUAD"]
            for i, face in enumerate(obj.surface_quads):
                commands.append(
                    f"EN,{start + len(obj.hexes) + i + 1},"
                    + ",".join(str(self.object_offset + int(n) + 1) for n in face)
                )
            commands += ["NSEL,NONE"]
            commands += [
                f"NSEL,A,NODE,,{self.object_offset + int(n) + 1}" for n in obj.grip_nodes
            ]
            commands += ["CM,GRIP,NODE", "D,ALL,ALL,0", "ALLSEL,ALL"]
            self.object_displacement = np.zeros_like(obj.coordinates)
        else:
            commands += self.rigid_target_commands(nsolid + nface + 1)
        commands += [
            f"NSEL,S,NODE,,1,{len(mesh.bottom_nodes)}",
            "D,ALL,ALL,0",
            "ALLSEL,ALL",
            "FINISH",
            "/SOLU",
            "ANTYPE,STATIC",
            "NLGEOM,ON",
            "NROPT,UNSYM" if c.solver.newton_raphson == "unsymmetric" else "NROPT,FULL",
            f"EQSLV,{c.solver.equation_solver.upper()}",
            "AUTOTS,ON",
            "LNSRCH,ON",
            f"NEQIT,{c.solver.iterations}",
            f"NSUBST,{c.solver.initial_substeps},{c.solver.maximum_substeps},1",
            f"CNVTOL,F,,{c.solver.force_tolerance:.16g},{c.solver.force_norm},1e-6",
            "RESCONTROL,DEFINE,ALL,LAST",
            "OUTRES,ALL,LAST",
            "KBC,0",
            "FINISH",
        ]
        if self.restart:
            step, substep = self.restart
            self.command_block(
                ["FINISH", "RESUME,gel,rdb", "/POST1", f"SET,{step},{substep}"],
                "restore_model",
            )
            self.mapdl.nsel("S", "NODE", "", 1, len(mesh.coordinates))
            restored = self.mapdl.post_processing.nodal_displacement("ALL")
            with np.load(
                self.directory.parent / "bodies" / f"frame_{step:04d}.npz"
            ) as saved:
                if not np.allclose(
                    restored, saved["gel_displacement_m"], rtol=1e-10, atol=1e-12
                ):
                    raise RuntimeError(
                        "Restart results do not match the saved converged body state"
                    )
            self.mapdl.allsel()
        else:
            self.command_block(commands, "model")
        geometry = dict(
            gel_reference_m=mesh.coordinates,
            gel_hexes=mesh.hexes,
            gel_material_ids=mesh.material_ids,
            surface_nodes=mesh.surface_nodes,
        )
        if self.object_mesh is not None:
            obj = self.object_mesh
            geometry.update(
                indenter_reference_m=obj.coordinates,
                indenter_hexes=obj.hexes,
                indenter_surface_quads=obj.surface_quads,
                indenter_grip_nodes=obj.grip_nodes,
            )
        geometry.update(self.additional_geometry())
        np.savez_compressed(self.directory.parent / "solid_mesh.npz", **geometry)

    def solve(self, pose):
        c, mapdl = self.config, self.mapdl
        self.frame_number += 1
        commands = ["FINISH", "/SOLU"]
        # POST1 result reads end the in-memory solution continuation. Restore the
        # converged nonlinear state explicitly, including frictional history.
        if self.frame_number > 1:
            if self.restart:
                step, substep = self.restart
                commands.append(f"ANTYPE,,REST,{step},{substep},CONTINUE")
                self.restart = None
            else:
                commands.append("ANTYPE,,REST")
        commands += [
            f"TIME,{pose.time_s:.16g}",
            f"NSUBST,{c.solver.initial_substeps},{c.solver.maximum_substeps},1",
        ]
        node = "ALL" if c.indenter.deformable else str(self.pilot)
        if c.indenter.deformable:
            commands += ["CMSEL,S,GRIP"]
        commands += [
            f"D,{node},UX,{pose.x_m:.16g}",
            f"D,{node},UY,{pose.y_m:.16g}",
            f"D,{node},UZ,{-pose.depth_m - c.indenter.clearance_m:.16g}",
        ]
        if c.indenter.deformable:
            commands += ["ALLSEL,ALL"]
        else:
            commands += [f"D,{node},ROTZ,{pose.twist_rad:.16g}"]
        commands += [
            "NCNV,2",
            "GS_CNV=-1",
            "SOLVE",
            "*GET,GS_CNV,ACTIVE,0,SOLU,CNVG",
            "FINISH",
            "/POST1",
            "SET,LAST",
        ]
        # MAPDL can recover an intermediate element-distortion error by bisection.
        # Let it finish, then require the explicit convergence/time/history checks.
        # NCNV=2 and set_no_abort=False still stop a failed nonlinear solution.
        timings = {}
        started = time.perf_counter()
        previous_ignore = mapdl.ignore_errors
        try:
            mapdl.ignore_errors = True
            output = self.command_block(commands, f"solve_{self.frame_number:04d}")
        finally:
            mapdl.ignore_errors = previous_ignore
        timings["solve_command_s"] = time.perf_counter() - started
        started = time.perf_counter()
        actual_time = mapdl.get_value("ACTIVE", 0, "SET", "TIME")
        load_step = int(mapdl.get_value("ACTIVE", 0, "SET", "LSTP"))
        validate_solve(
            mapdl.parameters["GS_CNV"],
            str(output),
            actual_time,
            pose.time_s,
            load_step,
            self.frame_number,
        )
        timings["solve_validation_s"] = time.perf_counter() - started
        started = time.perf_counter()
        state = self.extract(pose)
        timings["extraction_s"] = time.perf_counter() - started
        started = time.perf_counter()
        state.load_step = load_step
        state.substep = int(mapdl.get_value("ACTIVE", 0, "SET", "SBST"))
        stats = gpu_statistics(self.directory)
        for file in self.directory.iterdir():
            if file.suffix.lower() == ".dsp":
                (self.directory / f"frame_{self.frame_number:04d}_sparse.txt").write_text(
                    file.read_text(errors="replace")
                )
        timings["solver_evidence_s"] = time.perf_counter() - started
        self.last_timings = timings
        return state, stats

    def extract(self, pose):
        m, a = self.mesh, self.mapdl
        a.allsel()
        a.nsel("S", "NODE", "", 1, len(m.coordinates))
        displacement = a.post_processing.nodal_displacement("ALL")
        if displacement.shape != m.coordinates.shape:
            raise RuntimeError("Unexpected nodal displacement ordering or count")
        self.last_displacement = displacement.copy()
        top_start = int(m.surface_nodes[0]) + 1
        nf = len(m.surface_quads)
        nc = len(m.surface_nodes)
        # Select only contact elements attached to each gel surface node.
        # The resulting FSUM force is the contact load on the gel; its sum
        # must match the prescribed-motion pilot reaction and oppose the backing.
        commands = [
            "ALLSEL,ALL",
            "RSYS,0",
            "ESEL,S,TYPE,,2",
            "ETABLE,CP,CONT,PRES",
            "ETABLE,CS,NMISC,41",
            "ETABLE,PN,CONT,PENE",
            "*DEL,CF",
            f"*DIM,CF,ARRAY,{nc},3",
            "*DEL,EP",
            f"*DIM,EP,ARRAY,{nf},11",
            "*DEL,BR",
            f"*DIM,BR,ARRAY,{len(m.bottom_nodes)},3",
            "/NOPR",
            f"*DO,II,1,{nc}",
            f"NN={top_start}-1+II",
            "NSEL,S,NODE,,NN",
            "FSUM,,CONT",
        ]
        for point in range(4):
            commands.insert(6, f"ETABLE,ES{point},NMISC,{136 + point}")
            commands.insert(6, f"ETABLE,ST{point},NMISC,{1 + point}")
        for j, dof in enumerate(("FX", "FY", "FZ")):
            commands.append(f"*GET,CF(II,{j + 1}),FSUM,0,ITEM,{dof}")
        commands += [
            "*ENDDO",
            "ALLSEL,ALL",
            f"*DO,II,1,{nf}",
            f"EE={self.contact_start}+II",
        ]
        for j, label in enumerate(
            ("CP", "CS", "PN", "ES0", "ES1", "ES2", "ES3", "ST0", "ST1", "ST2", "ST3")
        ):
            commands.append(f"*GET,EP(II,{j + 1}),ELEM,EE,ETAB,{label}")
        commands += ["*ENDDO", f"*DO,II,1,{len(m.bottom_nodes)}"]
        for j, dof in enumerate(("FX", "FY", "FZ")):
            commands.append(f"*GET,BR(II,{j + 1}),NODE,II,RF,{dof}")
        commands += ["*ENDDO", "/GOPR", "ALLSEL,ALL"]
        self.command_block(commands, f"extract_{self.frame_number:04d}")
        contact_force = np.array(a.parameters["CF"], copy=True)
        fields = np.array(a.parameters["EP"], copy=True)
        backing = np.array(a.parameters["BR"], copy=True).sum(axis=0)
        if self.object_mesh is not None:
            obj = self.object_mesh
            a.nsel(
                "S",
                "NODE",
                "",
                self.object_offset + 1,
                self.object_offset + len(obj.coordinates),
            )
            self.object_displacement = a.post_processing.nodal_displacement("ALL").copy()
            if self.object_displacement.shape != obj.coordinates.shape:
                raise RuntimeError("Unexpected deformable indenter displacement ordering")
            grip_ids = self.object_offset + obj.grip_nodes + 1
            commands = ["*DEL,GR", f"*DIM,GR,ARRAY,{len(grip_ids)},3", "/NOPR"]
            for i, nid in enumerate(grip_ids):
                for j, dof in enumerate(("FX", "FY", "FZ")):
                    commands.append(f"*GET,GR({i + 1},{j + 1}),NODE,{nid},RF,{dof}")
            commands += ["/GOPR", "ALLSEL,ALL"]
            self.command_block(commands, f"grip_{self.frame_number:04d}")
            grip_forces = np.array(a.parameters["GR"], copy=True)
            reaction = grip_forces.sum(axis=0)
            # Translating grip: its deformed lever arm about the driver center
            # equals the reference arm. All grip translations are prescribed.
            moments = np.cross(
                obj.coordinates[obj.grip_nodes] - self.initial_pilot, grip_forces
            ).sum(axis=0)
        else:
            reaction = np.array(
                [a.get_value("NODE", self.pilot, "RF", dof) for dof in ("FX", "FY", "FZ")]
            )
            moments = np.array(
                [a.get_value("NODE", self.pilot, "RF", dof) for dof in ("MX", "MY", "MZ")]
            )
        reference = self.reference_surface()
        surface_displacement = displacement[m.surface_nodes].copy()
        contact_couple = np.zeros_like(contact_force)
        center = self.initial_pilot + [
            pose.x_m,
            pose.y_m,
            -pose.depth_m - self.config.indenter.clearance_m,
        ]
        return SurfaceState(
            pose.time_s,
            reference,
            surface_displacement,
            m.triangles,
            m.surface_quads,
            contact_force,
            fields[:, 0],
            fields[:, 1],
            fields[:, 2],
            backing,
            reaction,
            moments,
            center,
            m.surface_nodes + 1,
            contact_elastic_slip_m=fields[:, 3:7],
            contact_integration_status=fields[:, 7:11],
            contact_couple_nm=contact_couple,
        )
