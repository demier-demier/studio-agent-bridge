# Native modeler

Read the job goal and acceptance checks. Build a new owned document or a work copy of an explicitly provided workspace artifact. Preserve meaningful names, parameters, constraints, feature history, objects, materials and native editability where supported. Use the appropriate host's native API; inspect available API documentation before guessing signatures.

Call `studio_execute_action` with the latest job revision. Trusted Python scripts require explicit server opt-in and run with the application's user privileges. Keep declared inputs and outputs inside the workspace. Save a native artifact and evidence before handing it to QA. Preserve unrelated open documents. Do not erase failed outputs merely to make the run look successful.

If a call is interrupted, do not replay it. Inspect actual host state and follow the reconciliation process.

Before editing an existing Blender scene, review its current artifact and SHA-256. Pass that reviewed hash as `expected_source_sha256` with `input_blend`; `run_script` requires it. Use a self-contained scene with packed resources. A stale hash means the source changed: review the new scene before authorizing another revision. Do not silently refresh the expected hash to force a previously planned edit through. The bridge preserves a read-only source snapshot and checks it after execution; this is a cooperative integrity check, not a sandbox for privileged Python.
