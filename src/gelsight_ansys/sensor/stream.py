"""View and record a USB GelSight Mini, optionally beside a finished simulation run.

The window shows the live 320 x 240 tactile image and its signed difference
from an unloaded reference. By default each frame is cropped and resized
exactly as slip-perception's data collection does it (gs_sdk ``resize_crop``,
a 1/25 border), and recordings are saved as that collection's ``gs.npz``, so
marker pixel positions agree with the trial data. With ``--sim-run`` it also
shows that run's
``images/frame_XXXX.png`` and their difference from the run's unloaded frame,
on the same scale, so a press on the real gel can be compared with the
simulated one while it happens.

Keys:
  space / r  start or stop recording        b  capture a new unloaded reference
  s          save a snapshot PNG            d  show or hide the difference row
  g          overlay the sim's unloaded marker grid (grid framing only)
  + / -      difference gain                , / .  previous / next sim frame
  p          play or pause the sim frames   q / Esc  quit (finishes a recording)
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from .mini import (
    OUTPUT_SIZE,
    MiniCamera,
    Recording,
    find_devices,
    parse_grid,
    parse_size,
    read_controls,
    set_controls,
    signed_difference,
    sim_marker_pixels,
    write_png,
)

WINDOW = "GelSight Mini"
DEFAULT_OUTPUT = Path("outputs") / "gelsight_mini"


def parser():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--device", help="V4L2 device, e.g. /dev/video0 (default: first GelSight found)"
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List GelSight devices and their controls, then exit",
    )
    p.add_argument(
        "--size",
        default=f"{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}",
        help="Processed frame size WxH (default 320x240, the simulator's)",
    )
    p.add_argument(
        "--crop",
        default="gs_sdk",
        help=(
            "gs_sdk (default: the data collection's resize_crop, 1/25 border);"
            " gsrobotics (upstream SDK and stream_gelsight_utils.py, 1/7 border);"
            " full (whole sensor); x0,y0,x1,y1 in raw pixels; or grid:ROWSxCOLS[:MARGIN]"
            " to warp the unloaded markers onto a sim preset's marker grid instead (bare"
            " 'grid' reads it from --sim-run's config.json, else 11x17:10)"
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where recordings go (default {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--label", help="Appended to the recording directory name, e.g. sphere3mm_press"
    )
    p.add_argument(
        "--save-raw",
        action="store_true",
        help="Also keep every full-resolution camera JPEG in raw/",
    )
    p.add_argument(
        "--control",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Set a V4L2 control before capture, e.g. exposure_time_absolute=156 (repeatable)",
    )
    p.add_argument(
        "--reference-frames",
        type=int,
        default=10,
        help="Frames averaged into an unloaded reference",
    )
    p.add_argument(
        "--warmup-s",
        type=float,
        default=1.0,
        help="Wait before the automatic reference (default 1 s)",
    )
    p.add_argument(
        "--diff-gain",
        type=float,
        default=0.5,
        help="Difference display: 128 + gain * delta (default 0.5)",
    )
    p.add_argument(
        "--sim-run",
        type=Path,
        help="A finished simulation run directory to show alongside",
    )
    p.add_argument(
        "--scale", type=int, default=2, help="Integer display magnification (default 2)"
    )
    p.add_argument("--record", action="store_true", help="Start recording immediately")
    p.add_argument(
        "--duration", type=float, help="Stop after this many seconds (implies --record)"
    )
    p.add_argument(
        "--no-display",
        action="store_true",
        help="Headless: record for --duration, no window",
    )
    return p


def grid_spec(crop, sim_run):
    """Expand a bare ``grid`` crop from the simulation run's marker layout."""
    if crop != "grid":
        return crop
    config = sim_run / "config.json" if sim_run else None
    if config is None or not config.exists():
        return "grid:11x17:10"
    optics = json.loads(config.read_text()).get("optics", {})
    rows, cols = optics.get("marker_grid_rows_cols") or (11, 17)
    margin_y, margin_x = optics.get("marker_margin_px") or (10, 10)
    return f"grid:{rows}x{cols}:{margin_y:g},{margin_x:g}"


def draw_grid(bgr, points):
    """The simulator's unloaded marker centers as small crosses on a copy of ``bgr``."""
    import cv2

    image = bgr.copy()
    for x, y in points:
        cv2.drawMarker(
            image, (int(round(x)), int(round(y))), (255, 0, 255), cv2.MARKER_CROSS, 5, 1
        )
    return image


def parse_controls(items):
    controls = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise SystemExit(f"--control expects NAME=VALUE, got {item!r}")
        controls[name.strip()] = int(value)
    return controls


def load_sim_run(run, size):
    """``(frames, reference)`` as BGR arrays at ``size``; the reference is the unloaded frame.

    General-contact runs render the unloaded reference as frame 0; plane runs
    save it separately as ``unloaded_reference.png`` (see docs/dataset.md).
    """
    import cv2

    paths = sorted((run / "images").glob("frame_*.png"))
    if not paths:
        raise SystemExit(f"{run} has no images/frame_XXXX.png")

    def load(path):
        bgr = cv2.imread(str(path))
        if (bgr.shape[1], bgr.shape[0]) != size:
            bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
        return bgr

    frames = [load(path) for path in paths]
    unloaded = run / "unloaded_reference.png"
    reference = load(unloaded) if unloaded.exists() else frames[0]
    return frames, reference


def labelled(bgr, text, scale):
    import cv2

    image = bgr
    if scale != 1:
        image = cv2.resize(
            image, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
        )
    bar = np.full((22, image.shape[1], 3), 32, np.uint8)
    cv2.putText(
        bar, text, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA
    )
    return np.vstack([bar, image])


def compose(rows):
    """Stack rows of equal-height panels, padding short rows with black."""
    rows = [np.hstack(row) for row in rows if row]
    width = max(row.shape[1] for row in rows)
    padded = [np.pad(row, ((0, 0), (0, width - row.shape[1]), (0, 0))) for row in rows]
    return np.vstack(padded)


def status_bar(width, text, recording):
    import cv2

    bar = np.full((28, width, 3), 16, np.uint8)
    if recording:
        cv2.circle(bar, (14, 14), 7, (0, 0, 255), -1)
    cv2.putText(
        bar,
        text,
        (28 if recording else 8, 19),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (235, 235, 235),
        1,
        cv2.LINE_AA,
    )
    return bar


def window_open(name):
    """False once the window is closed from its title bar.

    The Qt backend raises, rather than reporting 0, when asked about a window
    the user has already closed.
    """
    import cv2

    try:
        return cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) >= 1
    except cv2.error:
        return False


class Session:
    def __init__(self, args, camera, device_name):
        self.args = args
        self.camera = camera
        self.device_name = device_name
        self.reference = None
        self.reference_frames = 0
        self.message = ""
        self.message_until = 0.0

    def say(self, text, seconds=3.0):
        print(text, flush=True)
        self.message, self.message_until = text, time.monotonic() + seconds

    def capture_reference(self):
        self.reference = self.camera.average(self.args.reference_frames)
        self.reference_frames = self.args.reference_frames
        recording = self.camera.recording
        if recording is not None:
            recording.save_reference(self.reference, self.reference_frames)
        self.say(f"Unloaded reference: mean of {self.reference_frames} frames")

    def start_recording(self):
        meta = self.camera.metadata(self.device_name)
        if self.args.sim_run:
            meta["compared_sim_run"] = str(self.args.sim_run)
        recording = Recording(
            self.args.output_dir,
            meta,
            self.camera.output_size,
            self.args.label,
            self.args.save_raw,
        )
        if self.reference is not None:
            recording.save_reference(self.reference, self.reference_frames)
        self.camera.start_recording(recording)
        self.say(f"Recording to {recording.path}")

    def stop_recording(self):
        recording = self.camera.recording
        path = self.camera.stop_recording()
        if path is not None:
            fps = recording.meta.get("mean_fps")
            self.say(
                f"Saved {recording.count} frames, {recording.duration_s:.1f} s"
                + (f" at {fps:.1f} fps" if fps else "")
                + f" -> {path}"
            )
        return path


def run_headless(session):
    args = session.args
    if args.duration is None:
        raise SystemExit("--no-display needs --duration")
    time.sleep(args.warmup_s)
    session.capture_reference()
    session.start_recording()
    end = time.monotonic() + args.duration
    while time.monotonic() < end:
        session.camera.wait_frame()
        time.sleep(0.05)
    session.stop_recording()


def run_window(session):
    import cv2

    args, camera = session.args, session.camera
    size = camera.output_size
    sim_frames, sim_reference = (
        load_sim_run(args.sim_run, size) if args.sim_run else (None, None)
    )
    sim_index, sim_playing, sim_next = 0, False, 0.0
    show_difference = True
    grid = None
    if camera.framing.grid is not None:
        rows, cols, margin = parse_grid(args.crop)
        grid = sim_marker_pixels(rows, cols, margin, size)
    show_grid = False
    gain = args.diff_gain
    started = time.monotonic()
    auto_reference = True
    frame, fps_times = None, []

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE | cv2.WINDOW_GUI_NORMAL)
    try:
        while True:
            frame = camera.wait_frame(after=frame)
            fps_times = [t for t in fps_times if frame.t_ns - t < 2e9] + [frame.t_ns]
            if auto_reference and time.monotonic() - started >= args.warmup_s:
                auto_reference = False
                session.capture_reference()
                if args.record or args.duration:
                    session.start_recording()
                continue

            live = (
                draw_grid(frame.image, grid)
                if show_grid and grid is not None
                else frame.image
            )
            top = [labelled(live, f"live  #{frame.index}", args.scale)]
            bottom = []
            if show_difference:
                if session.reference is not None:
                    difference = signed_difference(frame.image, session.reference, gain)
                    bottom.append(
                        labelled(difference, f"live - reference  (x{gain:g})", args.scale)
                    )
                else:
                    blank = np.full_like(frame.image, 128)
                    bottom.append(labelled(blank, "no reference yet", args.scale))
            if sim_frames is not None:
                if sim_playing and time.monotonic() >= sim_next:
                    sim_index = (sim_index + 1) % len(sim_frames)
                    sim_next = time.monotonic() + 0.1
                top.append(
                    labelled(
                        sim_frames[sim_index],
                        f"sim  {sim_index}/{len(sim_frames) - 1}",
                        args.scale,
                    )
                )
                if show_difference:
                    difference = signed_difference(
                        sim_frames[sim_index], sim_reference, gain
                    )
                    bottom.append(
                        labelled(difference, f"sim - unloaded  (x{gain:g})", args.scale)
                    )
            canvas = compose([top, bottom])

            fps = (
                (len(fps_times) - 1) / ((fps_times[-1] - fps_times[0]) / 1e9)
                if len(fps_times) > 1
                else 0.0
            )
            recording = camera.recording
            text = f"{fps:4.1f} fps"
            if recording is not None:
                text += f"   REC {recording.count} frames  {recording.duration_s:5.1f} s"
            if time.monotonic() < session.message_until:
                text += "   " + session.message
            else:
                text += "   space: record  b: reference  s: snapshot  q: quit"
            cv2.imshow(
                WINDOW,
                np.vstack(
                    [status_bar(canvas.shape[1], text, recording is not None), canvas]
                ),
            )

            if (
                args.duration
                and recording is not None
                and recording.duration_s >= args.duration
            ):
                session.stop_recording()
                break
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if not window_open(WINDOW):
                break
            if key in (ord(" "), ord("r")):
                session.stop_recording() if recording is not None else session.start_recording()
            elif key == ord("b"):
                session.capture_reference()
            elif key == ord("s"):
                stamp = time.strftime("%Y%m%d-%H%M%S")
                path = args.output_dir / "snapshots" / f"{stamp}_{frame.index:06d}.png"
                write_png(path, frame.image)
                if session.reference is not None:
                    write_png(
                        path.with_name(path.stem + "_diff.png"),
                        signed_difference(frame.image, session.reference, gain),
                    )
                session.say(f"Snapshot {path}")
            elif key == ord("d"):
                show_difference = not show_difference
            elif key == ord("g"):
                show_grid = not show_grid
            elif key in (ord("+"), ord("=")):
                gain *= 2
            elif key in (ord("-"), ord("_")):
                gain /= 2
            elif sim_frames is not None and key == ord(","):
                sim_index = (sim_index - 1) % len(sim_frames)
            elif sim_frames is not None and key == ord("."):
                sim_index = (sim_index + 1) % len(sim_frames)
            elif sim_frames is not None and key == ord("p"):
                sim_playing = not sim_playing
    finally:
        cv2.destroyAllWindows()


def main(argv=None):
    args = parser().parse_args(argv)
    devices = find_devices()
    if args.list:
        if not devices:
            print("No GelSight camera found under /sys/class/video4linux")
            return 1
        for path, name in devices:
            print(f"{path}  {name}")
            for control, value in read_controls(path).items():
                print(f"    {control} = {value}")
        return 0
    if args.device:
        device = args.device
        name = dict(devices).get(device)
    elif devices:
        device, name = devices[0]
    else:
        raise SystemExit("No GelSight camera found; plug one in or pass --device")
    set_controls(device, parse_controls(args.control))

    args.crop = grid_spec(args.crop, args.sim_run)
    camera = MiniCamera(device, parse_size(args.size), args.crop).start()
    framing = camera.framing
    if framing.grid is not None:
        how = (
            f"{args.crop} lattice remap (one homography would leave"
            f" {framing.grid['homography_rms_residual_px']:.2f} px rms)"
            f" (grid spans raw {framing.box})"
        )
    else:
        how = f"crop {framing.box}"
    print(
        f"{name or device}: raw {camera.raw_size[0]}x{camera.raw_size[1]}, {how},"
        f" decoded at 1/{camera.reduction}, output {camera.output_size[0]}x{camera.output_size[1]} BGR",
        flush=True,
    )
    session = Session(args, camera, name)
    try:
        if args.no_display:
            run_headless(session)
        else:
            run_window(session)
    except KeyboardInterrupt:
        pass
    finally:
        path = camera.recording and camera.recording.path
        if camera.close() is not None:
            print(f"Saved recording {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
