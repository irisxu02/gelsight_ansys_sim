"""Long animations must retain frames/timing without a full-resolution atlas."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from gelsight_ansys.artifacts import ImageFiles, save_gif


class StreamingGifTests(unittest.TestCase):
    def test_lazy_frames_and_identical_frames_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            paths = []
            for i in range(41):
                rgb = np.zeros((30, 40, 3), dtype=np.uint8)
                rgb[:, :, 0] = i // 2 * 10
                path = root / f"{i}.png"
                Image.fromarray(rgb).save(path)
                paths.append(path)
            save_gif(ImageFiles(paths), root / "test.gif", 100, durations=[10] * 41)
            with Image.open(root / "test.gif") as gif:
                self.assertEqual(gif.n_frames, 41)
                self.assertEqual(gif.size, (40, 30))
                for i in range(41):
                    gif.seek(i)
                    self.assertEqual(gif.info["duration"], 10)
                    np.testing.assert_array_equal(
                        np.asarray(gif.convert("RGB"))[0, 0], [i // 2 * 10, 0, 0]
                    )
