"""Assemble the 16-second published edit from recorded simulation renders.

Requires hero frames 24..239, contact frames 108..197 and trail frame 240,
rendered at 1920x1080 from an eight-second, 30 Hz video-preset run. This is
editorial replay of a recorded trajectory, not real-time simulation. No new
physics positions are generated. Output directories must not already exist.
"""

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from cinema.design import SIZE, build_assets
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FPS = 30
N_HERO, N_CONTACT, N_TRAIL, N_CLOSE = 216, 90, 84, 90
DURATION = (N_HERO + N_CONTACT + N_TRAIL + N_CLOSE) / FPS


def source_frames(run):
    return (
        [run / "render-hero" / f"frame-{i:04d}.png" for i in range(24, 240)]
        + [run / "render-contact" / f"frame-{i:04d}.png" for i in range(108, 198)]
        + [run / "render-trail/frame-0240.png"]
    )


def contact_events(run, meta):
    trace = meta["trace"]
    feet = np.load(run / "foot_centers.npy", mmap_mode="r", allow_pickle=False)
    load = np.array([x["grain_load_z"] for x in trace]) + np.array(
        [x["substrate_load"] for x in trace]
    )
    times = np.array([x["t"] for x in trace])
    if feet.shape != (len(trace), 4, 3) or load.shape != (len(trace), 4):
        raise ValueError("Recorded foot positions and forces must match the trace")
    if any(not np.isfinite(x).all() for x in (feet, load, times)) or np.any(np.diff(times) <= 0):
        raise ValueError("Non-finite contact data or non-monotonic timestamps")
    if len(trace) < 241 or times[-1] < 8:
        raise ValueError("The 16-second edit requires a complete eight-second video-preset run")
    controller = meta.get("controller", {})
    step_time = float(controller.get("step_time", 0.6))
    if not math.isfinite(step_time) or step_time <= 0:
        raise ValueError("Invalid controller step time")
    events = []
    for leg in range(4):
        armed = False
        last = -1
        for i in range(1, len(trace)):
            t = trace[i]["t"]
            if t < 0.9:
                armed = False
                continue
            if load[i, leg] < 0.6 and feet[i, leg, 2] > meta["foot_radius_m"] + 0.008:
                armed = True
            phase = max(0, (t - 1.0) / step_time)
            scheduled_leg = [0, 3, 1, 2][int(phase) % 4]
            if (
                armed
                and load[i, leg] > 2
                and t - last > 0.28
                and t > 1.0
                and leg == scheduled_leg
                and phase % 1 > 0.5
            ):
                if feet[i, leg, 2] < feet[max(0, i - 2), leg, 2] + 0.001:
                    events.append({"simulation_time": t, "leg": leg, "load": float(load[i, leg])})
                    last = t
                    armed = False
    events.sort(key=lambda x: x["simulation_time"])
    final = []
    for shot, start, source_in, duration in [("hero", 0, 0.8, 7.2), ("contact", 7.2, 3.6, 3.0)]:
        for event in events:
            if source_in <= event["simulation_time"] < source_in + duration:
                strength = 0.60 if shot == "hero" else (0.90 if event["leg"] == 1 else 0.35)
                final.append(
                    {
                        "time": start + event["simulation_time"] - source_in,
                        "leg": event["leg"],
                        "strength": strength,
                        "source_time": event["simulation_time"],
                        "shot": shot,
                    }
                )
    return {
        "duration": DURATION,
        "events": final,
        "source_events": events,
        "source": "Scheduled primary swing touchdowns from recorded foot force and height. Thresholds: load crossing 2 N after unloading below 0.6 N, lifting 8 mm above floor-pad height, descending during latter swing half; 280 ms refractory period. Minor support recontacts omitted from artistic foley. Timestamp labels retain the simulation's documented 1 ms offset.",
    }


def validate_run(run, resolution):
    meta = json.loads((run / "metadata.json").read_text())
    if meta.get("fps") != FPS or meta.get("frames", 0) < 241:
        raise ValueError("Expected at least 241 frames recorded at 30 Hz")
    if not isinstance(meta.get("grain_count"), int) or meta["grain_count"] <= 0:
        raise ValueError("Metadata must include the actual positive grain count")
    if any(v < 16 or v > 1920 or v % 2 for v in resolution):
        raise ValueError("Output dimensions must be even and between 16 and 1920")
    for path in source_frames(run):
        with Image.open(path) as im:
            if im.size != SIZE:
                raise ValueError(f"Expected a 1920x1080 source frame: {path.name}")
            im.verify()
    return meta, contact_events(run, meta)


def alpha_overlay(frame, overlay, alpha=1):
    if alpha <= 0:
        return frame
    if alpha < 1:
        overlay = overlay.copy()
        overlay.putalpha(overlay.getchannel("A").point(lambda v: round(v * alpha)))
    return Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")


def picture_frames(run, opening, macro_title, closing):
    w, h = SIZE
    for i in range(N_HERO):
        with Image.open(run / "render-hero" / f"frame-{i + 24:04d}.png") as image:
            frame = image.convert("RGB")
        yield alpha_overlay(frame, opening, min(1, (i + 1) / 8, max(0, (66 - i) / 10)))
    gradient = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(340):
        gd.line((0, y, w, y), fill=(0, 0, 0, round(120 * (1 - y / 340) ** 2)))
    for i in range(N_CONTACT):
        with Image.open(run / "render-contact" / f"frame-{i + 108:04d}.png") as image:
            frame = image.convert("RGB")
        frame = alpha_overlay(frame, gradient)
        yield alpha_overlay(frame, macro_title, min(1, (i + 1) / 6))
    with Image.open(run / "render-trail/frame-0240.png") as image:
        trail = image.convert("RGB")
    for i in range(N_TRAIL):
        scale = 1 + 0.035 * i / (N_TRAIL - 1)
        width, height = round(w / scale), round(h / scale)
        left, top = (w - width) // 2, (h - height) // 2
        yield trail.crop((left, top, left + width, top + height)).resize(
            SIZE, Image.Resampling.LANCZOS
        )
    for i in range(N_CLOSE):
        frame = closing.copy()
        if i < 8:
            frame = Image.blend(trail, frame, (i + 1) / 8)
        if i >= N_CLOSE - 10:
            frame = Image.blend(frame, Image.new("RGB", SIZE), (i - (N_CLOSE - 11)) / 10)
        yield frame


def build(args):
    meta, events = validate_run(args.run, args.resolution)
    if args.validate_only:
        print(
            json.dumps(
                {
                    "source_frames": len(source_frames(args.run)),
                    "final_frames": 480,
                    "contact_events": len(events["events"]),
                    "duration_seconds": DURATION,
                    "validation": "passed",
                },
                indent=2,
            )
        )
        return
    if shutil.which(args.ffmpeg) is None:
        raise FileNotFoundError("FFmpeg was not found; install it or pass --ffmpeg")
    if args.music and not args.music.is_file():
        raise FileNotFoundError("The music file does not exist; run audio/download_music.py first")
    platform = args.platform_label or (
        "Apple Silicon." if meta.get("backend", "").lower() == "metal" else "Local compute."
    )
    for label in (platform, args.hardware_label or ""):
        if len(label) > 32 or "\n" in label:
            raise ValueError(
                "Hardware/platform labels must be single lines of at most 32 characters"
            )
    opening, macro_title, closing, regular = build_assets(args.font, args.bold_font, platform)
    draw = ImageDraw.Draw(closing)
    grain_text = f"{meta['grain_count']:,} simulated grains"
    if args.hardware_label:
        grain_text += f"  /  {args.hardware_label}"
    draw.text((140, 719), grain_text, font=ImageFont.truetype(regular, 27), fill=(146, 169, 186))
    draw.text(
        (140, 766),
        "Experimental granular testbed",
        font=ImageFont.truetype(regular, 27),
        fill=(117, 143, 160),
    )
    args.output.mkdir(parents=True, exist_ok=False)
    event_path = args.output / "contact-events.json"
    event_path.write_text(json.dumps(events, indent=2) + "\n")
    closing.save(args.output / "closing-card.png")
    audio_command = [
        sys.executable,
        str(ROOT / "audio/mix_contact_audio.py"),
        "--events",
        str(event_path),
        "--output",
        str(args.output / "soundtrack.wav"),
        "--foley-db",
        "-15",
        "--music-gain",
        "-1",
        "--ffmpeg",
        args.ffmpeg,
    ]
    if args.music:
        audio_command += ["--music", str(args.music)]
    subprocess.run(audio_command, check=True)
    silent = args.output / "Scenward-Solo12-Picture.mp4"
    cmd = [
        args.ffmpeg,
        "-n",
        "-v",
        "error",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{args.resolution[0]}x{args.resolution[1]}",
        "-framerate",
        str(FPS),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(silent),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    count = 0
    try:
        for count, frame in enumerate(
            picture_frames(args.run, opening, macro_title, closing), start=1
        ):
            if tuple(args.resolution) != SIZE:
                frame = frame.resize(args.resolution, Image.Resampling.LANCZOS)
            if count - 1 in {0, 40, 160, 220, 270, 330, 400, 465}:
                frame.save(args.output / f"qa-{count - 1:04d}.jpg", quality=94)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        status = proc.wait()
    if status:
        raise RuntimeError("Video encoder failed")
    final = args.output / "Scenward-Solo12.mp4"
    subprocess.run(
        [
            args.ffmpeg,
            "-n",
            "-v",
            "error",
            "-i",
            str(silent),
            "-i",
            str(args.output / "soundtrack.wav"),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-ar",
            "48000",
            "-movflags",
            "+faststart",
            "-shortest",
            str(final),
        ],
        check=True,
    )
    subprocess.run([args.ffmpeg, "-v", "error", "-i", str(final), "-f", "null", "-"], check=True)
    manifest = {
        "duration": DURATION,
        "fps": FPS,
        "frames": count,
        "resolution": args.resolution,
        "source_run": args.run.name,
        "shots": [
            {"id": "hero", "source_frames": [24, 239], "final_time": [0, 7.2]},
            {"id": "contact", "source_frames": [108, 197], "final_time": [7.2, 10.2]},
            {
                "id": "trail",
                "source_frame": 240,
                "final_time": [10.2, 13],
                "treatment": "3.5 percent camera crop on recorded final state",
            },
            {"id": "closing", "final_time": [13, 16]},
        ],
        "motion_speed": 1,
        "video": final.name,
        "video_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
        "platform_label": platform,
        "hardware_label": args.hardware_label,
        "audio": "Editorial recorded foley; not simulated acoustics",
        "verification": "Full media decode passed; subjective viewing/listening remains separate",
    }
    (args.output / "edit-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(final)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--music",
        type=Path,
        help="Optional CC0 MindStream file obtained by audio/download_music.py",
    )
    parser.add_argument("--font", type=Path)
    parser.add_argument("--bold-font", type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--resolution", type=int, nargs=2, default=list(SIZE))
    parser.add_argument(
        "--hardware-label", help="Explicit measured machine name; omitted by default"
    )
    parser.add_argument(
        "--platform-label", help="Defaults to Apple Silicon for Metal runs, otherwise Local compute"
    )
    parser.add_argument("--validate-only", action="store_true")
    build(parser.parse_args())
