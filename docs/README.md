# Documentation

[Project overview](../README.md)

## Install and run

1. [Getting started](getting-started.md): Python environments, Linux with pyenv,
   Windows, ANSYS paths, and GPU setup.
2. [Usage](usage.md): run a preset, [use your own object mesh](usage.md#use-your-own-object-mesh),
   change configuration, replay results, and inspect outputs.
3. [Configuration](configuration.md): select object geometry, material cases,
   parameter overrides, and surface/contact properties; includes the
   [custom-mesh interface](configuration.md#custom-object-meshes).
4. [Examples](examples/README.md): what every shipped example shows and
   [what each one costs](examples/README.md#choosing-one), from a seven-minute
   press to an overnight slide.

## Understand the model

| Guide | Scope |
|---|---|
| [Modeling](modeling.md) | Constitutive laws, geometry, boundary conditions, optics, and limitations |
| [Materials and contact](materials-and-contact.md) | Uniform gel, coating simplification, friction, and plane material specifications |
| [Sensor alignment](sensor-alignment.md) | Nominal camera, FOV, marker texture, and calibration requirements |

## Use the data and code

| Guide | Scope |
|---|---|
| [Dataset](dataset.md) | File layout, array shapes, units, signs, metrics, and visualization scales |
| [Architecture](architecture.md) | Modules, solver state, force mapping, and CPU/GPU boundaries |
| [Convergence](convergence.md) | Reading a stalled solve, convergence tolerances, contact damping, travel vs load control, and inertia for stick-slip |
| [Contributing](../CONTRIBUTING.md) | Local checks and portable, non-identifying exports |

## Assess validation

[Verification](verification.md) records example checks and GPU execution
evidence for the uniform-gel model.
[Performance](performance.md) records measured timing, CPU/GPU boundaries,
license limits, and implemented speedups.

Successful numerical checks do not establish hardware calibration or mesh
convergence. A saved example's `config.json` and `summary.json` describe that
snapshot; current presets are in [`configs/`](../configs/).
