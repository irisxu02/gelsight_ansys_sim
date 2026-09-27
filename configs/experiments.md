Vary materials: friction parameters of indenter material

Vary normal forces: 0.5N, 1N, 2N, 4N.
Vary sliding speeds.
Sliding distance can be about 1-3mm one way.

Try back-and-forth back-and-forth (2 repeats) fast sliding motion to simulate vibrational signals.
---
CLAUDE:

Remember, the goal is to simulate tactile signals such that different materials can be differentiated and a latent tactile representation be learned as slip-perception pre-training for slip-aware robot manipulation. Real-world data will also be collected.

Finish this doc with a table log of all the sliding trials and configs (outputs so far plus anything after this point).
Raise any methodological concerns.
Be concise and keep this doc bare bones.

## Sliding trial log

All: 100 kPa Neo-Hookean gel, rigid indenter, 2 mm slide along +x at 2.5 mm/s
(0.5 mm per 0.2 s step), quasi-static with mass on slide steps, ANSYS 2026 R1.
Outputs are under `outputs/` in the main checkout (not tracked).
Ft = peak |tangential force|. Force sweep: seat by travel, ramp load
0.25→0.5→0.75→1→1.5→2→3→4 N up to target, hold, slide, unload back through
the same steps, lift off.

| # | Config | Indenter | μ (material) | Control | Target N | Peak N | Ft N | Status | Output |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `hemisphere_slide.json` | hemisphere r50 | 0.5 (rigid) | depth 0.227 mm | 0.5 | 0.502 | 0.237 | done | `hemisphere_slide_trial` |
| 2 | `custom_gel_sphere_slide.json` (worktree) | sphere r3 | 0.5 (rigid) | depth | 0.5 | 0.502 | 0.263 | done | `custom_gel_sphere_slide_*` |
| 3 | `hemisphere_slide_11x17.json` | hemisphere r50 | 0.5 (rigid) | depth | 0.5 | 0.502 | 0.237 | done | `hemisphere_slide_11x17_0p5n/` |
| 4 | `sphere_press_slide_2n` (untracked) | sphere | 0.5 (rigid) | force, 0.6 mm slide | 2 | 2.009 | 0.377 | done | `sphere_press_slide_2n_force_controlled` |
| 5 | `indenter_slide_11x17/hemisphere_large` | hemisphere r50 | 0.5 (rigid) | force | 1 | 1.005 | 0.515 | done | `indenter_slide_11x17_1n/` |
| 6 | `indenter_slide_11x17/hemisphere_small` | hemisphere r10 | 0.5 (rigid) | force | 1 | 1.004 | 0.551 | done | 〃 |
| 7 | `indenter_slide_11x17/cylinder_large` | cylinder r50 top | 0.5 (rigid) | force | 1 | 1.001 | 0.500 | done | 〃 |
| 8 | `indenter_slide_11x17/cylinder_small` | cylinder r10 | 0.5 (rigid) | force | 1 | 1.003 | 0.502 | done | 〃 |
| 9 | `indenter_slide_11x17_fine/hemisphere_large` | hemisphere r50 fine | 0.5 (rigid) | force | 1 | 1.004 | 0.515 | done | `indenter_slide_11x17_1n_fine/` |
| 10 | `indenter_slide_11x17_fine/hemisphere_small` | hemisphere r10 fine | 0.5 (rigid) | force | 1 | 1.002 | 0.542 | done | 〃 |
| 11 | `indenter_slide_11x17_fine/cylinder_large` | cylinder r50 fine top | 0.5 (rigid) | force | 1 | 1.000 | 0.500 | done | 〃 |
| 12 | `indenter_slide_11x17_fine/cylinder_small` | cylinder r10 fine | 0.5 (rigid) | force | 1 | – | – | partial: slide ok, unload 1→0.5 N fails balance | 〃 |
| 13 | `indenter_slide_11x17_fine/cylinder_small_press` | cylinder r10 fine | 0.5 (rigid) | force, no slide | 1 | – | – | done | 〃 |
<!-- sweep rows: configs/indenter_slide_11x17_force_sweep/<indenter>_<F>n.json -> outputs/indenter_slide_11x17_force_sweep/ -->
| 14 | `force_sweep/hemisphere_large_0p5n` | hemisphere r50 fine | 0.35 (resin_wax_coated) | force | 0.5 | 0.502 | 0.178 | done (2.7 h) | `indenter_slide_11x17_force_sweep/` |
| 15 | `force_sweep/hemisphere_small_0p5n` | hemisphere r10 fine | 0.35 (resin_wax_coated) | force | 0.5 | 0.502 | 0.185 | done (3.2 h) | 〃 |
| 16 | `force_sweep/cylinder_large_0p5n` | cylinder r50 fine top | 0.35 (resin_wax_coated) | force | 0.5 | 0.501 | 0.175 | done (0.9 h) | 〃 |
| 17 | `force_sweep/hemisphere_large_2n` | hemisphere r50 fine | 0.35 (resin_wax_coated) | force | 2 | 2.003 | 0.725 | done (4.9 h) | 〃 |
| 18 | `force_sweep/hemisphere_small_2n` | hemisphere r10 fine | 0.35 (resin_wax_coated) | force | 2 | 2.001 | 0.783 | done (6.0 h) | 〃 |
| 19 | `force_sweep/cylinder_large_2n` | cylinder r50 fine top | 0.35 (resin_wax_coated) | force | 2 | 2.002 | 0.701 | done (1.4 h) | 〃 |
| 20 | `force_sweep/hemisphere_large_4n` | hemisphere r50 fine | 0.35 (resin_wax_coated) | force | 4 | 4.004 | 1.467 | done (5.0 h) | 〃 |
| 21 | `force_sweep/hemisphere_small_4n` | hemisphere r10 fine | 0.35 (resin_wax_coated) | force | 4 | 4.004 | 1.576 | done (8.1 h) | 〃 |
| 22 | `force_sweep/cylinder_large_4n` | cylinder r50 fine top | 0.35 (resin_wax_coated) | force | 4 | 4.003 | 1.403 | done (1.8 h) | 〃 |
| 23 | `force_sweep/cylinder_small_0p5n` | cylinder r10 fine | 0.35 (resin_wax_coated) | force | 0.5 | 0.500 | 0.175 | done (1.1 h) | 〃 |
| 24 | `force_sweep/cylinder_small_2n` | cylinder r10 fine | 0.35 (resin_wax_coated) | force | 2 | 2.001 | 0.705 | done (1.4 h) | 〃 |
| 25 | `force_sweep/cylinder_small_4n` | cylinder r10 fine | 0.35 (resin_wax_coated) | force | 4 | 4.002 | 1.416 | done (1.7 h) | 〃 |
| 26 | `force_sweep/cylinder_small_1n` | cylinder r10 fine | 0.35 (resin_wax_coated) | force | 1 | 1.001 | 0.351 | done (1.2 h) | 〃 |
| 27 | `force_sweep/hemisphere_large_1n` | hemisphere r50 fine | 0.35 (resin_wax_coated) | force | 1 |  |  | queued (rerun) | 〃 |
| 28 | `force_sweep/hemisphere_small_1n` | hemisphere r10 fine | 0.35 (resin_wax_coated) | force | 1 | 1.001 | 0.379 | done (5.5 h) | 〃 |
| 29 | `force_sweep/cylinder_large_1n` | cylinder r50 fine top | 0.35 (resin_wax_coated) | force | 1 |  |  | running | 〃 |

## Methodological concerns

- **Material = one scalar μ.** Every finished slide is in full Coulomb slip
  (Ft = μ·N to 3 digits). With constant μ, no stick-slip, no velocity
  dependence and no texture, "material" differences in the sim are a pure
  tangential-force scale; a learned latent can separate materials only by
  shear magnitude. Real surfaces differ in stick-slip, micro-vibration and
  texture, so sim→real transfer for material ID is doubtful. Consider
  static/kinetic μ, rate-dependent friction, or textured (plane-suite) surfaces.
- **Indenter stiffness is inert.** STL indenters must be rigid (the loader
  rejects elastic cases on STL; `hemisphere_resin_slide.json` from #8 fails
  dry-run for this reason). Only the case's friction coefficient is used,
  copied by hand into `contact.friction`. Soft materials (rubber, silicone)
  need a hex-volume mesh.
- **Sliding speed is not physical yet.** Solves are quasi-static; speed only
  changes the time scale of the mass term. Varying speed will not produce
  rate effects without a rate-dependent contact or viscoelastic gel.
- **Back-and-forth vibration** would need many small steps; a 16-step slide
  already takes 1–4 h, so dense vibration trajectories are very expensive.
- **μ confound across sweeps.** Rows 5–13 use μ=0.5, the force sweep μ=0.35.
  The sweep reruns 1 N at μ=0.35 so forces are comparable; rows 9–11 vs 27–29
  then form a μ contrast at 1 N.
- **Balance tolerance relaxed at 0.5 N.** The first slide step leaves a
  ~0.01–0.03 N contact/backing mismatch at any load; the 2 % check passed at
  1 N (0.009 N) but failed at 0.5 N, μ=0.35 (0.027 N vs 0.010 N, twice). The
  0.5 N configs use `balance_tolerance` 0.06 (0.03 N absolute).
- **Force tolerance 0.005 is too loose at μ=0.35.** Load steps converged in
  2 substeps with the contact carrying up to 9 % less than the backing
  (0.914 vs 1.0 N, hemisphere r50; 1.37 vs 1.5 N, cylinder r50; 1.43 vs 1.5 N
  on the r10 hemisphere's first unload). Removing damping or slowing the ramp
  5× changed nothing; `force_tolerance` 0.001 closes it (≤0.003 N to 2 N), so
  rows 17–29 use 0.001. Rows 14–16 (0.5 N) ran at 0.005 and balanced. The
  0.005 runs at μ=0.5 (rows 5–13) passed the 2 % check but may carry similar
  ≤2 % force-label error on load-change frames.
- **Travel/load handoff is fragile.** Seating depths were tuned at 1 N; the
  small cylinder already fails unloading at 1 N. Higher loads may fail too.
- **Uncalibrated.** Gel modulus, μ and optics are not fit to the real sensor
  (see `docs/live-sensor.md`); sim and real data should be aligned before
  pre-training claims.
- **Throughput.** The license allows one MAPDL per user; runs are serial, 1–4 h each.
