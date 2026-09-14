"""Finite-slab experiment validation and physical-time sampling.

The common geometry/material config resolver supplies this mechanical adapter.
Resolved records retain the physical-time suite and fully expanded material case.
"""

import dataclasses
import math
from copy import deepcopy
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PlaneCase:
    suite: dict
    case: dict

    def __post_init__(self):
        object.__setattr__(self, "suite", deepcopy(self.suite))
        object.__setattr__(self, "case", deepcopy(self.case))
        # Normalize launcher annotations independently of physical parameters.
        self.case["entry_point"] = "gelsight-ansys run --config <case.json>"
        self.suite["entry_point"] = self.case["entry_point"]
        contract = self.suite.get("config_contract", {})
        contract["composition"] = (
            "Simulation inputs select object geometry, material case and parameter overrides, "
            "surface, and contact; this setup supplies shared sensor, fixture, protocol, and numerics."
        )
        contract["current_loader_behavior"] = (
            "Shared setup only; run a contact_simulation config with run --config. "
            "The object geometry selects the mechanical adapter."
        )

    @classmethod
    def load(cls, path):
        from .config import Config

        resolved = Config.load(path)
        if not resolved.is_plane:
            raise ValueError(
                "Expected a simulation config with object.geometry.shape=plane"
            )
        return resolved.specification

    @property
    def name(self):
        return self.case["name"]

    @property
    def bulk(self):
        return self.case["bulk_material"]

    def refined(self, coarse, key, fallback=True):
        """Add each transient window's own grid to a coarse schedule.

        The interval has to divide the span, which validate_transient enforces.
        Otherwise `round` fits a whole number of intervals into the window and
        grids it at a spacing nobody declared - a 0.25 s window asked for 0.02 s
        becomes 13 intervals of 0.019231 s, which lands off any checkpoint grid.

        Checkpoints fall back to the window's own increment, which is the finest
        a window can be solved at and a sensible default. Frames do not: a window
        that says nothing about sampling should add no frames, because falling
        back to a 0.1 ms increment would ask for thousands of them.
        """
        times = [coarse]
        for window in self.transient_windows:
            step = window.get(key, window["time_increment_s"] if fallback else None)
            if step is None:
                continue
            a, b = window["start_time_s"], window["end_time_s"]
            times.append(np.linspace(a, b, round((b - a) / step) + 1))
        return np.unique(np.round(np.concatenate(times), 12))

    @property
    def frame_times(self):
        start, end = self.suite["protocol"]["recorded_interval_s"]
        step = self.suite["dataset"]["sample_interval_s"]
        grid = np.linspace(start, end, round((end - start) / step) + 1)
        return self.refined(grid, "sample_interval_s", fallback=False)

    @property
    def solve_times(self):
        """Mechanical checkpoints independent of saved frames, including load corners."""
        start, end = self.suite["protocol"]["recorded_interval_s"]
        dataset = self.suite["dataset"]
        step = dataset.get("solve_interval_s", dataset["sample_interval_s"])
        grid = np.linspace(start, end, round((end - start) / step) + 1)
        corners = [p["time_s"] for p in self.suite["protocol"]["keyframes"]]
        bounds = [w[k] for w in self.transient_windows for k in ("start_time_s", "end_time_s")]
        # A window's own solve_interval_s, where it declares one, keeps the
        # checkpoint grid coarser than the increment: DELTIM then subdivides
        # inside a single SOLVE instead of costing one gRPC round trip per
        # increment. Absent the key this falls back to the increment itself,
        # which is what every window did before.
        return self.refined(np.r_[grid, corners, bounds], "solve_interval_s")

    @property
    def transient_windows(self):
        """Intervals solved with inertia, each with its own time increment.

        A stick-slip release is a wave event: the shear wave speed here is about
        5 m/s and the bodies are millimetres thick, so the physics lives at a
        fraction of a millisecond. Integrating the whole protocol at that step is
        not affordable, so inertia is switched on only where it is needed.
        """
        return self.suite["protocol"].get("transient", {}).get("windows", [])

    @property
    def inertia_windows(self):
        """Windows that actually integrate mass.

        A window can also exist only to refine the time step. Contact status
        chattering is a per-load-step quantity - how much of the interface
        changes state between one equilibrium and the next - so shortening the
        step attacks it directly, with no need for a dynamic solution.
        """
        return [w for w in self.transient_windows if w.get("inertia", True)]

    def transient_at(self, physical_time):
        """The window covering this instant, or None where the solve is static."""
        for window in self.transient_windows:
            if (
                window["start_time_s"] - 1e-12
                <= physical_time
                <= window["end_time_s"] + 1e-12
            ):
                return window
        return None

    def solved_step(self, physical_time):
        """(start, end) of the load step that solves this instant.

        Steps run between consecutive mechanical checkpoints and are closed at
        their end: a substep's time lies in (start, end].
        """
        grid = self.solve_times
        index = int(np.searchsorted(grid, physical_time - 1e-12))
        index = min(max(index, 1), len(grid) - 1)
        return float(grid[index - 1]), float(grid[index])

    def step_window(self, physical_time):
        """The transient window governing the load step that solves this instant.

        Inertia and the time increment are load-step settings, so they are
        decided once for the whole step. A step is inside a window when its
        interior is: the step that ends exactly at a window's start is still
        outside it, and the step that begins exactly at a window's end is too.
        Judging by an endpoint instead put the switch-on step half in and half
        out - integrated with mass by the solver, then held to the quasi-static
        balance by the checks, which read the gel's inertial force as an error.
        """
        start, end = self.solved_step(physical_time)
        return self.transient_at((start + end) / 2)

    def integrates_mass(self, physical_time):
        """Whether the load step that solves this instant carries the gel's mass."""
        window = self.step_window(physical_time)
        return window is not None and window.get("inertia", True)

    def time_increment_at(self, physical_time, default):
        window = self.step_window(physical_time)
        return window["time_increment_s"] if window else default

    @property
    def normal_control(self):
        return self.suite["protocol"].get(
            "normal_control", "prescribed_platen_travel"
        )

    def force_controlled(self, physical_time):
        """Whether the normal direction is driven by load at this instant.

        Two segments are always travel-driven whatever the mode. Before first
        touch the normal stiffness is zero, so a commanded load has no
        equilibrium position; and once the load reaches zero on release, holding
        it there cannot express lifting clear of the surface.
        """
        if self.normal_control != "prescribed_normal_force":
            return False
        protocol = self.suite["protocol"]
        # The preload load step is solved as one ramp ending exactly at this
        # instant, so the handover has to fall on the travel side of it.
        if physical_time <= protocol["initialization"]["end_time_s"]:
            return False
        if protocol.get("release", False):
            return physical_time <= protocol["release_start_time_s"] + 1e-10
        return True

    def pose(self, physical_time):
        """Return the commanded normal target and translation in SI units."""
        protocol = self.suite["protocol"]
        init = protocol["initialization"]
        if (
            physical_time < init["start_time_s"]
            or physical_time > protocol["recorded_interval_s"][1]
        ):
            raise ValueError("Physical time lies outside the prescribed history")
        force = self.force_controlled(physical_time)
        if physical_time < init["end_time_s"]:
            weight = (physical_time - init["start_time_s"]) / (
                init["end_time_s"] - init["start_time_s"]
            )
            return {
                "time_s": physical_time,
                "normal_travel_m": init["start_normal_travel_m"]
                + weight * (init["end_normal_travel_m"] - init["start_normal_travel_m"]),
                "normal_force_n": None,
                "force_controlled": False,
                **{key: init[key] for key in ("x_m", "y_m", "twist_rad")},
            }
        points = protocol["keyframes"]
        times = [p["time_s"] for p in points]

        def track(key, default=0.0):
            return float(
                np.interp(
                    physical_time, times, [p.get(key, default) for p in points]
                )
            )

        record = {
            "time_s": physical_time,
            "force_controlled": force,
            **{key: track(key) for key in ("x_m", "y_m", "twist_rad")},
        }
        if self.normal_control == "prescribed_normal_force":
            # Travel is an outcome here; the mechanics substitutes what it reached.
            record["normal_force_n"] = track("normal_force_n") if force else None
            if force:
                record["normal_travel_m"] = 0.0
            elif physical_time <= init["end_time_s"]:
                # The handover instant still belongs to the travel-driven preload.
                record["normal_travel_m"] = init["end_normal_travel_m"]
            else:
                record["normal_travel_m"] = track("release_travel_m")
        else:
            record["normal_force_n"] = None
            record["normal_travel_m"] = track("normal_travel_m")
        return record

    def requires_contact(self, physical_time):
        protocol = self.suite["protocol"]
        return (
            not protocol.get("release", False)
            or physical_time <= protocol["release_start_time_s"] + 1e-10
        )

    def validate(self):
        suite, case = self.suite, self.case
        if suite.get("schema_version") != 2 or case.get("schema_version") != 2:
            raise ValueError("Expected plane-suite schema 2")
        if suite.get("config_kind") not in (
            "proposed_material_plane_suite",
            "material_plane_suite",
        ):
            raise ValueError("Invalid plane suite")
        if case["bulk_material"]["model"] not in (
            "rigid",
            "neo_hookean",
            "ogden_hyperfoam",
            "homogenized_orthotropic_fibrous_layer",
        ):
            raise ValueError("Unknown specimen material")
        for key in ("width_m", "length_m", "thickness_m"):
            if not math.isfinite(suite["specimen"][key]) or suite["specimen"][key] <= 0:
                raise ValueError("Specimen dimensions must be positive")
        interval = suite["protocol"]["recorded_interval_s"]
        dt = suite["dataset"]["sample_interval_s"]
        if (
            dt <= 0
            or interval[1] <= interval[0]
            or not np.isclose(
                (interval[1] - interval[0]) / dt,
                round((interval[1] - interval[0]) / dt),
            )
        ):
            raise ValueError(
                "Recording interval must contain an integer number of samples"
            )
        solve_dt = suite["dataset"].get("solve_interval_s", dt)
        if (
            not math.isfinite(solve_dt)
            or solve_dt <= 0
            or not np.isclose(dt / solve_dt, round(dt / solve_dt), rtol=0, atol=1e-9)
            or round(dt / solve_dt) < 1
        ):
            raise ValueError(
                "Saved frame interval must be an integer multiple of solve_interval_s"
            )
        points = suite["protocol"]["keyframes"]
        times = np.asarray([p["time_s"] for p in points])
        if (
            np.any(np.diff(times) <= 0)
            or times[0] != interval[0]
            or times[-1] != interval[1]
        ):
            raise ValueError("Keyframes must increase and span the recording interval")
        init = suite["protocol"]["initialization"]
        if init["end_time_s"] != times[0] or init["start_time_s"] >= init["end_time_s"]:
            raise ValueError("Preload must end at recording start")
        if self.normal_control not in (
            "prescribed_platen_travel",
            "prescribed_normal_force",
        ):
            raise ValueError(
                "normal_control must be prescribed_platen_travel or "
                "prescribed_normal_force"
            )
        if self.normal_control == "prescribed_normal_force":
            self.validate_force_control(points)
        elif init["end_normal_travel_m"] != points[0]["normal_travel_m"]:
            raise ValueError("Preload and recorded travel must be continuous")
        if not init["carry_material_and_contact_history_into_recording"]:
            raise ValueError("Plane recording requires the preload history")
        if any(p["twist_rad"] != 0 or p["y_m"] != 0 for p in points):
            raise ValueError("This slab fixture supports the prescribed x-slide protocol")
        if suite["discretization"].get("gel_mesh", "uniform") not in (
            "uniform",
            "refined",
        ):
            raise ValueError("Gel mesh must be uniform or refined")
        protocol = suite["protocol"]
        if protocol.get("release", False):
            release = protocol.get("release_start_time_s")
            if (
                release is None
                or not math.isfinite(release)
                or not interval[0] <= release < interval[1]
            ):
                raise ValueError(
                    "Release requires a start time inside the recorded interval"
                )
            if not protocol.get("allow_recorded_lift_off", False):
                raise ValueError("Release requires explicit lift-off permission")
            if release not in times:
                raise ValueError("Release needs a keyframe boundary")
            released = [p for p in points if p["time_s"] >= release]
            # Under force control the lift lives in release_travel_m, which
            # validate_force_control checks; the travel keys are absent here.
            if self.normal_control == "prescribed_platen_travel":
                if points[-1]["normal_travel_m"] >= 0:
                    raise ValueError("Release must finish above first touch")
                if any(
                    b["normal_travel_m"] > a["normal_travel_m"]
                    for a, b in zip(released, released[1:])
                ):
                    raise ValueError("Release travel must decrease monotonically")
            if any(p["x_m"] != released[0]["x_m"] for p in released):
                raise ValueError("Release must retain the final slide position")
        elif protocol.get("allow_recorded_lift_off", False):
            raise ValueError("Lift-off is only supported in an explicit release phase")
        self.validate_transient()
        self.validate_sampling()
        self.validate_declared_keys()
        self.validate_relaxation()
        surface = case["surface_geometry"]
        if surface["model"] not in (
            "smooth_plane",
            "sinusoidal_height_field",
            "homogenized_planar_pile_envelope",
        ):
            raise ValueError("Unsupported plane surface model")
        self.validate_surface_resolution()
        if surface["model"] == "sinusoidal_height_field" and self.bulk["model"] != "rigid":
            # Only the rigid target carries the height field; the deformable
            # slab is meshed with a flat contact face, so accepting the texture
            # here would silently solve a smooth specimen.
            raise ValueError(
                "A sinusoidal surface is only meshed on a rigid specimen; a "
                "textured deformable slab is not implemented"
            )
        friction = case["contact"]["friction"]
        expected_model = (
            "orthotropic_coulomb_exponential_velocity_decay"
            if "x" in friction
            else "coulomb_exponential_velocity_decay"
        )
        if friction["model"] != expected_model:
            raise ValueError("Unsupported plane friction model")
        laws = [friction[axis] for axis in ("x", "y")] if "x" in friction else [friction]
        for law in laws:
            if not 0 <= law["kinetic_coefficient"] <= law["static_coefficient"]:
                raise ValueError(
                    "Friction coefficients must satisfy 0 <= kinetic <= static"
                )
        if any(
            law["kinetic_coefficient"] == 0 < law["static_coefficient"] for law in laws
        ):
            raise ValueError(
                "A static-only friction law with zero kinetic coefficient is unsupported"
            )
        if friction["decay_velocity_m_s"] <= 0:
            raise ValueError("Friction decay velocity must be positive")
        adhesion = case["contact"]["adhesion"]
        if adhesion["model"] not in ("none", "reversible_short_range_adhesive_contact"):
            raise ValueError("Unsupported adhesion model")
        if adhesion["model"] != "none":
            area = adhesion["tensile_strength_pa"] * adhesion["cutoff_gap_m"] / 2
            if not np.isclose(area, adhesion["work_of_adhesion_j_m2"], rtol=1e-12):
                raise ValueError("Adhesion law area must equal the specified work")
        return self

    def validate_force_control(self, points):
        """Load-driven keyframes, plus the travel the release still needs."""
        protocol = self.suite["protocol"]
        if any("normal_travel_m" in p for p in points):
            raise ValueError(
                "Force-controlled keyframes carry normal_force_n, not normal_travel_m"
            )
        forces = [p.get("normal_force_n") for p in points]
        if any(f is None or not math.isfinite(f) or f < 0 for f in forces):
            raise ValueError("Every keyframe needs a finite, nonnegative normal_force_n")
        if forces[0] <= 0:
            raise ValueError("Force control must begin in established contact")
        if protocol["initialization"]["end_normal_travel_m"] <= 0:
            raise ValueError("Force control requires a travel-driven preload that closes contact")
        if not protocol.get("release", False):
            return
        release = protocol["release_start_time_s"]
        released = [p for p in points if p["time_s"] >= release]
        travels = [p.get("release_travel_m") for p in released]
        if any(t is None or not math.isfinite(t) for t in travels):
            raise ValueError(
                "A force-controlled release needs release_travel_m on every keyframe "
                "from the release onward; holding zero load cannot lift clear"
            )
        if travels[-1] >= 0:
            raise ValueError("Release must finish above first touch")
        if any(b > a for a, b in zip(travels, travels[1:])):
            raise ValueError("Release travel must decrease monotonically")

    CONTACT_NUMERICS_KEYS = {
        "formulation",
        "sliding",
        "separation",
        "normal_stiffness_factor",
        "tangential_stiffness_factor",
        "penetration_tolerance_m",
        "elastic_slip_tolerance_m",
        "pinball_radius_m",
        "update_stiffness_each_iteration",
        "stabilization_damping",
        "symmetric_pair",
    }

    SUITE_SOLVER_KEYS = {'maximum_time_increment_s'}

    def validate_declared_keys(self):
        """Every declared numeric setting must be one the solver actually reads.

        A key that nothing reads looks like a parameter and is not one; the
        contact pinball sat hard-coded in the deck for as long as three such
        keys sat in the setup. Documentation goes in keys ending in _note.
        """
        from .config import Solver

        numerics = self.suite["contact_numerics"]
        unknown = {
            k for k in numerics if k not in self.CONTACT_NUMERICS_KEYS and not k.endswith("_note")
        }
        if unknown:
            raise ValueError(
                "contact_numerics declares settings nothing reads: " + ", ".join(sorted(unknown))
            )
        missing = {"formulation", "sliding", "separation", "pinball_radius_m",
                   "update_stiffness_each_iteration"} - set(numerics)
        if missing:
            raise ValueError("contact_numerics must declare " + ", ".join(sorted(missing)))
        if numerics["sliding"] != "finite":
            raise ValueError("Only finite sliding is implemented for the plane contact")
        # Solver fields plus the settings the plane driver reads straight off
        # the setup because they shape the schedule rather than the solver.
        fields = {f.name for f in dataclasses.fields(Solver)} | self.SUITE_SOLVER_KEYS
        unknown = {k for k in self.suite["solver"] if k not in fields and k != "note"}
        if unknown:
            raise ValueError(
                "solver declares settings nothing reads: " + ", ".join(sorted(unknown))
            )

    def validate_sampling(self):
        """Every recorded frame must fall on a mechanical checkpoint.

        A frame is written from the state a checkpoint leaves behind, so one that
        lands between checkpoints is not recorded at all - silently, leaving a
        gap in the saved sequence that only shows up when something tries to read
        it back. The grids can drift apart because `refined` fits a rounded
        number of intervals into each window: a 0.25 s window asked for 0.02 s
        samples gets 13 intervals of 0.019231, which no 0.005 s checkpoint grid
        contains. Declare window intervals that divide the span.
        """
        frames, checkpoints = self.frame_times, self.solve_times
        index = np.searchsorted(checkpoints, frames)
        index = np.clip(index, 0, len(checkpoints) - 1)
        near = np.minimum(
            np.abs(checkpoints[index] - frames),
            np.abs(checkpoints[np.maximum(index - 1, 0)] - frames),
        )
        missing = frames[near > 1e-10]
        if missing.size:
            raise ValueError(
                "Frame times must be mechanical checkpoints; "
                f"{missing.size} are not, first at {missing[0]:.6g} s"
            )

    def shortest_wavelength(self):
        """The finest in-plane feature the declared surface height field holds."""
        lengths = [
            mode[axis]
            for mode in self.case["surface_geometry"].get("modes", [])
            for axis in ("wavelength_x_m", "wavelength_y_m")
            if mode.get(axis) is not None
        ]
        return min(lengths) if lengths else None

    def validate_surface_resolution(self):
        """A declared texture must be resolved by the meshes that carry and sense it.

        The case states how many elements its shortest wavelength needs. That is
        a requirement on two surfaces: the rigid target the height field is built
        on, and the gel face grid that has to register it. Neither was checked,
        and the target was built five times finer than the gel that senses it
        while the case asked for a sixth of that.
        """
        from .plane_mesh import texture_edge

        wavelength = self.shortest_wavelength()
        if wavelength is None:
            return
        if not math.isfinite(wavelength) or wavelength <= 0:
            raise ValueError("Surface wavelengths must be positive")
        required = self.case["surface_geometry"].get(
            "minimum_elements_per_shortest_wavelength"
        )
        if required is None:
            raise ValueError(
                "A surface height field must declare "
                "minimum_elements_per_shortest_wavelength"
            )
        if type(required) is not int or required < 2:
            raise ValueError(
                "minimum_elements_per_shortest_wavelength must be an integer of at least 2"
            )
        gel = self.suite["sensor"]["gel"]
        surfaces = {
            "the rigid target": texture_edge(self),
            "the gel contact face": max(
                gel["width_m"] / gel["elements"][0], gel["length_m"] / gel["elements"][1]
            ),
        }
        for name, edge in surfaces.items():
            if edge > wavelength / required + 1e-12:
                raise ValueError(
                    f"{name} is {edge * 1000:.3g} mm, which puts "
                    f"{wavelength / edge:.1f} elements across the {wavelength * 1000:.3g} mm "
                    f"wavelength; the case requires {required}"
                )
        if surfaces["the rigid target"] > surfaces["the gel contact face"] + 1e-12:
            raise ValueError(
                "The textured target must be at least as fine as the gel faces that sense it"
            )

    def validate_transient(self):
        """Windows must be ordered, disjoint, inside the record, and resolvable."""
        transient = self.suite["protocol"].get("transient")
        if transient is None:
            return
        if set(transient) - {"windows", "numerical_damping", "note"}:
            raise ValueError(
                "protocol.transient accepts windows, numerical_damping and note"
            )
        damping = transient.get("numerical_damping", 0.005)
        if not math.isfinite(damping) or not 0 <= damping < 1:
            raise ValueError("Transient numerical_damping must be in [0, 1)")
        start, end = self.suite["protocol"]["recorded_interval_s"]
        previous = start - 1
        for window in transient.get("windows", []):
            if set(window) - {
                "start_time_s",
                "end_time_s",
                "time_increment_s",
                "sample_interval_s",
                "solve_interval_s",
                "inertia",
                "reason",
            }:
                raise ValueError("Unknown transient window setting")
            a, b, dt = (
                window["start_time_s"],
                window["end_time_s"],
                window["time_increment_s"],
            )
            if window.get("solve_interval_s", dt) < dt:
                raise ValueError(
                    "A window's solve_interval_s cannot be finer than "
                    "its time_increment_s"
                )
            if not start <= a < b <= end:
                raise ValueError("A transient window must lie inside the recording")
            if a < previous:
                raise ValueError("Transient windows must be ordered and disjoint")
            previous = b
            if not math.isfinite(dt) or dt <= 0:
                raise ValueError("A transient window needs a positive time increment")
            span = (b - a) / dt
            if not np.isclose(span, round(span), rtol=0, atol=1e-9):
                raise ValueError("A transient window must hold whole time increments")
            sample = window.get("sample_interval_s", dt)
            ratio = sample / dt
            if not np.isclose(ratio, round(ratio), rtol=0, atol=1e-9) or ratio < 1:
                raise ValueError(
                    "A transient window's sample interval must be a whole "
                    "multiple of its time increment"
                )
            checkpoint = window.get("solve_interval_s", dt)
            if "sample_interval_s" in window:
                # A frame is written from the state a checkpoint leaves behind,
                # so sampling finer than the checkpoint grid, or off it, asks
                # for frames that cannot exist. validate_sampling catches the
                # consequence; this names the cause.
                steps = sample / checkpoint
                if not np.isclose(steps, round(steps), rtol=0, atol=1e-9) or steps < 1:
                    raise ValueError(
                        "A transient window's sample interval must be a whole "
                        "multiple of the interval it is solved at"
                    )
            # Being a multiple of the increment is not enough: the increment
            # divides the span but a multiple of it need not, and a grid that
            # does not divide the span is built at a spacing nobody declared.
            for name in ("sample_interval_s", "solve_interval_s"):
                step = window.get(name)
                if step is None:
                    continue
                steps = (b - a) / step
                if not np.isclose(steps, round(steps), rtol=0, atol=1e-9):
                    raise ValueError(
                        f"A transient window's {name} must divide its span: "
                        f"{b - a:.6g} s is not a whole number of {step:.6g} s steps"
                    )

    def validate_relaxation(self):
        data = self.bulk.get("viscoelasticity")
        if data is None:
            return
        if (
            data["model"] != "generalized_maxwell_prony"
            or data["normalization"] != "fractions_of_instantaneous_moduli"
        ):
            raise ValueError("Unknown relaxation law or modulus convention")
        for kind in ("shear_terms", "bulk_terms"):
            terms = data[kind]
            if any(
                not 0 <= t["fraction"] < 1
                or not math.isfinite(t["relaxation_time_s"])
                or t["relaxation_time_s"] <= 0
                for t in terms
            ):
                raise ValueError("Invalid Prony branch")
            if sum(t["fraction"] for t in terms) >= 1:
                raise ValueError("Prony series requires positive equilibrium stiffness")

    def surface_height(self, x, y):
        """Specimen material height; translating x does not re-anchor the texture."""
        surface = self.case["surface_geometry"]
        height = np.zeros(np.broadcast_shapes(np.shape(x), np.shape(y)))
        for mode in surface.get("modes", []):
            phase = np.full_like(height, mode["phase_rad"])
            if mode["wavelength_x_m"] is not None:
                phase += 2 * np.pi * np.asarray(x) / mode["wavelength_x_m"]
            if mode["wavelength_y_m"] is not None:
                phase += 2 * np.pi * np.asarray(y) / mode["wavelength_y_m"]
            height += mode["amplitude_m"] * np.cos(phase)
        return height
