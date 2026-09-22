"""USB GelSight Mini capture, preprocessing, and recording.

The Mini enumerates as a UVC camera with a single mode: 3280 x 2464 MJPEG,
advertised at 25 fps. The tested unit delivers about 18.7 fps even when frames
are grabbed without decoding, so recordings keep a timestamp per frame rather
than assuming a rate.

Frames are decoded straight from the camera's JPEG with libjpeg's DCT scaling
(1/2, 1/4 or 1/8 size), cropped, and area-resized to the simulator's 320 x 240
landscape image. The crop is in raw-sensor pixels, so it means the same thing
whatever reduction the decoder used. Everything this module hands back is RGB,
the channel order of the simulator's ``images/frame_XXXX.png``.

OpenCV is an optional dependency (``pip install -e .[sensor]``) and is imported
only where a frame is decoded or the camera is opened.
"""

import json
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RAW_SIZE = (3280, 2464)
OUTPUT_SIZE = (320, 240)
SCHEMA_VERSION = 1


def _cv2():
    try:
        import cv2
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise SystemExit(
            "OpenCV is required for GelSight capture: pip install -e .[sensor]"
        ) from error
    return cv2


def find_devices(sysfs=Path("/sys/class/video4linux")):
    """(device path, name) for every GelSight capture node, lowest index first.

    A UVC camera exposes a capture node (``index`` 0) and a metadata node
    (``index`` 1) under the same name; only the first delivers frames.
    """
    found = []
    for node in sorted(sysfs.glob("video*"), key=lambda p: int(p.name[5:] or 0)):
        try:
            name = (node / "name").read_text().strip()
            index = int((node / "index").read_text().strip())
        except (OSError, ValueError):
            continue
        if "gelsight" in name.lower() and index == 0:
            found.append((f"/dev/{node.name}", name))
    return found


def parse_size(text):
    """``"320x240"`` -> ``(320, 240)``, width first."""
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text)
    if not match:
        raise ValueError(f"size must look like 320x240, got {text!r}")
    return int(match[1]), int(match[2])


def crop_box(spec, raw_size=RAW_SIZE, output_size=OUTPUT_SIZE):
    """The raw-pixel box ``(x0, y0, x1, y1)`` a crop specification selects.

    ``full`` keeps the whole sensor, which is how ``gs.npz`` was captured.
    ``gsrobotics`` reproduces the GelSight SDK's ``resize_crop_mini``: a 1/7
    border off each side, then more off one axis to reach the output aspect.
    Otherwise ``spec`` is ``"x0,y0,x1,y1"`` in raw pixels.
    """
    width, height = raw_size
    out_w, out_h = output_size
    if spec == "full":
        return 0, 0, width, height
    if spec == "gsrobotics":
        border_y, border_x = int(height / 7), int(np.floor(width / 7))
        cropped_h, cropped_w = height - 2 * border_y, width - 2 * border_x
        extra_h = extra_w = 0
        if cropped_h * out_w / out_h > cropped_w + 1e-8:
            extra_h = int(cropped_h - cropped_w * out_h / out_w)
        elif cropped_h * out_w / out_h < cropped_w - 1e-8:
            extra_w = int(cropped_w - cropped_h * out_w / out_h)
        return border_x + extra_w, border_y + extra_h, width - border_x, height - border_y
    try:
        x0, y0, x1, y1 = (int(v) for v in spec.split(","))
    except ValueError:
        raise ValueError(
            f"crop must be full, gsrobotics or x0,y0,x1,y1; got {spec!r}"
        ) from None
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ValueError(f"crop {spec!r} lies outside the {width}x{height} sensor")
    return x0, y0, x1, y1


def decode_reduction(box, output_size=OUTPUT_SIZE):
    """The largest JPEG DCT reduction that still leaves the crop at least output size."""
    box_w, box_h = box[2] - box[0], box[3] - box[1]
    for reduction in (8, 4, 2):
        if box_w / reduction >= output_size[0] and box_h / reduction >= output_size[1]:
            return reduction
    return 1


def parse_grid(spec):
    """``"grid:11x17:10"`` -> ``(11, 17, (10.0, 10.0))``; the margin may be ``Y,X``."""
    match = re.fullmatch(r"grid:(\d+)x(\d+)(?::([\d.]+)(?:,([\d.]+))?)?", spec)
    if not match:
        raise ValueError(
            f"grid crop must look like grid:11x17 or grid:11x17:10, got {spec!r}"
        )
    margin_y = float(match[3]) if match[3] else 10.0
    margin_x = float(match[4]) if match[4] else margin_y
    return int(match[1]), int(match[2]), (margin_y, margin_x)


def sim_marker_pixels(rows, cols, margin_yx, output_size=OUTPUT_SIZE):
    """Unloaded marker centers, row-major from top left, as ``camera.reference_marker_pixels``."""
    width, height = output_size
    my, mx = margin_yx
    yy = np.linspace(my, height - 1 - my, rows) if rows > 1 else [(height - 1) / 2]
    xx = np.linspace(mx, width - 1 - mx, cols) if cols > 1 else [(width - 1) / 2]
    xx, yy = np.meshgrid(xx, yy)
    return np.column_stack((xx.ravel(), yy.ravel()))


def detect_markers(bgr, raw_size=RAW_SIZE):
    """Centers of the dark gel markers, in raw-sensor pixels, from a decoded frame.

    The illumination varies smoothly across the gel, so dots are found as pixels
    well below a heavily blurred copy of the image, then kept by size and shape.
    """
    cv2 = _cv2()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    background = cv2.GaussianBlur(gray, (0, 0), gray.shape[1] / 70)
    mask = (background - gray > 12).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    areas = stats[1:, cv2.CC_STAT_AREA]
    widths, heights = stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]
    sizeable = areas > max(4, gray.shape[1] / 400) ** 2
    if not sizeable.any():
        return np.zeros((0, 2))
    typical = np.median(areas[sizeable])
    keep = (
        (areas > 0.4 * typical)
        & (areas < 2.5 * typical)
        & (widths < 3 * heights)
        & (heights < 3 * widths)
    )
    scale = np.array([raw_size[0] / bgr.shape[1], raw_size[1] / bgr.shape[0]])
    return (centroids[1:][keep] + 0.5) * scale - 0.5


def grid_members(points):
    """Drop detections that are not part of a regular lattice.

    Every marker, a grid corner included, has at least two others about one
    pitch away; dark vignetting in the sensor corners and stray specks do not.
    """
    points = np.asarray(points, float)
    if len(points) < 3:
        return points
    distances = np.linalg.norm(points[:, None] - points[None], axis=2)
    np.fill_diagonal(distances, np.inf)
    pitch = np.median(distances.min(axis=1))
    neighbours = np.sum((distances > 0.7 * pitch) & (distances < 1.3 * pitch), axis=1)
    return points[neighbours >= 2]


def order_grid(points, rows, cols):
    """Detected centers ordered row-major from top left, or ValueError if not a grid.

    Rows are separated by sorting on y, which holds while the grid's tilt across
    its width stays under half a row pitch.
    """
    points = grid_members(points)
    if len(points) != rows * cols:
        raise ValueError(
            f"found {len(points)} markers, expected {rows} x {cols} = {rows * cols}"
        )
    by_row = points[np.argsort(points[:, 1])].reshape(rows, cols, 2)
    ordered = np.stack([row[np.argsort(row[:, 0])] for row in by_row])
    return ordered.reshape(-1, 2)


@dataclass(frozen=True)
class Framing:
    """How a raw sensor frame becomes an output image.

    Either an axis-aligned crop and area resize, or a marker-lattice remap: the
    detected unloaded marker centers are interpolated (bicubic, extrapolated
    linearly past the outer markers) into a raw position for every output pixel,
    so each unloaded marker lands exactly where the simulator draws it and the
    lens distortion goes with it. A single homography fitted to the same markers
    is kept only to report how far the optics are from a pinhole view.
    """

    raw_size: tuple
    output_size: tuple
    box: tuple  # raw-pixel crop, or the detected grid's bounding box
    lattice: np.ndarray | None = None  # (rows, cols, 2) detected centers, raw px
    grid: dict | None = None
    _maps: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def crop(cls, spec, raw_size=RAW_SIZE, output_size=OUTPUT_SIZE):
        return cls(raw_size, output_size, crop_box(spec, raw_size, output_size))

    @classmethod
    def from_markers(
        cls, points, rows, cols, margin_yx, raw_size=RAW_SIZE, output_size=OUTPUT_SIZE
    ):
        cv2 = _cv2()
        detected = order_grid(points, rows, cols)
        target = sim_marker_pixels(rows, cols, margin_yx, output_size)
        homography, _ = cv2.findHomography(detected, target, 0)
        mapped = cv2.perspectiveTransform(detected[None], homography)[0]
        residual = np.sqrt(np.mean(np.sum((mapped - target) ** 2, axis=1)))
        box = tuple(int(v) for v in (*detected.min(0), *np.ceil(detected.max(0))))
        grid = {
            "rows_cols": [rows, cols],
            "margin_px_yx": list(margin_yx),
            "homography_rms_residual_px": float(residual),
            "detected_centers_raw_px": detected.round(2).tolist(),
        }
        return cls(raw_size, output_size, box, detected.reshape(rows, cols, 2), grid)

    def _output_pitch(self):
        rows, cols, _ = self.lattice.shape
        margin_y, margin_x = self.grid["margin_px_yx"]
        return (
            (self.output_size[0] - 1 - 2 * margin_x) / max(cols - 1, 1),
            (self.output_size[1] - 1 - 2 * margin_y) / max(rows - 1, 1),
        )

    @property
    def reduction(self):
        if self.lattice is None:
            return decode_reduction(self.box, self.output_size)
        # Decode no finer than about one decoded pixel per output pixel, so the
        # bilinear remap samples without aliasing.
        raw_pitch = min(
            np.median(np.linalg.norm(np.diff(self.lattice, axis=1), axis=2)),
            np.median(np.linalg.norm(np.diff(self.lattice, axis=0), axis=2)),
        )
        scale = min(self._output_pitch()) / raw_pitch  # output px per raw px
        for reduction in (8, 4, 2):
            if reduction * scale <= 1.1:
                return reduction
        return 1

    def raw_positions(self):
        """(H, W, 2) raw-pixel position that every output pixel samples."""
        cv2 = _cv2()
        width, height = self.output_size
        margin_y, margin_x = self.grid["margin_px_yx"]
        pitch_x, pitch_y = self._output_pitch()
        # Enough rings of linear extrapolation to cover the margins, plus the
        # extra ring bicubic interpolation reads.
        ring = int(np.ceil(max(margin_x / pitch_x, margin_y / pitch_y))) + 2
        padded = np.pad(
            self.lattice,
            ((ring, ring), (ring, ring), (0, 0)),
            mode="reflect",
            reflect_type="odd",
        )
        u, v = np.meshgrid(
            np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
        )
        lattice_x = (u - margin_x) / pitch_x + ring
        lattice_y = (v - margin_y) / pitch_y + ring
        return cv2.remap(padded.astype(np.float32), lattice_x, lattice_y, cv2.INTER_CUBIC)

    def _decoded_maps(self, decoded_shape):
        if decoded_shape not in self._maps:
            raw = self.raw_positions()
            sx = self.raw_size[0] / decoded_shape[1]
            sy = self.raw_size[1] / decoded_shape[0]
            # Pixel centers: raw = (decoded + 0.5) * s - 0.5.
            map_x = ((raw[..., 0] + 0.5) / sx - 0.5).astype(np.float32)
            map_y = ((raw[..., 1] + 0.5) / sy - 0.5).astype(np.float32)
            self._maps[decoded_shape] = (map_x, map_y)
        return self._maps[decoded_shape]

    def apply(self, bgr):
        """RGB output image from a BGR frame decoded at any reduction of ``raw_size``."""
        if self.lattice is None:
            return process_image(bgr, self.box, self.raw_size, self.output_size)
        cv2 = _cv2()
        map_x, map_y = self._decoded_maps(bgr.shape[:2])
        warped = cv2.remap(
            bgr, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
        return cv2.cvtColor(warped, cv2.COLOR_BGR2RGB)

    def metadata(self):
        if self.lattice is None:
            return {
                "framing": "crop",
                "crop_box_xyxy_raw_px": list(self.box),
                "resize": "cv2.INTER_AREA",
            }
        return {
            "framing": "marker_lattice_remap",
            "grid_bbox_xyxy_raw_px": list(self.box),
            "grid": self.grid,
            "resize": "bicubic lattice interpolation, then cv2.remap INTER_LINEAR",
        }


def process_image(bgr, box, raw_size=RAW_SIZE, output_size=OUTPUT_SIZE):
    """Crop a decoded BGR frame (at any reduction of ``raw_size``) and resize to RGB."""
    cv2 = _cv2()
    sx, sy = bgr.shape[1] / raw_size[0], bgr.shape[0] / raw_size[1]
    x0, y0 = int(round(box[0] * sx)), int(round(box[1] * sy))
    x1, y1 = int(round(box[2] * sx)), int(round(box[3] * sy))
    cropped = bgr[y0:y1, x0:x1]
    resized = cv2.resize(cropped, output_size, interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)


def decode_jpeg(jpeg, reduction=1):
    cv2 = _cv2()
    flags = {
        1: cv2.IMREAD_COLOR,
        2: cv2.IMREAD_REDUCED_COLOR_2,
        4: cv2.IMREAD_REDUCED_COLOR_4,
        8: cv2.IMREAD_REDUCED_COLOR_8,
    }
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), flags[reduction])
    if image is None:
        raise ValueError("camera returned a frame that is not a decodable JPEG")
    return image


def signed_difference(frame, reference, gain=0.5):
    """uint8 image of ``128 + gain * (frame - reference)``.

    Gain 0.5 is the convention of ``compare_rendering``'s subtracted panels, so
    live and simulated differences are drawn on the same scale.
    """
    difference = frame.astype(np.float32) - reference.astype(np.float32)
    return np.clip(np.rint(128 + gain * difference), 0, 255).astype(np.uint8)


def read_controls(device):
    """Current V4L2 control values, or ``{}`` when ``v4l2-ctl`` is unavailable."""
    if not shutil.which("v4l2-ctl"):
        return {}
    try:
        text = subprocess.run(
            ["v4l2-ctl", "-d", device, "--list-ctrls"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    controls = {}
    for line in text.splitlines():
        match = re.match(r"\s*(\w+)\s+0x[0-9a-f]+\s+\(\w+\).*?\bvalue=(-?\d+)", line)
        if match:
            controls[match[1]] = int(match[2])
    return controls


def set_controls(device, controls):
    """Apply ``{name: value}`` V4L2 controls, e.g. to lock exposure for a session."""
    if not controls:
        return
    if not shutil.which("v4l2-ctl"):
        raise SystemExit(
            "v4l2-ctl is needed to set camera controls (apt install v4l-utils)"
        )
    setting = ",".join(f"{name}={value}" for name, value in controls.items())
    subprocess.run(["v4l2-ctl", "-d", device, "-c", setting], check=True, timeout=5)


@dataclass(frozen=True)
class Frame:
    rgb: np.ndarray  # (H, W, 3) uint8, processed
    jpeg: bytes | None  # the camera's untouched MJPEG frame, when available
    t_ns: int  # time.monotonic_ns() when the frame arrived
    index: int  # 0-based count of frames received since the camera opened


class Recording:
    """Frames appended from the capture thread, written out as a run directory.

    ``frames.npz`` keeps the key names of the supplied ``gs.npz`` capture
    (``frames``, ``t_ns``, ``frame_idx``), but its frames are RGB; ``meta.json``
    states the channel order, crop and camera controls so a recording can be
    matched to a simulation's camera and a later session's settings.
    """

    def __init__(self, root, meta, output_size, label=None, save_raw=False):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        label = re.sub(r"[^\w.-]+", "_", label).strip("_") if label else None
        name = f"{stamp}_{label}" if label else stamp
        self.path = Path(root) / name
        suffix = 1
        while self.path.exists():
            suffix += 1
            self.path = Path(root) / f"{name}-{suffix}"
        self.path.mkdir(parents=True)
        self.meta = dict(meta)
        self.meta["label"] = label
        self.meta["started_at"] = datetime.now(timezone.utc).isoformat()
        self.output_size = output_size
        self.save_raw = save_raw
        if save_raw:
            (self.path / "raw").mkdir()
        self._scratch = open(self.path / "frames.partial", "wb")  # noqa: SIM115
        self.t_ns = []
        self.frame_idx = []
        self.closed = False

    @property
    def count(self):
        return len(self.t_ns)

    @property
    def duration_s(self):
        return (self.t_ns[-1] - self.t_ns[0]) / 1e9 if self.count > 1 else 0.0

    def add(self, frame):
        self._scratch.write(np.ascontiguousarray(frame.rgb).tobytes())
        if self.save_raw and frame.jpeg is not None:
            (self.path / "raw" / f"frame_{self.count:06d}.jpg").write_bytes(frame.jpeg)
        self.t_ns.append(frame.t_ns)
        self.frame_idx.append(frame.index)

    def save_reference(self, rgb, frames_averaged):
        write_png(self.path / "reference.png", rgb)
        self.meta["reference"] = {
            "file": "reference.png",
            "frames_averaged": frames_averaged,
        }

    def close(self):
        if self.closed:
            return self.path
        self.closed = True
        self._scratch.close()
        partial = self.path / "frames.partial"
        width, height = self.output_size
        if self.count:
            frames = np.memmap(
                partial, np.uint8, "r", shape=(self.count, height, width, 3)
            )
        else:
            frames = np.zeros((0, height, width, 3), np.uint8)
        t_ns = np.asarray(self.t_ns, np.int64)
        np.savez(
            self.path / "frames.npz",
            frames=frames,
            t_ns=t_ns,
            t_s=(t_ns - t_ns[0]) / 1e9 if self.count else np.zeros(0),
            frame_idx=np.asarray(self.frame_idx, np.int64),
        )
        del frames
        partial.unlink()
        intervals = np.diff(t_ns) / 1e9
        self.meta.update(
            frame_count=self.count,
            duration_s=self.duration_s,
            mean_fps=(self.count - 1) / self.duration_s if self.duration_s else None,
            max_frame_interval_s=float(intervals.max()) if intervals.size else None,
            dropped_frames=int(np.sum(np.diff(self.frame_idx) - 1)) if self.count else 0,
        )
        (self.path / "meta.json").write_text(json.dumps(self.meta, indent=2) + "\n")
        return self.path


def write_png(path, rgb):
    cv2 = _cv2()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)):
        raise OSError(f"could not write {path}")


def load_recording(path):
    """A recording directory as ``{"frames", "t_s", "t_ns", "frame_idx", "meta", "reference"}``."""
    path = Path(path)
    with np.load(path / "frames.npz", allow_pickle=False) as data:
        loaded = {key: data[key] for key in data.files}
    loaded["meta"] = json.loads((path / "meta.json").read_text())
    reference = path / "reference.png"
    if reference.exists():
        cv2 = _cv2()
        loaded["reference"] = cv2.cvtColor(cv2.imread(str(reference)), cv2.COLOR_BGR2RGB)
    else:
        loaded["reference"] = None
    return loaded


class MiniCamera:
    """A GelSight Mini read on a background thread; the newest frame is always ready.

    Frames are recorded from the capture thread itself, so a slow display never
    drops frames from a recording.
    """

    def __init__(self, device, output_size=OUTPUT_SIZE, crop="full"):
        self.device = device
        self.output_size = output_size
        self.crop_spec = crop
        self._lock = threading.Lock()
        self._fresh = threading.Condition(self._lock)
        self._latest = None
        self._recording = None
        self._running = False
        self._thread = None
        self._capture = None
        self.error = None

    def open(self):
        cv2 = _cv2()
        capture = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not capture.isOpened():
            raise SystemExit(
                f"cannot open {self.device}; is it plugged in and in the 'video' group?"
            )
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, RAW_SIZE[0])
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, RAW_SIZE[1])
        # Hand back the MJPEG buffer undecoded, so decoding can use DCT scaling.
        self.raw_passthrough = bool(capture.set(cv2.CAP_PROP_CONVERT_RGB, 0))
        self._capture = capture
        buffer = self._grab()
        image = self._image(buffer, reduction=1)
        self.raw_size = (image.shape[1], image.shape[0])
        if self.crop_spec.startswith("grid"):
            self.framing = self._framing_from_markers(parse_grid(self.crop_spec))
        else:
            self.framing = Framing.crop(self.crop_spec, self.raw_size, self.output_size)
        self.box = self.framing.box
        self.reduction = self.framing.reduction if self._is_jpeg(buffer) else 1
        return self

    def _framing_from_markers(self, grid, frames=5):
        """Fit the marker homography to a few averaged frames; the gel must be unloaded."""
        rows, cols, margin = grid
        reduction = 4 if self._is_jpeg(self._grab()) else 1
        total = None
        for _ in range(frames):
            image = self._image(self._grab(), reduction).astype(np.float32)
            total = image if total is None else total + image
        mean = np.clip(np.rint(total / frames), 0, 255).astype(np.uint8)
        points = detect_markers(mean, self.raw_size)
        try:
            return Framing.from_markers(
                points, rows, cols, margin, self.raw_size, self.output_size
            )
        except ValueError as error:
            raise SystemExit(
                f"marker-grid framing failed: {error}. Start with nothing touching the gel,"
                " check the grid size, or use --crop full"
            ) from None

    def _grab(self):
        ok, buffer = self._capture.read()
        if not ok:
            raise RuntimeError(f"{self.device} stopped delivering frames")
        return buffer

    @staticmethod
    def _is_jpeg(buffer):
        return buffer.ndim == 2 and buffer.shape[0] == 1 or buffer.ndim == 1

    def _image(self, buffer, reduction):
        if self._is_jpeg(buffer):
            return decode_jpeg(buffer.tobytes(), reduction)
        return buffer  # the backend decoded to BGR itself

    def _run(self):
        index = 0
        try:
            while self._running:
                buffer = self._grab()
                t_ns = time.monotonic_ns()
                jpeg = buffer.tobytes() if self._is_jpeg(buffer) else None
                rgb = self.framing.apply(self._image(buffer, self.reduction))
                frame = Frame(rgb, jpeg, t_ns, index)
                index += 1
                with self._lock:
                    self._latest = frame
                    if self._recording is not None:
                        self._recording.add(frame)
                    self._fresh.notify_all()
        except Exception as error:  # surfaced to the caller through wait_frame
            self.error = error
            with self._lock:
                self._fresh.notify_all()

    def start(self):
        if self._capture is None:
            self.open()
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name="gelsight-capture", daemon=True
        )
        self._thread.start()
        return self

    def latest(self):
        with self._lock:
            return self._latest

    def wait_frame(self, after=None, timeout=5.0):
        """The first frame newer than ``after`` (a Frame or None)."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while self.error is None and (
                self._latest is None
                or (after is not None and self._latest.index <= after.index)
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no frame from {self.device} within {timeout} s")
                self._fresh.wait(remaining)
            if self.error is not None:
                raise RuntimeError(f"capture failed: {self.error}") from self.error
            return self._latest

    def average(self, count):
        """Mean of the next ``count`` frames, as uint8 RGB (an unloaded reference)."""
        total = np.zeros((self.output_size[1], self.output_size[0], 3), np.float64)
        frame = None
        for _ in range(count):
            frame = self.wait_frame(after=frame)
            total += frame.rgb
        return np.clip(np.rint(total / count), 0, 255).astype(np.uint8)

    def start_recording(self, recording):
        with self._lock:
            self._recording = recording

    def stop_recording(self):
        with self._lock:
            recording, self._recording = self._recording, None
        return recording.close() if recording is not None else None

    @property
    def recording(self):
        return self._recording

    def metadata(self, name=None):
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "gelsight_mini_recording",
            "device": self.device,
            "device_name": name,
            "raw_size_wh": list(self.raw_size),
            "crop": self.crop_spec,
            **self.framing.metadata(),
            "output_size_wh": list(self.output_size),
            "jpeg_decode_reduction": self.reduction,
            "channel_order": "RGB",
            "t_ns_clock": "time.monotonic_ns() on frame arrival",
            "camera_controls": read_controls(self.device),
        }

    def close(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
        path = self.stop_recording()
        if self._capture is not None:
            self._capture.release()
        return path
