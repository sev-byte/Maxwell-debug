# Skyrim compute-write barrier A/B test

Diagnostic captures comparing Skyrim in Eden 98 with the experimental Vulkan compute-write barrier disabled and enabled.

- [`Eden98barrierOFF.zip`](https://github.com/sev-byte/Eden-98-barrier-off-on/releases/download/capture-a-barrier-off-on/Eden98barrierOFF.zip) — Compute Barrier Probe OFF
- [`Eden98barrierON.zip`](https://github.com/sev-byte/Eden-98-barrier-off-on/releases/download/capture-a-barrier-off-on/Eden98barrierON.zip) — Compute Barrier Probe ON

Both captures were recorded with Maxwell Issue Tracking enabled. The barrier probe is the experimental variable; the diagnostics remain enabled in both runs.



## Related projects

- Capture repository: [`sev-byte/Eden-98-barrier-off-on`](https://github.com/sev-byte/Eden-98-barrier-off-on) — contains the Eden 98 OFF/ON release assets and timestamp-based extraction workflow.
- Code repository: [`sev-byte/Maxwell-debug`](https://github.com/sev-byte/Maxwell-debug/tree/skyrim-light-data-capture) — contains the diagnostic instrumentation and compute-barrier probe on the `skyrim-light-data-capture` branch.
- Probe introduction: [commit `4176a67`](https://github.com/sev-byte/Maxwell-debug/commit/4176a67bded689e6bc7fb7f071799e18b1eef51d).

## Why this experiment exists

The original symptom was intermittent/flickering Skyrim lighting in Eden on the Adreno 750. Earlier diagnostics had concentrated on identifying the relevant graphics/compute shaders, their constant buffers and resource bindings. That left a separate possibility: a compute dispatch could write a storage resource, but later GPU work might not observe those writes in the intended order or visibility scope.

The probe was therefore added as a deliberately broad A/B intervention. It inserts an extra Vulkan memory barrier immediately after each guest compute dispatch, allowing us to test whether strengthening compute-write visibility changes the symptom. It is diagnostic only, not a production fix proposal.

## Exact code behaviour

The setting is `maxwell_compute_barrier_probe`, default OFF. The Android setting is shown alongside Maxwell Issue Tracking. The user must leave Maxwell Issue Tracking ON for the diagnostic events to be recorded.

For both direct and indirect guest compute dispatches, when the probe is enabled, the code records:

- Source stage: `VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT`
- Destination stage: `VK_PIPELINE_STAGE_ALL_COMMANDS_BIT`
- Source access: `VK_ACCESS_SHADER_WRITE_BIT`
- Destination access: `VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT`

The code then writes a `[SkyrimBarrierProbe]` event containing the shader hash, direct/indirect path, dispatch dimensions (zero dimensions for indirect), and whether the barrier was recorded. The same build logs `[SkyrimBufferAccess]`, `[SkyrimQueueSubmit]` and `[SkyrimGpuComplete]` events to help relate resource accesses to submission/completion. Normal draw and constant-buffer diagnostics provide workload identity.

The barrier is broad: it is inserted after every guest compute dispatch, not only a confirmed Skyrim lighting dispatch or a particular storage buffer. This can alter scheduling and performance, so any visual result must be checked against the actual event workload.

## Why the two Eden 98 versions exist

The paired captures were produced with the same diagnostic profile enabled, to keep instrumentation comparable:

| Capture | Maxwell Issue Tracking | Compute Barrier Probe |
|---|---:|---:|
| [Barrier OFF](https://github.com/sev-byte/Eden-98-barrier-off-on/releases/download/capture-a-barrier-off-on/Eden98barrierOFF.zip) | ON | OFF |
| [Barrier ON](https://github.com/sev-byte/Eden-98-barrier-off-on/releases/download/capture-a-barrier-off-on/Eden98barrierON.zip) | ON | ON |

This isolates the extra compute-write barrier as the intended variable. The two archive sizes are not expected to match: logging volume, event frequency and capture duration can change their compressed size. Size is not an outcome metric.

The capture repository's workflow chooses video-correlated event windows using `wall_ms`:

- OFF: 22:21:30–22:22:21 on 13 November 2026
- ON: 22:37:00–22:37:25 on 13 November 2026

The windows should be compared by shader identity, dispatch counts and dimensions, draw/CB activity, buffer writes/reads, barrier-probe records, queue submissions, GPU completions and render-pass cadence. First establish that comparable workloads are being compared; do not compare global event totals without accounting for the different window lengths.

## Earlier diagnostic versions and why they changed

The sequence matters because some earlier branches were interventions, while later branches were observational:

1. **Maxwell Issue Tracking (6 November).** Added a single diagnostic profile to turn on comprehensive GPU/Vulkan logging without having to enable many independent logging toggles. This established the foundation for repeatable captures.
2. **Light-buffer diagnostic v1/v2 (7 November).** Added draw, shader/buffer address and texture-descriptor records. The goal was to find which buffers and descriptors were bound to the flickering scene and whether handles/content changed from draw to draw. The v2 capture expanded the information available around the flickering interval.
3. **Shader-isolation and descriptor tests (8–9 November).** Temporarily blocking selected fragment shaders, and separately aliasing the two CB2 descriptor candidates at offsets 32 and 40, were controlled attempts to see whether specific shader/resource inputs affected the symptom. These were not the same experiment as the barrier probe. Because blocking shaders changes the workload, later work favoured observational instrumentation.
4. **Cache-barrier and render-pass tracing (9 November).** The `skyrim-diag-cache-barriers` branch logged when existing `FragmentBarrier` and `TiledCacheBarrier` methods were called, with a sequence number and render-pass state. Its commit explicitly preserved existing implementation; it did **not** insert the new compute-write barrier. The render-pass branch added timestamps, framebuffer and render-area context to establish ordering.
5. **Native recorder (10 November).** Added frame IDs and resource/image/descriptor/buffer transition records so resource lifetime and synchronization activity could be followed across draws and dispatches.
6. **Light-data capture (11–13 November).** Focused the logs on Skyrim draw shader hashes, constant buffers, compute shader identities/resources/dispatches, render attachments and buffer ranges. The main scene shader pair used as a reference was vertex `a71664c0a2643d30` and fragment `331f0de4dc79b7ba`. These diagnostics were intended to keep the workload identifiable while moving beyond generic Vulkan logging.
7. **Compute barrier probe (13 November).** Added the distinct `maxwell_compute_barrier_probe` switch and resource/submit/completion timeline events. The Eden 98 OFF/ON release uses this version of the experiment.

## How to interpret the result

- **If ON visibly stabilizes the flicker:** investigate compute-write visibility/order further. This is evidence for a synchronization-related contribution, not proof that this broad barrier is the correct final fix.
- **If ON makes no visible difference and the shader/dispatch/render workload is comparable:** the broad post-compute barrier is a weaker explanation for the symptom.
- **If ON changes dispatch/draw counts, render cadence or queue timing substantially:** separate a true visibility effect from timing/scheduling perturbation before drawing a conclusion.

The first comparison should use only the two timestamp windows above and should match shader hashes and dispatch types wherever possible. Keep all conclusions provisional until both logs are parsed and aligned with the corresponding video intervals.

## Related earlier code

- [Cache barrier call tracing](https://github.com/sev-byte/Maxwell-debug/commit/7cc0f623e14f35b0fd113038e6c1437b983bbc9e) — diagnostic logging only; not the compute-write barrier intervention.
- [Render-pass timing/identity tracing](https://github.com/sev-byte/Maxwell-debug/commit/d41eab985a80f00ddc2e48bb4ba2e496c4dca8db).
- [Native recorder frame association](https://github.com/sev-byte/Maxwell-debug/commit/20714065df10483a845d8da1f7f9e7615e6311c9).
- [Compute barrier probe and resource timeline](https://github.com/sev-byte/Maxwell-debug/commit/4176a67bded689e6bc7fb7f071799e18b1eef51d).


## Visual and dispatch-cadence follow-up — 14 November 2026

The paired recordings were aligned to the exact video-correlated event windows and analysed per frame. The active gameplay segments used for one-second correlation were 30 seconds OFF (video-relative seconds 15–44) and 15 seconds ON (seconds 5–19), excluding loading/menu seconds.

- Centre-screen grayscale threshold crossings: OFF 272/30 s = 9.07/s; ON 99/15 s = 6.60/s.
- Large frame-to-frame brightness jumps (>30 grayscale units): OFF 267/30 s = 8.90/s; ON 97/15 s = 6.47/s.
- The ON clip still shows repeated bright/dark changes. The barrier did not eliminate the flicker in this test.

One-second event-count correlation between \`SkyrimCompute\` and brightness-transition count was Pearson \`r=0.540\` / Spearman \`ρ=0.628\` OFF, versus Pearson \`r=0.071\` / Spearman \`ρ=0.268\` ON. This may indicate that the barrier changes the alignment between bursts of rendering activity and visible brightness changes. The sample is small and camera paths differ, so it is not causal proof.

Probe \`wall_ms\` timestamps inside the full gameplay seconds show:

| Timing | OFF | ON |
|---|---:|---:|
| Probe count | 537 | 203 |
| Rate | 17.90/s | 13.53/s |
| Mean interval | 55.87 ms | 73.97 ms |
| Median interval | 56 ms | 73 ms |
| P10–P90 interval | 48–62 ms | 69–80 ms |
| Interval CV | 0.094 | 0.063 |

This is evidence that the barrier slows and slightly regularises the **host-side dispatch-record cadence**. It is not a measurement of hardware GPU execution/completion time.

**Current conclusion:** the broad post-compute barrier changes the dispatch rhythm and appears to reduce the measured brightness-transition rate, but flicker remains visible. It is not a fix as tested. A controlled repeat from the same saved position, with a fixed camera and identical 30-second OFF/ON windows, is needed before assigning the visual reduction to the barrier.

Full data and methodology: [capture repository visual/GPU correlation report](https://github.com/sev-byte/Eden-98-barrier-off-on/blob/main/analysis/video-gpu-correlation.md). Successful correlation/timing workflow: [run #10](https://github.com/sev-byte/Eden-98-barrier-off-on/actions/runs/43542953191).
