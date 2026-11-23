# Maxwell pipeline and transfer audit — Build 1

## Purpose and baseline

Build 1 is an observational diagnostic branch based directly on the original Maxwell fork's master baseline. It does not merge the later barrier experiments or Skyrim-specific rendering toggles.

It does **not** attempt to fix the flicker. It records pipeline targets, descriptor payloads, resource identities, usage tracking, transfer-path decisions, descriptor/ring reuse and GPU completion so the evidence can identify which execution path needs a controlled follow-up test.

The instrumentation does not intentionally change pipeline selection, descriptor contents, copy ordering, image layouts, barriers or upload-hoisting decisions. Audit writes are best-effort; an audit logging failure is counted rather than propagated into the renderer.

## Capture configuration

Use the same game state, emulator settings and route for every capture.

1. Set **Optimized Vertex Buffers = ON**. Keep it ON for every capture in the comparison. The effective Android setting is written to the standard emulator log as **[CaptureConfig] use_optimized_vertex_buffers=true|false** for every run, including audit-disabled runs. Reject or separately label a capture if it does not say true.
2. Enable GPU logging at **Verbose** for audit captures. PipelineAudit records are gated at Verbose. The same effective settings are also repeated as a CAPTURE_CONFIG audit event in Verbose captures.
3. Keep resolution, accuracy, driver, frame limit and all other graphics settings unchanged.
4. Use the established Helgen Keep route: the large chandelier near the entrance, then the first fireplace by the forge.
5. Capture a video with timestamps for the flicker interval and retain the full eden_gpu.log.

Do not silently change the optimized vertex buffer setting between runs. It is a controlled configuration variable and should be recorded with each result.

## Event ordering and tick semantics

Every PipelineAudit row carries a global seq number. Sequence allocation and the file write are serialized together, so sequence numbers provide the authoritative total order across the recording thread, scheduler worker and semaphore/fence completion thread. Use seq for correlation; do not infer causal order from timestamps or from messages emitted by different threads.

The tick fields have distinct meanings:

- expected_submit_tick on a recorded transfer is the scheduler's next logical tick at the point the transfer is queued.
- SUBMISSION_SCHEDULE semaphore_value is the signal value actually assigned to that submission. It should match the transfer's expected tick.
- SUBMISSION_ACCEPTED means the queue accepted the submission; it is **not** proof that the GPU finished it.
- GPU_TICK_COMPLETE tick is emitted when the timeline semaphore refresh or fence-wait thread observes completion.

For a transfer, join expected_submit_tick to semaphore_value, then wait for a GPU_TICK_COMPLETE with the same or a later tick before treating the resource as GPU-free.

## What is recorded

| Prefix | Meaning |
|---|---|
| CAPTURE_CONFIG | Effective GPU log level and optimized vertex buffer setting |
| TARGET_DISCOVERY / PIPELINE_BIND | Graphics/compute target identity, shader hashes, descriptor counts and binding mode |
| COMPUTE_DISPATCH | Direct/indirect dispatch, compute shader hash and workgroup dimensions |
| DESCRIPTOR_QUEUE_* | Guest/internal descriptor queue frame and reservation state |
| DESCRIPTOR_CLASS_BIND / COMPUTE_BUFFER_BIND | Descriptor class plus guest GPU address, backing CPU address, Vulkan buffer handle and buffer device address |
| COMPUTE_MULTI_RANGE_SEGMENT / COMPUTE_MULTI_RANGE_DESCRIPTOR | Guest range mapped across backing segments and the final composite descriptor buffer |
| DESCRIPTOR_WRITE | Actual descriptor payload; compute writes also inherit shader/stage and resource-address context |
| DESCRIPTOR_SET_SLOT / DESCRIPTOR_SET_COMMIT | Descriptor set slot's previous use tick, observed completed tick and newly assigned tick |
| DESCRIPTOR_BUFFER_* | Descriptor ring allocation, generation, frame reuse waits and exhaustion stalls |
| BUFFER_CREATE / BUFFER_VIEW_CREATE | Vulkan buffer and texel-buffer-view identity |
| BUFFER_USAGE / BUFFER_USAGE_RESET | Buffer identity, tracked range and usage/reset ticks |
| UPLOAD_REORDER_RANGE / UPLOAD_REORDER_DECISION | Per-range IsRegionUsed check and resulting upload-vs-main-command-buffer decision |
| BUFFER_TRANSFER* / BUFFER_CLEAR | Buffer copy/clear handles, offsets, sizes, selected path and expected submission tick |
| IMAGE_CREATE / IMAGE_VIEW_CREATE / SAMPLER_CREATE | Image, view and sampler identity, including guest GPU addresses |
| IMAGE_COPY_REQUEST / IMAGE_BLIT_REQUEST | Image-to-image copy/blit context |
| BUFFER_TO_IMAGE_REQUEST / IMAGE_TO_BUFFER_REQUEST | Image upload/readback context |
| SUBMISSION_* / GPU_TICK_COMPLETE | Submission scheduling, queue acceptance and observed completion |

## Hypotheses the build can test

### Hop 1 — producer usage/reset to upload-hoisting decision

Join events by Vulkan buffer handle and backing CPU address:

1. BUFFER_USAGE records a producer's buffer range and the scheduler tick.
2. BUFFER_USAGE_RESET records when the usage map is cleared after the tracked last-use tick is free.
3. UPLOAD_REORDER_RANGE records the exact destination range check and whether that range is still marked used.
4. UPLOAD_REORDER_DECISION records whether the copy is sent to upload_command_buffer or main_command_buffer.
5. BUFFER_TRANSFER records the actual copy handles/path and the expected submission tick.

This is the cross-submission hoisting window. The log shows whether the reset occurred before the range check, whether the destination range was still considered used, and which command buffer received the transfer.

### Hop 2 — target descriptors and resource identity

For compute shader 4577aed68dd75ecc, use COMPUTE_BUFFER_BIND to connect:

**guest GPU VA → backing CPU address → VkBuffer handle → VkDeviceAddress**

DESCRIPTOR_WRITE records inherit that compute shader hash and the current resource scope. A regular buffer descriptor matches by VkBuffer handle; a descriptor-buffer address matches by device address; a texel-buffer descriptor matches through BUFFER_VIEW_CREATE (VkBufferView → VkBuffer). Image descriptors resolve through IMAGE_VIEW_CREATE (VkImageView → VkImage → guest GPU VA).

The target address to search for is **0x508380000**. The post-processor reports all matching writes and groups them by shader hash/stage.

### Hypothesis 3 — descriptor slot or descriptor-ring reuse before GPU completion

This is an additional hypothesis, not an assumed cause.

For traditional descriptor sets, compare DESCRIPTOR_SET_SLOT:
- previous_use_tick: the tick that last used the slot;
- observed_completed_tick: the GPU completion value observed while selecting it;
- assigned_use_tick: the new tick assigned to the slot;
- reuse_safe: whether the observed completion had reached the previous use.

A slot is suspicious only if it is selected for reuse with reuse_safe=false or observed_completed_tick is less than previous_use_tick.

For descriptor buffers, compare DESCRIPTOR_BUFFER_REUSE_WAIT_BEGIN and DESCRIPTOR_BUFFER_REUSE_WAIT_END. A normal reuse that begins before completion but returns with completed_after_wait=true is safe. A reuse wait that still reports false, or an allocation that reuses a previously occupied frame without a completed wait, is a finding to investigate.

Always compare against GPU_TICK_COMPLETE, not just SUBMISSION_ACCEPTED.

## Overhead comparison

Use an A/B/B/A sequence to reduce thermal and time drift:

- **A:** GPU audit logging disabled (GPU log level Off).
- **B:** GPU audit logging enabled (GPU log level Verbose).
- Run: **A, B, B, A**, with equal capture durations and the same route/settings.

Keep **Optimized Vertex Buffers ON** for all four runs. The OFF/ON factor above refers to audit logging, not the optimized vertex buffer setting.

Record FPS, frame-time distribution (especially p95), compute dispatch rate and the number of visible flicker crossings for each identical observation interval. COMPUTE_DISPATCH gives dispatch count/rate in audit captures. For audit-disabled captures, obtain dispatch rate from an independent profiler/counter if available; do not use line counts from a disabled audit log. Calculate the flicker measure as:

**visible flicker crossings ÷ compute dispatches**

Use the same video interval and dispatch-count interval. Compare the two A captures with each other and the two B captures with each other before attributing a difference to logging overhead.

## Coverage and missing records

At orderly GPU logger shutdown, PipelineAuditSummary reports:

- attempted: audit records submitted to the writer, including records that failed formatting/writing;
- written: complete audit lines written;
- dropped: records that could not be written or formatted;
- last_sequence: highest assigned sequence number.

A missing summary means the app may have crashed, logging may not have shut down cleanly, or the log may be truncated. Sequence gaps and a mismatch between written and parsed records should be treated as capture-integrity problems.

Run the post-processor after each capture. It reports expected descriptor entries, captured descriptor writes, target-buffer matches, unresolved descriptor identities, transfer-timeline events and premature-reuse findings:

**python3 tools/pipeline_audit/analyze_pipeline_audit.py eden_gpu.log --shader-hash 4577aed68dd75ecc --gpu-addr 0x508380000**

A missing consumer is only evidence when the relevant target's descriptor/transfer coverage is resolved and the audit summary shows no unexplained dropped records. Otherwise classify it as **unresolved**, not as "no consumer".

## Recommended capture sequence

1. Confirm CAPTURE_CONFIG says use_optimized_vertex_buffers=true.
2. Capture the A/B/B/A overhead sequence using the same Helgen Keep route.
3. For the diagnostic run, begin at the entrance chandelier and then move to the first fireplace by the forge.
4. Keep the capture focused; Verbose logs can grow quickly.
5. Run the analyzer and retain its output alongside the video, exact settings and APK/build commit.

## Build 1 limits

Build 1 provides correlation evidence, not a diagnosis by itself. It does not yet identify the lighting mesh or prove a descriptor/transfer race. The next experiment should be selected only after the target buffer's descriptor writes, usage/reset/reorder timeline, submission/completion timeline and reuse checks have been joined by address, handle and sequence.
