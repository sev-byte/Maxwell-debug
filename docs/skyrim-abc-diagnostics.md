# Skyrim ABC diagnostics (Tests A, B and C)

This branch adds observational diagnostics only. It does not block shaders, change game data,
alter light selection, or change rendering behaviour.

## Files written to the Eden dump directory

- `skyrim_abc_diagnostics.log`: timestamped per-pipeline/per-scheduler-tick records.
- `skyrim_abc_buffers.bin`: deduplicated raw guest constant-buffer and storage-buffer payloads.
- Existing files such as `skyrim_gpu_events.log`, `skyrim_compute_events.log` and
  `skyrim_pipeline_diagnostic.txt` remain enabled.

## Test A — actual buffer inputs and candidate light records

For each sampled draw and shader stage, the log records the constant-buffer mask, each used
constant-buffer GPU address/size/content hash, and CB payload snapshots. It also resolves each
storage-buffer descriptor in the same way as BufferCache: the descriptor's guest GPU address,
the possible packed size field, the resolved resource size, and a snapshot of the first 64 KiB
of the guest storage-buffer payload.

The output also records vertex-stream/index-buffer addresses and draw arguments so a payload
can be associated with the mesh draw that referenced it.

Samples are de-duplicated per pipeline, geometry-buffer set, and constant-buffer binding set within a scheduler tick. This keeps separate meshes that reuse the same shader pipeline from collapsing into one sample. The capture is capped at 60,000 diagnostic records and 64 MiB of raw payloads per emulator process.

This does not label a buffer as a "light list" automatically. That semantic identification is the
offline reverse-engineering step; the build preserves the bytes and descriptor provenance needed
to establish it.

## Test B — CB2 descriptor references and actual texture resources

Each shader descriptor is identified by its resource kind (sampled image, storage image,
uniform texel buffer, storage texel buffer, or storage buffer). Texture-backed descriptors are
resolved through the guest TIC/TSC tables. The log includes the TIC/TSC indices, TIC backing GPU
address, format/type/dimensions/mip metadata, and raw descriptor words.

For every enabled constant-buffer slot 2, the raw 64-bit values at offsets 32 and 40 are recorded.
The descriptor table mapping shows whether either value is actually referenced as a texture or
image descriptor by that shader stage.

## Test C — CB5 emissive values and shader-stage linkage

For each sampled stage where CB5 is enabled, the log records the contents' hash and the three
32-bit floats at byte offsets 80, 84 and 88. The same record contains the stage shader hashes,
pipeline hashes and timestamp. This allows the values to be aligned with video brightness changes
and the saved shader modules can be inspected to determine how the fields are consumed.

The output does not assume that CB5 is the game's dynamic-light list.

## Binary payload format

`skyrim_abc_buffers.bin` is a sequence of little-endian 56-byte headers followed by
`captured_size` payload bytes. Header fields, in order:

1. `u32 magic = 0x41424353`
2. `u32 version = 1`
3. `u64 sequence`
4. `u64 guest_gpu_address`
5. `u64 FNV-1a content hash`
6. `u32 declared_size`
7. `u32 captured_size`
8. `u32 kind` (1 = constant buffer, 2 = storage buffer)
9. `u32 shader stage`
10. `u32 slot or SSBO binding index`
11. `u32 reserved`

Payloads are deduplicated by content hash, kind, stage and slot. Each object is capped at 64 KiB
and the binary file is capped at 64 MiB per emulator process. A deduplicated payload is still
referenced by hash in each timestamped text record.

## Helgen Keep capture procedure

Keep GPU Accuracy = Accurate. Use the same game/build/settings and capture three stationary
views in order:

1. Large chandelier near the entrance.
2. Intermediate wall sconce.
3. First fireplace beside the forge.

Hold each view still for roughly 30 seconds. Record a screen video with a visible clock or another
reliable timestamp. Avoid moving between views during each stationary window. The movement
between locations is kept separate from the three focus windows.

The diagnostic files are reset at process start, so copy the dump-directory files after the run
before starting another capture.
