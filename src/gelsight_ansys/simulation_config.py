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
    if shape not in ("sphere", "flat", "plane", "mesh"):
        raise ConfigurationError(
            "Supported object shapes are sphere, flat, plane, and mesh"
        )
    material = resolve_material(obj["material"], base_directory)
    if shape == "plane":
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
        if key not in ("object", "contact", "surface", "config_kind")
    }
    resolved.update(schema_version=4, indenter=indenter)
    if imported is not None:
        resolved["imported_mesh"] = asdict(imported)
    return Config.from_dict(resolved)


def resolve_plane(data, geometry, material, base_directory):
    from .plane_config import PlaneCase

    if set(geometry) != {"shape", "width_m", "length_m", "thickness_m"}:
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


def plane_solver(specification, mode="cpu"):
    """Resolve the solver allocation; the suite's convergence tolerances are kept.

    Only the equilibrium-iteration ceiling is raised above the declared value, to
    leave headroom for contact status changes. Tolerances are never tightened
    here. A hidden 1e-4/L2 override used to replace the declared 0.005/L1, making
    the criterion unreachable once a near-incompressible slab carried real load:
    Newton then kept iterating past an already-converged state until contact
    re-detection and the u-P volumetric constraint tore the specimen elements.
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
    values["iterations"] = max(150, values["iterations"])
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


def config_for_plane(
    specification,
    *,
    element_size_m=None,
    object_mesh="simplified",
    object_element_size_m=None,
    solver_mode="cpu",
):
    """Build the common runtime directly from a validated slab experiment."""
    from dataclasses import asdict

    from .config import Config

    specification.validate()
    rules = specification.suite["contact_numerics"]
    slab = specification.suite["specimen"]
    law = specification.case["contact"]["friction"]
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
            "indenter": dict(
                shape="plane",
                half_width_m=slab["width_m"] / 2,
                half_length_m=slab["length_m"] / 2,
                clearance_m=0.0,
                friction=law.get("x", law)["kinetic_coefficient"],
                stiffness_factor=rules["normal_stiffness_factor"],
                tangential_stiffness_factor=rules["tangential_stiffness_factor"],
                penetration_tolerance_m=rules["penetration_tolerance_m"],
                elastic_slip_tolerance_m=rules["elastic_slip_tolerance_m"],
                **plane_contact_damping(rules),
                symmetric_contact=bool(rules.get("symmetric_pair", False)),
                deformable=specification.bulk["model"] != "rigid",
            ),
        }
    )
