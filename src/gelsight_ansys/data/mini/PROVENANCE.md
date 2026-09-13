# Example GelSight Mini optical response

The marker-free background and polynomial coefficients are from the public
[MMintLab sensor assets](https://github.com/MMintLab/hydroshear/tree/f815b82fdf3451852acd918933020a82cede1f3b/rl/tacsl_sensors/tactile_utils/gs_mini_data)
at commit `f815b82fdf3451852acd918933020a82cede1f3b`. The upstream MIT license
is retained in [LICENSE](LICENSE). Background pixels were preserved; PNG
metadata was removed. Coefficient arrays are unchanged.

These assets characterize an example sensor; **device-specific calibration is required**.
They supply the default appearance only; `calibrated` remains false.
The 320-row by 240-column background and calibration coordinates are rotated
90 degrees clockwise into this project's 320-column by 240-row image.
The table has 125 by 125 bins, with the upstream 120-bin angular convention.

This implementation evaluates the table from ANSYS surface normals. Before
rendering, it averages the equivalent azimuth endpoints, removes each direction's
zero-tilt offset, and smooths the coefficient table with a Gaussian of default
sigma 2 angular bins (about 1.5 degrees in tilt and 6 degrees in azimuth).
Azimuth filtering is periodic. A final flat-offset correction preserves the
unloaded background exactly. Cubic interpolation gives continuous gradients
across angular bins, including the azimuth seam.

`optics.response_smoothing_bins` controls the table smoothing; zero disables that
filter. This regularization and the default response gain of 2 are documented
appearance choices, not new calibration measurements. They do not smooth or
change the ANSYS displacement, force fields, or marker material coordinates.
Markers are composed separately after shading.

Polynomial response follows the [TAXIM method](https://arxiv.org/abs/2109.04027).
No mechanics, displacement spreading, or geometry corrections are taken from
these optical assets.
