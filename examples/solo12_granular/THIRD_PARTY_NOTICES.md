# Source attribution and licenses

## Original implementation and presentation

The original simulator, controller additions, rendering/editing scripts and graphic assets in this example are distributed under the repository's [MIT license](../../LICENSE), copyright 2026 Bernardo Demier. This does not replace the licenses of the third-party materials below.

## Solo12 robot

- Source: [Open Dynamic Robot Initiative — robot_properties_solo](https://github.com/open-dynamic-robot-initiative/robot_properties_solo)
- Pinned upstream commit: `2201f454f4caffa0cad307fa8cbd1d64dd844008`
- License: **BSD-3-Clause**, retained verbatim in [robot/LICENSE-BSD-3-Clause.txt](robot/LICENSE-BSD-3-Clause.txt).
- Packaged materials: nine robot meshes, converted geometry and a modified simulation description derived from the upstream Solo12 model. [robot/PROVENANCE.json](robot/PROVENANCE.json) records asset hashes and modifications.
- Changes for this demonstration include experimental 20 mm spherical foot pads, collision exclusions for assembled overlapping parts, joint motors and a feedback crawl. The original authors do not endorse or validate these changes.

The rendered video depicts this derived Solo12 model. Redistribution of the model files must retain the upstream copyright notice, conditions and disclaimer.

## Sound recordings

Six sand-footstep recordings are from [Fantozzi's Footsteps (Grass/Sand & Stone)](https://opengameart.org/content/fantozzis-footsteps-grasssand-stone), created by Fantozzi and edited by qubodup, under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

Music in the released video is [MindStream by DST](https://opengameart.org/content/mindstream), also CC0 1.0. The full track is not vendored; the optional download helper uses its public source and verifies its recorded hash. See [audio/sources.json](audio/sources.json), [audio/CREDITS.txt](audio/CREDITS.txt) and [audio/LICENSE-NOTICE.txt](audio/LICENSE-NOTICE.txt).

Audio is edited sound design synchronized to simulated contact events; it is not recorded robot hardware or physically computed acoustics.

## Runtime dependencies

MuJoCo, Taichi, NumPy, Pillow, Blender and FFmpeg are separate projects with their own licenses. Python packages are installed from the pinned requirements. Blender and FFmpeg executables are not redistributed by this example. Review the licenses of your installed binaries when distributing a complete runtime.

No NVIDIA software, video or audio is copied into this example. Mentioning a platform or robot does not imply affiliation, sponsorship or a measured comparison.
