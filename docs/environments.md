# Detailed environments through chat

The chat client supplies the artist and coordinator. It interprets a natural-language brief, writes native scripts, invokes Blender and Unreal, reviews images, and applies follow-up changes. Studio Agent Bridge supplies the native execution, revision records and measured artifacts. The server does not contain an LLM or turn arbitrary prose into a completed scene by itself.

## The finished-scene standard

High detail is the default. A finished environment includes complete architecture, equipped functional zones, secondary construction, surface detail, deliberate lighting and composition. Bevelled primitives may be building blocks, but an empty shell or a set of primitive placeholders is not an accepted final scene.

Use detail where it is visible and meaningful: joints, frames, cable routes, mechanical assemblies, ventilation, furniture, tools, controls, signage, roughness and surface variation. Share mesh data and textures for repeated elements. Preserve a hardware budget; increasing polygon counts is not a replacement for art direction.

The structured environment brief separates intended requirements from measured evidence. Its result is always `planned`. Completion requires native reopening and actual image review.

## Chat examples

> Build a robotics lab with complete walls, floor and ceiling, a robotic workcell, electronics benches and a server area. Use realistic scale, detailed assemblies, original PBR materials and balanced practical lighting. Show three interior views and a close-up. Keep an editable Blender file and reproduce the scene in Unreal.

> Keep the existing geometry and layout. Make a separate evening version, lower the ambient light, keep the task lights practical and move the camera to the electronics workbench. Show the new render and preserve the daytime file.

> Add a maintenance corridor with visible cable trays, labelled utility panels and service access. Match the existing material palette and transfer the changed environment to a new Unreal level.

The client resolves optional style/layout choices with explicit assumptions. Ask only for missing constraints that would materially change the work. User-supplied drawings, models and references stay in the workspace; public examples use original or explicitly licensed assets with recorded provenance.

## Native sequence

1. Read `studio_status`, call `studio_plan_environment`, and establish units, layout, zones, budget and acceptance views.
2. Create a Blender job. Build exportable assets in `StudioEnvironment`, using `studio_asset=True` and `studio_category`. Keep lights/cameras in `PresentationRig`.
3. Save the `.blend`, reopen it in a fresh process and inspect geometry, transforms, materials and texture availability.
4. Render multiple occupied zones and a close view. Inspect pixels and iterate on visibly unfinished details, clipping, floating objects, exposure and materials.
5. Export an environment manifest and reusable mesh assets. Retain source units, instance transforms, PBR maps, lights and cameras; preserve the native source.
6. Import into a separately owned Unreal project, reconstruct the environment, save and reopen its level, and compare measured geometry and placement.
7. Inspect a real Unreal render or viewport. A headless structural check cannot establish visual parity.
8. Make one meaningful revision from a chat instruction, saving new native artifacts and preserving the baseline.

The new `studio_environment` MCP prompt embeds this workflow. Older active connections can use the existing native tools; reconnect to discover newly added prompts/tools. The CLI `environment-plan` also exposes the validated brief.

## Recorded complete example

The [industrial laboratory](../examples/environments/industrial_lab/) demonstrates the sequence with 1,571 mesh instances, 84 evaluated FBX assets, 17 PBR materials and 18 texture maps. Editable day/night Blender files were reopened independently. A meaningful revision changed the lighting and cart placement; reversing it restored the baseline within measured tolerances.

The final Unreal level was reopened in a fresh editor process, checked against the same export manifest and captured through the GPU at 1920 × 1080. Its 1,571 mesh actors, 84 assets, nine lights and four cameras persisted. Native per-asset bounds checks used a 0.05 cm tolerance; the maximum observed error was below 0.000072 cm. The browser gallery labels Blender, Unreal and interactive mesh views separately so their different lighting is visible.

This is evidence for this original environment and the tested revision. New briefs still require their own native and visual acceptance checks.

## Evidence boundaries

| Evidence | Establishes | Does not establish |
| --- | --- | --- |
| Saved native file, SHA-256 and fresh reopen | The recorded artifact exists and is readable | Visual quality or a complete brief |
| Mesh/instance counts and bounds | Geometry coverage and measured scale | Identical surfaces, every vertex or correct shading |
| Three inspected Blender renders | Visible composition and finish in those views | The Unreal renderer's appearance |
| Native Unreal import/reopen and render | Saved target structure and visible target result | Packaged gameplay, performance across machines or full shader equivalence |
| A preserved baseline plus edited revision | A demonstrated change workflow | Arbitrary semantic editing without client reasoning |

PBR parameters and texture images can be reconstructed across hosts; arbitrary Blender shader graphs, Geometry Nodes, rigs and simulations do not automatically become equivalent Unreal systems. Native originals retain the richest editability. The client must report conversions and unsupported features, with `observed`, `failed` or `untested` outcomes.
