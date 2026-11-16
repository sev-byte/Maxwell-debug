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

## Capture inspection — 15 November 2026

The uploaded release asset `Eden.preflushable.log.zip` contains only `eden_gpu.log` (3,540,084 lines; 1.6 GB uncompressed). It does not contain the ordinary application log `eden_log.txt`.

**Logging distinction:** the `[SkyrimCompute]`, `[SkyrimComputeResource]`, `[SkyrimComputeCB]` and `[SkyrimBufferAccess]` events are written by GPULogger to `eden_gpu.log`. The three `MAXWELL-PREFLUSH` diagnostics are emitted with ordinary `LOG_INFO` calls in `vk_compute_pipeline.cpp` and `buffer_cache.h`; the ordinary logging backend writes to `eden_log.txt`. Their absence from this GPU-only ZIP is therefore not evidence that the preflush code failed.

### What the GPU log confirms

- 705 direct dispatch records for shader `0x4577aed68dd75ecc`, all with groups `320x6x1`.
- 705 writable SSBO resource records: binding 0, constant buffer 0, descriptor offset 784, `written=true`.
- 705 CB0 snapshots for that shader.
- 705 `SkyrimBarrierProbe` events report `barrier_disabled`; the compute-write barrier probe was off in this capture.
- The CB0 descriptor at byte offset 784 decodes consistently in all 705 snapshots to candidate GPU VA `0x508380000`; the following qword is `0x0000000000640000` (lower 32 bits `0x00640000`, upper 32 bits zero; 6,553,600 bytes).
- 705 storage-write access records all refer to one BufferCache window: `buffer_cpu_addr=0x7490000`, offset `4096`, size `6553600`. Since `Buffer::Offset()` is relative to `CpuAddr()`, the corresponding CPU-side start address is `0x7491000`.
- The target dispatches span 32,951 ms (705 records; 21.40 host-side records/second; median interval 45 ms, min 30 ms, max 178 ms). This is the host-side diagnostic record cadence, not hardware GPU execution time.
- The separate compute hash `0x8c0d043b68e7a6d1` appears only in a shader-compilation record; no dispatch/resource record for it appears in this capture.

The user reports no visible change with this test build. That result alone does not tell us whether the bypass path ran, because the required `MAXWELL-PREFLUSH` messages are in the missing ordinary application log.

### Supplied companion application log — 15 November 2026

The supplied file `eden_log(5).txt` was added to the public investigation branch as [`eden_log_companion.txt`](../captures/preflush-bypass/eden_log_companion.txt).

- Size: 16,780 bytes
- Lines: 110
- First/last elapsed timestamps: 0.001041s / 0.147786s
- Span: 0.146745 seconds
- Content: UDP input initialization followed by NCA decryption/key-generation errors
- `MAXWELL-PREFLUSH matched target lighting compute shader`: absent
- `MAXWELL-PREFLUSH target SSBO write`: absent
- `MAXWELL-PREFLUSH bypass`: absent

This is only a short startup fragment, not a runtime application log covering the 32.951-second GPU trace. The absence of markers in this fragment does **not** prove that the bypass failed. GitHub Actions now scans this file alongside the GPU capture and explicitly reports its short time coverage.

### Still needed

Please provide the complete `eden_log.txt` from the same test session as the GPU capture, including the runtime period. We need these messages:

- `MAXWELL-PREFLUSH matched target lighting compute shader`
- `MAXWELL-PREFLUSH target SSBO write` — compare against CPU address `0x7491000`, size `6553600`
- `MAXWELL-PREFLUSH bypass` — whether a CPU read reached the targeted bypass for that range

Only the full runtime log can distinguish:
- Match + SSBO-write + bypass: the intervention reached the readback path; unchanged visuals would make preflush alone an unlikely explanation.
- Match + SSBO-write but no bypass: the output was tracked, but no relevant CPU read reached the bypass.
- Match but no SSBO-write: investigate why the output binding did not enter the targeted write path.
- No match in a complete same-session log: investigate build/configuration or the dispatch predicate.

Full GitHub-runner scan, archive manifest, raw event samples and descriptor decoding: [preflush capture report](preflush-bypass-capture-report.md).


## Broad compute preflush experiment — build #126

The runtime application log `eden_log.txt.old(1).txt` resolved the earlier missing-logger issue. It contains 705 matches for the identified compute shader and 705 corresponding writable SSBO records. All 705 records refer to CPU range `0x7491000`, size `0x640000` (6,553,600 bytes), and all report `preflushable_before=false`. The log contains **zero** `MAXWELL-PREFLUSH bypass` messages.

Interpretation: shader matching and SSBO write marking worked, but the log does not show a CPU read reaching the targeted bypass. The 705 writes were not found to be preflushable before being marked, so this test did not demonstrate the specific stale-preflush-promise scenario.

### Build #126 change

Commit: `253c30abdb629bbd32c74424cdcc83cbb0aab160`

Added the per-game Debug setting **Lighting experiment → Disable light preflushing** (`maxwell_disable_light_preflush`).

When enabled, it broadens the intervention from one shader hash and one dispatch shape to **every writable compute-shader SSBO**:
- all writable compute SSBO ranges are marked as pending compute output;
- a prior CPU-read preflushable promise is revoked for those ranges;
- an overlapping CPU read cannot take the preflush shortcut and must take the non-preemptive download path;
- ordinary graphics-stage storage buffers are not changed by this flag;
- compute storage-image / image-descriptor paths are not yet included.

This is intentionally a broad A/B test of the compute-buffer route, not a complete volumetric-lighting intervention. Keep it off for other games; enable it only in Skyrim for this experiment. It is off by default.

### Test instructions

1. Install the build from workflow run #126.
2. Open Skyrim's per-game Debug settings.
3. Under **Lighting experiment**, enable **Disable light preflushing**.
4. Revisit the same scene and same flickering light sources used for the previous capture.
5. Record whether the flicker changes, disappears, or remains identical. A visual change would support a compute-SSBO readback/preflush contribution; no change would weaken that route but would not exclude storage-image or graphics/volumetric paths.

The next investigation should separately trace writable compute images and volumetric-related shaders. Do not describe those resource types as covered by build #126.


### Build failure and Debug-settings audit — 15 November 2026

Build #126 failed during C++ compilation in `vk_rasterizer.cpp`: two callers still referenced the old method name `HasTargetShaderReadFlush` after the buffer-cache API and range tracker had been widened to compute SSBOs. The compiler identified both stale references and suggested `HasComputeShaderReadFlush`. Both callers were corrected in commit `cacd278bbdb1f12ca8ff5bfdaa1f052e8351fc83`.

The Debug UI was checked across the complete registration path:
- Native config declares `maxwell_disable_light_preflush` as `SwitchableSetting<bool>`.
- Android `BooleanSetting` uses the exact same key.
- `SettingsItem.settingsItems` contains a normal `SwitchSetting` row for that key.
- All three string resources used by the row and section header exist.
- `SettingsActivity` loads the selected game's custom config and sets `SettingsViewModel.game` before building the settings navigation graph.
- The Debug presenter includes this row only when `settingsViewModel.game != null`, so it is absent from global Debug and appears in a game's own Debug settings.
- The standard switch click handler only writes the setting, refreshes the row/list, and does not request navigation.

Commit `6ef4259e6d5515cabda3c7424e2b2e86d7629f23` includes the per-game-only UI condition. This is a source-level audit; the settings screen has not been exercised on a device in this session. If it still exits unexpectedly, preserve the matching `eden_log.txt` from that launch so the navigation/crash can be distinguished from the build issue.

Build #127 includes the rasterizer caller fix; build #128 includes both the caller fix and the per-game-only Debug UI condition.


### Corrected ARM64 build — build #128 successful

- Workflow: https://github.com/sev-byte/Maxwell-debug/actions/runs/43767545164
- Source commit built: `6ef4259e6d5515cabda3c7424e2b2e86d7629f23`
- Result: complete success, including ARM64 compilation and APK artifact upload
- Artifact: `eden-maxwell-issue-tracking-arm64`
- Artifact ID: `14182984894`
- Artifact archive size: 23,449,433 bytes
- Artifact expiry: 29 November 2026, 22:25 UTC

The APK contains the corrected compute-wide preflush path and the per-game-only Debug UI row. The build itself validates compilation and packaging; the Settings screen navigation behaviour still needs confirmation on the user's device.

### Broad compute SSBO experiment — user result, 15 November 2026

The user tested successful ARM64 build #128 with **Lighting experiment → Disable light preflushing** enabled in Skyrim's per-game Debug settings before launch. They report that the flickering was unchanged. They also cleared the shader cache and repeated the test with a different graphics driver; neither changed the result.

**Interpretation at the time:** this was a strong visual negative for the broad compute-SSBO preflush/readback hypothesis. The follow-up image-cache tests are now documented below and have also been reported unchanged by the user.

**Logging caveat:** no matching runtime `eden_log.txt` has yet been attached for build #128, so the visual result is user-confirmed but the log marker `MAXWELL-PREFLUSH broad compute mode active` has not been independently checked for this session. A matching runtime log would confirm that the switch was read by the running renderer; it is not necessary to repeat the visual A/B test.

### Writable storage-image path — instrumented in build #129; hypothesis now parked

The source keeps these paths separate:

- Compute SSBOs are bound through `BufferCache::BindComputeStorageBuffer`; build #128's switch affects this path.
- Compute storage images are described by `info.image_descriptors`, resolved through `TextureCache::FillImageViews`, then supplied to image descriptors. They do not pass through the same compute-SSBO range tracker.
- Graphics stages independently bind storage buffers and writable images through the graphics pipeline configuration path.

Build #129 added explicit GPU-write tracking for selected writable compute and graphics storage-image descriptors and routed selected image writes through the image-cache preflush test. The user subsequently reports no visible change from the ambient and volumetric image tests. Do not continue this as an active hypothesis unless a future capture directly demonstrates that an untested image representation is involved.

The earlier shader hash `0x8c0d043b68e7a6d1` was only seen in a shader-compilation record in the earlier capture; it was not dispatched there. Do not label it a volumetric-lighting shader until a runtime dispatch and its writable image binding are observed.

## Ambient and volumetric image-cache preflush tests — build #129 and result

Build #129 kept the compute-SSBO switch separate and added two independent image-cache switches. The user reports that the ambient and volumetric tests produced no visible change to the flickering:

- `maxwell_disable_ambient_preflush`: broad candidate selector for GPU-written, non-3D colour images.
- `maxwell_disable_volumetric_preflush`: candidate selector for GPU-written true 3D colour images.

These are intentionally resource-class probes, not claims that the game labels every non-3D image as ambient or every 3D image as volumetric. The volumetric selector does not yet include effects represented as 2D textures, 2D arrays, or screen-space slices. A resource trace is needed to confirm the actual formats and shader passes used by Skyrim.

The image-cache experiment differs from the compute-SSBO test:
- It acts in `TextureCache::GetFlushArea`, where image preflushability is represented by `ImageInfo::forced_flushed` and `ImageViewFlagBits::PreemtiveDownload`.
- For selected pending GPU-written images, it forces `RasterizerDownloadArea::preemtive=false`, clears the delayed `PreemtiveDownload` view flag, and bypasses the per-core CPU-read-area cache. This makes CPU reads take the synchronous texture-cache download path even after an earlier read made the image appear preflushable.
- Writable compute and graphics storage-image descriptors are now explicitly marked as GPU-modified when their selected test switch is active. Existing render-target writes were already marked through normal render-target preparation.
- Both new switches are per-game and off by default. They are independent of the existing compute-SSBO switch.

**Result:** no visible change reported. The final investigation decision below supersedes the earlier proposed A/B protocol.
 
### Final investigation decision — preflushing parked as a cause, 15 November 2026

The user reports that the preflushing tests produced no change to the Skyrim lighting flicker. Together, the tested routes were:

- **Compute SSBO path:** build #128's broad switch was enabled in Skyrim's per-game Debug settings before launch. The user cleared the shader cache and also repeated the test with a different graphics driver; the flicker remained unchanged.
- **Ambient image-cache candidate:** build #129's switch targets GPU-written non-3D colour images, including selected writable storage images and render targets. User reports no change.
- **Volumetric image-cache candidate:** build #129's switch targets GPU-written true 3D colour images, including selected writable storage images. User reports no change.

**Decision:** strike preflushing off the active cause list. Do not spend more cycles widening or tuning the same preflush/readback hypothesis. Retain the switches and instrumentation in the repository as historical test controls, but treat this line of investigation as closed unless new direct evidence points back to it.

Scope caveat: the volumetric selector is based on true 3D colour-image resources; effects represented as 2D arrays, slice atlases, or other non-3D layouts are not semantically identified by that selector. This caveat is not a reason to keep pursuing preflushing speculatively. Reopen only if a future resource trace positively identifies a relevant untested resource or a new reproduction specifically links the flicker to a preflush event.

Build #129 (the image-cache tests) completed successfully:
- Workflow: https://github.com/sev-byte/Maxwell-debug/actions/runs/43775141925
- Source commit: `f054a938fcd3010d4886c25afc2b03ac2968e701`
- Artifact: `eden-maxwell-issue-tracking-arm64`
- Artifact ID: `14185964903`
- Size: 23,453,238 bytes
- Expiry: 29 November 2026, 23:49 UTC

**Next investigation should move away from preflush/readback behaviour.** Keep the broader lighting investigation open, with ambient/volumetric shader execution, resource contents, dispatch/draw cadence, and synchronization/barrier behaviour as separate hypotheses rather than assuming another cache-flush variant is involved.


## Follow-up review: cache-miss claim and volumetric lighting — 16 November 2026

The preflush A/B result is unchanged: the user reports no visible improvement from compute-SSBO, ambient-image or volumetric-3D-image preflush switches. This rules down the tested CPU readback/preflush mechanism. It does **not** disprove a GPU-side cache miss, a resource transition/coherency error, or a read-before-write ordering issue.

The prior runtime census has two material instrumentation gaps:
- `ComputePipeline::Configure` logs SSBO, sampled-texture and storage-image descriptors, but omits `texture_buffer_descriptors` and `image_buffer_descriptors`. `BufferCache::BindTextureBuffer` also does not emit a buffer-access event. A buffer-backed texture/image consumer can therefore be missing from the apparent writer/reader chain.
- The image trace exports `ImageViewBase::modification_tick` and `invalidation_tick`, but these fields are initialised to zero and not assigned in the inspected implementation. Do not use those zeros to infer that an image was untouched. The underlying `ImageBase::modification_tick` is a separate field.

The image-space volumetric system is confirmed in the Switch executable: GenerateCS → RaymarchCS → BlurHCS → BlurVCS, followed by apply/composite and lens-flare composite passes. It has enable, disable-interior, weather-update, quality, 3D volume dimension and temporal-accumulation controls. However, the chandelier/sconce runtime windows did not show the expected regular compute image descriptors or another compute shader hash; only `0x4577aed68dd75ecc` with an SSBO was recorded. This is strong evidence that the volume compute chain was not configured in those captured interior windows, but not absolute proof of total inactivity because the current logger omits buffer-backed descriptor arrays, does not enumerate every graphics image-space pass, and the runtime interior setting is unknown.

Separately, the private ESM audit resolved the capture cell to `YsgramorsTomb01`: it contains 70 placed point-light references across nine base forms, including multiple candle/torch variants with animation-mask flags and periods from about 0.167 to 0.5 seconds. The `DefaultTorch01NS_NoMove` variant is an important control: its period/amplitude fields are non-zero but its animation-mask bits are clear. This confirms intentional authored point-light modulation exists, but does not explain abnormal popping by itself.

Detailed game-side findings and exact placements are recorded in the private [Skyrim executable/ROMFS inspection report](https://github.com/sev-byte/Skyrim-exefs-romfs/blob/main/INSPECTION.md#11-placed-point-light-records-in-the-captured-interior).

**Current verdict:** preflush/readback is ruled down; general GPU cache miss/coherency is still unproven; the native volumetric pipeline is real but has not been shown active in the flickering indoor capture. Next work should close the descriptor-logging gaps, use the real image modification state, and capture a known volumetric exterior scene before attributing the candle flicker to volumetrics.
