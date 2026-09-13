# Getting started

[Documentation](README.md) · [Usage](usage.md)

## Requirements and platform scope

| Component | Requirement |
|---|---|
| Python | 3.11 or later; the documented environments use 3.13 |
| Mechanics | Separately installed ANSYS MAPDL 2025 R2 and a configured license |
| GPU mechanics | NVIDIA GPU supported by ANSYS, or an explicit device override for evaluation |
| GPU optics | NVIDIA driver and the `gpu` package extra, which installs Warp |
| CPU tests and optical replay | No ANSYS installation or license required |

The licensed solver results in this repository were obtained with Windows
MAPDL. CPU tests can run on Linux. Native Linux mechanics uses the existing
`--exec-file` option and requires a Linux MAPDL installation; that combination
has not been verified with a licensed native Linux installation.

## Linux environment with pyenv

Install [pyenv](https://github.com/pyenv/pyenv#installation) and the
[platform-specific build dependencies](https://github.com/pyenv/pyenv/wiki#suggested-build-environment).
For a new Git-based installation, the following initializes pyenv in the current
Bash session:

```bash
git clone https://github.com/pyenv/pyenv.git "$HOME/.pyenv"
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - bash)"
```

Skip the clone if pyenv is already installed. Follow pyenv's shell setup
instructions to make initialization persistent. From the repository root:

```bash
pyenv install -s 3.13
pyenv local 3.13
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[gpu]"
python scripts/run_simulation.py --help
```

`pyenv install 3.13` selects the latest 3.13 release known to the installed pyenv
installation. `pyenv local` records the selected version in `.python-version`;
`.venv` isolates project dependencies. For a CPU-only environment, install
`python -m pip install -e .` instead of the GPU extra.

Install from `pyproject.toml` on Linux. `requirements-lock.txt` is the recorded
Windows environment and includes Windows-oriented dependencies.

## Windows environment

In PowerShell, from the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe scripts\run_simulation.py --help
```

Use `.\.venv\Scripts\python.exe` wherever the usage guide shows `python`.
Alternatively, activate the environment with `.\.venv\Scripts\Activate.ps1`.

## ANSYS executable and license

The ANSYS client requires the institution's or organization's license settings
before running mechanics. Python dependencies do not install ANSYS or
provide a license. Keep local registration and server details out of the presets.

On Windows, the launcher uses `AWP_ROOT252` or the standard installation directory.
On Linux, pass the complete executable path because the project launcher otherwise
constructs a Windows path:

```bash
python scripts/run_simulation.py run \
  --config configs/sphere_press.json \
  --exec-file /usr/ansys_inc/v252/ansys/bin/ansys252
```

The path follows the [official PyMAPDL Linux layout](https://mapdl.docs.pyansys.com/version/stable/getting_started/launcher.html#setting-the-mapdl-location-in-pymapdl).
Set the path to the installed MAPDL executable. Keep the executable version
consistent with `solver.version` in the configuration.

### WSL with Windows ANSYS

Pyenv inside WSL installs Linux Python. For an ANSYS installation on Windows,
invoke Windows Python and use a Windows executable path. For example, from a
repository checkout on a Windows-mounted drive:

```bash
./.venv/Scripts/python.exe scripts/run_simulation.py run \
  --config configs/sphere_press.json \
  --exec-file 'C:\Program Files\ANSYS Inc\v252\ansys\bin\winx64\ANSYS252.exe' \
  --allow-unlisted-gpu
```

This example assumes `.venv` was created with Windows Python. A Linux environment
instead contains `.venv/bin/python`; use separate directories if maintaining both.

## GPU configuration

The default allocation is four CPU cores for ANSYS mechanics, with CUDA surface
projection, camera sampling and rendering. It was faster than solver GPU
offloading on the tested workstation. See [performance](performance.md).

Select `--solver-gpu` for three CPU solver cores plus one NVIDIA GPU. If ANSYS
recognizes but rejects the device, also add `--allow-unlisted-gpu`; this sets
`ANSGPU_OVERRIDE=1` only in its child process. The tested RTX 4070 SUPER requires
that override for ANSYS offloading, but not for CUDA optics/projection.

GPU mechanics and CUDA optics are checked separately. In `--solver-gpu` mode the
sparse solver must report GPU activation and positive accelerated work. The
default CPU mechanics mode records solver GPU activity as false while recording
CUDA projection and rendering separately. [GPU execution](verification.md#gpu-execution).

`--cpu` explicitly selects CPU mechanics, projection and rendering. It still
requires ANSYS for a new solve; optical replay with `--backend cpu` does not.

## Check the environment

These commands run without starting ANSYS:

```bash
python scripts/run_simulation.py --help
python -B -m unittest discover -s tests -v
```

[Usage](usage.md) documents solving, optical replay, and configuration.
[Verification](verification.md) defines licensed numerical checks.
