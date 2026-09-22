"""Geometry/material composition for the common simulation entry point.

Input schema 3 resolves into one self-contained Config for every geometry. Geometry
chooses the mechanical adapter; a material case supplies constitutive parameters,
independently of the object's geometry and its contact pair.
"""

import json
from copy import deepcopy
from pathlib import Path


class ConfigurationError(ValueError):
    """An actionable input/capability error safe to display at the CLI."""


def merge_parameters(base, overrides):
    result = deepcopy(base)
    if not isinstance(overrides, dict):
        raise ConfigurationError("Parameter overrides must be a JSON object")
    for key, value in overrides.items():
        if key not in result:
            raise ConfigurationError(f"Unknown material parameter: {key}")
        if isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_parameters(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_material(selection, base_directory):
    if not isinstance(selection, dict):
        raise ConfigurationError("object.material must be a JSON object")
    if set(selection) - {"case", "model", "parameters"}:
        raise ConfigurationError("Use case or model and parameters in object.material")
    if ("case" in selection) == ("model" in selection):
        raise ConfigurationError("Select exactly one material case or inline model")
    parameters = selection.get("parameters", {})
    if not isinstance(parameters, dict) or "model" in parameters:
        raise ConfigurationError(
            "Material parameters must be an object without a model override"
        )
    if "case" in selection:
        if base_directory is None:
            raise ConfigurationError("Material case references require Config.load(path)")
        profile = json.loads(
            (Path(base_directory) / selection["case"]).read_text(encoding="utf-8")
        )
        if profile.get("config_kind") != "object_material_case":
            raise ConfigurationError(
                "Material case must reference an object_material_case file"
            )
        material = profile["material"]
        result = merge_parameters(material, parameters)
    else:
        result = {"model": selection["model"], **deepcopy(parameters)}
    model = result.get("model")
    common = {
        "model",
        "density_kg_m3",
        "modulus_reference",
        "formulation",
        "viscoelasticity",
    }
    allowed = {
        "rigid": {"model"},
        "linear": common | {"young_pa", "poisson", "mooney_fraction"},
        "neo_hookean": common | {"young_pa", "poisson", "mooney_fraction"},
        "mooney_rivlin": common | {"young_pa", "poisson", "mooney_fraction"},
        "ogden_hyperfoam": common | {"terms"},
        "homogenized_orthotropic_fibrous_layer": common
        | {
            "material_axes",
            "instantaneous_young_pa",
            "instantaneous_shear_pa",
            "poisson",
            "reciprocal_poisson_ratios",
            "through_thickness_compression",
        },
    }
    if model not in allowed:
        raise ConfigurationError("Unsupported object material model")
    if set(result) - allowed[model]:
        raise ConfigurationError(
            "Unknown parameter for the selected object material model"
        )
    if model in ("linear", "neo_hookean", "mooney_rivlin"):
        from .config import Material

        if not {"young_pa", "poisson"} <= set(result):
            raise ConfigurationError(
                "Elastic material parameters require young_pa and poisson"
            )
        elastic = {
            key: value
            for key, value in result.items()
            if key in ("model", "young_pa", "poisson", "mooney_fraction", "formulation")
        }
        Material(**elastic).validate()
        result.setdefault("formulation", "displacement")
    return result


def resolve_simulation(data, base_directory=None):
    """Resolve a geometry/material config without importing or starting ANSYS."""
    from .config import Config

    if data.get("schema_version") != 3 or data.get("config_kind") != "contact_simulation":
        raise ConfigurationError("Expected contact_simulation input schema 3")
    obj = data["object"]
    if set(obj) != {"geometry", "material"}:
        raise ConfigurationError("object must contain geometry and material")
    geometry = deepcopy(obj["geometry"])
    shape = geometry.get("shape")
    if shape not in ("sphere", "flat", "plane", "cylinder", "mesh"):
        raise ConfigurationError(
            "Supported object shapes are sphere, flat, plane, cylinder, and mesh"
        )
    material = resolve_material(obj["material"], base_directory)
    # A plane and a cylinder are the same finite-target adapter: a rigid or
    # deformable body pressed onto the gel under a physical-time protocol.
    if shape in ("plane", "cylinder"):
        return resolve_plane(data, geometry, material, base_directory)
    if "setup" in data:
        raise ConfigurationError(
            "A plane suite setup cannot drive a sphere/flat/mesh trajectory"
        )
    if shape != "mesh" and set(geometry) - {
        "shape",
        "radius_m",
        "half_width_m",
        "half_length_m",
        "clearance_m",
        "sphere_elements_per_axis",
        "grip_height_fraction",
    }:
        raise ConfigurationError("Unsupported geometry parameter for sphere/flat")
    if data.get("surface", {"model": "smooth"}) != {"model": "smooth"}:
        raise ConfigurationError(
            "Resolved surface texture currently requires plane geometry"
        )
    if material["model"] not in ("rigid", "linear", "neo_hookean", "mooney_rivlin"):
        raise ConfigurationError(
            "This constitutive model currently requires plane geometry"
        )
    if material.get("viscoelasticity"):
        raise ConfigurationError(
            "Viscoelastic material history currently requires plane geometry"
        )
    if shape == "flat" and material["model"] != "rigid":
        raise ConfigurationError(
            "Deformable flat objects use shape plane and a slab setup"
        )
    contact = deepcopy(data["contact"])
    if set(contact) - {"friction", "adhesion", "numerics"}:
        raise ConfigurationError("Unknown contact setting")
    friction = contact["friction"]
    if set(friction) != {"model", "coefficient"} or friction["model"] != "coulomb":
        raise ConfigurationError(
            "Sphere/flat/mesh currently require constant Coulomb friction"
        )
    if contact.get("adhesion", {"model": "none"}) != {"model": "none"}:
        raise ConfigurationError("Adhesive contact currently requires plane geometry")
    numerics = contact.get("numerics", {})
    if set(numerics) - {
        "stiffness_factor",
        "penetration_tolerance",
        "penetration_tolerance_m",
        "tangential_stiffness_factor",
        "elastic_slip_tolerance_m",
        "stabilization_damping_normal",
        "stabilization_damping_tangential",
        "stabilization_damping_activation",
    }:
        raise ConfigurationError("Unknown contact numerical control")
    imported = None
    if shape == "mesh":
        from dataclasses import asdict

        from .mesh_import import import_mesh

        try:
            imported = import_mesh(geometry, base_directory, material["model"] != "rigid")
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise ConfigurationError(f"Mesh import rejected: {error}") from error
        geometry = {"shape": "mesh", "clearance_m": geometry["clearance_m"]}
    indenter = {
        **geometry,
        **numerics,
        "friction": friction["coefficient"],
        "deformable": material["model"] != "rigid",
    }
    if indenter["deformable"]:
        # Density is inactive in the existing quasi-static, inertia-free adapter.
        indenter["material"] = {
            key: value
            for key, value in material.items()
            if key not in ("density_kg_m3", "modulus_reference", "viscoelasticity")
        }
    resolved = {
        key: deepcopy(value)
        for key, value in data.items()
        if key not in ("object", "contact", "surface", "config_kind", "status")
    }
    resolved.update(schema_version=4, indenter=indenter)
    if imported is not None:
        resolved["imported_mesh"] = asdict(imported)
    return Config.from_dict(resolved)


def resolve_plane(data, geometry, material, base_directory):
    from .plane_config import PlaneCase

    if geometry["shape"] == "cylinder":
        if set(geometry) != {"shape", "diameter_m", "length_m", "axis"}:
            raise ConfigurationError(
                "Cylinder geometry requires diameter_m, length_m, and axis"
            )
        if material["model"] != "rigid":
            raise ConfigurationError("Only a rigid cylinder target is implemented")
    elif set(geometry) != {"shape", "width_m", "length_m", "thickness_m"}:
        raise ConfigurationError(
            "Plane geometry requires width_m, length_m, and thickness_m"
        )
    if base_directory is None or "setup" not in data:
        raise ConfigurationError(
            "Plane geometry requires a suite setup file and Config.load(path)"
        )
    if material["model"] not in (
        "rigid",
        "neo_hookean",
        "ogden_hyperfoam",
        "homogenized_orthotropic_fibrous_layer",
    ):
        raise ConfigurationError("Unsupported plane constitutive model")
    if set(data) - {
        "schema_version",
        "config_kind",
        "name",
        "calibrated",
        "object",
        "setup",
        "surface",
        "contact",
        "status",
        "runnable_with_current_cli",
        "parameter_origin",
        "material_label",
        "required_capabilities",
        "model_notes",
        "expected_observables",
        "calibration_measurements",
        "entry_point",
    }:
        raise ConfigurationError(
            "Unknown plane config setting; shared settings belong in setup"
        )
    contact = deepcopy(data["contact"])
    if set(contact) != {"friction", "adhesion"}:
        raise ConfigurationError(
            "Plane contact requires friction and adhesion; numerical controls belong in setup"
        )
    friction = contact["friction"]
    if friction.get("model") == "coulomb":
        if set(friction) != {"model", "coefficient"}:
            raise ConfigurationError(
                "Constant Coulomb friction requires only model and coefficient"
            )
        contact["friction"] = {
            "model": "coulomb_exponential_velocity_decay",
            "static_coefficient": friction["coefficient"],
            "kinetic_coefficient": friction["coefficient"],
            "decay_velocity_m_s": 0.001,
        }
    suite = json.loads((Path(base_directory) / data["setup"]).read_text(encoding="utf-8"))
    suite["specimen"].update(
        {key: value for key, value in geometry.items() if key != "shape"}
    )
    case = {
        key: deepcopy(value)
        for key, value in data.items()
        if key not in ("object", "setup", "surface")
    }
    case.update(
        schema_version=2,
        config_kind="material_plane_case",
        suite=data["setup"],
        bulk_material=material,
        surface_geometry=deepcopy(data["surface"]),
        contact=contact,
    )
    if case["surface_geometry"] == {"model": "smooth"}:
        case["surface_geometry"] = {"model": "smooth_plane", "height_variation_m": 0}
    return config_for_plane(PlaneCase(suite, case).validate())


# Solver settings that say where a run executes rather than how it converges.
# They are the only solver fields a resolved plane configuration may hold
# differently from its suite.
ALLOCATION_KEYS = frozenset({"cores", "gpu", "require_gpu", "allow_unlisted_gpu"})

# Optics fields that scale with the camera resolution. The suite declares them
# for the nominal sensor; with_render_scale derives the resolved values.
SCALE_KEYS = ("marker_margin_px", "marker_radius_px")

# Contact settings: the resolved Indenter field for each suite contact_numerics
# key. The suite is the source; the indenter is what the adapter reads.
CONTACT_RULE_FIELDS = {
    "normal_stiffness_factor": "stiffness_factor",
    "tangential_stiffness_factor": "tangential_stiffness_factor",
    "penetration_tolerance_m": "penetration_tolerance_m",
    "elastic_slip_tolerance_m": "elastic_slip_tolerance_m",
    "pinball_radius_m": "pinball_radius_m",
    "formulation": "contact_formulation",
    "separation": "contact_separation",
    "update_stiffness_each_iteration": "update_stiffness_each_iteration",
    "symmetric_pair": "symmetric_contact",
}
DAMPING_FIELDS = {
    "normal_factor": "stabilization_damping_normal",
    "tangential_factor": "stabilization_damping_tangential",
    "activation": "stabilization_damping_activation",
}


def plane_solver(specification, mode="cpu"):
    """Resolve the solver allocation; every convergence setting is the suite's.

    Nothing here is overridden or tightened. A hidden 1e-4/L2 override used to
    replace the declared 0.005/L1, making the criterion unreachable once a
    near-incompressible slab carried real load: Newton then kept iterating past
    an already-converged state until contact re-detection and the u-P volumetric
    constraint tore the specimen elements. The iteration ceiling used to be
    raised silently for the same reason; it is declared in the suite instead.
    """
    from dataclasses import fields

    from .config import Solver

    values = {
        k: v
        for k, v in specification.suite["solver"].items()
        if k in {f.name for f in fields(Solver)}
    }
    if mode == "cpu":
        values.update(cores=4, gpu=False, require_gpu=False)
    elif mode != "specified":
        raise ConfigurationError("Plane solver mode must be cpu or specified")
    return Solver(**values)


def plane_contact_damping(rules):
    """Read optional contact stabilization damping from the setup's numerics.

    Absent settings leave the ANSYS defaults, so existing setups are unchanged.
    """
    from .config import DAMPING_ACTIVATION

    damping = rules.get("stabilization_damping")
    if damping is None:
        return {}
    if not isinstance(damping, dict) or set(damping) - {
        "normal_factor",
        "tangential_factor",
        "activation",
    }:
        raise ConfigurationError(
            "contact_numerics.stabilization_damping accepts normal_factor, "
            "tangential_factor and activation"
        )
    activation = damping.get("activation", "first_load_step")
    if activation not in DAMPING_ACTIVATION:
        raise ConfigurationError(
            "stabilization_damping.activation must be one of "
            + ", ".join(DAMPING_ACTIVATION)
        )
    return {
        "stabilization_damping_normal": damping.get("normal_factor"),
        "stabilization_damping_tangential": damping.get("tangential_factor"),
        "stabilization_damping_activation": activation,
    }


def plane_indenter(specification):
    """The contact definition the plane adapter reads, derived from the suite."""
    rules = specification.suite["contact_numerics"]
    cylinder = specification.cylinder
    half_width, half_length = specification.target_half_extents()
    law = specification.case["contact"]["friction"]
    return dict(
        shape="cylinder" if cylinder else "plane",
        # The footprint the target presents to the gel, which for a cylinder is
        # the patch cut from its lateral surface rather than the whole body.
        half_width_m=half_width,
        half_length_m=half_length,
        **({"radius_m": cylinder["diameter_m"] / 2} if cylinder else {}),
        clearance_m=0.0,
        friction=law.get("x", law)["kinetic_coefficient"],
        **{field: rules[key] for key, field in CONTACT_RULE_FIELDS.items() if key != "symmetric_pair"},
        **plane_contact_damping(rules),
        symmetric_contact=bool(rules.get("symmetric_pair", False)),
        deformable=specification.bulk["model"] != "rigid",
    )


def contact_rules(indenter):
    """The suite contact_numerics an Indenter corresponds to; the reverse map.

    A runtime override of a contact setting is written back into the suite
    through this, so the record a run keeps has one statement of each setting.
    """
    rules = {key: getattr(indenter, field) for key, field in CONTACT_RULE_FIELDS.items()}
    # Written even when both factors are absent, so that switching damping off
    # states it rather than falling back to a default nobody chose.
    rules["stabilization_damping"] = {
        key: getattr(indenter, field) for key, field in DAMPING_FIELDS.items()
    }
    return rules


def suite_configuration(specification, scale=1):
    """The common dataclasses a plane suite defines, at a camera scale.

    Everything the adapter reads twice - the gel, its material, the camera and
    optics, the contact definition, the solver - is derived here from the one
    place that declares it. A resolved run holds the same values; that is what
    plane_consistency_errors checks.
    """
    from .config import (
        Camera,
        Config,
        Gel,
        Indenter,
        Material,
        Optics,
        Visualization,
    )

    sensor = specification.suite["sensor"]
    scaled = Config(
        camera=Camera(**sensor["camera"]), optics=Optics(**sensor["optics"])
    ).with_render_scale(scale, validate=False)
    return {
        "sensor_model": sensor["sensor_model"],
        "calibrated": sensor["calibrated"],
        "gel": Gel(**sensor["gel"]),
        "material": Material(**sensor["material"]),
        "indenter": Indenter(**plane_indenter(specification)),
        "solver": plane_solver(specification, "specified"),
        "camera": scaled.camera,
        "optics": scaled.optics,
        "visualization": Visualization(**sensor["visualization"]),
    }


def plane_consistency_errors(config):
    """Where a resolved plane configuration's common fields disagree with its suite.

    The suite is the source of truth: the adapter reads the gel mesh, the
    specimen, the contact table and the time schedule from it, and the common
    fields from the resolved dataclasses. A field that could hold a different
    value from the suite's would be accepted and, depending on which copy a
    given command reads, ignored. Only the solver allocation and the camera
    scale may differ, and the scale has to be a whole multiple.
    """
    import json
    from dataclasses import asdict

    nominal = config.specification.suite["sensor"]["camera"]
    scale, remainder = divmod(config.camera.width_px, nominal["width_px"])
    if (
        remainder
        or scale < 1
        or config.camera.height_px != nominal["height_px"] * scale
    ):
        return [
            "camera resolution is not a whole multiple of the suite's sensor camera"
        ]
    expected = suite_configuration(config.specification, scale)

    def normalized(value):
        return json.loads(json.dumps(asdict(value) if hasattr(value, "__dataclass_fields__") else value))

    errors = []
    for key, value in expected.items():
        if key == "solver":
            continue
        if normalized(getattr(config, key)) != normalized(value):
            errors.append(f"{key} differs from the suite's declaration")
    mine, theirs = normalized(config.solver), normalized(expected["solver"])
    for key in sorted(set(mine) - ALLOCATION_KEYS):
        if mine[key] != theirs[key]:
            errors.append(f"solver.{key} differs from the suite's solver block")
    return errors


def config_for_plane(
    specification,
    *,
    element_size_m=None,
    object_mesh="simplified",
    object_element_size_m=None,
    solver_mode="cpu",
    validate=True,
):
    """Build the common runtime directly from a validated slab experiment."""
    from dataclasses import asdict

    from .config import Config

    if validate:
        specification.validate()
    trajectory = []
    for t in specification.frame_times:
        pose = specification.pose(t)
        trajectory.append(
            dict(
                time_s=float(t),
                depth_m=pose["normal_travel_m"],
                normal_force_n=pose.get("normal_force_n"),
                force_controlled=pose.get("force_controlled", False),
                x_m=pose["x_m"],
                y_m=pose["y_m"],
                twist_rad=pose["twist_rad"],
            )
        )
    return Config.from_dict(
        {
            **specification.suite["sensor"],
            "schema_version": 4,
            "name": specification.name,
            "specification": asdict(specification),
            "plane_options": dict(
                element_size_m=element_size_m,
                object_mesh=object_mesh,
                object_element_size_m=object_element_size_m,
            ),
            "solver": asdict(plane_solver(specification, solver_mode)),
            "trajectory": trajectory,
            "indenter": plane_indenter(specification),
        },
        validate=validate,
    )
