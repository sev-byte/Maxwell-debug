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
