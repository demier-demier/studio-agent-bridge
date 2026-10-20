# Studio Agent Bridge browser demo

Static, responsive case-study page with the **actual Fusion → Blender → GLB** fixture, native measurements, source attribution and downloadable native files. The browser is a read-only mesh viewer, not a CAD editor or a native MCP host.

The `/environment/` case adds a complete original robotics laboratory, several real Blender/Unreal views, interior-camera GLB exploration, a reversible lighting/layout example and downloadable native scenes. Renderer labels distinguish actual native images from the browser's own lighting.

## Reproducible build

Use Node 22.12+ (Node 22 in CI). Direct dependencies are pinned and the lockfile fixes transitive versions.

```sh
cd web
npm ci
npm run build
npm run preview
```

`dist/` is generated and ignored. Deploy its contents to `/studio-agent-bridge/`; relative asset URLs also support another subdirectory. The navigation link back to the portfolio uses `/`. No API key, server, analytics or external CDN is needed. The model-viewer/Three.js bundle is local and about 293 KB gzipped. Third-party licences are included in `public/THIRD_PARTY_NOTICES.txt`.

`npm run build` first verifies every committed asset against `public/assets/manifest.json`, confirms the native checks passed and checks the GLB hash against the native export evidence. CI installs with `npm ci` and performs this build separately from the Python job.

Environment assets have a separate hash manifest in `public/assets/environment/`. Packaging requires a successful independent reopen of both portable `.blend` files and a current per-object GLB material-assignment check. `scripts/package-environment.py --config /absolute/local/package-config.json` consumes explicitly selected verified run directories and render paths; it copies original native assets, strips only PNG metadata, writes curated evidence without private local logs, and validates the resulting ZIP. The local configuration is not committed.

## Source assets

- `rpi5-open-tray.glb`: the actual baseline STL imported into Blender at 0.001 scale, then exported via Studio Agent Bridge. A fresh process reimported it: one mesh, 1,472 triangles, approximately 101 × 72 × 11 mm.
- `rpi5-render.png` and `fusion-native.png`: original native render/viewport images. Text metadata was removed; image data was preserved.
- `evidence.json` and `dimensions.json`: measured results and attributed source facts. Fields distinguish original native-run hashes from sanitized public artifacts.
- `rpi5-native-case.zip`: two native Fusion configurations, STEP/STL exports, a Blender scene, images, scripts, source metadata and an internal hash manifest. No official third-party drawing is included.

To recreate the public package from a newly verified local delivery, first run `examples/fusion/rpi5-open-tray/sanitize_public_scene.py` through the Blender bridge on the source `.blend`. This produces and reopens a work copy with a relative render path and no unused file-browser screen retaining a local directory. Then run:

```sh
python3 scripts/package-public-assets.py \
  --native-zip /absolute/path/to/the/verified-native-case.zip \
  --sanitized-blend /absolute/path/to/the/reopened-sanitized-scene.blend
```

The script preserves the original ZIP, strips only PNG metadata, substitutes the verified sanitized scene, updates evidence and internal manifests, and fixes archive timestamps. Native F3D/STEP/STL bytes are unchanged. It refreshes the committed asset manifest; review any changed hash before building. The original local proof has its own hashes, so it is not interchangeable with the sanitized public package.

The demo falls back to the native render if the 3D component reports an error. WebGL is required for the interactive mesh. Browser appearance does not certify physical board fit, fabrication or engineering performance.
# Source archive hosting

The portfolio publisher creates `/studio-agent-bridge-source.zip` from the same clean Git commit recorded in `release.json`. It contains the complete tracked source, tests, documentation and example assets, except the generated native-scene ZIP, which is available as a separate download. An included `SOURCE_ARCHIVE_README.txt` explains where to restore that one file before building the demo. This keeps each public file within the hosting size limit. When deploying independently, also create the archive at the hosting root or update its download link.
