"""Selective, read-only contact records from an ANSYS result file.

This adapter deliberately isolates the reader's internal record pointers. It
reads only the requested contact elements, avoiding solid stress records and a
second in-memory VTK mesh. Cross-check it against MAPDL ETABLE/FSUM after reader
upgrades; recorded element forces retain the precision present in the RST file.
"""

import numpy as np


def two_ints_to_long(low, high):
    """One 64-bit file offset from the two 32-bit words MAPDL stores it in.

    The words arrive as numpy int32, which cannot be masked against 0xFFFFFFFF
    in its own width, so each is widened to a Python integer first.
    """
    return ((int(high) & 0xFFFFFFFF) << 32) | (int(low) & 0xFFFFFFFF)


def allow_large_result_files():
    """Let the reader address result files past a 2 GiB pointer boundary.

    MAPDL keeps each 64-bit file offset as two 32-bit words. The reader
    reassembles them by packing both as unsigned, but reads them from the file
    as signed, so the low word turns negative the moment an offset crosses a
    2 GiB boundary and the pack raises struct.error. A transient slide writes
    every converged substep, so a plane run reaches that point tens of
    gigabytes in - a 100 mm cylinder hit it 30.7 GB and 259 frames into its
    record, with every frame it had already written still perfectly good.

    Masking each word restores the unsigned value the format actually holds.
    It cannot disturb a file that reads correctly today: for a non-negative
    word the mask is the identity, so the only offsets it changes are the ones
    that currently raise.
    """
    from ansys.mapdl.reader import common

    common.two_ints_to_long = two_ints_to_long


class ContactResult:
    def __init__(
        self, path, first_element, count, user_values_per_point=0, nonmisc_base=197
    ):
        from ansys.mapdl.reader.rst import Result

        allow_large_result_files()
        self.result = Result(path, read_mesh=False, parse_vtk=False)
        numbers = self.result._eeqv
        selected = np.flatnonzero(
            (numbers >= first_element) & (numbers < first_element + count)
        )
        self.selected = selected[np.argsort(numbers[selected])]
        if not np.array_equal(
            numbers[self.selected], np.arange(first_element, first_element + count)
        ):
            raise ValueError("Contact element numbering does not match the saved mesh")
        self.count = count
        self.user_values_per_point = user_values_per_point
        self.nonmisc_base = nonmisc_base

    def records(self, step):
        r = self.result
        index = r.parse_step_substep(step)
        header = r._result_solution_header(index)
        offset = int(r._resultheader["rpointers"][index]) + int(header["ptrESL"])
        if header["ptrESL"] <= 0:
            raise ValueError("Contact element results are absent")
        pointers = np.ascontiguousarray(
            r.read_record(offset).view(np.int64)[self.selected]
        )
        values = {}
        for name, table_index, width in (
            ("misc", 0, 49),
            ("force", 1, 12),
            ("nonmisc", 12, self.nonmisc_base + 4 * self.user_values_per_point),
        ):
            raw = r._cfile.read_element_data(pointers, table_index, offset)
            data = np.zeros((self.count, width))
            for i, record in enumerate(raw):
                if record is not None and len(record):
                    if len(record) < width:
                        raise ValueError(
                            f"Unexpected ANSYS contact {name} record length"
                        )
                    data[i] = record[:width]
            values[name] = data
        return values

    def details(self, records):
        m, n = records["misc"], records["nonmisc"]
        details = {
            "pressure": m[:, :4].copy(),
            "penetration": n[:, 8:12].copy(),
            "status": n[:, :4].copy(),
            "slip_r": n[:, 16:20].copy(),
            "slip_s": n[:, 20:24].copy(),
            "elastic_slip": n[:, 135:139].copy(),
            "plastic_slip": n[:, 163:167].copy(),
        }

        if self.user_values_per_point:
            user = n[:, self.nonmisc_base :].reshape(
                self.count, 4, self.user_values_per_point
            )
            details.update(
                repulsive_pressure=user[:, :, 5].copy(),
                elastic_slip=user[:, :, 6].copy(),
                friction_coefficients=user[:, :, 7:9].copy(),
                attached_fraction=user[:, :, 9].copy(),
                plastic_slip_vector=user[:, :, 12:14].copy(),
                cumulative_dissipation_j_m2=user[:, :, 14].copy(),
                point_area_m2=user[:, :, 15].copy(),
                point_position_m=user[:, :, 16:19].copy(),
                reference_point_xy_m=user[:, :, 19:21].copy(),
                point_solver_time_s=user[:, :, 21].copy(),
            )
        return details

    def contact_forces(self, records, quads, node_count):
        forces = np.zeros((node_count, 3))
        values = records["force"].reshape(-1, 4, 3)
        for corner in range(4):
            np.add.at(forces, quads[:, corner], values[:, corner, :])
        return forces
