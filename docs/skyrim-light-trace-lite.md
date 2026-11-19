# Skyrim lightweight image trace

This branch replaces the broad ABC and per-draw captures with a narrowly scoped trace.

## What is observed

- The fragment shader with hash `0xde45de8572884068` is checked after normal graphics-pipeline configuration.
- Only sampled-image descriptors resolving to a 400×225 image are considered.
- The trace records the guest GPU address, Vulkan image handle, dimensions, format, shader hash and scheduler tick when that image is selected and sampled.
- The texture cache reports GPU modifications only when they target that exact Vulkan image handle. Repeated modifications in the same scheduler tick are coalesced.
- The Vulkan scheduler records render-pass image barriers only when that exact image is one of the render-pass attachments.
- Maxwell macro sequence assertions include the incoming method, expected argument method, macro register, macro entry, queued argument count, DMA segment and last-call flag. These details are emitted only when the assertion fails.

## What is deliberately not captured

There are no global draw logs, per-pipeline dumps, constant/storage-buffer snapshots, descriptor-table dumps, per-dispatch compute logs, GPU readbacks or forced GPU waits. The old experimental fragment-shader blacklist is disabled; this branch does not suppress any shader.

## Output

`skyrim_light_trace.log` contains target, sample, image-write and matching render-pass-barrier events.

The existing `eden_log.txt` contains the enriched Maxwell assertion messages.

The image-write hook reports texture-cache GPU modifications, not GPU completion timestamps. Render-pass barrier records describe command recording and the configured GENERAL-to-GENERAL barrier; they do not claim that the GPU had completed the work at the time the record was written. A compute or transfer producer may therefore appear as an image-write event without a matching render-pass barrier.

## Helgen Keep test

Keep GPU Accuracy = Accurate. Capture the same three stationary views, in order:

1. Large entrance chandelier.
2. Intermediate wall sconce.
3. First fireplace beside the forge.

Record a screen video with a visible clock or another reliable timestamp. Keep each view still during its capture window. Copy `skyrim_light_trace.log` and `eden_log.txt` after the run.
