# Material Configuration Guide

This guide explains how to use the material definition files to approximate different indenter materials in your simulations.

## Quick Start

### For Mesh Indenters (STL files)

Reference a material case file in your config's `object.material` section:

```json
{
  "object": {
    "geometry": {
      "shape": "mesh",
      "file": "../assets/meshes/hemisphere-r50-fine.stl",
      ...
    },
    "material": {
      "case": "materials/resin_wax_coated.json"
    }
  },
  "contact": {
    "friction": {
      "model": "coulomb",
      "coefficient": 0.35
    },
    ...
  }
}
```

### For Parametric Indenters (sphere, flat, plane, cylinder)

Define material inline in the `indenter.material` section:

```json
{
  "indenter": {
    "shape": "sphere",
    "radius_m": 0.003,
    "material": {
      "model": "linear",
      "young_pa": 3.0e9,
      "poisson": 0.35,
      "density_kg_m3": 1200
    },
    "friction": 0.35,
    "stiffness_factor": 1.0
  }
}
```

## Available Materials

### Rigid Materials (High Stiffness)

| Material | Young's Modulus | Poisson | Friction | Use Case |
|----------|-----------------|---------|----------|----------|
| **Steel** | 200 GPa | 0.30 | 0.6 | Hard metal surfaces |
| **Aluminum** | 70 GPa | 0.33 | 0.4 | Lightweight rigid |
| **Concrete** | 30 GPa | 0.17 | 0.5 | Brittle surfaces |
| **Acrylic** | 3.2 GPa | 0.40 | 0.45 | Transparent indenters |
| **Resin (wax-coated)** | 3.0 GPa | 0.35 | 0.35 | 3D printed + wax |

### Semi-Rigid Materials

| Material | Young's Modulus | Poisson | Friction | Use Case |
|----------|-----------------|---------|----------|----------|
| **Wood (along grain)** | 12 GPa | 0.35 | 0.5 | Longitudinal loading |
| **Wood (across grain)** | 1 GPa | 0.40 | 0.5 | Radial/tangential loading |

### Soft Materials (Hyperelastic)

| Material | Young's Modulus | Poisson | Friction | Model | Use Case |
|----------|-----------------|---------|----------|-------|----------|
| **Rubber** | 5 MPa | 0.49 | 0.8 | Neo-Hookean | Elastomer cylinders |
| **Silicone** | 2 MPa | 0.49 | 0.6 | Neo-Hookean | Soft seals |
| **Fabric (cotton)** | 0.2 GPa | 0.30 | 0.7 | Linear | Woven textiles |

## How to Use Custom Materials

### Option 1: Reference Existing Material Case

Use any material from `configs/materials/`:

```json
{
  "object": {
    "material": {
      "case": "materials/steel.json"
    }
  }
}
```

### Option 2: Override Friction in Contact Section

The material case defines Young's modulus and Poisson's ratio. Override friction in the `contact` section:

```json
{
  "object": {
    "material": {
      "case": "materials/resin_wax_coated.json"
    }
  },
  "contact": {
    "friction": {
      "coefficient": 0.3  // Override default 0.35
    }
  }
}
```

### Option 3: Inline Material Definition (Mesh Only)

Define material directly in the config:

```json
{
  "object": {
    "material": {
      "model": "linear",
      "parameters": {
        "young_pa": 50e9,
        "poisson": 0.3
      }
    }
  }
}
```

## Material Model Selection

Choose the material model based on expected deformation:

### Linear Elastic
- Use for **small-strain** applications
- Rigid and semi-rigid materials (metals, ceramics, stiff plastics)
- All materials in "Rigid" and "Semi-Rigid" categories

### Neo-Hookean
- Use for **large-strain, finite-deformation** applications
- Soft elastomers and rubbers
- Captures nonlinear stress-strain behavior better than linear

### Mooney-Rivlin
- Advanced hyperelastic model for precise elastomer behavior
- Use when Neo-Hookean is insufficient (rarely needed here)

## Important Contact Parameters

When using different materials, you may need to adjust:

### Friction Coefficient
- **Metals**: 0.4–0.6
- **Wax-coated**: 0.3–0.4
- **Wood**: 0.4–0.6
- **Rubber/soft materials**: 0.6–1.0
- **Fabric**: 0.6–0.8

### Stiffness Factor
- Default: 1.0
- If contact is too stiff/too loose, adjust by ±20%
- Higher stiffness → tighter contact, slower convergence
- Lower stiffness → looser contact, faster convergence

### Penetration Tolerance
- Default: 1e-6 m (1 µm)
- May need to increase for soft materials (e.g., 5e-6 m)
- Decrease for very stiff materials if interpenetration is excessive

## Example Configurations

### Hemisphere press with wood (along grain):

```json
{
  "object": {
    "geometry": {
      "shape": "mesh",
      "file": "../assets/meshes/hemisphere-r50-fine.stl"
    },
    "material": {
      "case": "materials/wood_along_grain.json"
    }
  },
  "contact": {
    "friction": {
      "coefficient": 0.5
    },
    "numerics": {
      "stiffness_factor": 1.0,
      "penetration_tolerance_m": 1e-06
    }
  }
}
```

### Soft silicone slide (may need looser tolerances):

```json
{
  "object": {
    "geometry": {
      "shape": "mesh",
      "file": "../assets/meshes/hemisphere-r50-fine.stl"
    },
    "material": {
      "case": "materials/silicone.json"
    }
  },
  "contact": {
    "friction": {
      "coefficient": 0.6
    },
    "numerics": {
      "stiffness_factor": 0.8,
      "penetration_tolerance_m": 3e-06
    }
  }
}
```

## Creating New Materials

To add a new material, create a JSON file in `configs/materials/`:

```json
{
  "config_kind": "object_material_case",
  "name": "my_material",
  "calibrated": false,
  "description": "Description of material and typical use case",
  "material": {
    "model": "linear",  // or "neo_hookean", "mooney_rivlin"
    "young_pa": 50e9,   // Young's modulus in Pascals
    "poisson": 0.3,     // Poisson's ratio (0 to 0.5)
    "density_kg_m3": 2700  // Optional: for transient dynamics
  },
  "contact": {
    "friction_coefficient": 0.5,
    "notes": "Any notes about material behavior"
  }
}
```

Then reference it:
```json
{
  "object": {
    "material": {
      "case": "materials/my_material.json"
    }
  }
}
```

## Troubleshooting Convergence

If your simulation fails to converge with a particular material:

1. **Too stiff**: Reduce `stiffness_factor` (try 0.8, then 0.6)
2. **Too soft**: Increase `stiffness_factor` (try 1.2, then 1.5)
3. **High penetration**: Reduce `penetration_tolerance_m` or increase stiffness
4. **Oscillation**: Increase `stabilization_damping_normal` and `_tangential` (try 0.1)
5. **Slow convergence with soft material**: Use `penetration_tolerance_m: 5e-06` instead of 1e-06

## References

- ANSYS CONTA174 contact element documentation
- Hyperelastic material models: Neo-Hookean, Mooney-Rivlin
- Typical material properties: MatWeb, CRC Materials Science and Engineering Handbook
