"""Finite slab contact with physical-time preload and retained nonlinear history."""

import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from .ansys.contact import friction_commands
from .ansys.materials import bulk_commands, material_commands
from .ansys.session import AnsysSession, gpu_statistics, validate_solve
from .plane_mesh import gel_mesh, slab_mesh, textured_target


class AnsysPlane(AnsysSession):
    def __init__(
        self, config, directory, executable=None, libraries=None, *, restart=None
    ):
        self.case = config.specification
        super().__init__(
            config,
            directory,
            gel_mesh(self.case, config.plane_options.element_size_m),
            executable,
            (restart.load_step, restart.substep) if restart else None,
        )
        self.resume_point = restart
        self.previous_solver_time = 0.0
        self.time_offset = -self.case.suite["protocol"]["initialization"]["start_time_s"]
        self.extra_environment = {}
        self.native_manifest = None
        # Load control changes which platen degrees of freedom exist, so it is
        # fixed for the life of the model rather than decided per substep.
        self.load_controlled = (
            self.case.normal_control == "prescribed_normal_force"
        )
        self.load_node = None
        self.symmetric_contact = config.indenter.symmetric_contact
        self.second_pair_start = None
        needs_native = (
            self.case.bulk["model"] == "homogenized_orthotropic_fibrous_layer"
            or self.case.case["contact"]["adhesion"]["model"] != "none"
            or "x" in self.case.case["contact"]["friction"]
        )
        if needs_native:
            if libraries is None:
                raise ValueError(
                    "This material requires native plane adapters; supply --libraries"
                )
            from .native_adapter import verify_libraries

            self.native_manifest = verify_libraries(libraries)
            self.extra_environment["ANS_USER_PATH"] = str(Path(libraries).resolve())

    def build(self):
        c, mesh = self.config, self.mesh
        ns, nf = self.contact_start, len(mesh.surface_quads)
        clearance = c.indenter.clearance_m
        self.pilot = len(mesh.coordinates) + 1
        self.initial_pilot = np.array(
            [0.0, 0.0, clearance + self.case.suite["specimen"]["thickness_m"]]
        )
        cmds = [
            "FINISH",
            "/FCOMP,RST,0",
            "/PREP7",
            "ET,1,SOLID185",
            f"KEYOPT,1,6,{int(c.material.formulation == 'mixed_up')}",
        ]
        cmds += material_commands(c.material, 1)
        cmds += ["TYPE,1", "MAT,1"]
        cmds += [
            f"N,{i + 1},{x:.16g},{y:.16g},{z:.16g}"
            for i, (x, y, z) in enumerate(mesh.coordinates)
        ]
        cmds += [
            f"EN,{i + 1}," + ",".join(str(int(n) + 1) for n in e)
            for i, e in enumerate(mesh.hexes)
        ]
        cmds += ["ET,2,CONTA174", *contact_keyopt_commands(c.indenter, 2), "ET,3,TARGE170"]
        if self.symmetric_contact:
            # ANSYS picks which way round the pair runs at solve time; both
            # definitions have to exist for it to have the choice.
            cmds.append("KEYOPT,2,8,2")
        cmds += contact_real_commands(c.indenter, 1)
        cmds += contact_damping_commands(c.indenter, 2)
        cmds += friction_commands(
            self.case.case["contact"], self.case.suite["contact_numerics"], 3
        )
        cmds += ["REAL,1", "TYPE,2", "MAT,3"]
        cmds += [
            f"EN,{ns + i + 1}," + ",".join(str(int(mesh.surface_nodes[n]) + 1) for n in q)
            for i, q in enumerate(mesh.surface_quads)
        ]
        if c.indenter.deformable:
            self.object_mesh = slab_mesh(
                self.case,
                clearance,
                c.plane_options.element_size_m,
                object_mode=c.plane_options.object_mesh,
                object_size=c.plane_options.object_element_size_m,
            )
            obj = self.object_mesh
            self.object_offset = len(mesh.coordinates)
            cmds += [
                "ET,5,SOLID185",
                f"KEYOPT,5,6,{int(self.case.bulk.get('formulation') == 'mixed_up')}",
            ]
            cmds += bulk_commands(self.case.bulk, 4)
            cmds += ["TYPE,5", "MAT,4"]
            cmds += [
                f"N,{self.object_offset + i + 1},{x:.16g},{y:.16g},{z:.16g}"
                for i, (x, y, z) in enumerate(obj.coordinates)
            ]
            cmds += [
                f"EN,{ns + nf + i + 1},"
                + ",".join(str(self.object_offset + int(n) + 1) for n in e)
                for i, e in enumerate(obj.hexes)
            ]
            cmds += ["TYPE,3", "REAL,1", "MAT,3", "TSHAP,QUAD"]
            cmds += [
                f"EN,{ns + nf + len(obj.hexes) + i + 1},"
                + ",".join(str(self.object_offset + int(n) + 1) for n in q)
                for i, q in enumerate(obj.surface_quads)
            ]
            if self.symmetric_contact:
                # The reversed pair is numbered after everything the result
                # reader indexes, so the gel-side block keeps its numbering.
                start = ns + nf + len(obj.hexes) + len(obj.surface_quads)
                self.second_pair_start = start + 1
                cmds += contact_real_commands(c.indenter, 2)
                cmds += contact_damping_commands(c.indenter, 2, number=2)
                cmds += ["TYPE,2", "REAL,2", "MAT,3"]
                cmds += [
                    f"EN,{start + i + 1},"
                    + ",".join(str(self.object_offset + int(n) + 1) for n in q)
                    for i, q in enumerate(obj.surface_quads)
                ]
                start += len(obj.surface_quads)
                cmds += ["TYPE,3", "REAL,2", "MAT,3", "TSHAP,QUAD"]
                cmds += [
                    f"EN,{start + i + 1},"
                    + ",".join(str(int(mesh.surface_nodes[n]) + 1) for n in q)
                    for i, q in enumerate(mesh.surface_quads)
                ]
            # Tensor slab numbering makes the complete platen face contiguous.
            cmds += [
                f"NSEL,S,NODE,,{self.object_offset + int(obj.grip_nodes[0]) + 1},{self.object_offset + int(obj.grip_nodes[-1]) + 1}",
                "CM,GRIP,NODE",
            ]
            if self.load_controlled:
                # A rigid platen under load: one coupled z degree of freedom
                # carrying the total force, with the slide still prescribed.
                cmds += ["D,ALL,UX,0", "D,ALL,UY,0", "CP,NEXT,UZ,ALL"]
                self.load_node = self.object_offset + int(obj.grip_nodes[0]) + 1
            else:
                cmds.append("D,ALL,ALL,0")
            cmds.append("ALLSEL,ALL")
            self.object_displacement = np.zeros_like(obj.coordinates)
        else:
            points, faces = textured_target(
                self.case,
                clearance,
                c.plane_options.element_size_m,
                object_mode=c.plane_options.object_mesh,
            )
            self.target_reference = points
            self.target_quads = faces
            cmds += [f"N,{self.pilot},0,0,{self.initial_pilot[2]:.16g}"]
            cmds += [
                f"N,{self.pilot + i + 1},{x:.16g},{y:.16g},{z:.16g}"
                for i, (x, y, z) in enumerate(points)
            ]
            cmds += ["TYPE,3", "MAT,3", "REAL,1", "TSHAP,QUAD"]
            cmds += [
                f"EN,{ns + nf + i + 1},"
                + ",".join(str(self.pilot + int(n) + 1) for n in q)
                for i, q in enumerate(faces)
            ]
            cmds += [
                "TSHAP,PILO",
                f"EN,{ns + nf + len(faces) + 1},{self.pilot}",
            ]
            if self.load_controlled:
                cmds += [
                    f"D,{self.pilot},UX,0",
                    f"D,{self.pilot},UY,0",
                    f"D,{self.pilot},ROTX,0",
                    f"D,{self.pilot},ROTY,0",
                    f"D,{self.pilot},ROTZ,0",
                ]
                self.load_node = self.pilot
            else:
                cmds.append(f"D,{self.pilot},ALL,0")
        cmds += [
            f"NSEL,S,NODE,,1,{len(mesh.bottom_nodes)}",
            "D,ALL,ALL,0",
            "ALLSEL,ALL",
            "FINISH",
            "/SOLU",
            "ANTYPE,TRANS" if self.case.inertia_windows else "ANTYPE,STATIC",
            "NLGEOM,ON",
            "EQSLV,SPARSE",
        ]
        if self.case.inertia_windows:
            cmds += ["TRNOPT,FULL"]
        cmds += solution_control_commands(c.solver)
        cmds += self.time_integration_commands(None)
        cmds += [
            f"RESCONTROL,DEFINE,ALL,LAST,-1,,{self.retained_restart_points()}",
            "ESEL,S,TYPE,,2",
            "CM,GS_CONTACT,ELEM",
            "ALLSEL,ALL",
            "OUTRES,ALL,NONE",
            "OUTRES,NSOL,ALL",
            "OUTRES,RSOL,ALL",
            "OUTRES,NLOAD,ALL,GS_CONTACT",
            "OUTRES,MISC,ALL,GS_CONTACT",
            "KBC,0",
            "FINISH",
        ]
        if self.extra_environment:
            cmds.insert(-1, "USRCAL,USEROU")
        cmds.insert(-1, "PLANE_NMISC=ETYIQR(2,-110)")
        if self.resume_point is not None:
            self.restore_model()
            return
        # Write incrementally: a finely meshed slab can have over a million cells.
        path = self.directory / "plane_model.inp"
        with path.open("w", encoding="ascii") as stream:
            for line in cmds:
                stream.write(line + "\n")
        del cmds
        output = self.mapdl.input(str(path.resolve()))
        (self.directory / "plane_model.log").write_text(str(output), encoding="utf-8")
        if int(self.mapdl.get_value("NODE", 0, "COUNT")) != len(mesh.coordinates) + (
            len(self.object_mesh.coordinates)
            if self.object_mesh is not None
            else len(self.target_reference) + 1
        ):
            raise RuntimeError("Plane model node import failed")
        self.nonmisc_base = int(self.mapdl.parameters["PLANE_NMISC"])
        geometry = {
            "gel_reference_m": mesh.coordinates,
            "gel_hexes": mesh.hexes,
            "gel_material_ids": mesh.material_ids,
            "surface_nodes": mesh.surface_nodes,
        }
        if self.object_mesh is not None:
            geometry.update(
                indenter_reference_m=obj.coordinates,
                indenter_hexes=obj.hexes,
                indenter_surface_quads=obj.surface_quads,
                indenter_grip_nodes=obj.grip_nodes,
            )
        else:
            geometry.update(
                target_reference_m=self.target_reference, target_quads=self.target_quads
            )
        np.savez_compressed(self.directory.parent / "solid_mesh.npz", **geometry)

    def restore_model(self):
        from .contracts import SurfaceState
        from .rst_contact import ContactResult

        point = self.resume_point
        with np.load(self.directory.parent / "solid_mesh.npz") as geometry:
            np.testing.assert_array_equal(
                geometry["gel_reference_m"], self.mesh.coordinates
            )
            np.testing.assert_array_equal(geometry["gel_hexes"], self.mesh.hexes)
            if self.object_mesh is not None:
                np.testing.assert_array_equal(
                    geometry["indenter_reference_m"], self.object_mesh.coordinates
                )
                np.testing.assert_array_equal(
                    geometry["indenter_hexes"], self.object_mesh.hexes
                )
        self.command_block(
            [
                "FINISH",
                "RESUME,gel,rdb",
                "/POST1",
                f"SET,{point.load_step},{point.substep}",
            ],
            "restore_plane",
        )
        expected_time = point.time_s + self.time_offset
        actual_time = self.mapdl.get_value("ACTIVE", 0, "SET", "TIME")
        if not np.isclose(actual_time, expected_time, rtol=0, atol=1e-10):
            raise RuntimeError("Restart result time does not match the saved state")
        self.nonmisc_base = int(self.mapdl.parameters["PLANE_NMISC"])
        reader = ContactResult(
            self.directory / "gel.rst",
            self.contact_start + 1,
            len(self.mesh.surface_quads),
            user_values_per_point=24 if self.extra_environment else 0,
            nonmisc_base=self.nonmisc_base,
        )
        index = reader.result.parse_step_substep([point.load_step, point.substep])
        pose = self.config.physical_pose(point.time_s)
        if pose.force_controlled:
            pose = replace(pose, depth_m=self.achieved_travel(reader, index))
        state = self.extract_saved_result(reader, index, pose)
        state.load_step, state.substep = point.load_step, point.substep
        # A checkpoint resume may land exactly on a frame time whose frame was
        # never written; the pipeline renders it from this state.
        self.restored_state = (state, pose)
        if point.replay_from_substep is None:
            # The point is a saved frame, so the result file must reproduce it.
            saved = SurfaceState.load(
                self.directory.parent / "states" / f"frame_{point.frame_index:04d}.npz"
            )
            for key in (
                "reference_m",
                "displacement_m",
                "contact_force_n",
                "contact_elastic_slip_m",
                "contact_integration_status",
            ):
                np.testing.assert_allclose(
                    getattr(state, key), getattr(saved, key), rtol=1e-10, atol=1e-12
                )
            with np.load(
                self.directory.parent / "bodies" / f"frame_{point.frame_index:04d}.npz"
            ) as body:
                np.testing.assert_allclose(
                    self.last_displacement,
                    body["gel_displacement_m"],
                    rtol=1e-10,
                    atol=1e-12,
                )
                if self.object_mesh is not None:
                    np.testing.assert_allclose(
                        self.object_displacement,
                        body["indenter_displacement_m"],
                        rtol=1e-10,
                        atol=1e-12,
                    )
        del reader
        self.previous_solver_time = expected_time
        # gel.rdb carries the controls written when the model was first built, so
        # a changed tolerance only reaches the solver if it is restated here.
        listing = self.command_block(
            [
                "FINISH",
                "/SOLU",
                *solution_control_commands(self.config.solver),
                *self.time_integration_commands(None),
                "RESCONTROL,FILE_SUMMARY",
                "FINISH",
            ],
            "restart_files",
        )
        # ANTYPE,,REST against a load step the index does not hold is a fatal
        # error that leaves the solver gone and the gRPC channel waiting on it.
        # Ask first, and fail with what exists.
        available = restart_points(str(listing))
        wanted = (point.load_step, point.substep)
        if wanted not in available:
            raise RuntimeError(
                f"Restart point load step {wanted[0]} substep {wanted[1]} is not in "
                f"the solver's restart index; it holds {available or 'nothing'}. "
                "The index (gel.ldhi) is rewritten by /CLEAR and holds at most "
                "RESCONTROL's MAXFILES load steps."
            )

    def solve_interval(self, physical_pose):
        """Yield every converged substep, in order, with an explicit physical time."""
        c, a = self.config, self.mapdl
        solver_time = physical_pose.time_s + self.time_offset
        delta = solver_time - self.previous_solver_time
        if delta <= 0:
            raise ValueError("Plane solve times must increase after first touch")
        self.frame_number += 1
        commands = ["FINISH", "/SOLU"]
        if self.frame_number > 1:
            if self.restart:
                step, substep = self.restart
                commands.append(f"ANTYPE,,REST,{step},{substep},CONTINUE")
                self.restart = None
            else:
                commands.append("ANTYPE,,REST")
        max_dt = self.case.time_increment_at(
            physical_pose.time_s, self.case.suite["solver"]["maximum_time_increment_s"]
        )
        initial_dt = (
            min(max_dt, delta)
            if physical_pose.time_s > 0
            else min(max_dt, delta / c.solver.initial_substeps)
        )
        commands += [
            f"TIME,{solver_time:.16g}",
            f"DELTIM,{initial_dt:.16g},{min(delta, max_dt) / c.solver.maximum_substeps:.16g},{max_dt:.16g}",
        ]
        commands += self.time_integration_commands(physical_pose)
        commands += self.platen_commands(physical_pose)
        commands += [
            "ALLSEL,ALL",
            "NCNV,2",
            "PLANE_CNV=-1",
            "SOLVE",
            "*GET,PLANE_CNV,ACTIVE,0,SOLU,CNVG",
            "FINISH",
            "/POST1",
            "SET,LAST",
        ]
        start = time.perf_counter()
        previous_ignore = a.ignore_errors
        try:
            a.ignore_errors = True
            output = self.command_block(commands, f"solve_{self.frame_number:04d}")
        finally:
            a.ignore_errors = previous_ignore
        validate_solve(
            a.parameters["PLANE_CNV"],
            str(output),
            a.get_value("ACTIVE", 0, "SET", "TIME"),
            solver_time,
            int(a.get_value("ACTIVE", 0, "SET", "LSTP")),
            self.frame_number,
        )
        last_substep = int(a.get_value("ACTIVE", 0, "SET", "SBST"))
        self.last_timings = {"solve_command_s": time.perf_counter() - start}
        stats = gpu_statistics(self.directory)
        yield from self.converged_states(self.frame_number, 1, last_substep, stats)
        self.previous_solver_time = solver_time

    def converged_states(self, load_step, first, last, stats):
        """Read substeps first..last of a load step back from gel.rst, in order."""
        from .rst_contact import ContactResult

        c = self.config
        reader = ContactResult(
            self.directory / "gel.rst",
            self.contact_start + 1,
            len(self.mesh.surface_quads),
            user_values_per_point=24 if self.extra_environment else 0,
            nonmisc_base=self.nonmisc_base,
        )
        for substep in range(first, last + 1):
            index = reader.result.parse_step_substep([load_step, substep])
            at = float(reader.result.time_values[index]) - self.time_offset
            pose = c.physical_pose(
                max(self.case.suite["protocol"]["initialization"]["start_time_s"], at)
            )
            if pose.force_controlled:
                # Travel is the outcome of a commanded load, so read back what the
                # platen actually reached before anything downstream consumes it.
                pose = replace(pose, depth_m=self.achieved_travel(reader, index))
            state = self.extract_saved_result(reader, index, pose)
            state.load_step, state.substep = load_step, substep
            yield state, pose, stats
        del reader

    def restored(self):
        """The state the resume continues from, with solver statistics."""
        state, pose = self.restored_state
        return state, pose, gpu_statistics(self.directory)

    def replay_load_step(self, point):
        """Substeps the solver converged after the last one the pipeline checked.

        A checkpoint resume continues from the last converged load step. The
        solve that produced it ran to its end before the pipeline stopped, so its
        later substeps exist in gel.rst but were never validated or recorded;
        they are yielded here so the caller can put them through the same checks
        it applies while solving.
        """
        if point.replay_from_substep is None:
            return
        yield from self.converged_states(
            point.load_step,
            point.replay_from_substep,
            point.substep,
            gpu_statistics(self.directory),
        )

    def retained_restart_points(self):
        """How many load steps of restart state to keep (RESCONTROL MAXFILES).

        A resume continues from the last saved frame, so the restart point for
        that frame's load step has to still exist. Frames are written every
        sample interval while a restart point is written every load step, and a
        load step is one solve checkpoint, so a refined window can put ten load
        steps between two frames. A solve also runs on past its last frame before
        it fails. Keeping a couple of restart points covers neither, and the
        resume fails with no restart file matching the requested load step.

        Two frames' worth with a floor covers the gap and a failure that walks
        some way beyond it. Each file is tens of megabytes: this is disk spent on
        being able to resume at all.
        """
        import numpy as np

        case = self.case
        frames, checkpoints = case.frame_times, case.solve_times
        # The widest gap, not the average: a refined window is both where a slide
        # is likely to fail and where frames are furthest apart in checkpoints.
        counts = np.diff(np.searchsorted(checkpoints, frames, side="left"))
        per_frame = int(counts.max()) if counts.size else 1
        return min(100, max(8, 2 * per_frame + 4))

    def time_integration_commands(self, pose):
        """Switch inertia on for a transient window and off everywhere else.

        Like the convergence controls, these live in the resumed database and are
        restated rather than assumed. TINTP's amplitude decay damps the numerical
        high frequencies that a stick-slip release excites; the material's own
        Prony branches supply the physical damping, so no Rayleigh terms are
        added on top of them.
        """
        if not self.case.inertia_windows:
            return []
        window = None if pose is None else self.case.transient_at(pose.time_s)
        if window is None or not window.get("inertia", True):
            return ["TIMINT,OFF"]
        decay = (
            self.case.suite["protocol"]["transient"].get("numerical_damping", 0.005)
        )
        return ["TIMINT,ON", f"TINTP,{decay:.16g}"]

    def platen_commands(self, pose):
        """Drive the platen for one load step, in whichever mode this instant is.

        Switching mode is not just a different command: the constraint or load
        that drove the previous mode has to go, or it keeps acting. A stale
        displacement constraint silently pins the platen and the commanded load
        becomes reaction, which looks like a converged solve delivering the wrong
        force.
        """
        c = self.config
        node = "ALL" if c.indenter.deformable else str(self.pilot)
        commands = ["CMSEL,S,GRIP"] if c.indenter.deformable else []
        commands += [
            f"D,{node},UX,{pose.x_m:.16g}",
            f"D,{node},UY,{pose.y_m:.16g}",
        ]
        if pose.force_controlled:
            commands.append(f"DDELE,{self.load_node},UZ")
            # Compression pushes the platen toward the sensor, along -z.
            commands.append(f"F,{self.load_node},FZ,{-pose.normal_force_n:.16g}")
        else:
            travel = -pose.depth_m - clearance(c)
            if self.load_controlled:
                commands.append(f"FDELE,{self.load_node},FZ")
                commands.append(f"D,{self.load_node},UZ,{travel:.16g}")
            else:
                commands.append(f"D,{node},UZ,{travel:.16g}")
        # Loads ramp across a load step from the previous step's value. At the
        # handover there is no previous load, so ramping would unload the platen
        # to separation and back before reaching the command, carrying the
        # relaxation and friction history with it. Step it on instead.
        commands.append("KBC,1" if self.hands_over(pose) else "KBC,0")
        return commands

    def hands_over(self, pose):
        """Whether this step is where travel control gives way to load control."""
        if not pose.force_controlled:
            return False
        previous = self.previous_solver_time - self.time_offset
        return not self.case.force_controlled(previous)

    def check_active_pair(self, force):
        """Refuse to report half a load, or none of it.

        A symmetric definition lets ANSYS decide at solve time which way round
        the pair runs, and its criteria favour the finer, softer surface - the
        specimen. Everything downstream reads the gel-side block: coverage bins
        integrate pressure over gel material coordinates and the force balance
        sums gel-side nodal forces. If ANSYS activated the other pair those
        arrive empty, and half of them if it kept both.
        """
        if not self.symmetric_contact:
            return
        carried = float(np.linalg.norm(force))
        expected = self.expected_normal_load()
        if expected is None or expected <= 1e-6:
            return
        share = carried / expected
        if share < 0.9:
            raise RuntimeError(
                f"The gel-side contact pair carries {share:.0%} of the applied "
                f"load ({carried:.4f} N against {expected:.4f} N). ANSYS ran the "
                "reversed pair, or both; the gel-side pressure field the coverage "
                "and force checks read is incomplete. Mapping specimen-side "
                "contact results onto the gel surface is not implemented."
            )

    def expected_normal_load(self):
        """What the platen is pushing with, when that is known independently."""
        if not self.load_controlled:
            return None
        pose = getattr(self, "last_pose", None)
        return None if pose is None or not pose.force_controlled else pose.normal_force_n

    def platen_load(self, pose, reaction, moment):
        """Close the platen's force books when the normal load is applied, not held.

        Under travel control the platen's share arrives as a constraint reaction.
        Under load control its normal degree of freedom is coupled and loaded, so
        no reaction is reported there and the commanded load takes its place.
        ANSYS ramps a load linearly across its load step, and the keyframes are
        themselves piecewise linear in time, so the pose's value at this substep
        is the load actually applied.
        """
        if not pose.force_controlled:
            return reaction, moment
        reaction = np.array([reaction[0], reaction[1], -pose.normal_force_n])
        # The in-plane reactions are real and stay. The normal load arrives on a
        # coupled degree of freedom, which reports one lumped value, so how it
        # spreads over the platen face is not recoverable and the moment it
        # carries is not comparable against the contact moment. metrics reports
        # that error as null rather than substituting a sentinel, which the
        # nonfinite guard would rightly reject.
        return reaction, moment

    def achieved_travel(self, reader, index):
        """Platen travel reached under a commanded load, from the platen itself."""
        numbers, displacement = reader.result.nodal_solution(
            index, in_nodal_coord_sys=True
        )
        position = int(np.searchsorted(numbers, self.load_node))
        if position >= len(numbers) or numbers[position] != self.load_node:
            raise ValueError("Platen node is missing from the result file")
        # Travel is positive toward the sensor; the platen moves along -z.
        return float(-displacement[position, 2] - self.config.indenter.clearance_m)

    def extract_saved_result(self, reader, index, pose):
        from .contracts import SurfaceState

        self.last_pose = pose
        r, m = reader.result, self.mesh
        numbers, displacement = r.nodal_solution(index, in_nodal_coord_sys=True)
        gel_ids = np.arange(1, len(m.coordinates) + 1)
        where = np.searchsorted(numbers, gel_ids)
        if not np.array_equal(numbers[where], gel_ids):
            raise ValueError("Gel node ordering is missing from the result file")
        self.last_displacement = displacement[where, :3].copy()
        if not np.isfinite(self.last_displacement).all():
            raise ValueError("ANSYS nodal results contain non-finite displacements")
        records = reader.records(index)
        self.contact_details = reader.details(records)
        force = reader.contact_forces(records, m.surface_quads, len(m.surface_nodes))
        reaction, ids, dofs = r.nodal_reaction_forces(index)
        backing = np.array(
            [
                reaction[(ids <= len(m.bottom_nodes)) & (dofs == j)].sum()
                for j in (1, 2, 3)
            ]
        )
        if self.object_mesh is not None:
            obj = self.object_mesh
            obj_ids = self.object_offset + np.arange(1, len(obj.coordinates) + 1)
            where = np.searchsorted(numbers, obj_ids)
            if not np.array_equal(numbers[where], obj_ids):
                raise ValueError("Specimen node ordering is missing from the result file")
            self.object_displacement = displacement[where, :3].copy()
            grip_ids = self.object_offset + obj.grip_nodes + 1
            grip = np.zeros((len(grip_ids), 3))
            mask = (ids >= grip_ids[0]) & (ids <= grip_ids[-1]) & (dofs <= 3)
            np.add.at(grip, (ids[mask] - grip_ids[0], dofs[mask] - 1), reaction[mask])
            pilot_reaction = grip.sum(axis=0)
            pilot_moment = np.cross(
                obj.coordinates[obj.grip_nodes] - self.initial_pilot, grip
            ).sum(axis=0)
            pilot_reaction, pilot_moment = self.platen_load(
                pose, pilot_reaction, pilot_moment
            )
        else:
            pilot_reaction = np.array(
                [reaction[(ids == self.pilot) & (dofs == j)].sum() for j in (1, 2, 3)]
            )
            pilot_moment = np.array(
                [reaction[(ids == self.pilot) & (dofs == j)].sum() for j in (4, 5, 6)]
            )
            pilot_reaction, pilot_moment = self.platen_load(
                pose, pilot_reaction, pilot_moment
            )
        details = self.contact_details
        self.check_active_pair(force)
        return SurfaceState(
            pose.time_s,
            self.reference_surface(),
            self.last_displacement[m.surface_nodes].copy(),
            m.triangles,
            m.surface_quads,
            force,
            records["misc"][:, 12].copy(),
            records["nonmisc"][:, 40].copy(),
            details["penetration"].mean(axis=1),
            backing,
            pilot_reaction,
            pilot_moment,
            self.initial_pilot
            + [pose.x_m, pose.y_m, -pose.depth_m - self.config.indenter.clearance_m],
            m.surface_nodes + 1,
            contact_elastic_slip_m=details["elastic_slip"],
            contact_integration_status=details["status"],
        )

def clearance(config):
    return config.indenter.clearance_m


def contact_keyopt_commands(indenter, element_type):
    """CONTA174 key options from the declared contact settings.

    KEYOPT(4) detection at Gauss points, (11) shell thickness off and (18)
    sliding behaviour are held at their defaults; the three that a setup can
    reasonably need to change are read from it.
    """
    from .config import CONTACT_FORMULATIONS, CONTACT_SEPARATION

    return [
        f"KEYOPT,{element_type},2,{CONTACT_FORMULATIONS[indenter.contact_formulation]}",
        f"KEYOPT,{element_type},4,0",
        f"KEYOPT,{element_type},10,{2 if indenter.update_stiffness_each_iteration else 0}",
        f"KEYOPT,{element_type},11,0",
        f"KEYOPT,{element_type},12,{CONTACT_SEPARATION[indenter.contact_separation]}",
        f"KEYOPT,{element_type},18,0",
    ]


def contact_real_commands(indenter, number):
    """One real constant set per contact pair; a symmetric definition needs two.

    FKN, FTOLN and PINB (negative: absolute) go in the R command; the tangential
    pair follows as RMODIF because its slots lie past the first six.
    """
    return [
        f"R,{number},0,0,{indenter.stiffness_factor:.16g},"
        f"{-indenter.penetration_tolerance_m:.16g},0,{-indenter.pinball_radius_m:.16g}",
        f"RMODIF,{number},12,{indenter.tangential_stiffness_factor:.16g}",
        f"RMODIF,{number},23,{-indenter.elastic_slip_tolerance_m:.16g}",
    ]


# CONTA174 real constant slots. FDMN/FDMT are the stabilization damping factors;
# 33/34 are the squeal damping pair and must not be written in their place.
FDMN, FDMT = 31, 32


def contact_damping_commands(indenter, contact_type, number=1):
    """Stabilization damping for a contact edge that opens and closes under load.

    Damping acts only on near-field detection points, so it never bonds closed
    contact or suppresses separation. KEYOPT(15) decides whether points that were
    previously closed keep it once they reopen.
    """
    from .config import DAMPING_ACTIVATION

    commands = []
    for slot, value in (
        (FDMN, indenter.stabilization_damping_normal),
        (FDMT, indenter.stabilization_damping_tangential),
    ):
        if value is not None:
            commands.append(f"RMODIF,{number},{slot},{value:.16g}")
    if commands:
        activation = DAMPING_ACTIVATION[indenter.stabilization_damping_activation]
        commands.append(f"KEYOPT,{contact_type},15,{activation}")
    return commands


def restart_points(listing):
    """(load step, substep) pairs that RESCONTROL,FILE_SUMMARY reports.

    Each entry is a FILENAME line, a LOADSTEP/SUBSTEP header, and one row of
    numbers. Only the index decides what ANTYPE,,REST can reach; files on disk
    that the index has forgotten do not count.
    """
    points, expect_row = [], False
    for line in str(listing).splitlines():
        fields = line.split()
        if expect_row and len(fields) >= 5:
            try:
                points.append((int(fields[0]), int(fields[1])))
            except ValueError:
                pass
            expect_row = False
        elif fields[:2] == ["LOADSTEP", "SUBSTEP"]:
            expect_row = True
    return points


def solution_control_commands(solver):
    """Newton-Raphson controls that a restart must restate to take effect.

    A resumed model is rebuilt from gel.rdb, which carries the settings written
    when it was first built, so these are reissued rather than assumed.
    """
    commands = [
        "NROPT,UNSYM" if solver.newton_raphson == "unsymmetric" else "NROPT,FULL",
        "AUTOTS,ON",
        "LNSRCH,ON",
        "PRED,OFF",
        f"NEQIT,{solver.iterations}",
        f"CNVTOL,F,,{solver.force_tolerance:.16g},{solver.force_norm},1e-6",
    ]
    if solver.transient_points_per_cycle is not None:
        commands.append(f"CUTCONTROL,NPOINT,{solver.transient_points_per_cycle}")
    if not solver.predict_cutback:
        commands.append("CUTCONTROL,NOITERPREDICT,1")
    if solver.nonlinear_diagnostics:
        # Identify the elements behind a distortion or penetration abort; the
        # streamed solver text reports "Element 0" once numbers are stripped.
        commands += ["NLDIAG,NRRE,ON", "NLDIAG,CONT,ITER"]
    else:
        commands += ["NLDIAG,NRRE,OFF", "NLDIAG,CONT,OFF"]
    return commands
