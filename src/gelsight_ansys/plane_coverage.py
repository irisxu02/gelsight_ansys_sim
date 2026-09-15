"""Material-coordinate bin integration and conservative footprint coverage checks."""

import numpy as np


class ContactCoverage:
    def __init__(self, case, reference, quads):
        self.case, self.quads = case, quads
        self.rules = case.suite["contact_acceptance"]
        bin_size = self.rules["macroscopic_contact_bins"]["size_xy_m"]
        minimum, maximum = reference[:, :2].min(axis=0), reference[:, :2].max(axis=0)
        edges = [
            np.r_[np.arange(lo, hi - 1e-12, step), hi]
            for lo, hi, step in zip(minimum, maximum, bin_size)
        ]
        self.edges = edges
        self.shape = (len(edges[1]) - 1, len(edges[0]) - 1)
        records = []
        for face, ids in enumerate(quads):
            xy = reference[ids, :2]
            x0, y0 = xy.min(axis=0)
            x1, y1 = xy.max(axis=0)
            # Reference faces are rectangles; splitting at bin boundaries makes
            # quadrature integrate clipped bins instead of assigning face centers.
            ix0 = max(0, np.searchsorted(edges[0], x0, side="right") - 1)
            iy0 = max(0, np.searchsorted(edges[1], y0, side="right") - 1)
            ix1 = min(self.shape[1] - 1, np.searchsorted(edges[0], x1, side="left") - 1)
            iy1 = min(self.shape[0] - 1, np.searchsorted(edges[1], y1, side="left") - 1)
            for iy in range(iy0, iy1 + 1):
                for ix in range(ix0, ix1 + 1):
                    u0, u1 = (
                        (max(x0, edges[0][ix]) - x0) / (x1 - x0),
                        (min(x1, edges[0][ix + 1]) - x0) / (x1 - x0),
                    )
                    v0, v1 = (
                        (max(y0, edges[1][iy]) - y0) / (y1 - y0),
                        (min(y1, edges[1][iy + 1]) - y0) / (y1 - y0),
                    )
                    for a in (-1 / np.sqrt(3), 1 / np.sqrt(3)):
                        for b in (-1 / np.sqrt(3), 1 / np.sqrt(3)):
                            u, v = (
                                (u0 + u1 + a * (u1 - u0)) / 2,
                                (v0 + v1 + b * (v1 - v0)) / 2,
                            )
                            records.append(
                                (
                                    face,
                                    iy * self.shape[1] + ix,
                                    u,
                                    v,
                                    (u1 - u0) * (v1 - v0) / 4,
                                )
                            )
        records = np.asarray(records)
        a = 1 / np.sqrt(3)
        u_gp = (1 + np.array([-a, a, a, -a])) / 2
        v_gp = (1 + np.array([-a, -a, a, a])) / 2
        shape_gp = np.column_stack(
            ((1 - u_gp) * (1 - v_gp), u_gp * (1 - v_gp), u_gp * v_gp, (1 - u_gp) * v_gp)
        )
        self.gauss_to_corner = np.linalg.inv(shape_gp)
        self.region = self.gated_region()
        # Reject an unreadable setting once, here, rather than on every substep.
        self.scope
        self.face, self.bin = records[:, 0].astype(int), records[:, 1].astype(int)
        u, v = records[:, 2], records[:, 3]
        self.weights = records[:, 4]
        self.N = np.column_stack(((1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v))
        self.du = np.column_stack((v - 1, 1 - v, v, -v))
        self.dv = np.column_stack((u - 1, -u, u, 1 - u))

    @property
    def scope(self):
        """Which sensor nodes the geometry acceptance thresholds speak for."""
        name = self.rules.get("edge_margin_scope", "all_sensor_nodes")
        if name not in ("all_sensor_nodes", "loaded_sensor_nodes"):
            raise ValueError(f"Unknown edge margin scope: {name}")
        return name

    def gated_region(self):
        """Which bins the activity requirement is allowed to fail on.

        Pressing loads a free-edged gel almost uniformly, so demanding that every
        bin of the whole outer surface carry load is a fair statement that the
        specimen is seated. Sliding is not like that. The gel's free corners
        already sit near zero pressure, and a slide takes them through it into
        separation while the rest of the footprint stays well seated - not by
        tilting the pressure field, which stays symmetric because the platen's
        rotational constraint takes the moment, but locally at the rim, where the
        slip ring also nucleates. Those corners lie outside the camera's field of
        view, so they are in no frame the dataset holds, and no threshold rescues
        them: the weakest bin decays smoothly through several decades.

        A setup that slides therefore declares which region the requirement
        speaks for. Both fractions are recorded either way.
        """
        rules = self.rules["macroscopic_contact_bins"]
        name = rules.get("region", "whole_sensor_surface")
        if name == "whole_sensor_surface":
            return None
        if name != "camera_field_of_view":
            raise ValueError(f"Unknown macroscopic contact bin region: {name}")
        camera = self.case.suite["sensor"]["camera"]
        centres = [(e[:-1] + e[1:]) / 2 for e in self.edges]
        half = (camera["fov_width_m"] / 2, camera["fov_height_m"] / 2)
        centre = (camera["center_x_m"], camera["center_y_m"])
        inside = [
            np.abs(c - o) <= h for c, o, h in zip(centres, centre, half)
        ]
        return inside[1][:, None] & inside[0][None, :]

    def evaluate(self, state, details, pose, object_mesh=None, object_displacement=None):
        corners = state.position_m[self.quads[self.face]]
        du = np.einsum("pi,pij->pj", self.du, corners)
        dv = np.einsum("pi,pij->pj", self.dv, corners)
        jac = np.linalg.norm(np.cross(du, dv), axis=1)
        nodal_pressure = details["pressure"] @ self.gauss_to_corner.T
        pressure = np.einsum("pi,pi->p", self.N, nodal_pressure[self.face])
        adhesion = self.case.case["contact"]["adhesion"]
        if adhesion["model"] != "none":
            penetration = np.einsum("pi,pi->p", self.N, details["penetration"][self.face])
            near = np.any(details["status"][self.face] > 0, axis=1)
            attached = np.clip(1 + penetration / adhesion["cutoff_gap_m"], 0, 1) * near
            pressure += attached * adhesion["tensile_strength_pa"]
        repulsive = np.maximum(pressure, 0)
        loads = np.bincount(
            self.bin,
            weights=repulsive * jac * self.weights,
            minlength=np.prod(self.shape),
        ).reshape(self.shape)
        if "repulsive_pressure" in details:
            xy = details["reference_point_xy_m"]
            ix = np.clip(
                np.searchsorted(self.edges[0], xy[:, :, 0], side="right") - 1,
                0,
                self.shape[1] - 1,
            )
            iy = np.clip(
                np.searchsorted(self.edges[1], xy[:, :, 1], side="right") - 1,
                0,
                self.shape[0] - 1,
            )
            pressure = np.where(details["status"] > 0, details["repulsive_pressure"], 0)
            weights = np.maximum(pressure, 0) * details["point_area_m2"]
            loads = np.bincount(
                (iy * self.shape[1] + ix).ravel(),
                weights=weights.ravel(),
                minlength=np.prod(self.shape),
            ).reshape(self.shape)
        half_width, half_length = self.case.target_half_extents()
        left, right = pose.x_m - half_width, pose.x_m + half_width
        bottom, top = pose.y_m - half_length, pose.y_m + half_length
        if object_mesh is not None:
            original = object_mesh.coordinates
            current = original + object_displacement
            surface = np.unique(object_mesh.surface_quads)
            original, current = original[surface], current[surface]
            left = current[
                np.isclose(original[:, 0], original[:, 0].min(), atol=1e-12), 0
            ].max()
            right = current[
                np.isclose(original[:, 0], original[:, 0].max(), atol=1e-12), 0
            ].min()
            bottom = current[
                np.isclose(original[:, 1], original[:, 1].min(), atol=1e-12), 1
            ].max()
            top = current[
                np.isclose(original[:, 1], original[:, 1].max(), atol=1e-12), 1
            ].min()
        xy = state.position_m[:, :2]
        margins = np.minimum.reduce(
            (xy[:, 0] - left, right - xy[:, 0], xy[:, 1] - bottom, top - xy[:, 1])
        )
        threshold = self.rules["macroscopic_contact_bins"]["minimum_bin_force_n"]
        # A target smaller than the sensor - a cylinder lying across it - cannot
        # cover the gel and is not meant to. What still has to hold is that the
        # load it does carry stays well inside the target, which is the same
        # statement measured where there is load to measure it on. Both are
        # recorded whichever one the setup gates on, so neither key changes its
        # meaning between runs. Repulsive contact pushes the gel along -z.
        carrying = -np.asarray(state.contact_force_n)[:, 2] > threshold
        loaded = margins[carrying] if carrying.any() else margins
        active = loads > threshold
        record = {
            "minimum_bin_repulsive_force_n": float(loads.min()),
            "total_repulsive_force_n": float(loads.sum()),
            "active_bin_fraction": float(active.mean()),
            # The gated region's own numbers, so the whole-surface figures above
            # stay comparable with every run that came before.
            "gated_bin_region": self.rules["macroscopic_contact_bins"].get(
                "region", "whole_sensor_surface"
            ),
            "gated_active_bin_fraction": float(
                active.mean() if self.region is None else active[self.region].mean()
            ),
            "gated_minimum_bin_repulsive_force_n": float(
                loads.min() if self.region is None else loads[self.region].min()
            ),
            "minimum_plane_edge_margin_m": float(margins.min()),
            "geometric_footprint_coverage_fraction": float(np.mean(margins >= 0)),
            "loaded_edge_margin_m": float(loaded.min()),
            "loaded_footprint_coverage_fraction": float(np.mean(loaded >= 0)),
            "loaded_sensor_node_count": int(carrying.sum()),
            "edge_margin_scope": self.scope,
            "bin_count": int(loads.size),
            "pressure_integration": "bilinear reconstruction of ANSYS Gauss pressure, integrated on each material-bin intersection",
            "footprint_bound": "conservative rectangle inside the current target footprint, which for a curved target is the patch cut from it",
        }
        if "repulsive_pressure" in details:
            record["pressure_integration"] = (
                "ANSYS detection-point repulsive pressure times integration area, binned at fixed sensor material coordinates"
            )
        return record, loads

    def geometry_gate(self, record):
        """The margin and coverage the acceptance thresholds speak for.

        A setup that declares a target smaller than the sensor gates on what the
        load sits inside; every other setup gates on the whole surface, as they
        always have. Records written before either key existed carry only the
        whole-surface pair, which is what they were judged on.
        """
        if self.scope == "loaded_sensor_nodes":
            return (
                record.get("loaded_edge_margin_m", record["minimum_plane_edge_margin_m"]),
                record.get(
                    "loaded_footprint_coverage_fraction",
                    record["geometric_footprint_coverage_fraction"],
                ),
                "the loaded region",
            )
        return (
            record["minimum_plane_edge_margin_m"],
            record["geometric_footprint_coverage_fraction"],
            "the sensor",
        )

    def validate(self, record, *, require_contact=True):
        r = self.rules
        failures = []
        if (
            require_contact
            # Records written before the region was declarable carry only the
            # whole-surface fraction, which is what they were gated on.
            and record.get("gated_active_bin_fraction", record["active_bin_fraction"])
            < r["macroscopic_contact_bins"]["required_active_bin_fraction"]
        ):
            failures.append("inactive macroscopic contact bins")
        if (
            require_contact
            and record["total_repulsive_force_n"]
            <= r["minimum_total_repulsive_normal_force_n"]
        ):
            failures.append("insufficient repulsive normal force")
        margin, coverage, scope = self.geometry_gate(record)
        if coverage < r["minimum_geometric_footprint_coverage_fraction"]:
            failures.append(f"specimen does not cover {scope}")
        if margin < r["minimum_plane_edge_margin_m"]:
            failures.append(f"insufficient specimen edge margin over {scope}")
        if failures:
            raise RuntimeError("Plane contact acceptance failed: " + ", ".join(failures))
