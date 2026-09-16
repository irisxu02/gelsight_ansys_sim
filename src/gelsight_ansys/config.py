"""Validated, serializable configuration in SI units."""

import json
import math
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .mesh_import import ImportedMesh
from .plane_config import PlaneCase


def positive(value, name):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


# CONTA174 KEYOPT(15). "always" keeps damping on near-field points even after they
# have been closed, which is the case for a contact edge that peels open.
# CONTA174 KEYOPT(2): how the contact constraint is enforced.
CONTACT_FORMULATIONS = {"augmented_lagrange": 0, "penalty": 1}
# CONTA174 KEYOPT(12): whether a closed point may open again.
CONTACT_SEPARATION = {"allowed": 0, "no_separation": 2}
DAMPING_ACTIVATION = {"first_load_step": 0, "all_load_steps": 2, "always": 3}


@dataclass(frozen=True)
class Gel:
    width_m: float = 0.02525
    length_m: float = 0.02075
    thickness_m: float = 0.004
    elements: tuple[int, int, int] = (36, 30, 8)
    in_plane_bias: float = 0.0
    through_thickness_bias: float = 0.0
    contact_element_size_m: float | None = None
    refinement_half_extents_m: tuple[float, float] = (0.0036, 0.003)
    # A gel that narrows towards the surface it senses with. width/length/
    # thickness stay the backing the gel is bonded to; these three describe the
    # contact face and how far down the transition to it reaches. All three
    # together or none: a pad that narrows is one shape, not three settings.
    top_width_m: float | None = None
    top_length_m: float | None = None
    taper_height_m: float | None = None

    @property
    def tapered(self):
        return self.top_width_m is not None


@dataclass(frozen=True)
class Material:
    model: str = "neo_hookean"
    young_pa: float = 100000.0
    poisson: float = 0.49
    mooney_fraction: float = 0.2
    formulation: str = "displacement"
    # Mass only reaches the solver through MP,DENS. A transient analysis without
    # it has no inertia, which is the whole point of running one.
    density_kg_m3: float | None = None

    def validate(self, label="material"):
        positive(self.young_pa, f"{label}.young_pa")
        if self.density_kg_m3 is not None:
            positive(self.density_kg_m3, f"{label}.density_kg_m3")
        if not 0 <= self.poisson < 0.5:
            raise ValueError(f"{label}.poisson must be in [0, 0.5)")
        if self.model not in ("neo_hookean", "mooney_rivlin", "linear"):
            raise ValueError("Unknown material model")
        if self.formulation not in ("displacement", "mixed_up"):
            raise ValueError("Unknown element formulation")
        if not 0 <= self.mooney_fraction <= 1:
            raise ValueError("mooney_fraction must be in [0, 1]")
        return self

    @property
    def shear_pa(self):
        return self.young_pa / (2 * (1 + self.poisson))

    @property
    def bulk_pa(self):
        return self.young_pa / (3 * (1 - 2 * self.poisson))


@dataclass(frozen=True)
class Indenter:
    shape: str = "sphere"
    radius_m: float = 0.003
    half_width_m: float = 0.003
    half_length_m: float = 0.002
    clearance_m: float = 0.0001
    friction: float = 0.5
    stiffness_factor: float = 1.0
    penetration_tolerance: float = (
        0.01  # Legacy relative FTOLN, used only if *_m is None.
    )
    penetration_tolerance_m: float | None = 0.000001
    tangential_stiffness_factor: float = 1.0
    elastic_slip_tolerance_m: float | None = 0.000005
    # Contact stabilization damping (CONTA174 FDMN/FDMT). None leaves the ANSYS
    # default; damping only acts on near-field points, so it steadies a contact
    # edge that opens and closes under load without bonding it.
    stabilization_damping_normal: float | None = None
    stabilization_damping_tangential: float | None = None
    stabilization_damping_activation: str = "first_load_step"
    # Define the pair both ways and let ANSYS choose which runs, per its own
    # criteria. Only meaningful against a deformable specimen: a rigid target has
    # no body to carry contact elements.
    symmetric_contact: bool = False
    # CONTA174 settings that used to be fixed in the deck. Each is a real
    # parameter of the contact, so each is declared where the others are.
    pinball_radius_m: float = 0.004
    contact_formulation: str = "augmented_lagrange"
    contact_separation: str = "allowed"
    update_stiffness_each_iteration: bool = True
    deformable: bool = False
    material: Material = field(
        default_factory=lambda: Material(young_pa=50000.0, poisson=0.45)
    )
    sphere_elements_per_axis: int = 12
    grip_height_fraction: float = 0.5


@dataclass(frozen=True)
class Pose:
    time_s: float
    depth_m: float
    x_m: float = 0.0
    y_m: float = 0.0
    twist_rad: float = 0.0
    # Load-driven normal direction. depth_m is then an outcome, replaced with the
    # travel actually reached once the substep is solved.
    normal_force_n: float | None = None
    force_controlled: bool = False


@dataclass(frozen=True)
class Solver:
    version: int = 252
    license_type: str = "ansys"
    cores: int = 4
    gpu: bool = False
    allow_unlisted_gpu: bool = False
    require_gpu: bool = False
    initial_substeps: int = 2
    maximum_substeps: int = 200
    iterations: int = 80
    newton_raphson: str = "unsymmetric"
    equation_solver: str = "sparse"
    force_norm: int = 1
    force_tolerance: float = 0.005
    balance_tolerance: float = 0.02
    # Write NLDIAG Newton-Raphson residual and contact tracking files. These name
    # the elements behind a distortion or penetration abort, which the streamed
    # solver text does not (it reports "Element 0").
    nonlinear_diagnostics: bool = False
    # Points per response cycle that automatic time stepping aims for once
    # inertia is integrated (CUTCONTROL,NPOINT). None sends no command and
    # leaves ANSYS's own choice; the shipped plane suite declares 13, measured
    # at the inertia window's onset against sending nothing. The increment this
    # produces is not the whole story either way: as sliding develops it decays
    # to a few 1e-5 s under either setting, which is the interface changing
    # state, not the control.
    transient_points_per_cycle: int | None = None
    # Cut back on a predicted iteration count (CUTCONTROL,NOITERPREDICT) rather
    # than on an actual failure to converge. ANSYS predicts by default.
    predict_cutback: bool = True
    # Which converged substeps reach the result file, and so which ones can be
    # checked and rendered. "every_substep" records the whole nonlinear path;
    # "each_checkpoint" records the last substep of each load step, which is the
    # instant a frame is written from. A slide that bisects to a few 1e-5 s
    # solves hundreds of substeps between checkpoints and writes a couple of
    # megabytes for each, so the choice decides whether the file stays in the
    # tens of megabytes or reaches the hundreds of gigabytes.
    result_substeps: str = "every_substep"


@dataclass(frozen=True)
class Camera:
    width_px: int = 160
    height_px: int = 128
    fov_width_m: float = 0.018
    fov_height_m: float = 0.014
    center_x_m: float = 0.0
    center_y_m: float = 0.0
    projection: str = "orthographic"
    standoff_m: float = 0.024


@dataclass(frozen=True)
class Optics:
    model: str = "taxim"
    render_mode: str = "raw"
    response_gain: float = 2.0
    response_smoothing_bins: float = 2.0
    backend: str = "cuda"
    ambient: float = 0.12
    diffuse: float = 0.55
    specular: float = 0.08
    shininess: float = 24.0
    gamma: float = 2.2
    light_directions: tuple = (
        (0.8, 0.0, 0.6),
        (-0.4, 0.692820323, 0.6),
        (-0.4, -0.692820323, 0.6),
    )
    light_colors: tuple = ((1.0, 0.12, 0.08), (0.10, 1.0, 0.15), (0.12, 0.16, 1.0))
    marker_spacing_m: float = 0.0014
    marker_radius_m: float = 0.00010
    marker_opacity: float = 0.85
    marker_style: str = "gaussian"
    marker_grid_rows_cols: tuple[int, int] | None = None
    marker_margin_px: tuple[float, float] = (24.0, 24.0)
    marker_radius_px: float | None = None
    background_image: str | None = None
    animation_fps: int = 5


@dataclass(frozen=True)
class Visualization:
    marker_scale: float = 10.0
    marker_key_um: float = 100.0
    save_panel_frames: bool = False
    save_tactile_gif: bool = False
    # A finite-element view of the solved bodies, written while the run solves.
    # It reads the same saved mesh and nodal solution the dataset already holds,
    # so switching it off costs nothing but the pictures.
    save_mesh_frames: bool = False
    mesh_deformation_scale: float = 1.0


@dataclass(frozen=True)
class PlaneOptions:
    """Mesh controls for the slab adapter; solver/optics use common fields."""

    element_size_m: float | None = None
    object_mesh: str = "simplified"
    object_element_size_m: float | None = None

    def validate(self):
        if self.object_mesh not in ("simplified", "matched"):
            raise ValueError("Object mesh must be simplified or matched")
        for key in ("element_size_m", "object_element_size_m"):
            if getattr(self, key) is not None:
                positive(getattr(self, key), key)
        if self.object_mesh == "matched" and self.object_element_size_m is not None:
            raise ValueError(
                "An explicit object size requires the simplified object mesh"
            )
        return self


@dataclass(frozen=True)
class Config:
    schema_version: int = 4
    specification: PlaneCase | None = None
    plane_options: PlaneOptions | None = None
    imported_mesh: ImportedMesh | None = None
    name: str = "sphere_press"
    calibrated: bool = False
    sensor_model: str = "gelsight_mini_nominal"
    gel: Gel = field(default_factory=Gel)
    material: Material = field(default_factory=Material)
    indenter: Indenter = field(default_factory=Indenter)
    solver: Solver = field(default_factory=Solver)
    camera: Camera = field(
        default_factory=lambda: Camera(
            width_px=320,
            height_px=240,
            fov_width_m=0.0186,
            fov_height_m=0.0143,
            projection="pinhole",
            standoff_m=0.024,
        )
    )
    optics: Optics = field(
        default_factory=lambda: Optics(
            marker_style="material",
            marker_grid_rows_cols=(7, 9),
            marker_margin_px=(24.0, 24.0),
            marker_radius_px=6.0,
            marker_opacity=0.45,
        )
    )
    visualization: Visualization = field(default_factory=Visualization)
    trajectory: tuple[Pose, ...] = field(
        default_factory=lambda: tuple(
            Pose(round(i * 0.2, 8), depth / 1000)
            for i, depth in enumerate(
                (-0.1, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 0.8, 0.6, 0.4, 0.2, 0.0, -0.1)
            )
        )
    )

    @property
    def is_plane(self):
        """Whether the finite-target adapter drives this run.

        A flat slab and a cylinder differ in the shape of the target surface and
        in nothing else: both are driven by a physical-time suite, solved by the
        same adapter, and read back by the same result reader.
        """
        return self.indenter.shape in ("plane", "cylinder")

    def suite_updated(self, **sections):
        """Restate suite settings a runtime override changed.

        A plane run reads its sensor, contact and solver settings from the
        setup; the resolved dataclasses carry the same values. An override has
        to change both, or the record would state one setting twice and
        disagree with itself.
        """
        if not self.is_plane:
            return self
        # PlaneCase copies what it is given, so the receiver keeps its own.
        specification = PlaneCase(self.specification.suite, self.specification.case)
        for path, values in sections.items():
            target = specification.suite
            for key in path.split("."):
                target = target[key]
            target.update(values)
        return replace(self, specification=specification)

    def with_solver(self, **overrides):
        from .simulation_config import ALLOCATION_KEYS

        updated = replace(self, solver=replace(self.solver, **overrides))
        declared = {k: v for k, v in overrides.items() if k not in ALLOCATION_KEYS}
        return updated.suite_updated(solver=declared).validate()

    def with_optics(self, **overrides):
        from .simulation_config import SCALE_KEYS

        if self.is_plane and set(overrides) & set(SCALE_KEYS):
            raise ValueError(
                "Marker pixel geometry follows the camera scale; use with_render_scale"
            )
        updated = replace(self, optics=replace(self.optics, **overrides))
        return updated.suite_updated(**{"sensor.optics": overrides}).validate()

    def with_gel_material(self, **overrides):
        """Change the sensor gel's constitutive or element settings in place.

        A formulation change alters the element DOF set, so a run started from it
        cannot restart ANSYS files written by the other formulation.
        """
        material = replace(self.material, **overrides).validate("material")
        updated = replace(self, material=material)
        if updated.is_plane:
            specification = PlaneCase(
                updated.specification.suite, updated.specification.case
            )
            specification.suite["sensor"]["material"] = asdict(material)
            updated = replace(updated, specification=specification.validate())
        return updated.validate()

    def with_contact_damping(self, **overrides):
        from .simulation_config import contact_rules

        updated = replace(self, indenter=replace(self.indenter, **overrides))
        return updated.suite_updated(
            contact_numerics=contact_rules(updated.indenter)
        ).validate()

    def with_plane_options(self, **overrides):
        if not self.is_plane:
            raise ValueError("Slab mesh options require plane geometry")
        return replace(
            self, plane_options=replace(self.plane_options, **overrides)
        ).validate()

    def with_plane_sampling(
        self,
        *,
        sample_interval_s=None,
        solve_interval_s=None,
        maximum_time_increment_s=None,
    ):
        """Choose recorded frames and mechanical steps without changing the protocol."""
        if not self.is_plane:
            raise ValueError("Physical sampling options require plane geometry")
        specification = PlaneCase(self.specification.suite, self.specification.case)
        dataset = specification.suite["dataset"]
        # Preserve the existing solve schedule when only output sampling changes.
        dataset.setdefault("solve_interval_s", dataset["sample_interval_s"])
        for key, value in (
            ("sample_interval_s", sample_interval_s),
            ("solve_interval_s", solve_interval_s),
        ):
            if value is not None:
                positive(value, key)
                dataset[key] = value
        if maximum_time_increment_s is not None:
            positive(maximum_time_increment_s, "maximum_time_increment_s")
            specification.suite["solver"]["maximum_time_increment_s"] = (
                maximum_time_increment_s
            )
        specification.validate()
        updated = replace(self, specification=specification)
        return replace(
            updated,
            trajectory=tuple(
                updated.physical_pose(float(t)) for t in specification.frame_times
            ),
        ).validate()

    def physical_pose(self, physical_time):
        if not self.is_plane:
            raise ValueError("Physical-time interpolation requires a plane protocol")
        p = self.specification.pose(physical_time)
        return Pose(
            float(physical_time),
            p["normal_travel_m"],
            p["x_m"],
            p["y_m"],
            p["twist_rad"],
            p.get("normal_force_n"),
            p.get("force_controlled", False),
        )

    def with_render_scale(self, scale, *, validate=True):
        """Increase optical sampling, preserving physical FOV and marker attachments."""
        if type(scale) is not int or scale < 1:
            raise ValueError("render-scale must be a positive integer")
        scaled = replace(
            self,
            camera=replace(
                self.camera,
                width_px=self.camera.width_px * scale,
                height_px=self.camera.height_px * scale,
            ),
            optics=replace(
                self.optics,
                marker_margin_px=tuple(
                    (v + 0.5) * scale - 0.5 for v in self.optics.marker_margin_px
                ),
                marker_radius_px=self.optics.marker_radius_px * scale
                if self.optics.marker_radius_px is not None
                else None,
            ),
        )
        return scaled.validate() if validate else scaled

    def with_contact_refinement(self):
        """Apply the preview's local mesh grading, preserving dimensions and counts."""
        if self.is_plane:
            raise ValueError(
                "A plane run's gel mesh is declared in its setup's discretization"
            )
        return replace(
            self,
            gel=replace(
                self.gel,
                in_plane_bias=0.0,
                contact_element_size_m=0.0003,
                refinement_half_extents_m=(0.0036, 0.003),
                through_thickness_bias=2.0,
            ),
        ).validate()

    def validate(self):
        positive(self.visualization.marker_scale, "visualization.marker_scale")
        positive(self.visualization.marker_key_um, "visualization.marker_key_um")
        positive(
            self.visualization.mesh_deformation_scale,
            "visualization.mesh_deformation_scale",
        )
        for key in ("save_panel_frames", "save_tactile_gif", "save_mesh_frames"):
            if type(getattr(self.visualization, key)) is not bool:
                raise ValueError(f"visualization.{key} must be boolean")
        if self.schema_version != 4:
            raise ValueError("Unsupported configuration schema")
        if not self.name or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for c in self.name
        ):
            raise ValueError(
                "name must contain only letters, digits, underscores, or hyphens"
            )
        for key in ("width_m", "length_m", "thickness_m"):
            positive(getattr(self.gel, key), f"gel.{key}")
        if len(self.gel.elements) != 3 or any(
            type(v) is not int or v < 2 for v in self.gel.elements
        ):
            raise ValueError("gel.elements must contain three integers >= 2")
        if self.gel.contact_element_size_m is not None:
            positive(self.gel.contact_element_size_m, "contact_element_size_m")
            if len(self.gel.refinement_half_extents_m) != 2:
                raise ValueError("refinement_half_extents_m needs x and y half extents")
            for extent, width, count in zip(
                self.gel.refinement_half_extents_m,
                (self.gel.width_m, self.gel.length_m),
                self.gel.elements,
            ):
                positive(extent, "refinement half extent")
                inner = math.ceil(extent / self.gel.contact_element_size_m - 1e-12)
                if extent >= width / 2 or count % 2 or count <= 2 * inner:
                    raise ValueError(
                        "Contact refinement needs an even count and outer transition elements"
                    )
        for name in ("in_plane_bias", "through_thickness_bias"):
            value = getattr(self.gel, name)
            if not math.isfinite(value) or not 0 <= value <= 4:
                raise ValueError(f"gel.{name} must be in [0, 4]")
        self.validate_taper()
        self.material.validate()
        self.indenter.material.validate("indenter.material")
        if type(self.indenter.deformable) is not bool:
            raise ValueError("indenter.deformable must be boolean")
        if self.indenter.deformable and self.indenter.shape not in (
            "sphere",
            "plane",
            "mesh",
        ):
            raise ValueError(
                "Deformable objects require sphere, plane, or imported mesh geometry"
            )
        if (
            type(self.indenter.sphere_elements_per_axis) is not int
            or self.indenter.sphere_elements_per_axis < 4
            or self.indenter.sphere_elements_per_axis % 2
        ):
            raise ValueError("sphere_elements_per_axis must be an even integer >= 4")
        if not 0 < self.indenter.grip_height_fraction < 1:
            raise ValueError("grip_height_fraction must be in (0, 1)")
        for key in ("penetration_tolerance_m", "elastic_slip_tolerance_m"):
            value = getattr(self.indenter, key)
            if value is not None:
                positive(value, f"indenter.{key}")
        positive(self.indenter.tangential_stiffness_factor, "tangential_stiffness_factor")
        if self.indenter.shape not in ("sphere", "flat", "plane", "cylinder", "mesh"):
            raise ValueError(
                "indenter.shape must be sphere, flat, plane, cylinder, or mesh"
            )
        for key in (
            "radius_m",
            "half_width_m",
            "half_length_m",
            "stiffness_factor",
            "penetration_tolerance",
        ):
            positive(getattr(self.indenter, key), f"indenter.{key}")
        if self.is_plane:
            if self.indenter.clearance_m != 0:
                raise ValueError(
                    "Plane protocols start at first touch with zero clearance"
                )
            if self.specification is None or self.plane_options is None:
                raise ValueError(
                    "Plane geometry requires a physical-time specification and mesh controls"
                )
            self.specification.validate()
            self.plane_options.validate()
            if self.trajectory != tuple(
                self.physical_pose(t) for t in self.specification.frame_times
            ):
                raise ValueError("Plane trajectory must match its physical-time protocol")
        else:
            positive(self.indenter.clearance_m, "indenter.clearance_m")
            if self.specification is not None or self.plane_options is not None:
                raise ValueError("Slab specifications require plane geometry")
        if self.indenter.shape == "mesh":
            if self.imported_mesh is None:
                raise ValueError("Mesh geometry requires embedded imported mesh data")
            self.imported_mesh.validate(
                self.indenter.deformable, self.indenter.clearance_m
            )
        elif self.imported_mesh is not None:
            raise ValueError("Imported mesh data requires shape mesh")
        if not math.isfinite(self.indenter.friction) or self.indenter.friction < 0:
            raise ValueError("friction must be finite and nonnegative")
        for value in (
            self.indenter.stabilization_damping_normal,
            self.indenter.stabilization_damping_tangential,
        ):
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(
                    "Contact stabilization damping factors must be finite and nonnegative"
                )
        if self.indenter.symmetric_contact and not self.indenter.deformable:
            raise ValueError("Symmetric contact requires a deformable specimen")
        positive(self.indenter.pinball_radius_m, "indenter.pinball_radius_m")
        if self.indenter.contact_formulation not in CONTACT_FORMULATIONS:
            raise ValueError(
                "contact_formulation must be one of " + ", ".join(CONTACT_FORMULATIONS)
            )
        if self.indenter.contact_separation not in CONTACT_SEPARATION:
            raise ValueError(
                "contact_separation must be one of " + ", ".join(CONTACT_SEPARATION)
            )
        if self.indenter.stabilization_damping_activation not in DAMPING_ACTIVATION:
            raise ValueError(
                "stabilization_damping_activation must be one of "
                + ", ".join(DAMPING_ACTIVATION)
            )
        for value in (
            self.solver.cores,
            self.solver.initial_substeps,
            self.solver.maximum_substeps,
            self.solver.iterations,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("Solver counts must be positive integers")
        if self.solver.result_substeps not in ("every_substep", "each_checkpoint"):
            raise ValueError(
                "solver.result_substeps must be every_substep or each_checkpoint"
            )
        if self.solver.equation_solver not in ("sparse", "mixed"):
            raise ValueError("equation_solver must be sparse or mixed")
        if self.solver.newton_raphson not in ("full", "unsymmetric"):
            raise ValueError("newton_raphson must be full or unsymmetric")
        if self.solver.maximum_substeps < self.solver.initial_substeps:
            raise ValueError("maximum_substeps must be >= initial_substeps")
        if self.solver.force_norm not in (1, 2):
            raise ValueError("force_norm must be 1 (L1) or 2 (L2)")
        for value in (self.solver.force_tolerance, self.solver.balance_tolerance):
            positive(value, "solver tolerance")
        if self.solver.require_gpu and not self.solver.gpu:
            raise ValueError("require_gpu requires gpu=true")
        for count in (
            self.camera.width_px,
            self.camera.height_px,
            self.optics.animation_fps,
        ):
            if type(count) is not int or count < 1:
                raise ValueError(
                    "Image dimensions and animation_fps must be positive integers"
                )
        for value in (
            self.camera.fov_width_m,
            self.camera.fov_height_m,
            self.optics.marker_spacing_m,
            self.optics.marker_radius_m,
            self.optics.gamma,
            self.optics.shininess,
        ):
            positive(value, "camera/optics parameter")
        if not all(
            math.isfinite(v) for v in (self.camera.center_x_m, self.camera.center_y_m)
        ):
            raise ValueError("Camera center must be finite")
        if self.camera.projection not in ("orthographic", "pinhole"):
            raise ValueError("camera.projection must be orthographic or pinhole")
        positive(self.camera.standoff_m, "camera.standoff_m")
        if self.optics.marker_style not in ("gaussian", "material"):
            raise ValueError("marker_style must be gaussian or material")
        if self.optics.marker_radius_px is not None:
            positive(self.optics.marker_radius_px, "marker_radius_px")
        grid = self.optics.marker_grid_rows_cols
        if grid is not None:
            if len(grid) != 2 or any(type(v) is not int or v < 1 for v in grid):
                raise ValueError(
                    "marker_grid_rows_cols must contain two positive integers"
                )
            margins = self.optics.marker_margin_px
            if len(margins) != 2 or any(
                not math.isfinite(v) or v < 0 or v >= (n - 1) / 2
                for v, n in zip(margins, (self.camera.height_px, self.camera.width_px))
            ):
                raise ValueError("Marker margins must lie inside the image")
        if self.optics.model not in ("analytic", "taxim"):
            raise ValueError("optics.model must be analytic or taxim")
        if self.optics.render_mode not in ("raw", "subtracted"):
            raise ValueError("render_mode must be raw or subtracted")
        positive(self.optics.response_gain, "optics.response_gain")
        if (
            not math.isfinite(self.optics.response_smoothing_bins)
            or not 0 <= self.optics.response_smoothing_bins <= 8
        ):
            raise ValueError("response_smoothing_bins must be in [0, 8]")
        if self.optics.backend not in ("cuda", "cpu"):
            raise ValueError("optics.backend must be cuda or cpu")
        if not 0 <= self.optics.marker_opacity <= 1:
            raise ValueError("marker_opacity must be in [0, 1]")
        for value in (self.optics.ambient, self.optics.diffuse, self.optics.specular):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Lighting coefficients must be finite and nonnegative")
        if (
            len(self.optics.light_directions) != len(self.optics.light_colors)
            or not self.optics.light_colors
        ):
            raise ValueError("Each light needs a direction and a color")
        for direction, color in zip(
            self.optics.light_directions, self.optics.light_colors
        ):
            if (
                len(direction) != 3
                or len(color) != 3
                or not all(math.isfinite(v) for v in (*direction, *color))
            ):
                raise ValueError("Lights must have finite 3D directions and RGB colors")
            if (
                sum(v * v for v in direction) == 0
                or min(color) < 0
                or (direction[0] == direction[1] == 0 and direction[2] < 0)
            ):
                raise ValueError("Invalid light direction or color")
        if len(self.trajectory) < 2:
            raise ValueError("At least two trajectory poses are required")
        first = self.trajectory[0]
        if not self.is_plane and first != Pose(0.0, -self.indenter.clearance_m):
            raise ValueError(
                "First pose must be the unloaded reference: t=0, depth=-clearance, x=y=twist=0"
            )
        previous = -1.0
        for pose in self.trajectory:
            values = [pose.time_s, pose.depth_m, pose.x_m, pose.y_m, pose.twist_rad]
            # A load-driven pose carries a target force instead; its travel is an
            # outcome and stays at the placeholder until the substep is solved.
            if pose.force_controlled:
                if pose.normal_force_n is None:
                    raise ValueError("A force-controlled pose needs normal_force_n")
                values.append(pose.normal_force_n)
            elif pose.normal_force_n is not None:
                raise ValueError("normal_force_n belongs to force-controlled poses")
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Trajectory values must be finite")
            if pose.time_s <= previous or pose.depth_m >= 0.8 * self.gel.thickness_m:
                raise ValueError(
                    "Times must increase; indentation must remain below 80% of gel thickness"
                )
            if not self.is_plane and self.indenter.deformable and pose.twist_rad != 0:
                raise ValueError(
                    "Deformable spheres and imported volumes currently support translation only"
                )
            previous = pose.time_s
        if self.is_plane:
            from .simulation_config import plane_consistency_errors

            # The suite declares the sensor, the contact table and the solver
            # once. A resolved run carries the same values in its common fields
            # for every adapter to read; if the two could differ, a setting
            # changed in one of them would be accepted and then ignored. This
            # runs last, so a specific rule reports itself first.
            disagreements = plane_consistency_errors(self)
            if disagreements:
                raise ValueError(
                    "Resolved plane settings disagree with their setup: "
                    + "; ".join(disagreements)
                )

        return self

    def validate_taper(self):
        """A gel that narrows towards its contact face, if it declares one."""
        gel = self.gel
        declared = (gel.top_width_m, gel.top_length_m, gel.taper_height_m)
        if all(value is None for value in declared):
            return
        if any(value is None for value in declared):
            raise ValueError(
                "A tapered gel needs top_width_m, top_length_m and taper_height_m"
            )
        for key in ("top_width_m", "top_length_m", "taper_height_m"):
            positive(getattr(gel, key), f"gel.{key}")
        if gel.top_width_m > gel.width_m or gel.top_length_m > gel.length_m:
            raise ValueError("A tapered gel's contact face cannot exceed its backing")
        if gel.taper_height_m >= gel.thickness_m:
            raise ValueError("The taper must be shorter than the gel it is cut into")

    def to_dict(self):
        return json.loads(
            json.dumps({"config_kind": "resolved_contact_simulation", **asdict(self)})
        )

    @classmethod
    def from_dict(cls, data, base_directory=None, *, validate=True):
        """Build a configuration, optionally without re-validating it.

        A saved run's config.json is a record of what happened, not a proposal.
        Reading one back has to keep working when a rule is added afterwards, or
        the rule would retroactively make every earlier run unresumable and
        unreadable. Authoring a configuration always validates.
        """
        if data.get("config_kind") == "contact_simulation":
            from .simulation_config import resolve_simulation

            return resolve_simulation(data, base_directory)
        if data.get("schema_version", 1) != 4:
            from .saved_config import read_saved_config

            return read_saved_config(data, validate=validate)
        values = dict(data)
        values.pop("config_kind", None)
        if "indenter" in values:
            values["indenter"] = dict(values["indenter"])
            if "material" in values["indenter"]:
                values["indenter"]["material"] = Material(
                    **values["indenter"]["material"]
                )
        if values.get("specification") is not None:
            values["specification"] = PlaneCase(**values["specification"])
        if values.get("imported_mesh") is not None:
            values["imported_mesh"] = ImportedMesh(**values["imported_mesh"])
        if values.get("plane_options") is not None:
            values["plane_options"] = PlaneOptions(**values["plane_options"])
        for key, kind in (
            ("gel", Gel),
            ("material", Material),
            ("indenter", Indenter),
            ("solver", Solver),
            ("camera", Camera),
            ("optics", Optics),
            ("visualization", Visualization),
        ):
            if key in values:
                values[key] = kind(**values[key])
        if "trajectory" in values:
            values["trajectory"] = tuple(Pose(**pose) for pose in values["trajectory"])
        built = cls(**values)
        return built.validate() if validate else built

    @classmethod
    def load(cls, path, *, validate=True):
        return cls.from_dict(
            json.loads(Path(path).read_text(encoding="utf-8")),
            Path(path).parent,
            validate=validate,
        )
