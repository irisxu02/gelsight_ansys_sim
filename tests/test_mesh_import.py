"""Imported geometry, fixture, command numbering, and portable replay contracts."""

import copy
import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from gelsight_ansys.cli import main
from gelsight_ansys.config import Config
from gelsight_ansys.imported_mechanics import AnsysImported
from gelsight_ansys.mesh_import import import_mesh, read_obj, read_stl
from gelsight_ansys.pipeline import rerender

ROOT = Path(__file__).resolve().parents[1]


class MeshImportTests(unittest.TestCase):
    def source(self, soft=False):
        path = ROOT / (
            "configs/imported_soft_press.json"
            if soft
            else "configs/imported_rigid_press.json"
        )
        return json.loads(path.read_text())

    def config(self, soft=False):
        return Config.from_dict(self.source(soft), ROOT / "configs")

    def test_ascii_and_binary_stl_agree_including_solid_binary_header(self):
        raw = (ROOT / "assets/meshes/block_surface.stl").read_bytes()
        ascii_mesh = read_stl(raw)
        xyz = np.asarray(ascii_mesh["vertices"])
        binary = b"solid header".ljust(80, b" ") + struct.pack(
            "<I", len(ascii_mesh["faces"])
        )
        for face in ascii_mesh["faces"]:
            binary += struct.pack("<12fH", 0, 0, 0, *xyz[face].ravel(), 0)
        self.assertEqual(read_stl(binary), ascii_mesh)
        with self.assertRaises((ValueError, UnicodeError)):
            read_stl(binary[:-1])

    def test_obj_accepts_quads_and_negative_indices_without_loading_material_files(self):
        data = read_obj(
            b"mtllib ignored.mtl\nv 0 0 0\nv 0 1 0\nv 1 1 0\nv 1 0 0\nf -4/1 -3/2 -2/3 -1/4\n"
        )
        self.assertEqual(data["faces"], [[0, 1, 2, 3]])
        with self.assertRaisesRegex(ValueError, "index"):
            read_obj(b"v 0 0 0\nf 0 1 2\n")
        with self.assertRaisesRegex(ValueError, "triangles or quads"):
            read_obj(b"f 1 2 3 4 5\n")

    def test_unit_conversion_rotation_and_reference_point(self):
        g = self.source()["object"]["geometry"]
        g["transform"] = {
            "translation_m": [0.002, -0.001, 0.0001],
            "rotation_xyzw": [0, 0, np.sqrt(0.5), np.sqrt(0.5)],
        }
        g["reference_point_m"] = [0.002, -0.001, 0.0021]
        m = import_mesh(g, ROOT / "configs", False)
        xyz = np.asarray(m.coordinates_m)
        np.testing.assert_allclose(np.ptp(xyz, axis=0), [0.003, 0.003, 0.002])
        np.testing.assert_allclose(xyz.mean(axis=0), [0.002, -0.001, 0.0011])
        self.assertEqual(m.reference_point_m, (0.002, -0.001, 0.0021))
        self.assertEqual(m.source_name, "block_surface.stl")
        self.assertNotIn(str(ROOT), json.dumps(self.config().to_dict()))

    def test_volume_has_explicit_boundary_sets_and_portable_roundtrip(self):
        c = self.config(True)
        self.assertEqual(len(c.imported_mesh.hexes), 48)
        self.assertEqual(len(c.imported_mesh.grip_nodes), 25)
        self.assertEqual(
            Config.from_dict(json.loads(json.dumps(c.to_dict()))).to_dict(), c.to_dict()
        )
        xyz = np.asarray(c.imported_mesh.coordinates_m)
        self.assertAlmostEqual(xyz[list(c.imported_mesh.grip_nodes), 2].min(), 0.0021)

    def test_saved_configuration_survives_source_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = tmp / "object.stl"
            source.write_bytes((ROOT / "assets/meshes/block_surface.stl").read_bytes())
            data = self.source()
            data["object"]["geometry"]["file"] = "object.stl"
            data["object"]["material"] = {"model": "rigid"}
            config = Config.from_dict(data, tmp)
            (tmp / "config.json").write_text(json.dumps(config.to_dict()))
            source.unlink()
            self.assertEqual(Config.load(tmp / "config.json").to_dict(), config.to_dict())

    def test_detached_snapshot_embeds_external_mesh_before_source_changes(self):
        from gelsight_ansys.batch.snapshot_meshes import freeze_mesh_inputs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, snapshot = root / "source", root / "snapshot"
            (source / "configs").mkdir(parents=True)
            external = root / "outside.stl"
            external.write_bytes((ROOT / "assets/meshes/block_surface.stl").read_bytes())
            data = self.source()
            data["object"]["geometry"]["file"] = "../../outside.stl"
            data["object"]["material"] = {"model": "rigid"}
            (source / "configs/case.json").write_text(json.dumps(data))
            expected = Config.load(source / "configs/case.json")
            self.assertEqual(freeze_mesh_inputs(source, snapshot), ["configs/case.json"])
            external.unlink()
            self.assertEqual(
                Config.load(snapshot / "configs/case.json").to_dict(), expected.to_dict()
            )

    def test_invalid_transform_and_placement_are_rejected(self):
        for changes in (
            {"units": "inch"},
            {
                "transform": {
                    "translation_m": [0, 0, 0.0001],
                    "rotation_xyzw": [0, 0, 0, 2],
                }
            },
            {"clearance_m": 0.001},
        ):
            data = self.source()
            data["object"]["geometry"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Config.from_dict(data, ROOT / "configs")

    def test_surface_topology_rejects_degenerate_duplicate_and_inward_faces(self):
        mesh = self.config().imported_mesh
        for faces in (
            (mesh.faces[0], mesh.faces[0]),
            ((0, 0, 1),),
            tuple(tuple(reversed(f)) for f in mesh.faces),
        ):
            with self.subTest(faces=faces), self.assertRaises(ValueError):
                replace(mesh, faces=faces).validate(False, 0.0001)

    def test_volume_rejects_inversion_internal_contact_and_invalid_grip(self):
        mesh = self.config(True).imported_mesh
        cells = copy.deepcopy(list(map(list, mesh.hexes)))
        cells[0][0], cells[0][1] = cells[0][1], cells[0][0]
        changes = [
            {"hexes": cells},
            {
                "faces": (
                    (
                        mesh.hexes[0][4],
                        mesh.hexes[0][5],
                        mesh.hexes[0][6],
                        mesh.hexes[0][7],
                    ),
                )
            },
            {"grip_nodes": (0, 1)},
            {"grip_nodes": (0, 1, 2)},
            {"grip_nodes": (0.0, 1.0, 2.0)},
        ]
        for change in changes:
            with self.subTest(change=change.keys()), self.assertRaises(ValueError):
                replace(mesh, **change).validate(True, 0.0001)

    def test_volume_and_motion_capabilities_are_rejected_before_launch(self):
        data = self.source(True)
        data["trajectory"][2]["twist_rad"] = 0.1
        with self.assertRaisesRegex(ValueError, "translation only"):
            Config.from_dict(data, ROOT / "configs")
        data = self.source(True)
        data["object"]["material"] = {"case": "materials/compressible_foam.json"}
        with self.assertRaisesRegex(ValueError, "requires plane"):
            Config.from_dict(data, ROOT / "configs")
        data = self.source(True)
        data["object"]["geometry"].pop("grip_node_set")
        with self.assertRaisesRegex(ValueError, "grip_node_set"):
            Config.from_dict(data, ROOT / "configs")
        with tempfile.TemporaryDirectory() as tmp:
            data = self.source()
            data["object"]["geometry"]["file"] = "missing.stl"
            data["object"]["material"] = {"model": "rigid"}
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps(data))
            with (
                patch("gelsight_ansys.pipeline.run") as run,
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(["run", "--config", str(path)]), 1)
            run.assert_not_called()

    def test_target_numbering_and_volume_fixture_export(self):
        for soft in (False, True):
            with self.subTest(soft=soft), tempfile.TemporaryDirectory() as tmp:
                config = self.config(soft)
                config = replace(config, gel=replace(config.gel, elements=(4, 4, 2)))
                model = AnsysImported(config, Path(tmp) / "solver")
                model.mapdl = Mock()
                model.mapdl.input_strings.return_value = "built"
                model.build()
                deck = (Path(tmp) / "solver/model.inp").read_text().splitlines()
                node_ids = [
                    int(line.split(",")[1]) for line in deck if line.startswith("N,")
                ]
                element_ids = [
                    int(line.split(",")[1]) for line in deck if line.startswith("EN,")
                ]
                self.assertEqual(len(set(node_ids)), len(node_ids))
                self.assertEqual(len(set(element_ids)), len(element_ids))
                self.assertEqual(
                    sum(line.startswith("TSHAP,TRIA") for line in deck), 0 if soft else 12
                )
                if soft:
                    self.assertIn("CM,GRIP,NODE", deck)
                    self.assertIn("ET,5,SOLID185", deck)
                with np.load(Path(tmp) / "solid_mesh.npz") as arrays:
                    self.assertIn(
                        "indenter_hexes" if soft else "target_triangles", arrays
                    )
                    np.testing.assert_allclose(
                        arrays["object_reference_point_m"],
                        config.imported_mesh.reference_point_m,
                    )
                np.testing.assert_allclose(
                    model.reference_state().pilot_position_m,
                    config.imported_mesh.reference_point_m,
                )

    def test_a_rigid_mesh_can_be_pressed_to_a_commanded_load(self):
        load = {"depth_m": 0.0, "normal_force_n": 0.2, "force_controlled": True}
        data = self.source(True)
        data["trajectory"][3].update(load)
        with self.assertRaisesRegex(ValueError, "Force control currently requires"):
            Config.from_dict(data, ROOT / "configs")
        data = self.source()
        data["trajectory"][3].update(load)
        config = Config.from_dict(data, ROOT / "configs")
        config = replace(config, gel=replace(config.gel, elements=(4, 4, 2)))
        with tempfile.TemporaryDirectory() as tmp:
            model = AnsysImported(config, Path(tmp) / "solver")
            model.mapdl = Mock()
            model.mapdl.input_strings.return_value = "built"
            model.build()
            deck = (Path(tmp) / "solver/model.inp").read_text().splitlines()
        # The pilot's travel is left to the per-step D or F; nothing else moves.
        pilot = model.pilot
        self.assertEqual(model.load_node, pilot)
        self.assertNotIn(f"D,{pilot},ALL,0", deck)
        for dof in ("UX", "UY", "ROTX", "ROTY", "ROTZ"):
            self.assertIn(f"D,{pilot},{dof},0", deck)
        self.assertNotIn(f"D,{pilot},UZ,0", deck)
        self.assertIn("ANTYPE,TRANS", deck)

    def test_rerender_rejects_changed_imported_geometry(self):
        config = self.config()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            data = config.to_dict()
            (directory / "config.json").write_text(json.dumps(data))
            changed = copy.deepcopy(data["imported_mesh"])
            changed["reference_point_m"][0] = 0.001
            (directory / "summary.json").write_text(
                json.dumps({"mechanical_configuration": {"imported_mesh": changed}})
            )
            with self.assertRaisesRegex(ValueError, "requires a new solve"):
                rerender(directory, directory / "output")


if __name__ == "__main__":
    unittest.main()
