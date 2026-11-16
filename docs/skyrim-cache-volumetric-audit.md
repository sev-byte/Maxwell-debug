# Skyrim cache hypothesis and volumetric-lighting audit

Date: 16 November 2026

This document combines the current Maxwell renderer capture evidence with the private Skyrim Switch executable/plugin inspection. It separates findings that are demonstrated from remaining possibilities.

## Executive result

- The three tested CPU preflush/readback routes remain closed as the cause: compute SSBO, non-3D colour images, and true 3D colour images showed no visible change when disabled.
- That does **not** disprove a hardware GPU cache miss. Current logging records resource bindings and host-side submission/completion, not Adreno cache hit/miss counters.
- The compute shader `0x4577aed68dd75ecc` writes one large SSBO. In the full chandelier trace, the exact buffer identity was bound 994 times as storage: 994 writes, zero SSBO reads.
- The current chandelier/sconce capture contains only one descriptor-bearing compute shader hash, `0x4577aed68dd75ecc`, with SSBO descriptors only. No compute image or sampled-texture descriptors were recorded in the video-correlated windows. That is evidence against the volumetric compute stages being active in those specific windows.
- Skyrim's executable does contain a substantial volumetric-lighting subsystem, including 3D-volume generation, raymarch, blur, apply, and composite shader classes, plus temporal-accumulation controls. It is real, but it is not currently linked to the captured flicker.
- The broad post-compute barrier changed host-side compute cadence and reduced measured brightness transitions, but did not stop the flicker. This keeps scheduling/synchronization open; it does not establish a missing barrier.

## 1. Cache-miss claim: what the evidence says

### CPU readback/preflush

The three negative user tests are documented in [the preflush investigation record](https://github.com/sev-byte/Maxwell-debug/blob/skyrim-target-preflush-test/docs/skyrim-target-preflush-test.md). The tests covered:

- writable compute SSBO preflush;
- non-3D colour-image preflush (ambient proxy);
- true 3D colour-image preflush (volumetric proxy).

The tested settings were off by default and scoped per game. No visible change was reported. Do not keep widening this same CPU readback shortcut without new evidence.

### General GPU cache miss

Not proved or disproved. The available log messages identify shader hashes, descriptor classes, resource handles/addresses, access modes, queue submissions, and host-observed completion notices. They do not expose hardware L1/L2 cache hits/misses or Adreno memory transaction counters.

There is a further instrumentation defect in the image coherency export: `invalidation_tick` and `modification_tick` were logged from `ImageViewBase` fields that are initialized to zero and have no assignments in the inspected implementation. The actual modification tick belongs to the underlying image object. Those exported zeros are not evidence that the image was never modified or invalidated.

Relevant source:
- [Texture/image-view logging and image-view fields](https://github.com/sev-byte/Maxwell-debug/tree/skyrim-chandelier-wall-sconce-test/src/video_core/texture_cache)
- [Vulkan graphics pipeline](https://github.com/sev-byte/Maxwell-debug/blob/skyrim-chandelier-wall-sconce-test/src/video_core/renderer_vulkan/vk_graphics_pipeline.cpp)

## 2. Compute-written SSBO: a specific consumer check

The chandelier trace identifies one recurring direct compute dispatch:

- shader hash: `0x4577aed68dd75ecc`;
- group count: `320 × 6 × 1`;
- SSBO descriptor: binding 0, CB0 offset 784, one descriptor, writable;
- CPU buffer address: `0x7490000`;
- Vulkan buffer handle: `0x3e5a0000003e5a`;
- bound Vulkan range: offset 4096, size 6,553,600 bytes.

The full captured log was re-scanned using the `SkyrimBufferAccess` storage-buffer binding logger. The exact CPU/Vulkan identity appeared 994 times:

| Video-correlated window | Storage reads | Storage writes |
|---|---:|---:|
| Before video anchor | 0 | 20 |
| Chandelier focus | 0 | 313 |
| Camera movement | 0 | 226 |
| Wall-sconce focus | 0 | 435 |
| **Total** | **0** | **994** |

This strongly weakens the simple explanation that this compute dispatch writes the data later consumed by the visible scene as an SSBO. The target graphics shader pair is separately logged with two read-only 2D sampled-texture resources and no storage-buffer or storage-image descriptor.

Scope limits:
- The storage-binding logger does not currently log texel-buffer bindings. `BindTextureBuffer`, `BindGraphicsTextureBuffer`, and `BindComputeTextureBuffer` are separate routes.
- A buffer could be aliased through a texel-buffer/image-buffer descriptor, or consumed outside the capture windows.
- The target graphics shader trace is one selected shader pair, not a complete inventory of every graphics pipeline.

The compute-pipeline census in the successful full-trace analysis run records only `0x4577aed68dd75ecc` in all four windows. It reports `ssbo` descriptors only: 20 before the anchor, 313 chandelier, 226 movement, 435 wall-sconce; no `image` or `texture` compute descriptors were reported. The census logs compute-pipeline descriptors at `ComputePipeline::Configure`, which is called before the direct/indirect dispatch split. This means the absence of another descriptor-bearing compute pipeline is not explained merely by indirect dispatches skipping the direct-dispatch event marker.

The remaining alias-path gap is real: `BindTextureBuffer` and the graphics/compute texture-buffer bind helpers are not included in the `SkyrimBufferAccess` identity census.

Relevant source:
- [Compute pipeline descriptor capture](https://github.com/sev-byte/Maxwell-debug/blob/skyrim-chandelier-wall-sconce-test/src/video_core/renderer_vulkan/vk_compute_pipeline.cpp)
- [Compute dispatch path](https://github.com/sev-byte/Maxwell-debug/blob/skyrim-chandelier-wall-sconce-test/src/video_core/renderer_vulkan/vk_rasterizer.cpp)
- [Storage and texel-buffer bind paths](https://github.com/sev-byte/Maxwell-debug/blob/skyrim-chandelier-wall-sconce-test/src/video_core/renderer_vulkan/vk_buffer_cache.h)

## 3. Synchronization and cadence

The earlier broad post-compute barrier A/B remains an important, separate result. The added barrier slowed the host-side target dispatch event rate from 17.90/s to 13.53/s and reduced centre-screen brightness transitions from 9.07/s to 6.60/s, but flicker remained visible.

The two clips had different camera paths and durations (30 seconds OFF versus 15 seconds ON), so this is not a controlled causal result. It nevertheless shows the barrier changed the rendering schedule enough to alter the observed brightness-transition rate. Do not summarise this as “barriers had no effect”; also do not claim it proves that a missing barrier is the root cause.

There is already a pre-dispatch `READ_BARRIER` in the normal direct-compute path in `RasterizerVulkan::DispatchCompute`. It makes prior memory writes visible to subsequent compute reads. That is not identical to a post-dispatch compute-write dependency for a later graphics or image-sampling consumer.

Full measurement and methodology: [compute barrier A/B report](https://github.com/sev-byte/Maxwell-debug/blob/skyrim-target-preflush-test/docs/skyrim-barrier-ab-test.md).

## 4. Native volumetric-lighting subsystem in the inspected executable

The update-labelled Switch executable contains a coherent image-space volumetric-lighting family:

- compute: `ISVolumetricLightingGenerateCS`, `ISVolumetricLightingRaymarchCS`, `ISVolumetricLightingBlurHCS`, `ISVolumetricLightingBlurVCS`;
- apply/composite: `ISApplyVolumetricLighting`, `ISCompositeVolumetricLighting`, `ISCompositeLensFlareVolumetricLighting`;
- engine classes: `BGSVolumetricLighting`, `BSVolumetricLightingRenderData`, `ImageSpaceEffectVolumetricLighting`.

The executable contains exact ARM64 ADRP+ADD references to the read-only shader-name strings at:

| Shader class name | Code reference |
|---|---|
| `ISVolumetricLightingGenerateCS` | `0x00D7DC54` |
| `ISVolumetricLightingRaymarchCS` | `0x00D7DC70` |
| `ISVolumetricLightingBlurHCS` | `0x00DFD2C0` |
| `ISVolumetricLightingBlurVCS` | `0x00DFD5C8` |
| `ISApplyVolumetricLighting` | `0x00DFC998` |
| `ISCompositeVolumetricLighting` | `0x00DFEB00` |
| `ISCompositeLensFlareVolumetricLighting` | `0x00DFECAC`, `0x00DFEF60`, `0x00DFF06C` |

These offsets are references to the named strings from code; they are not yet decompiled function bodies.

### Registered settings

The executable also has references to volumetric enable switches, a control to disable volumetrics indoors, weather-update control, quality selection, low/medium/high texture width/height/depth/format, noise-texture width/height/depth, intensity, density scale/contribution, phase contribution/scattering, range, wind speed, custom colour, and temporal accumulation.

The temporal pair is notable:

- `bVolumetricLightingEnableTemporalAccumulation`
- `fVolumetricLightingTemporalAccumulationFactor`

The existence of these controls establishes that the system can have a history-accumulation path. It does not establish that temporal accumulation is enabled in the user's capture or that it is responsible for the light flicker.

The separate `bVolumetricLightingDisableInterior` control matters for indoor scenes: we do not yet know the setting value on the user's installation. A scene where interior volumetrics are disabled will not exercise the full interior volume path.

### Related shader source as an architectural reference only

The public [Skyrim Community Shaders volumetric-lighting implementation](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/src/Features/VolumetricLighting.cpp) uses the same named shader family. Its current HLSL sources are useful to understand the resource topology, but they are **not** proof of the exact shader bytecode in the inspected Switch build.

In that reference implementation:

1. **Generate** reads shadow-map arrays, a 1D depth-repartition lookup, and a 3D noise texture; it writes a 3D density volume. Its density/scattering calculation uses the directional-light direction, shadow tests, noise, density contribution, and phase scattering. This is an atmospheric/directional-lighting pipeline, not the local candle/torch point-light update itself.
2. **Raymarch** reads and writes the same 3D density volume, accumulating density along its depth axis.
3. **Horizontal/vertical blur** operate on 2D screen-space volumetric-lighting and depth textures. The reference code has explicit bounds/clamping for dynamic resolution because sampling outside the active render area could read stale pixels from the unused part of a full-resolution target.
4. **Apply** samples the 3D volumetric result and, when temporal accumulation is enabled, reprojects a previous frame using motion vectors and previous depth before blending historical lighting.
5. **Composite** adds the volumetric-lighting contribution to the final colour.

Relevant references:
- [Generate compute shader](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/package/Shaders/ISVolumetricLightingGenerateCS.hlsl)
- [Raymarch compute shader](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/package/Shaders/ISVolumetricLightingRaymarchCS.hlsl)
- [Horizontal blur](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/package/Shaders/ISVolumetricLightingBlurHCS.hlsl)
- [Vertical blur](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/package/Shaders/ISVolumetricLightingBlurVCS.hlsl)
- [Apply/temporal pass](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/package/Shaders/ISApplyVolumetricLighting.hlsl)
- [Feature setup and dispatch adjustments](https://github.com/community-shaders/skyrim-community-shaders/blob/dev/src/Features/VolumetricLighting.cpp)

There are interesting implementation details in that reference: it adjusts the raymarch dispatch because the modified raymarch shader now iterates through the depth internally, and it explicitly skips an invocation whose read/write texture setup is in the wrong order. It also clamps horizontal/vertical blur work to the dynamic-resolution active region to avoid stale edge pixels. These are worthwhile leads for inspecting the exact pipeline ordering and resource transitions, but they belong to a community-modified PC implementation and must not be projected onto the Switch binary without verification.

### Shader archive identification

The only indexed path in `Skyrim - Shaders.bsa` is `shadersfx/shaders011.fxp`, size 116,484,924 bytes, SHA-256:

`2a74ce023da5c867ae430f1b8584972a6d350821fa93df42b634cc60e42da5df`

The package was extracted in memory and its header recorded, but its internal technique/shader table has not been decoded. Mapping the named volumetric entries to actual guest shader code hashes remains outstanding.

## 5. Is volumetric lighting responsible for the captured candle/sconce flicker?

Current evidence says **probably not in the particular chandelier/sconce capture**, with the following reasoning:

- The executable's volumetric system is confirmed, but static code strings alone cannot prove runtime execution.
- The full compute descriptor census in the captured chandelier, movement, and wall-sconce windows contains only the one SSBO-writing shader, `0x4577aed68dd75ecc`. There are no compute image or sampled-texture descriptor records for the other volume passes.
- The reference volumetric implementation requires a mixture of 3D volume images, sampled shadow/noise/depth resources, and 2D blur targets. That resource topology is absent from the captured compute census.
- The scene contains ordinary placed `LIGH` references with authored flicker parameters. Those are separate from the volumetric system and from the NIF emissive animation.
- The candle/torch local-light flicker in the game data is therefore a more direct scene-specific explanation for the *intended* oscillation, while the remaining bug is why the unstable renderer makes the visible result diverge from stable.

This is not an absolute exclusion: the volumetric package has not been mapped to runtime shader hashes for the Switch build, no complete graphics-stage volumetric pipeline census exists for these clips, and the interior enable/disable configuration is unknown. Still, it is no longer the leading explanation for local point-light flicker around the chandelier and sconces.

## 6. Next useful work

Prioritise instrumentation that closes identifiable gaps rather than another broad preflush toggle:

1. Add read/write identity logging to the graphics and compute texel-buffer/image-buffer binding paths, so a buffer cannot disappear from the consumer audit simply because it is bound as a texel buffer.
2. Correct image-coherency logging to read the underlying `ImageBase` modification tick and actual invalidation state, not the unassigned `ImageViewBase` counters.
3. Decode `shaders011.fxp` sufficiently to map the volumetric shader entries to actual guest program code and Maxwell shader hashes.
4. During a known scene with volumetric rays enabled, capture the full graphics and compute descriptor topology and correlate it with queue submissions/completions. For an interior test, first verify that interior volumetrics are enabled.

Do not reopen the CPU preflush hypothesis without new direct evidence. Do not claim a GPU cache miss from descriptor logs alone.
