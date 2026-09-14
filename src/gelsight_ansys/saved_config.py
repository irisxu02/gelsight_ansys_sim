"""Import earlier result snapshots into the common runtime, without old launchers.

This exists for already-generated datasets and frozen queues. Source presets use
contact_simulation schema 3; all newly saved configs use resolved schema 4.
"""

from copy import deepcopy


def read_saved_config(data, *, validate=True):
    """Migrate an old record to the current schema.

    With validate=False the record is read as what happened, not revalidated:
    a rule added since it was written must not make it unreadable.
    """
    from .config import Config
    from .plane_config import PlaneCase
    from .simulation_config import config_for_plane

    if data.get("config_kind") == "resolved_material_plane_run":
        spec, run = data["specification"], data["runtime"]
        config = config_for_plane(
            PlaneCase(**spec),
            element_size_m=run["pilot_element_size_m"],
            object_mesh=run.get("object_mesh", "matched"),
            object_element_size_m=run.get("object_element_size_m"),
            solver_mode=run["solver_mode"],
            validate=validate,
        )
        # Each builder revalidates, so a record is migrated field by field.
        record = config.to_dict()
        record["solver"].update(run.get("solver_overrides", {}))
        record["optics"].update(run.get("optics_overrides", {}))
        config = Config.from_dict(record, validate=validate)
        return config.with_render_scale(run["render_scale"], validate=validate)
    if data.get("schema_version", 1) != 1:
        raise ValueError("Unsupported saved configuration schema")
    values = deepcopy(data)
    coat = values.pop("coating", {})
    if coat.get("enabled", False):
        raise ValueError(
            "Separate mechanical coating layers are no longer supported; use a new uniform-gel solve"
        )
    values["schema_version"] = 4
    values.setdefault("sensor_model", "generic")
    if "solver" in values:
        values["solver"].setdefault("force_norm", 2)
    if "indenter" in values:
        values["indenter"].setdefault("penetration_tolerance_m", None)
        values["indenter"].setdefault("elastic_slip_tolerance_m", None)
    if "optics" in values:
        values["optics"].setdefault("model", "analytic")
    return Config.from_dict(values, validate=validate)

