"""Mix licensed recorded sand foley to supplied contact timestamps.

No speech, synthesized motors, generated buzz or fabricated physics events.
Input JSON: {"duration": 24, "events": [{"time":3.2,"leg":0,"strength":0.8}]}
All times refer to the FINAL VIDEO timeline, including intro and edited shots.
Optional --music is a local licensed music path. download_music.py obtains the
CC0 MindStream track; source records are in sources.json. Foley is editorial
sound design, not simulated acoustics. Listen to the final mix before use.
"""

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
RATE = 48000


def decode(path, duration=None, start=0, filter_chain=None, ffmpeg="ffmpeg"):
    cmd = [ffmpeg, "-v", "error", "-ss", str(start), "-i", str(path)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    if filter_chain:
        cmd += ["-af", filter_chain]
    raw = subprocess.check_output(cmd + ["-f", "f32le", "-ac", "2", "-ar", str(RATE), "-"])
    return np.frombuffer(raw, dtype="<f4").reshape(-1, 2).copy()


def fade(array, incoming=0.015, outgoing=0.045):
    n = min(len(array), round(incoming * RATE))
    m = min(len(array), round(outgoing * RATE))
    if n:
        array[:n] *= np.linspace(0, 1, n)[:, None]
    if m:
        array[-m:] *= np.linspace(1, 0, m)[:, None]
    return array


def loudness(path, ffmpeg="ffmpeg"):
    p = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-21:TP=-2:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.JSONDecoder().raw_decode(p.stderr[p.stderr.rfind("{") :])[0]


def main(args):
    config = json.loads(Path(args.events).read_text())
    duration = float(config["duration"])
    if not 0 < duration <= 120:
        raise ValueError("Duration must be 0..120 seconds")
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new audio output file; existing files are preserved")
    output.parent.mkdir(parents=True, exist_ok=True)
    mix = np.zeros((math.ceil(duration * RATE), 2), dtype=np.float64)
    samples = []
    sample_peaks = []
    files = sorted((ROOT / "foley").glob("Fantozzi-Sand*.flac"))
    if len(files) != 6:
        raise ValueError("Expected all six included CC0 sand recordings")
    expected = json.loads((ROOT / "sources.json").read_text())["sources"][0]["sha256"]
    for path in files:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected["foley/" + path.name]:
            raise ValueError(f"Recorded audio checksum mismatch: {path.name}")
    for path in files:
        audio = decode(
            path,
            filter_chain="highpass=f=100,lowpass=f=8500,acompressor=threshold=0.08:ratio=3:attack=2:release=70:makeup=2",
            ffmpeg=args.ffmpeg,
        )
        # Keep naturally recorded transient texture; scale peak, no added oscillator.
        active = np.flatnonzero(np.max(np.abs(audio), axis=1) > np.max(np.abs(audio)) * 0.025)
        if len(active):
            audio = audio[max(0, active[0] - 240) : min(len(audio), active[-1] + 1200)]
        audio /= max(float(np.max(np.abs(audio))), 1e-8)
        audio = fade(audio)
        # Align the recorded impact peak with the supplied physical contact time.
        # Keep at most 20ms of lead-in, avoiding a long scrape before foot contact.
        peak = int(np.argmax(np.max(np.abs(audio), axis=1)))
        trim = max(0, peak - round(0.020 * RATE))
        audio = fade(audio[trim:], 0.006, 0.040)
        sample_peaks.append(int(np.argmax(np.max(np.abs(audio), axis=1))))
        samples.append(audio)
    events = config.get("events", [])
    event_manifest = []
    for i, event in enumerate(events):
        t = float(event["time"])
        if not 0 <= t < duration:
            raise ValueError(f"Contact time outside video: {t}")
        leg = int(event.get("leg", i % 4))
        strength = float(event.get("strength", 0.8))
        pan = float(event.get("pan", -0.15 if leg in (0, 2) else 0.15))
        if leg not in range(4) or not math.isfinite(strength) or not math.isfinite(pan):
            raise ValueError("Contact leg must be 0..3 and levels must be finite")
        strength = float(np.clip(strength, 0.2, 1.2))
        pan = float(np.clip(pan, -0.5, 0.5))
        index = (i * 5 + leg) % len(samples)
        audio = samples[index].astype(np.float64).copy()
        # Gentle stereo bias; preserve recorded stereo width and conservative levels.
        audio[:, 0] *= math.sqrt((1 - pan) / 2) * math.sqrt(2)
        audio[:, 1] *= math.sqrt((1 + pan) / 2) * math.sqrt(2)
        audio *= 10 ** (args.foley_db / 20) * strength
        offset = round(t * RATE) - sample_peaks[index]
        if offset < 0:
            audio = audio[-offset:]
            offset = 0
        count = min(len(audio), len(mix) - offset)
        mix[offset : offset + count] += audio[:count]
        event_manifest.append(
            {
                "time": t,
                "leg": leg,
                "source": files[index].name,
                "strength": strength,
                "pan": pan,
                "alignment": "recorded impact peak aligned to contact timestamp",
            }
        )
    if args.music:
        music = decode(
            args.music,
            duration,
            args.music_start,
            "highpass=f=35,lowpass=f=14000,loudnorm=I=-25:TP=-5:LRA=7",
            ffmpeg=args.ffmpeg,
        )
        music = fade(music, 1.2, 2.0)
        count = min(len(mix), len(music))
        mix[:count] += music[:count] * 10 ** (args.music_gain / 20)
    raw_path = output.with_suffix(".premaster.wav")
    if raw_path.exists() or output.with_suffix(".json").exists():
        raise FileExistsError("Audio sidecar output already exists")
    if not np.isfinite(mix).all():
        raise ValueError("Audio mix contains non-finite samples")
    subprocess.run(
        [
            args.ffmpeg,
            "-n",
            "-v",
            "error",
            "-f",
            "f64le",
            "-ar",
            str(RATE),
            "-ac",
            "2",
            "-i",
            "-",
            "-c:a",
            "pcm_s24le",
            str(raw_path),
        ],
        input=mix.astype("<f8").tobytes(),
        check=True,
    )
    measures = loudness(raw_path, args.ffmpeg)
    # No automatic loudness increase for a deliberately sparse foley-only mix.
    gain = 0.0
    if args.music:
        gain = min(3.0, -21 - float(measures["input_i"]))
    limiter = (
        f"volume={gain}dB,alimiter=limit=0.794328:level=false:attack=5:release=80:latency=true"
    )
    subprocess.run(
        [
            args.ffmpeg,
            "-n",
            "-v",
            "error",
            "-i",
            str(raw_path),
            "-af",
            limiter,
            "-ar",
            str(RATE),
            "-ac",
            "2",
            "-c:a",
            "pcm_s24le",
            str(output),
        ],
        check=True,
    )
    final = loudness(output, args.ffmpeg)
    if not float(final["input_tp"]) < -1.0:
        raise ValueError("Final audio exceeds the true-peak safety limit")
    manifest = {
        "file": output.name,
        "duration": duration,
        "sample_rate": RATE,
        "channels": 2,
        "events": event_manifest,
        "music": args.music.name if args.music else None,
        "music_start": args.music_start if args.music else None,
        "foley_peak_db_before_overlap": args.foley_db,
        "integrated_lufs": float(final["input_i"]),
        "true_peak_dbtp": float(final["input_tp"]),
        "loudness_range_lu": float(final["input_lra"]),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "review": "Measured levels and source hashes checked. This script does not perform subjective listening review.",
        "provenance": "Recorded sand footsteps by Fantozzi, CC0; optional MindStream by DST, CC0. See sources.json.",
    }
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "events"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--foley-db", type=float, default=-12)
    parser.add_argument("--music", type=Path)
    parser.add_argument("--music-start", type=float, default=32)
    parser.add_argument("--music-gain", type=float, default=0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    main(parser.parse_args())
