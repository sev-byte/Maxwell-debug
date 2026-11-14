# Skyrim stable-cadence experiment

This branch adds a reversible, opt-in timing intervention for the compute dispatch already isolated in the barrier A/B captures.

## Target

- Compute shader hash: `0x4577aed68dd75ecc`
- Direct dispatch dimensions: `320x6x1`
- Target interval: `33,333,333 ns` (approximately 30 starts per second)

The limiter is intentionally narrow: it only matches this shader hash and these direct dispatch dimensions. Indirect dispatches and all other shaders are untouched by the limiter condition.

## Behaviour

When `maxwell_compute_cadence_30hz` is enabled, the Vulkan rasterizer delays an early matching dispatch until at least 33.33 ms after the previous matching dispatch start. It does not synthesize, drop, reorder, or replay a guest dispatch. It preserves the original dispatch parameters and submits every original dispatch.

This is a **maximum-rate limiter / minimum-interval test**, not a frame-rate generator. If the game naturally invokes the shader less often than 30 Hz, the limiter does not create extra invocations or force empty 30 Hz ticks.

The wait occurs on the guest GPU command-processing path before compute pipeline configuration and resource-cache locks. As a result, work later in that command stream is also held behind the delay. This is intentional for this timing experiment, but it can affect overall emulation pacing and performance.

## Diagnostics

Keep Maxwell Issue Tracking enabled to capture `[SkyrimComputeCadence]` records. Each record reports the target shader, dispatch dimensions, target interval and inserted wait in microseconds.

## Suggested A/B matrix

| Run | 30 Hz cadence limiter | Broad compute barrier |
|---|---|---|
| A | Off | Off |
| B | On | Off |
| C | Off | On |
| D | On | On |

Use the same unstable save position, camera direction, run duration and graphics settings. Record video and GPU diagnostics in each run. Compare the visible brightness-transition rate against the actual matching-dispatch start intervals and GPU queue/completion events.

The broad barrier and cadence limiter are independent toggles. Do not attribute an improvement to a targeted resource barrier from this test; this branch changes timing only.

## Important scope limitation

The hash/dimensions above identify the compute shader used in the recent barrier captures. Before treating this as the definitive stable-versus-unstable render sequence, verify it against the exact sequence identified in the earlier stable-control comparison. The limiter is a diagnostic intervention, not a proposed production fix.
