# Maxwell Build 3A — target ownership and readback ledger

Build 3A is a **measurement-only Vulkan build** for the Skyrim lighting investigation. It does not skip, poison, reorder, or force any GPU operation. It records the live storage-buffer binding for compute shader `0x4577AED68DD75ECC`, the buffer cache's ownership marks, readback batches, and the decisions that consume cache ownership.

## Capture order

1. Run the reproducible Helgen Keep route on **PC first**, with the same settings and scene used for the comparison captures.
2. Capture enough time at the entrance chandelier and the first forge-side fireplace to obtain a stable interval.
3. Keep the detailed GPU audit enabled. The target ledger remains dormant until the first live storage-buffer binding for shader 4577 is seen.
4. Repeat on Android only after the PC capture has been reviewed.

The previously observed binding was CPU-backed at `0x07491000`, size `0x00640000`, end-exclusive `0x07AD1000`. This is an example from an earlier capture, **not a hard-coded filter**. Build 3A derives each tracked interval from the live descriptor and logs its start and end-exclusive address. Ordinary and multi-range bindings are both covered.

## Event families

| Event | Meaning |
|---|---|
| `TARGET_RANGE_DISCOVERED` | New byte coverage discovered from a live 4577 storage-buffer binding or multi-range segment. Includes guest GPU address, backing CPU range, buffer, and offset. |
| `COMPUTE_BUFFER_BIND`, `COMPUTE_MULTI_RANGE_SEGMENT` | Per-bind descriptor values, including the current guest address/range and backing interval. These are logged each time the descriptor is bound, even when the tracked byte coverage already exists. |
| `TARGET_GPU_WRITE_MARK` | A new generation assigned when the buffer cache marks an overlapping interval GPU-modified. This is a cache ownership/write-intent mark, not proof of the exact bytes physically stored by the shader. |
| `TARGET_ASYNC_BATCH_SCHEDULED` | Target-overlapping BufferCache asynchronous readback scheduled by the fence path. Carries batch ID and schedule frame/tick/write sequence. |
| `TARGET_ASYNC_POP_RANGE`, `TARGET_ASYNC_BATCH_POP` | Per-range registration identity, newer-write overlap, batch mismatch, and pop latency in frames/ticks. |
| `TARGET_ASYNC_CANCEL` | Target registrations removed by `ClearDownload`, with the caller and count/range removed. |
| `TARGET_OWNERSHIP_CLEAR` | A range whose BufferCache GPU-owned state is cleared. `newer_write_bytes` counts bytes whose latest recorded write mark is newer than the readback's schedule sequence. Use `reason` to separate asynchronous completion from synchronous scheduling and CPU/DMA clears. |
| `TARGET_SYNC_READBACK` | Synchronous BufferCache readback request, copy scheduling, GPU completion, and guest-memory writeback. Fence association is deliberately marked untracked where the caller context is unavailable. |
| `TARGET_OWNERSHIP_DECISION` | Ownership view at `MustFlushRegion`, `OnCPUWrite`, `CachedWriteMemory`, and `SynchronizeBuffer` upload decisions; the generic BufferCache `GetFlushArea` path is also instrumented where used. Includes BufferCache state, memory-tracker state, ledger state, latest write mark and age. |
| `TARGET_DISPATCH`, `FRAME_METRICS`, `FENCE_SIGNAL_*`, `FENCE_WAIT_*` | Target dispatch cadence, frame elapsed time, fence signal/wait context. |

The asynchronous range-registration ledger is target-only and keeps batch IDs separately from the counted overlap range set. This lets offline analysis identify a pop consuming a registration added by a different batch.

## Per-capture summary

Produce one row per PC/Android capture, then a detail breakdown by route:

| Capture | Target dispatches | Dispatches/s | Frame time median / p95 | Async batches | Async pop latency median / p95 frames | Sync readback ranges / bytes | Ownership clears | Bytes cleared over newer write marks | Batch mismatches | Flush decisions: GPU-owned / not-owned |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| PC 3A | | | | | | | | | | |
| Android 3A | | | | | | | | | | |

Count ownership clears and newer-write bytes separately by `reason`. Report flush decisions by operation and state pair (`cache_gpu_owned`, `tracker_gpu_owned`, `ledger_gpu_owned`); do not collapse them into one total.

## Interpretation limits

- The write generation follows the BufferCache interval marked by `MarkWrittenBuffer` (or a GPU-modified range mirrored through BufferCache DMA copy). It is interval-granular, but it is **not shader-instrumented exact store coverage**. If the shader writes only a subrange inside a larger bound SSBO without changing the descriptor range, Build 3A will report the cache's bound/marked interval, not the exact shader store addresses.
- On Vulkan, the rasterizer's `GetFlushArea()` delegates to TextureCache rather than BufferCache, so a BufferCache `GetFlushArea` event is not expected from that override. Vulkan's buffer-cache ownership check is captured at `MustFlushRegion()`.
- Eden does not trap every arbitrary guest CPU load. The ledger reports the flush/download paths, ownership checks, and fence context visible to the emulator; it must not be described as a complete log of all CPU reads.
- A `TARGET_GPU_WRITE_MARK` is a cache ownership mark associated with a dispatch/binding, not a completion timestamp. Compare it with the separately logged dispatch, fence, and readback events.
- Build 3A is measurement-only. Skip-readback (3B), immediate fresh readback (3C), and wait-only timing control (3D) must remain separate intervention builds so their results are not mixed with the baseline ledger.

## Build status

This branch has source changes only. It has not yet been compiled or run. The repository's existing `build-maxwell-apk.yml` workflow is Android-specific and does not currently auto-trigger for this branch; the first requested validation is a PC Vulkan build/run.
