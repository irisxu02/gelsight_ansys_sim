# Convergence and solver diagnostics

Plane contact against a near-incompressible specimen is the hardest configuration
this project solves. This guide records how to read a stalled solve, which
controls are safe to vary, and which changes force a fresh run.

## Read the solve before changing it

Three sources describe a failure, in increasing cost to obtain.

**Saved frames.** `states/frame_*.npz` stores per-face contact pressure,
penetration, corner status and elastic slip for every recorded frame. Contact
health can be trended without reopening ANSYS:

```python
import numpy as np
d = np.load("runs/<run>/states/frame_0013.npz")
d["contact_penetration_m"].max()        # against the setup's penetration tolerance
d["contact_pressure_pa"].max()
np.unique(d["contact_integration_status"])   # 0 open far, 1 open near, 2 sliding, 3 sticking
```

Mapping corner status against distance to the gel boundary shows whether contact
is opening at the footprint edge, which is where a compliant slab on a finite gel
block loses contact first.

**The solution history.** `solver/gel.mntr` records, per converged substep, the
equilibrium iterations it cost and how many attempts it took. Both climb well
before a solve aborts:

```bash
python scripts/parse_solver_monitor.py runs/<run>/solver/gel.mntr
```

Rising `mean_iterations_last` against `mean_iterations_first`, and any
`retried_at` entries, are the early warning. A healthy solve holds a flat
iteration count and never retries.

**The solver log.** `solver/solve_NNNN.log` holds the per-iteration residuals.
Compare `FORCE CONVERGENCE VALUE` against `CRITERION`. A residual that descends
toward the criterion and then jumps back is not diverging physics; it is the
solution being rejected and re-iterated. Check whether `DISP CONVERGENCE VALUE`
reports `<<< CONVERGED` on the same iterations, and what the load increment was:
a failure at a nanometre-scale increment, after the model has sustained a
millimetre, is a numerical rejection rather than a material instability.

Element numbers are stripped from the streamed solver text, so a distortion abort
reads `Element 0 (type = 5, SOLID185)`. The type still identifies the body:
type 1 is the gel, type 5 the deformable specimen. For the element numbers
themselves, re-run with `--diagnose`.

## What the convergence tolerance has to be

The setup declares the force tolerance and norm under `solver` in the plane suite:

```json
"force_tolerance": 0.005,
"force_norm": 1
```

These reach `CNVTOL` unchanged. Only the equilibrium-iteration ceiling is raised
above the declared value, in `plane_solver`.

ANSYS computes the criterion as the tolerance times an automatic reference, so
the absolute criterion scales with load. A tolerance that a light preload
satisfies can become unreachable once the specimen carries real load. When the
criterion cannot be met, Newton does not stop at the solution: it keeps
iterating, contact re-detection fires, the mixed u-P volumetric constraint is
violated and the specimen elements invert. The abort then reports element
distortion, which points at the mesh and the load step rather than at the
tolerance that caused it.

Bracket the tolerance rather than assuming a value. Changing the norm changes
both the residual and the reference, so an L1 and an L2 tolerance are not
comparable:

```bash
gelsight-ansys run --config configs/material_plane_slide/soft_rubber.json \
    --force-tolerance 0.005 --force-norm 1
```

## Contact stabilization damping

A contact edge that opens and closes under increasing load makes the tangent
stiffness discontinuous. Stabilization damping steadies it without bonding the
contact or disabling separation, because it acts only on near-field detection
points.

Declare it in the setup under `contact_numerics`:

```json
"stabilization_damping": {
  "normal_factor": 1e-3,
  "tangential_factor": 1e-4,
  "activation": "always"
}
```

or per run with `--contact-damping-normal`, `--contact-damping-tangential` and
`--contact-damping-activation`. Absent settings leave the ANSYS defaults.

`activation` maps to `CONTA174` KEYOPT(15): `first_load_step` (0),
`all_load_steps` (2), `always` (3). The default applies damping only in the first
load step, so it is inactive for every later step of a long protocol. Points that
were previously closed keep damping only under `always`, which is the setting for
an edge that peels open.

The factors are written to real constants 31 (`FDMN`) and 32 (`FDMT`). Real
constants 33 and 34 are the squeal damping pair and are a different feature.

## Near-incompressible element formulation

The specimen formulation comes from its material case; the gel formulation comes
from `sensor.material.formulation` in the suite, and can be overridden per run:

```bash
gelsight-ansys run --config configs/material_plane_slide/soft_rubber.json \
    --gel-formulation mixed_up
```

`displacement` uses selective reduced integration, `mixed_up` adds the
hydrostatic pressure degree of freedom that ANSYS recommends for
near-incompressible hyperelastic material. The plane suite lists
`near_incompressible_locking` under `discretization.required_checks`; this flag
is how that comparison is run at fixed material parameters.

## What a restart can and cannot change

A resumed model is rebuilt from `gel.rdb`, which carries the settings written
when the model was first built. Newton-Raphson controls are restated into the
resumed database, so they take effect; everything else in that database does not.

| Change | Restart | Reason |
|---|---|---|
| `force_tolerance`, `force_norm`, `iterations` | yes, diagnostic | Restated as solution controls |
| `transient_points_per_cycle`, `predict_cutback` | yes, diagnostic | Restated as solution controls |
| `--diagnose` | yes, diagnostic | Restated as solution controls |
| `contact_acceptance` | yes, opt-in | Judged on results; never reaches ANSYS |
| Frame sampling and protocol tail | yes | Neither is in the saved state |
| Contact stabilization damping | no | Contact real constants live in `gel.rdb` |
| Gel element formulation | no | Changes the element DOF set, so saved state is incompatible |
| Specimen geometry | no | Different mesh |

Convergence changes across a restart need an explicit opt-in:

```bash
gelsight-ansys resume --run <failed run> --diagnostic-numerics \
    --force-tolerance 0.005 --force-norm 1 --diagnose --stop-after-s <t>
```

Frames before and after the change met different convergence criteria, so the
result is recorded with `diagnostic_run` and `numerics_overrides` in
`summary.json` and finishes as `diagnostic_passed`. It is evidence about solver
behaviour, not a dataset. A production sequence must be solved end to end under
one set of controls.

`--acceptance-override` is the same bargain for `contact_acceptance`. The
criterion is evaluated on results and never reaches the solver, so `gel.rdb` does
not constrain it, but frames written before the restart were validated under the
rules then in force and keep them. The change and the instant it took effect are
recorded in `acceptance_overrides`.

### Enough restart state has to survive

ANSYS keeps only the last `MAXFILES` load steps of restart state
(`RESCONTROL,DEFINE,ALL,LAST,-1,,MAXFILES`), and a resume continues from the
last *saved frame*. Those are different clocks: a restart point is written every
load step, which is every solve checkpoint, while a frame is written every
sample interval. A slide window sampled every 0.05 s with 0.005 s checkpoints
puts ten load steps between frames.

With `MAXFILES` at 2 the resume never worked. A run that stopped eight load steps
past its last frame had load steps 193 and 194 on disk and wanted 186:

```
*** ERROR ***
No restart file found matching loadstep and substep specified in the
ANTYPE,,REST command.  Restart cannot be performed.
```

Everything else about that resume was correct — `RESUME,gel,rdb`, `SET,186,50`,
the restated controls, `ANTYPE,,REST,186,50,CONTINUE`, `TIMINT,ON` — and it still
could not start. The count is now derived from the widest frame-to-frame gap in
checkpoints, doubled with margin, because the refined window is both where a
slide fails and where frames are furthest apart. That is tens of megabytes per
retained load step, spent to be able to resume at all.

Two consequences for a setup that wants to stay resumable. Declare a window's
`solve_interval_s` rather than letting checkpoints fall on every increment, or
the retained count follows the increment. And keep the sample interval close
enough to the checkpoint interval that the gap is small — the state that has to
survive is proportional to it.

## Fixture and acceptance

The whole specimen top face is clamped to the platen, so specimen area outside
the sensor footprint is a rigid flange beside the contact edge. Trimming the
overhang is a sensitivity test, not a fix:
`configs/material_plane_slide/soft_rubber_compact.json` holds material, contact
and protocol fixed and reduces the specimen to the smallest size that keeps the
declared edge margin through the full slide. Agreement with the full-size force
curve means the overhang was mechanically inert; divergence means the flange
carries load and the fixture needs checking against the physical backing plate.

### What the bin-activity requirement speaks for

`contact_acceptance` requires full-footprint contact on every recorded frame:
every 1 mm bin of the sensor surface must carry more than `minimum_bin_force_n`,
and `required_active_bin_fraction` is 1. For a press that is a fair statement
that the specimen is seated. A slide breaks it, and the break is at the gel's
free corners rather than anywhere the dataset looks.

Measured on the rigid 5 N reference, at 125 um of slide:

| | Inside the camera field of view | Outside it |
|---|---|---|
| Contact elements | 520 | 560 |
| Minimum pressure | 7.39 kPa | 1.57 kPa |

against a 16.8 kPa peak, and the twenty least loaded elements were all outside
the view — the weakest sitting on the gel corner, 3 mm beyond its edge. The gel
is 25.25 x 20.75 mm and the camera sees 18.6 x 14.3 mm of it, so the ring that
unloads is a ring the dataset never records. The weakest bin decays smoothly
from 930 uN to 1 uN across 85 ms, so lowering the threshold buys milliseconds,
not a protocol.

It is not a tilt. One reading of this would be that friction acts at the gel
surface while the platen reaction acts above it, and the couple unloads the
trailing half. The pressure field says otherwise: across the same 100 um the
profile is unchanged and the two halves differ by 0.2%, because the platen's
rotational constraint takes the moment. What collapses is local to the free
corner, which is also where the slip ring nucleates.

So a setup that slides may declare which region the requirement speaks for:

```json
"macroscopic_contact_bins": {
  "size_xy_m": [0.001, 0.001],
  "minimum_bin_force_n": 1e-06,
  "required_active_bin_fraction": 1,
  "region": "camera_field_of_view"
}
```

Only bin activity is scoped. Geometric footprint coverage, edge margin and total
repulsive normal force stay gated on the whole outer sensor surface, and
`active_bin_fraction` for that whole surface is recorded in every frame beside
the scoped one. Omit `region` and nothing changes.

## Travel control and load control

`protocol.normal_control` selects what drives the normal direction:

| Value | Keyframes carry | Travel is |
|---|---|---|
| `prescribed_platen_travel` (default) | `normal_travel_m` | the command |
| `prescribed_normal_force` | `normal_force_n` | an outcome, read back per substep |

Load control removes the per-material travel calibration. Equal travel is not
equal load — a rigid specimen leaves the gel carrying everything while foam
absorbs most of it — so matching materials at a force otherwise needs one
measured force-travel curve each (`scripts/calibrate_normal_force.py`). Under
load control the same protocol transfers to any specimen.

Two segments stay travel-driven whatever the mode, and the schema enforces it:

- **The preload.** Before first touch the normal stiffness is zero, so a
  commanded load has no equilibrium position. `initialization.end_normal_travel_m`
  must close contact before load control begins.
- **The release.** Once the load reaches zero, holding it there cannot express
  lifting clear of the surface. Every keyframe from `release_start_time_s` onward
  needs `release_travel_m`, decreasing to a negative final value.

Mechanically, load control frees the platen in z and couples it so it still moves
as one rigid plane, then applies the total force to the coupled prime degree of
freedom:

```
D,ALL,UX,0        prescribed slide
D,ALL,UY,0
CP,NEXT,UZ,ALL    platen face moves as one
F,<prime>,FZ,-5   compression is along -z
```

The release deletes that load before prescribing the lift, so the two cannot
fight. Because the platen degrees of freedom differ between modes, the mode is
fixed for the life of a model and cannot be changed on a restart.

## Inertia: studying stick-slip instead of avoiding it

A sliding contact loses its quasi-static path long before the interface breaks
out as a whole, and the measured runs say where. In the rigid 5 N reference the
slide divides into two phases:

| Physical time | Slide | Interface | Iterations per substep |
|---|---|---|---|
| 3.000-3.090 | 0-100 um | 1080 of 1080 points sticking, chattering 0 | 2, at the full increment |
| 3.090 onward | 100 um onward | the perimeter slips, then opens | 4 to 24, bisecting to 60 us |

The stick phase converges like a linear problem. The trouble begins at the first
status change, and at that instant the global traction ratio is 0.10 against a
static coefficient of 0.6 — nowhere near breakout. The points that move are the
ones around the rim of the contact, where the gel's free edge takes pressure to
zero and takes `mu * p` with it. Roughly forty micrometres of slide later the
same ring reports

```
Contact element N status changes abruptly from contact -> no-contact
Element M (type = 1, SOLID185) has become highly distorted
```

in that order, and the acceptance check independently reports inactive
macroscopic contact bins.

That ordering is the whole diagnosis. A point that opens releases its stored
elastic energy in a single instant; quasi-statically the rest of the interface
has to absorb it inside the same equilibrium, and the trial displacement that
Newton proposes turns a gel element inside out. Offline Jacobian analysis of the
saved bodies confirms the elements are healthy at every *converged* state — worst
corner ratio 0.91, none below 0.5 — so the distortion exists only in rejected
iterates, and refining the gel mesh does not address it.

Contact stabilization damping, load control, penalty stiffness, penetration
tolerance, pair symmetry and a rigid single-quad target all leave this intact,
because none of them supplies the missing dynamics.

Inertia supplies it. `protocol.transient` turns it on:

```json
"transient": {
  "windows": [
    {"start_time_s": 2.99, "end_time_s": 3.02,
     "time_increment_s": 1e-4, "sample_interval_s": 5e-4,
     "reason": "stick-slip release at slide onset"}
  ],
  "numerical_damping": 0.05
}
```

The analysis becomes `ANTYPE,TRANS` with `TRNOPT,FULL`, and `TIMINT` switches
time integration on inside a window and off everywhere else. `frame_times`
refines to the window's `sample_interval_s`; outside it the declared schedule is
unchanged.

Switch inertia on inside a quiet stretch, not at the event it is there to carry.
The shipped rigid window opens at 3.05, forty milliseconds before the first
contact status change, so the transition into transient and the first slip are
not the same load step.

**`solve_interval_s`.** A checkpoint is one `SOLVE` and one gRPC round trip, and
by default `solve_times` refines to the window's increment — one round trip per
increment, which at 0.1 ms over a quarter second is 2500 of them. A window that
declares `solve_interval_s` keeps the checkpoint grid at that spacing and lets
`DELTIM` subdivide inside each `SOLVE` instead: 5 ms checkpoints over a 0.1 ms
increment is fifty substeps per call, and bisection still reaches 0.5 us. It may
not be finer than `time_increment_s`. Omit it and the old behaviour stands.

### Sizing a window

Two numbers set it, both from the material:

```
shear wave speed   c = sqrt(mu / rho)
thickness transit  T = h / c
```

For this gel — 33.6 kPa shear modulus, 1100 kg/m³, 4 mm thick — that is 5.5 m/s
and 0.72 ms. The increment needs several steps per transit, and the window needs
enough transits for the burst to decay before integration is switched off again.
Switching back while kinetic energy remains records that energy as a static
solution. The shipped window uses 0.1 ms over 30 ms: seven steps per transit,
forty transits.

Integrating the whole protocol at that increment is not an option. Eight seconds
at 0.1 ms is 80,000 steps, weeks of solving, against a few hundred inside the
window.

### What else a window needs

**Mass.** Density only reaches the solver through `MP,DENS`, from
`material.density_kg_m3` on the gel and on the specimen's material case. A
transient window over a model without it integrates no inertia at all, which is
the one thing it exists to provide.

**A velocity that ramps.** A prescribed motion that steps from rest to speed is
an infinite acceleration once mass is present. The slide keyframes accelerate
over 20 ms; at 5 mm/s against a 5 g specimen that costs about 1 mN of inertia,
far below the 5 N contact load, and the slide still covers its declared distance.

**Damping that is not doubled.** `numerical_damping` sets `TINTP` amplitude
decay, which removes the integrator's own high-frequency content. The specimen's
Prony branches already supply physical damping, so no Rayleigh terms are added
on top of them.

### Reading the result

The window is dynamic, and the shared suite declares inertial stick-slip out of
its scope. A setup that opens one is studying that behaviour deliberately and
should say so in its notes.

The measurement is the tangential-to-normal force ratio through the release: it
should rise to the static coefficient as the interface breaks out and settle
toward the kinetic one as sliding establishes. Recorded frames carry
`sliding_contact_points` and `sticking_contact_points`, so the breakout instant
is visible directly — and, as the rigid reference shows, the first points to move
leave stick at a fraction of the static coefficient because they sit where the
pressure is near zero. Read the ratio against the whole interface, and the point
counts to see how far in the slip ring has grown.
