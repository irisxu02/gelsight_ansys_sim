"""Translate friction and adhesion laws to ANSYS contact tables."""

import numpy as np


def contact_properties(contact, numerics):
    law, adhesion = contact["friction"], contact["adhesion"]
    axes = [law[axis] for axis in ("x", "y")] if "x" in law else [law, law]
    rules = numerics
    return np.array(
        [
            value
            for axis in axes
            for value in (axis["static_coefficient"], axis["kinetic_coefficient"])
        ]
        + [
            law["decay_velocity_m_s"],
            adhesion.get("tensile_strength_pa", 0),
            adhesion.get("cutoff_gap_m", 1),
            adhesion.get("cohesive_shear_strength_pa", 0),
            rules["elastic_slip_tolerance_m"],
            rules["tangential_stiffness_factor"],
        ]
    )


def friction_commands(contact, numerics, number, contact_type=2):
    law = contact["friction"]
    if contact["adhesion"]["model"] != "none" or "x" in law:
        # Adhesion and orthotropic friction run in the user interaction
        # routine, which keeps 24 state variables at each of 4 detection points.
        values = contact_properties(contact, numerics)
        commands = [f"TB,INTER,{number},,{len(values)},USER"]
        for start in range(0, len(values), 6):
            commands.append(
                f"TBDATA,{start + 1},"
                + ",".join(f"{v:.16g}" for v in values[start : start + 6])
            )
        commands.append(f"NSVR,{contact_type},96")
        return commands
    return [
        f"MP,MU,{number},{law['kinetic_coefficient']:.16g}",
        f"RMODIF,1,21,{law['static_coefficient'] / law['kinetic_coefficient'] if law['kinetic_coefficient'] else 1:.16g}",
        f"RMODIF,1,22,{1 / law['decay_velocity_m_s']:.16g}",
    ]
