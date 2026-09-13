"""Translate constitutive material parameters to ANSYS tables."""

import numpy as np


def density_commands(density, number):
    """Mass for a transient analysis; a static solve never reads it."""
    return [] if density is None else [f"MP,DENS,{number},{density:.16g}"]


def material_commands(material, number):
    density = density_commands(getattr(material, "density_kg_m3", None), number)
    if material.model == "linear":
        return density + [
            f"MP,EX,{number},{material.young_pa:.16g}",
            f"MP,PRXY,{number},{material.poisson:.16g}",
        ]
    if material.model == "neo_hookean":
        return density + [
            f"TB,HYPER,{number},,,NEO",
            f"TBDATA,1,{material.shear_pa:.16g},{2 / material.bulk_pa:.16g}",
        ]
    return density + [
        f"TB,HYPER,{number},,2,MOONEY",
        f"TBDATA,1,{(1 - material.mooney_fraction) * material.shear_pa / 2:.16g},{material.mooney_fraction * material.shear_pa / 2:.16g},{2 / material.bulk_pa:.16g}",
    ]


def prony_commands(material, number):
    commands = []
    for name, option in (("shear_terms", "SHEAR"), ("bulk_terms", "BULK")):
        terms = [
            t
            for t in material.get("viscoelasticity", {}).get(name, [])
            if t["fraction"] > 0
        ]
        if terms:
            commands.append(f"TB,PRONY,{number},,{len(terms)},{option}")
            for i, term in enumerate(terms):
                commands.append(
                    f"TBDATA,{2 * i + 1},{term['fraction']:.16g},{term['relaxation_time_s']:.16g}"
                )
    return commands


def foam_commands(material, number):
    """ANSYS FOAM order is (mu_i, alpha_i) pairs followed by all beta_i."""
    terms = material["terms"]
    values = [v for t in terms for v in (t["mu_pa"], t["alpha"])] + [
        t["beta"] for t in terms
    ]
    commands = density_commands(material.get("density_kg_m3"), number)
    commands += [f"TB,HYPER,{number},,{len(terms)},FOAM"]
    for start in range(0, len(values), 6):
        commands.append(
            f"TBDATA,{start + 1},"
            + ",".join(f"{v:.16g}" for v in values[start : start + 6])
        )
    return commands + prony_commands(material, number)


def fabric_properties(material):
    """Reciprocal orthotropic compliance and monotone compression interpolant."""
    E = material["instantaneous_young_pa"]
    nu = material["poisson"]
    compliance = np.diag([1 / E[a] for a in ("x", "y", "z")])
    for i, j, label, axis in ((0, 1, "xy", "x"), (0, 2, "xz", "x"), (1, 2, "yz", "y")):
        compliance[i, j] = compliance[j, i] = -nu[label] / E[axis]
    if np.linalg.eigvalsh(compliance).min() <= 0:
        raise ValueError("Orthotropic compliance must be positive definite")
    C = np.linalg.inv(compliance)
    curve = np.asarray(
        material["through_thickness_compression"]["strain_stress_pa"], dtype=float
    )
    x, y = curve.T
    width, delta = np.diff(x), np.diff(y) / np.diff(x)
    if x[0] != 0 or y[0] != 0 or np.any(width <= 0) or np.any(delta <= 0) or x[-1] >= 1:
        raise ValueError("Compression curve must increase from (0,0) below unit strain")
    slopes = np.empty(len(x))
    slopes[0] = E["z"]
    slopes[-1] = ((2 * width[-1] + width[-2]) * delta[-1] - width[-1] * delta[-2]) / (
        width[-1] + width[-2]
    )
    slopes[-1] = np.clip(slopes[-1], 0, 3 * delta[-1])
    for i in range(1, len(x) - 1):
        w1, w2 = 2 * width[i] + width[i - 1], width[i] + 2 * width[i - 1]
        slopes[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])
    if slopes[0] > 3 * delta[0]:
        raise ValueError(
            "Initial z modulus is incompatible with a monotone first interval"
        )
    shear = material["instantaneous_shear_pa"]
    return np.r_[
        C.ravel(), [shear[a] for a in ("xy", "xz", "yz")], E["z"], len(x), x, y, slopes
    ]


def fabric_commands(material, number):
    values = fabric_properties(material)
    commands = density_commands(material.get("density_kg_m3"), number)
    commands += [
        f"TB,AHYPER,{number},,,USER",
        "TBDATA,1,101",
        f"TB,AHYPER,{number},,,FB01",
        "TBDATA,1,1,0,0,0,1,0",
        "TBDATA,7,0,0,1",
        f"TB,AHYPER,{number},,{len(values)},AU01",
    ]
    for start in range(0, len(values), 6):
        commands.append(
            f"TBDATA,{start + 1},"
            + ",".join(f"{v:.16g}" for v in values[start : start + 6])
        )
    return commands + prony_commands(material, number)


def bulk_commands(material, number):
    """Map declared instantaneous bulk behavior and its relaxation spectrum."""
    from ..config import Material

    model = material["model"]
    if model == "ogden_hyperfoam":
        return foam_commands(material, number)
    if model == "homogenized_orthotropic_fibrous_layer":
        return fabric_commands(material, number)
    if model != "neo_hookean":
        raise ValueError("A deformable bulk material is required")
    elastic = Material(
        model=model,
        young_pa=material["young_pa"],
        poisson=material["poisson"],
        formulation=material["formulation"],
        density_kg_m3=material.get("density_kg_m3"),
    )
    return material_commands(elastic, number) + prony_commands(material, number)
