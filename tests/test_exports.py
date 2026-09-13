"""Portable export completeness, storage selection, and lossless copy contracts."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from gelsight_ansys.batch import export_examples as exporter


class ExampleExportTests(unittest.TestCase):
    def create_run(self, root, layout):
        """Byte fixtures exercise export selection; solver content is opaque here."""
        run = root / "run"
        run.mkdir()
        for name in exporter.FILES:
            (run / name).write_bytes(b"generated artifact\n")
        (run / "config.json").write_text(
            json.dumps(
                {
                    "trajectory": [{"time_s": 0}, {"time_s": 1}],
                    "optics": {},
                }
            )
        )
        (run / "summary.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "frames": [{"time_s": 0}, {"time_s": 1}],
                }
            )
        )
        visualization = (
            {}
            if layout == "legacy"
            else {
                "frame_viewer_folder": "panels" if layout == "detailed" else "images",
                "tactile_gif_saved": layout == "detailed",
            }
        )
        (run / "visualization.json").write_text(json.dumps(visualization))
        for folder in ("states", "fields", "bodies", "images"):
            (run / folder).mkdir()
            extension = "png" if folder == "images" else "npz"
            for i in range(2):
                (run / folder / f"frame_{i:04d}.{extension}").write_bytes(
                    f"opaque {folder} payload for frame {i}\n".encode()
                )
                if folder == "fields":
                    (run / folder / f"frame_{i:04d}.json").write_text("{}")
        if layout != "compact":
            (run / "panels").mkdir()
            for i in range(2):
                (run / "panels" / f"frame_{i:04d}.png").write_bytes(b"panel\n")
            (run / "tactile.gif").write_bytes(b"rgb animation\n")
        if layout == "legacy":
            (run / "preview.gif").write_bytes((run / "process.gif").read_bytes())
        (run / "solver").mkdir()
        (run / "solver/gel.rst").write_bytes(b"preserved raw solver result\n")
        validation = root / "validation.json"
        validation.write_text(json.dumps({"press": {"status": "passed", "run": "run"}}))
        return run, validation

    def export(self, validation, output):
        with redirect_stdout(io.StringIO()):
            exporter.export_case(validation, "press", output)
        return output / "sphere_press"

    def test_all_layouts_preserve_data_and_export_only_required_visuals(self):
        for layout in ("compact", "detailed", "legacy"):
            with self.subTest(
                layout=layout
            ), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run, validation = self.create_run(root, layout)
                original = {
                    p.relative_to(run): p.read_bytes()
                    for p in run.rglob("*")
                    if p.is_file()
                }
                destination = self.export(validation, root / "examples")
                manifest = json.loads((destination / "manifest.json").read_text())
                self.assertEqual(manifest["frame_count"], 2)
                self.assertNotIn("preview.gif", manifest["files"])
                self.assertFalse((destination / "preview.gif").exists())
                self.assertFalse((destination / "solver").exists())
                self.assertEqual(
                    (destination / "tactile.gif").is_file(), layout != "compact"
                )
                self.assertEqual((destination / "panels").is_dir(), layout != "compact")
                for relative, content in original.items():
                    self.assertEqual((run / relative).read_bytes(), content)
                    if relative.parts[0] in ("states", "fields", "bodies", "images"):
                        self.assertEqual((destination / relative).read_bytes(), content)
                for relative, record in manifest["files"].items():
                    content = (destination / relative).read_bytes()
                    self.assertEqual(content, original[Path(relative)])
                    self.assertEqual(record["bytes"], len(content))
                    self.assertEqual(
                        record["sha256"], hashlib.sha256(content).hexdigest()
                    )

    def test_declared_optional_assets_are_required(self):
        for missing in ("panels/frame_0001.png", "tactile.gif"):
            with self.subTest(
                missing=missing
            ), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run, validation = self.create_run(root, "detailed")
                (run / missing).unlink()
                with self.assertRaisesRegex(FileNotFoundError, "Incomplete run"):
                    self.export(validation, root / "examples")

    def test_reexport_removes_stale_generated_visuals_and_keeps_other_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, validation = self.create_run(root, "legacy")
            destination = self.export(validation, root / "examples")
            # Simulate an older exporter and an independent note in the destination.
            (destination / "preview.gif").write_bytes(b"legacy duplicate")
            (destination / "notes.txt").write_text("Keep this file.")
            (destination / "panels/frame_0002.png").write_bytes(b"old longer cycle")
            (run / "visualization.json").write_text(
                json.dumps(
                    {
                        "frame_viewer_folder": "images",
                        "tactile_gif_saved": False,
                    }
                )
            )
            self.export(validation, root / "examples")
            self.assertFalse((destination / "preview.gif").exists())
            self.assertFalse((destination / "tactile.gif").exists())
            self.assertEqual(list((destination / "panels").glob("frame_*")), [])
            self.assertEqual((destination / "notes.txt").read_text(), "Keep this file.")
            self.assertEqual(
                (destination / "states/frame_0001.npz").read_bytes(),
                (run / "states/frame_0001.npz").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()
