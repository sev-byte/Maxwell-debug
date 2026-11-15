# Skyrim target-shader preflush test

## Target

- Compute shader hash: `0x4577aed68dd75ecc`
- Direct dispatch dimensions: `320x6x1`
- Target: writable compute storage-buffer ranges bound by this exact shader and dispatch shape.

## Diagnostic intervention

This branch is an isolated Vulkan-only experiment. It does not alter all compute shaders or all GPU writes.

For matching target dispatches, BufferCache:
1. removes the `preflushable` marker from each writable compute SSBO range;
2. records that range as having a pending target-shader write;
3. clears that pending marker when the range is downloaded or otherwise cleared.

The Vulkan CPU-read path now asks both TextureCache and BufferCache for a flush area. The guest-memory read-area fast path also asks whether the requested range overlaps a pending target-shader write. If it does, it bypasses the cached-area early return and re-enters the normal CPU-read flush path.

This last step is important: clearing `preflushable` alone would not be a valid experiment while the per-core cached read-area fast path could return before `GetFlushArea` is called.

## Scope limits

- Only hash `0x4577aed68dd75ecc` with direct dimensions `320x6x1` is selected.
- Indirect dispatches and other compute shaders are untouched.
- Only writable compute buffer ranges are tracked by this intervention.
- No broad GPU wait, global barrier, shader disable, or global cache-policy change is added.

## Suggested test

Use the same save position, camera direction, scene, and capture duration as the original chandelier trace. Keep Maxwell Issue Tracking enabled and capture the emulator log plus video.

Check whether matching target writes are followed by CPU-read downloads of the same SSBO range. Compare visible flicker and dispatch/read/flush cadence against the unmodified `skyrim-stable-30hz-cadence` build.

A change in flicker would support a stale CPU-visible buffer / skipped-download explanation. It would not by itself prove the preflushable marker is the only cause; the experiment also corrects Vulkan's omission of the BufferCache query and bypasses the cached CPU-read-area shortcut for this target range.

## Capture inspection — 15 November 2026

The uploaded release asset `Eden.preflushable.log.zip` contains only `eden_gpu.log` (3,540,084 lines; 1.6 GB uncompressed). It does not contain the ordinary application log `eden_log.txt`.

**Logging distinction:** the `[SkyrimCompute]`, `[SkyrimComputeResource]`, `[SkyrimComputeCB]` and `[SkyrimBufferAccess]` events are written by GPULogger to `eden_gpu.log`. The three `MAXWELL-PREFLUSH` diagnostics are emitted with ordinary `LOG_INFO` calls in `vk_compute_pipeline.cpp` and `buffer_cache.h`; the ordinary logging backend writes to `eden_log.txt`. Their absence from this GPU-only ZIP is therefore not evidence that the preflush code failed.

### What the GPU log confirms

- 705 direct dispatch records for shader `0x4577aed68dd75ecc`, all with groups `320x6x1`.
- 705 writable SSBO resource records: binding 0, constant buffer 0, descriptor offset 784, `written=true`.
- 705 CB0 snapshots for that shader.
- 705 `SkyrimBarrierProbe` events report `barrier_disabled`; the compute-write barrier probe was off in this capture.
- The CB0 descriptor at byte offset 784 decodes consistently in all 705 snapshots to candidate GPU VA `0x508380000`; the following qword is `0x0000000000640000` (lower 32 bits `0x00640000`, upper 32 bits zero; 6,553,600 bytes).
- 705 storage-write access records all refer to one BufferCache window: `buffer_cpu_addr=0x7490000`, offset `4096`, size `6553600`. Since `Buffer::Offset()` is relative to `CpuAddr()`, the corresponding CPU-side start address is `0x7491000`.
- The target dispatches span 32,951 ms (705 records; 21.40 host-side records/second; median interval 45 ms, min 30 ms, max 178 ms). This is the host-side diagnostic record cadence, not hardware GPU execution time.
- The separate compute hash `0x8c0d043b68e7a6d1` appears only in a shader-compilation record; no dispatch/resource record for it appears in this capture.

The user reports no visible change with this test build. That result alone does not tell us whether the bypass path ran, because the required `MAXWELL-PREFLUSH` messages are in the missing ordinary application log.

### Supplied companion application log — 15 November 2026

The supplied file `eden_log(5).txt` was added to the public investigation branch as [`eden_log_companion.txt`](../captures/preflush-bypass/eden_log_companion.txt).

- Size: 16,780 bytes
- Lines: 110
- First/last elapsed timestamps: 0.001041s / 0.147786s
- Span: 0.146745 seconds
- Content: UDP input initialization followed by NCA decryption/key-generation errors
- `MAXWELL-PREFLUSH matched target lighting compute shader`: absent
- `MAXWELL-PREFLUSH target SSBO write`: absent
- `MAXWELL-PREFLUSH bypass`: absent

This is only a short startup fragment, not a runtime application log covering the 32.951-second GPU trace. The absence of markers in this fragment does **not** prove that the bypass failed. GitHub Actions now scans this file alongside the GPU capture and explicitly reports its short time coverage.

### Still needed

Please provide the complete `eden_log.txt` from the same test session as the GPU capture, including the runtime period. We need these messages:

- `MAXWELL-PREFLUSH matched target lighting compute shader`
- `MAXWELL-PREFLUSH target SSBO write` — compare against CPU address `0x7491000`, size `6553600`
- `MAXWELL-PREFLUSH bypass` — whether a CPU read reached the targeted bypass for that range

Only the full runtime log can distinguish:
- Match + SSBO-write + bypass: the intervention reached the readback path; unchanged visuals would make preflush alone an unlikely explanation.
- Match + SSBO-write but no bypass: the output was tracked, but no relevant CPU read reached the bypass.
- Match but no SSBO-write: investigate why the output binding did not enter the targeted write path.
- No match in a complete same-session log: investigate build/configuration or the dispatch predicate.

Full GitHub-runner scan, archive manifest, raw event samples and descriptor decoding: [preflush capture report](preflush-bypass-capture-report.md).


## Broad compute preflush experiment — build #126

The runtime application log `eden_log.txt.old(1).txt` resolved the earlier missing-logger issue. It contains 705 matches for the identified compute shader and 705 corresponding writable SSBO records. All 705 records refer to CPU range `0x7491000`, size `0x640000` (6,553,600 bytes), and all report `preflushable_before=false`. The log contains **zero** `MAXWELL-PREFLUSH bypass` messages.

Interpretation: shader matching and SSBO write marking worked, but the log does not show a CPU read reaching the targeted bypass. The 705 writes were not found to be preflushable before being marked, so this test did not demonstrate the specific stale-preflush-promise scenario.

### Build #126 change

Commit: `253c30abdb629bbd32c74424cdcc83cbb0aab160`

Added the per-game Debug setting **Lighting experiment → Disable light preflushing** (`maxwell_disable_light_preflush`).

When enabled, it broadens the intervention from one shader hash and one dispatch shape to **every writable compute-shader SSBO**:
- all writable compute SSBO ranges are marked as pending compute output;
- a prior CPU-read preflushable promise is revoked for those ranges;
- an overlapping CPU read cannot take the preflush shortcut and must take the non-preemptive download path;
- ordinary graphics-stage storage buffers are not changed by this flag;
- compute storage-image / image-descriptor paths are not yet included.

This is intentionally a broad A/B test of the compute-buffer route, not a complete volumetric-lighting intervention. Keep it off for other games; enable it only in Skyrim for this experiment. It is off by default.

### Test instructions

1. Install the build from workflow run #126.
2. Open Skyrim's per-game Debug settings.
3. Under **Lighting experiment**, enable **Disable light preflushing**.
4. Revisit the same scene and same flickering light sources used for the previous capture.
5. Record whether the flicker changes, disappears, or remains identical. A visual change would support a compute-SSBO readback/preflush contribution; no change would weaken that route but would not exclude storage-image or graphics/volumetric paths.

The next investigation should separately trace writable compute images and volumetric-related shaders. Do not describe those resource types as covered by build #126.
