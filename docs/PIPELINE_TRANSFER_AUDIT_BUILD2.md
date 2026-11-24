# Maxwell pipeline and transfer audit — Build 2

Build 2 retains the observational Build 1 capture design and adds four targeted improvements based on the first Helgen Keep capture.

## Changes from Build 1

1. **Completion publication ordering.** Both timeline-semaphore refresh and fence-wait completion paths write `GPU_TICK_COMPLETE` before release-publishing the corresponding completed tick to `gpu_tick`. A buffer usage reset that observes the published tick can therefore no longer appear in the audit sequence ahead of that completion event.
2. **Wall-clock anchors.** The audit stream writes a `WALL_CLOCK_ANCHOR` at approximately 100 ms intervals. Each anchor includes `steady_us` and `wall_epoch_us`, allowing the globally sequenced audit stream to be aligned to the device clock visible in the recording.
3. **Configured and active GPU log levels.** `CAPTURE_CONFIG` now records both the settings value and the actual active GPULogger level, as well as `use_optimized_vertex_buffers`. This is intentional: Build 1's capture recorded settings level 3 while the separate initialization line said `All` (4).
4. **Derived-hop and sequence-rank analysis.** The analyzer finds all destination VkBuffers populated by copies from the target buffer, then searches every descriptor write, descriptor-class bind and usage record for shaders that reference those destinations. It also pairs the k-th target compute dispatch with the k-th outbound copy group by sequence order, reports the tick-delta histogram, and checks whether the two copy ranges are contiguous.

The Build 1 source branch and its capture remain unchanged.

## Capture settings

Keep the same game save, route and graphics settings as Build 1:

- Optimized Vertex Buffers: **ON**
- GPU Logging: **Verbose** (configured setting value 3)
- Keep other optional Debug-page capture switches OFF unless a separate test explicitly requires them.
- Keep resolution, GPU accuracy, driver and frame limit unchanged.
- Record FPS and frame time in the video.
- Keep the on-screen device clock visible where practical.

The Android startup and renderer logs should agree on the intended logging level. Check both `gpu_log_level` and `active_logger_level` in `CAPTURE_CONFIG`. A value of 3 is Verbose; 4 is All. Both levels enable PipelineAudit, but use 3 to keep the capture at the planned Verbose setting.

## Helgen Keep observation windows

Use the same sequence:

1. Load into the flickering entrance chandelier.
2. Remain stationary for a short observation.
3. Leave the chandelier and travel to the first forge fireplace.
4. At the fireplace, perform the camera swivel and observe the stabilisation.
5. Leave the area.

For the visual comparison, preserve the exact device-clock timestamps for:
- chandelier arrival, stationary interval and departure;
- forge arrival;
- the camera swivel;
- the approximately 600 ms stabilisation point;
- forge departure.

The `WALL_CLOCK_ANCHOR` records provide the mapping from audit sequence to monotonic and wall-clock time. The first Build 1 capture cannot be retroactively given these anchors.

## Analysis priorities

### 1. Completion and buffer resets

Join each target `BUFFER_USAGE_RESET.previous_usage_tick` to the ordered `GPU_TICK_COMPLETE` records. A reset is only considered to have observed a completed resource when a completion record for that tick or a later tick precedes the reset in the global sequence.

### 2. Copy destinations and consumers

For every destination VkBuffer written from target VkBuffer `0x8ce00000008ce`, list:
- copy count, byte ranges, offsets and destination handle;
- every `DESCRIPTOR_WRITE` and `DESCRIPTOR_CLASS_BIND` referencing the destination;
- shader hash, target kind, stage, resource kind and `is_written` where recorded;
- `BUFFER_USAGE` records that identify later resource use.

Do not infer read/write direction from a descriptor write alone. Use the explicit `is_written` field when present; otherwise mark direction as not recorded.

### 3. Dispatch-to-copy sequence pairing

Pair the k-th target dispatch with the k-th target-buffer outbound copy group by global `seq`, not by equality of tick values. Report the histogram of `copy_tick - dispatch_tick`, whether each copy group appears later in sequence, and the two chunk offsets/sizes.

A two-chunk copy is not automatically a ring wrap. If source and destination offsets are contiguous, report that as a contiguous split rather than a wraparound.

### 4. Same-session stable/unstable comparison

Use the video clock to select roughly 1–2 seconds before and after the image stabilises at the forge. Compare:
- compute shader pairs and dispatch counts per shader;
- graphics shader/draw activity and draw counts per shader;
- descriptor and constant-buffer changes for shaders appearing or disappearing;
- dispatch cadence normalised per displayed frame;
- target-buffer copy and consumer activity.

Repeat the same comparison around chandelier arrival and departure. The exact wall-clock anchors should be used for sequence boundaries.

## Target identity

- Compute shader: `4577aed68dd75ecc`
- Guest GPU address: `0x508380000`
- Build 1 resolved Vulkan buffer: `0x8ce00000008ce`

Treat this as a traced resource, not yet as a proven lighting buffer. The shader's visual relevance remains a separate hypothesis to test by a controlled skip-dispatch intervention after its consumers are mapped.

## Capture integrity

Retain the full original `eden_gpu.log`, accompanying `eden_log.txt`, video and the final `PipelineAuditSummary`. Do not treat missing consumers or missing events as negative evidence if the summary reports dropped records, sequence gaps or an incomplete capture.
