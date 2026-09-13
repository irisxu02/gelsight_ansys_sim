"""Solver-independent surface state. Coordinates and forces are in SI units."""

from dataclasses import dataclass

import numpy as np


@dataclass
class SurfaceState:
    time_s: float
    reference_m: np.ndarray
    displacement_m: np.ndarray
    triangles: np.ndarray
    quads: np.ndarray
    contact_force_n: np.ndarray
    contact_pressure_pa: np.ndarray
    contact_status: np.ndarray
    contact_penetration_m: np.ndarray
    backing_reaction_n: np.ndarray
    pilot_reaction_n: np.ndarray
    pilot_moment_nm: np.ndarray
    pilot_position_m: np.ndarray
    node_ids: np.ndarray
    source: str = "ansys"
    load_step: int = 0
    substep: int = 0
    contact_elastic_slip_m: np.ndarray | None = None
    contact_integration_status: np.ndarray | None = None
    contact_couple_nm: np.ndarray | None = None

    def __post_init__(self):
        if self.contact_couple_nm is None:
            self.contact_couple_nm = np.zeros_like(self.contact_force_n)
        # Empty arrays distinguish historical data from measured zero slip.
        if self.contact_elastic_slip_m is None:
            self.contact_elastic_slip_m = np.empty((0, 4))
        if self.contact_integration_status is None:
            self.contact_integration_status = np.empty((0, 4))

    @property
    def position_m(self):
        return self.reference_m + self.displacement_m

    def save(self, path):
        np.savez_compressed(path, **self.__dict__)

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as arrays:
            data = {key: arrays[key] for key in arrays.files}
        data["time_s"] = float(data["time_s"])
        data["source"] = str(data["source"])
        for key in ("load_step", "substep"):
            data[key] = int(data.get(key, 0))
        return cls(**data)
