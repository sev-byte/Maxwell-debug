# Target shader relevance intervention — skip-dispatch build

This is a separate, deliberately invasive test build based on the Build 2 diagnostic branch.

## What it changes

It skips only compute shader `4577aed68dd75ecc` in `RasterizerVulkan::DispatchCompute()`, after the normal pipeline configuration and descriptor/resource preparation have run, but before either direct or indirect dispatch is recorded.

Every suppressed occurrence emits:

`TARGET_SHADER_INTERVENTION mode=skip_dispatch shader_hash=0x4577aed68dd75ecc expected_submit_tick=...`

No other compute shader is skipped. No buffer is poisoned or overwritten by the intervention.

## Purpose

This is a relevance test, not a proposed fix. It asks whether executing this specific shader contributes to visible output in the Helgen Keep test scene.

- If the scene visibly changes, the candidate shader has a visible downstream effect in this test. Compare what changes and inspect its mapped destination consumers.
- If there is no visible change, that weakens the case that this shader is responsible for the observed lighting in this scene. It does not prove the shader is irrelevant in every scene or state.
- If the game becomes unstable or crashes, record that as the result; do not use the skip build for normal play.

## Safe comparison procedure

1. Keep the normal **Eden Pipeline Audit Build 2** installed.
2. Install this separate **Eden Target Shader Skip Test** APK alongside it. Its application ID is distinct.
3. Use the same Skyrim save, Helgen Keep route, resolution, GPU accuracy, driver and frame limit.
4. Keep Optimized Vertex Buffers ON.
5. Keep GPU Logging at Verbose. Leave optional Vulkan API calls, memory tracking, shader dumps and driver-debug switches OFF.
6. Record the same chandelier and forge intervals with FPS, frame time and the device clock visible.
7. Note whether the flicker changes, what other visible effects appear, and whether performance changes.
8. Upload the complete GPU log and video to a separate private release or clearly labelled capture entry. Do not mix it with the normal Build 2 capture.

This branch is a test instrument only. It must not be merged into the normal audit or baseline branch.
