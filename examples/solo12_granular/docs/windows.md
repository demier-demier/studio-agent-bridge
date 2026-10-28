# Windows with NVIDIA or AMD graphics

This is the native Windows x64 version of the same Solo12 simulation and video pipeline. It uses the original public robot model and physics code; it does not use WSL, CUDA emulation or an NVIDIA-only model API.

| Hardware | Particle simulation | Blender Cycles rendering |
| --- | --- | --- |
| NVIDIA | `--arch cuda` | `--device OPTIX` or `--device CUDA` |
| AMD | `--arch vulkan` | `--device HIP` |
| CPU | `--arch cpu` | `--device CPU` |

**GPU support is implemented, but Windows NVIDIA/AMD hardware validation is still pending.** The public GitHub Actions workflow runs native Windows x64 installation, CPU simulation, trajectory audit and launcher tests. It cannot establish GPU compatibility or performance. The million-particle video and timings in the main README were measured on an Apple M4 Pro. Hardware requirements depend on the selected driver and Blender version; this is not a claim that every NVIDIA or AMD card will work.

## Requirements

- Native Windows x64 and **Python 3.12 x64**. Windows ARM64 is outside this release's tested installation target.
- Current official graphics driver for your GPU, with CUDA support for NVIDIA simulation or Vulkan support for AMD simulation. The Taichi wheel does not require building the project from source. A Vulkan SDK is for building/debugging, not a prerequisite for this example's wheel installation.
- Blender with the requested Cycles backend, and FFmpeg for video assembly. The original scene was rendered with Blender 5.2; consult the [Blender GPU requirements](https://docs.blender.org/manual/en/latest/render/cycles/gpu_rendering.html) for your version, GPU and driver. AMD simulation uses Vulkan, while AMD rendering uses HIP; these are independent capabilities.
- Start with the small smoke preset. The full particle cache alone is approximately 2.8 GB; allow at least 8 GB of free disk for simulation and additional space for hundreds of rendered images. No Windows VRAM or performance minimum has been measured.

Get the source with Git, or extract the [Windows source package](https://github.com/demier-demier/studio-agent-bridge/releases/tag/solo12-granular-v1.1). The ZIP includes source and robot/audio assets; it is not an installer and does not bundle Python, drivers, Blender or FFmpeg.

```powershell
git clone https://github.com/demier-demier/studio-agent-bridge.git
Set-Location studio-agent-bridge/examples/solo12_granular
```

## Install

Run from the example directory in PowerShell:

```powershell
.\windows\setup.ps1
```

This creates `.venv`, validates Python 3.12 x64 and installs the pinned wheels. It does not activate the environment or change system execution policy. If your organization's policy prevents PowerShell scripts, use these equivalent commands directly instead of changing the policy:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt
```

For a Python installation without the `py` launcher, pass its executable path to `setup.ps1 -PythonCommand`. Paths containing spaces are supported.

## Run and audit

Choose the command for the installed GPU. Each launcher first runs twelve analytic contact checks on the requested backend, then runs a small coupled simulation, then audits the saved trajectory. Every output directory must be new.

```powershell
# NVIDIA
.\windows\run.ps1 -Vendor NVIDIA -Preset smoke

# AMD
.\windows\run.ps1 -Vendor AMD -Preset smoke

# Explicit CPU integration check
.\windows\run.ps1 -Vendor CPU -Preset smoke
```

The launcher prints the output path. To run the full case after the smoke check succeeds:

```powershell
.\windows\run.ps1 -Vendor NVIDIA -Preset video -OutputDirectory runs/nvidia-video
# Or:
.\windows\run.ps1 -Vendor AMD -Preset video -OutputDirectory runs/amd-video
```

The equivalent direct AMD commands are below. Replace `vulkan` with `cuda` for NVIDIA. For smoke, change `--preset video` to `--preset smoke` and pass `--minimum-travel 0` to the audit.

```powershell
.\.venv\Scripts\python.exe check_granular.py --backend vulkan
.\.venv\Scripts\python.exe simulate.py --preset video --arch vulkan --output runs/amd-video
.\.venv\Scripts\python.exe locomotion/audit_run.py runs/amd-video
.\.venv\Scripts\python.exe measure_tracks.py runs/amd-video
```

Backend selection is explicit. Unavailable GPUs and any nonempty `TI_ARCH` override fail instead of falling back to CPU. Runtime evidence records the requested and actual backend and a small atomic-kernel probe. Vulkan identifies the compute API, not the adapter vendor: on a system with multiple adapters, confirm the driver-selected device before attributing results to AMD. A successful initialization alone does not validate the full simulation: require the analytic checks, a smoke audit and then a full-run audit on the actual machine.

## Render

Set the actual path of your Blender executable. The PowerShell `&` call operator is needed when an executable path is quoted:

```powershell
$Blender = 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'
# Query the specific renderer you intend to use:
& $Blender --background --python-exit-code 1 --python cinema/render_scene.py -- --list-devices --device OPTIX
# For AMD, replace OPTIX with HIP. For NVIDIA CUDA, use CUDA.
```

For an AMD full run:

```powershell
$Run = 'runs/amd-video'
$Device = 'HIP'
& $Blender --background --python-exit-code 1 --python cinema/render_scene.py -- $Run --device $Device --shot hero --start 24 --end 240 --samples 48
& $Blender --background --python-exit-code 1 --python cinema/render_scene.py -- $Run --device $Device --shot contact --start 108 --end 198 --samples 48 --grain-subdivision 2
& $Blender --background --python-exit-code 1 --python cinema/render_scene.py -- $Run --device $Device --shot trail --still 240 --samples 64 --grain-subdivision 2
```

For NVIDIA, use `$Run = 'runs/nvidia-video'` and `$Device = 'OPTIX'` (or `'CUDA'` if that is your supported choice). CPU is the default renderer outside macOS; selecting `'CPU'` explicitly makes that choice clear. GPU mode enables only devices matching the requested backend and disables the CPU device. Missing or unsupported GPU devices cause an error before creating the render output directory. Use the recorded device information to confirm which renderer actually ran.

For a cheap preview of a completed smoke run, render only frame 30 with `--still 30 --samples 4 --resolution 640 360`. A smoke run has too few frames to generate the complete 16-second edit.

## Assemble the video

After all full-run shots are rendered:

```powershell
.\.venv\Scripts\python.exe audio/download_music.py --output "$Run/DST-MindStream.mp3"
.\.venv\Scripts\python.exe edit_video.py --run $Run --output runs/windows-edit --music "$Run/DST-MindStream.mp3" --ffmpeg 'C:\Tools\ffmpeg\bin\ffmpeg.exe'
```

Use the actual FFmpeg path, or omit `--ffmpeg` when it is on PATH. Optional `--hardware-label` must name the machine that actually ran the simulation. Omit `--music` for foley only. The editor uses the measured particle count and selected backend; it must not label a Windows run as an Apple M4 Pro run. The final file is `runs/windows-edit/Scenward-Solo12.mp4`.

## Troubleshooting and validation scope

- A CUDA error is not evidence that your AMD card failed: use Vulkan for AMD simulation.
- Vulkan initialization does not prove that Blender HIP is supported. Check rendering separately with `--list-devices` and a small preview.
- Check `TI_ARCH`, `CUDA_VISIBLE_DEVICES` and `TI_VISIBLE_DEVICE` if a different GPU was selected. The application rejects any nonempty `TI_ARCH` override. Use a shell without that override; the application does not rewrite your environment or drivers. Runtime API verification cannot detect a software Vulkan implementation, so inspect your driver/device setup before making GPU-performance claims.
- Avoid increasing particle count or running the renderer and simulator together until the small case passes. GPU memory and system memory are different limits on a discrete GPU system.
- Numerical accumulation can differ between CPU, Metal, CUDA and Vulkan. Passing bounded checks is expected; bit-identical trajectories and matching performance are not promised.
- The existing shallow-layer physics and [limitations](architecture.md) still apply. There is no learned locomotion policy or calibrated deep-sand model.

Primary references: [Taichi 1.7.4 runtime implementation](https://github.com/taichi-dev/taichi/blob/v1.7.4/python/taichi/lang/misc.py), [Taichi backend configuration](https://docs.taichi-lang.org/docs/global_settings), [Taichi 1.7.4 wheels](https://pypi.org/project/taichi/1.7.4/#files), [Blender GPU rendering](https://docs.blender.org/manual/en/latest/render/cycles/gpu_rendering.html).
