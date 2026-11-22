# Maxwell pipeline and transfer audit — Build 1

## Purpose

Build 1 adds an observational ledger to the clean Maxwell baseline. It is intended to establish the real sequence linking pipeline targets, descriptor payloads, resource usage, transfer-path decisions, and queue submissions.

**This build does not attempt to fix rendering.** It does not change descriptor selection, image layouts, pipeline state, barriers, copy ordering, or the decision to place a transfer on the upload command buffer or the main command buffer. The existing decision is measured and recorded, not overridden.

## Baseline and scope

This branch is based directly on the original fork's `master` baseline. It deliberately does not merge the later barrier experiments, Skyrim-specific toggles, or other diagnostic branches.

The new audit records cover:

- Graphics and compute target discovery, including shader hashes and descriptor-class counts.
- Guest and internal compute-pass descriptor queue activity, including buffer, image, sampled-image, texel-buffer and device-address payloads.
- Descriptor-set allocation/commit and descriptor-buffer ring allocation, reuse, frame and generation events.
- Buffer creation, use ranges, usage-map resets and the precise per-range test used by upload reordering.
- Buffer copies, image copies, blits, image uploads and image readbacks.
- Scheduler submission ticks and the result returned by the queue submission.

The texture ledger also records image and image-view creation so descriptor view handles can be resolved back to the image handle and guest GPU address.

## Enable the audit

1. In Eden's GPU logging settings, enable GPU logging and choose **Verbose**.
2. Reproduce the same test sequence and keep the graphics settings unchanged between captures.
3. Collect `eden_gpu.log` from Eden's log directory. The audit entries are written there with the `[PipelineAudit]` prefix.

The audit channel is gated at Verbose. With GPU logging disabled, or with a lower log level, the detailed audit formatting and file writes are skipped. Existing non-audit GPU logging retains its previous controls.

## Recommended capture protocol

Use the established Helgen Keep route so captures are comparable:

1. Start at the large chandelier near the entrance and capture the flickering scene.
2. Move to the first fireplace by the forge and capture the second lighting point.
3. Keep the route, game state, emulator settings and observation intervals consistent between runs.
4. Make one baseline run with GPU logging disabled, then a separate audit run with GPU logging set to Verbose.
5. Record frame rate or frame-time behaviour for both runs. The difference is the practical logging-overhead check; do not compare different routes or settings.

The log is intentionally detailed and may be large. Keep the run focused on the two known locations rather than leaving Verbose logging enabled for an extended play session.

## Reading the two derived hops

### Hop 1 — producer use/reset to upload-path decision

Match records using the Vulkan buffer handle and guest address:

1. `BUFFER_USAGE` identifies the buffer range used by a producer and the scheduler tick at which it was marked.
2. `BUFFER_USAGE_RESET` records when that buffer's usage map is reset after its last-use tick is free.
3. `UPLOAD_REORDER_RANGE` records the exact destination range tested by `CanReorderUpload`, including whether the range was still marked as used.
4. `UPLOAD_REORDER_DECISION` records the resulting path: `upload_command_buffer` or `main_command_buffer`.

This is the critical window for cross-submission hoisting: the upload path is only eligible after the producer's tracked destination range is no longer marked used. The records let us distinguish an actual reset from a merely elapsed-looking time interval.

### Hop 2 — transfer path to submitted work

1. `BUFFER_TRANSFER` and `BUFFER_TRANSFER_BATCH` record source/destination handles, offsets, sizes, selected command-buffer path, barrier flag and an expected submission tick.
2. `IMAGE_COPY_REQUEST`, `IMAGE_BLIT_REQUEST`, `BUFFER_TO_IMAGE_REQUEST` and `IMAGE_TO_BUFFER_REQUEST` record the corresponding image transfer context.
3. `SUBMISSION_SCHEDULE` records the actual scheduler signal tick being prepared.
4. `SUBMISSION_ACCEPTED` records the result returned by the queue submission.

Treat the transfer's expected tick as a correlation hint and verify it against the actual submission events in the same log. Do not infer GPU completion merely from a scheduled or accepted submission; completion is governed by the semaphore/tick lifecycle.

## Event index

| Prefix | Meaning |
|---|---|
| `TARGET_DISCOVERY` | Graphics/compute target identity and descriptor-class totals |
| `PIPELINE_BIND` | Pipeline and descriptor binding mode at configure time |
| `DESCRIPTOR_QUEUE_*` | Guest/internal queue, frame, reservation and acquire state |
| `DESCRIPTOR_CLASS_BIND` | Uniform, storage and texel-buffer resource class |
| `DESCRIPTOR_WRITE` | Descriptor payload handle/address and range |
| `DESCRIPTOR_SET_*` / `DESCRIPTOR_POOL_*` / `DESCRIPTOR_BANK_*` | Traditional descriptor allocation and reuse |
| `DESCRIPTOR_BUFFER_*` | Descriptor-buffer ring frame, allocation and reuse |
| `BUFFER_CREATE` / `BUFFER_USAGE` / `BUFFER_USAGE_RESET` | Buffer identity and usage tracking |
| `UPLOAD_REORDER_*` | Per-range decision that controls upload hoisting |
| `BUFFER_TRANSFER*` / `BUFFER_CLEAR` | Buffer copy and clear path |
| `IMAGE_CREATE` / `IMAGE_VIEW_CREATE` / `SAMPLER_CREATE` | Image resources and descriptor-view identity |
| `IMAGE_COPY_REQUEST` / `IMAGE_BLIT_REQUEST` | Image-to-image operations |
| `BUFFER_TO_IMAGE_REQUEST` / `IMAGE_TO_BUFFER_REQUEST` | Buffer/image transfer requests |
| `SUBMISSION_*` | Queue submission scheduling and acceptance |

## Build 1 limits

Build 1 provides correlation evidence, not a diagnosis by itself. It does not yet prove which descriptor or transfer is responsible for the lighting artefact, and it does not add a timing intervention or rendering workaround. The next step should be chosen from the captured evidence, with the target, descriptor, transfer and submission records linked before changing execution behaviour.
