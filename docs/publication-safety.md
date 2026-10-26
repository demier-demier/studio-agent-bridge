# Publishing portable scene copies

Native files can contain local paths outside the visible scene. A short relative
path assigned in Blender may leave previous bytes after the null terminator in a
fixed-size field. FBX files can also retain original application filenames and
texture paths. Reopening a scene successfully does not prove those fields are
safe to distribute.

## Versioned publication workflow

The original laboratory now has a separate versioned publication workflow in
[examples/publication_safety](../examples/publication_safety/). It preserves its
input archive privately and produces a new package rather than changing raw
execution evidence.

1. Verify each source member against the original package manifest.
2. Open the day and night scenes in Blender. Write benign long values into known
   native path fields, then assign portable relative paths and save new copies.
3. Reopen the copies in independent Blender processes and compare declared model
   fingerprints. These cover geometry and scene properties listed by the example;
   base-scene fields compare exactly, while evaluated floating-point geometry has
   an explicit absolute tolerance and a measured maximum residual. These are not
   a comparison of every Blender datablock or application setting.
4. Parse FBX through Blender's bundled reader/writer, rewrite only allowlisted
   file-path properties, serialize and parse again. Compare the complete typed
   trees outside those explicit replacements, including decoded geometry arrays.
5. Update FBX hashes in a new transfer manifest, then validate that bundle in a
   new owned Unreal project and reopen the saved level separately.
6. Scan every publication member, including decompressed Blender contents, before
   building the versioned archive. Verify the ZIP and the exact public download.

The original renders remain the dated original renders. Metadata cleanup and
static transfer validation do not produce a new appearance or performance result.

## Identity and provenance

The current package is `industrial-lab-native-v2.zip`. Its `manifest.json` hashes
the actual publication bytes. `publication.json` maps original inputs to the new
copies and identifies the measured preservation checks. The external
`observed-native-proof.json` also identifies the final archive, avoiding a
self-referential archive hash inside the package.

Historical `evidence.json` and previous observed proof files retain their original
hashes and dates. They describe the original executions, not the newly saved
publication files. The original ZIP is retired from the current repository tree
and portal downloads; this does not rewrite Git history or erase previously
downloaded copies. Current reproduction commands use the versioned package.

## Threat model and limits

The publication gate is intended to prevent accidental distribution of known
local-path patterns and explicitly configured private identifiers, including
fragments left in native string padding. It scans the packaged content rather
than trusting a successful native operation or a filename. Native Python remains
privileged, and publication scanning is not a sandbox.

The scan is a bounded deterministic check. It cannot prove the absence of every
secret, encoded identifier, proprietary model or private visual detail. Configure
the identifiers that must remain private, inspect the actual files, and review
the scene's ownership and content. Unsupported or malformed native compression
must fail closed rather than silently skip scanning.

The publication writer validates candidates before publishing a new version and
refuses to overwrite an existing versioned artifact. Tests cover rejection and
preservation on failure. A validated archive does not certify arbitrary scene
quality, engineering safety, physical simulation, or all future native formats.
