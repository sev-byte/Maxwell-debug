# Skyrim Switch exefs/romfs source archive

The original game-data working archive is maintained separately in the owner's private repository:

**[sev-byte/Skyrim-exefs-romfs](https://github.com/sev-byte/Skyrim-exefs-romfs)**

The repository is private and is intended to contain only the owner's own Switch dump. The source data must remain unchanged and must not be copied into the Maxwell source repository or its build artifacts.

## Uploaded archive and inspection status

The owner uploaded the release [Original Skyrim dump](https://github.com/sev-byte/Skyrim-exefs-romfs/releases/tag/Skyrim) on 2026-11-14. It contains eight sequential volumes (`.001` through `.008`), totalling 13,298,584,465 bytes. The outer ZIP64 entries are stored without compression (`Method = Store`).

The ZIP64 central directory was readable from volume `.008` alone. 7-Zip reported 199 files and 7 folders under the internal root path:

`The Elder Scrolls V_ Skyrim [01000A10041EA000] + update`

The final-volume listing reported an unavailable-start error because earlier split volumes were absent, but its directory records were readable. The first and final volume SHA-256 values independently matched the GitHub release asset digests. The remaining six volume digests are recorded in the private manifest and are queued for sequential verification.

### Relevant entries found

The outer archive directory identifies the exact source files we previously lacked:

| Entry | Size | ZIP64 local-record offset |
|---|---:|---:|
| `update/exefs/main` | 14,136,021 bytes | 178 |
| `update/romfs/Data/Skyrim - Meshes0.bsa` | 1,151,357,910 bytes | 468,768,765 |
| `update/romfs/Data/Skyrim - Meshes1.bsa` | 368,928,857 bytes | 1,620,126,792 |
| `update/romfs/Data/Skyrim - Shaders.bsa` | 116,485,026 bytes | 2,383,080,179 |

These entries report `Method = Store` and equal packed/unpacked sizes. `Meshes0.bsa` is wholly contained in volume `.001`; `Meshes1.bsa` spans the `.001` / `.002` boundary. A targeted workflow is now extracting `exefs/main` and `Meshes0.bsa` from volume `.001` only, into temporary runner storage. It will report hashes, executable header/string matches and the BSA header, without uploading proprietary game binaries or adding them to this repository.

The internal root is explicitly labelled `+ update`. Until the version manifest is completed and compared with a base-game dump, treat it as update-labelled data rather than assume it represents the base game as well.

## Why this source matters to the investigation

The lighting investigation has already identified the following leads:

- The Switch executable contains the string `fMsLightCount:BudgetCaps`, which still needs to be traced to active code/data.
- The original `Meshes0/1` archives were not available during the earlier investigation, preventing direct inspection of the Switch mesh/BSTriShape structures. The archive index now confirms both are present.
- No live per-mesh light count or light-index list has yet been identified.
- The Eden-side GPU captures have shader, constant-buffer, descriptor and resource-handle observations, but have not conclusively identified the source of the flicker.

Once the targeted extraction completes, use the executable as the cross-reference for the `fMsLightCount:BudgetCaps` path and inspect the BSA mesh index for the affected mesh families, BSTriShape structure, material links and any per-mesh light-related data. Keep those static findings separate from the runtime evidence in Maxwell; a file-level match alone will not establish what the renderer uses at runtime.

## Separation of responsibilities

- **Private data archive:** original `exefs/` and `romfs/` files, a version manifest and checksums.
- **This repository:** Eden/Maxwell source changes, diagnostic instrumentation and build workflows.
- Do not copy game binaries into this public source repository, source releases, or GitHub Actions artifacts.
- Share only specific extracted metadata, hashes, structural notes or small original-code excerpts needed to explain a finding. Keep copyrighted game content in the private archive.

## Known game identifier

The internal archive path and earlier Eden capture both record title ID `01000A10041EA000`. Record the exact base-game/update/DLC state in the private repository's `MANIFEST.md`.


## New archive cross-reference findings (2026-11-14)

The owner's private release has now been inspected through temporary GitHub Actions runners. The detailed metadata-only report is in the private archive's [INSPECTION.md](https://github.com/sev-byte/Skyrim-exefs-romfs/blob/main/INSPECTION.md); the extracted binaries remain private and were removed from the runners after inspection.

### Executable and budget-setting lead

- The update-labelled NSO0 executable was extracted and all three decompressed NSO sections matched their embedded SHA-256 values.
- The string fMsLightCount:BudgetCaps is at module address 0x0169B628 in rodata.
- Two exact ARM64 ADRP+ADD address constructions occur at 0x000FE5B4 and 0x000FE63C. Each writes the string pointer into a structure and sets a float default of 0.01. These appear to be setting-registration/default initialisation sites, not the runtime enforcement loop.
- A page-level scan returned 271 ADRP candidates to the 4 KiB page, but only those two exact address constructions so far. Do not treat all 271 as direct string references.

### Mesh and material cross-reference

The Rust Skyrim-aware NIF parser successfully parsed selected candle, wall-sconce, cave-lamp, firepit, torch, light-marker and light-effect assets with no truncation, block recovery or link errors.

The Imperial candle and wall-sconce meshes separate the warm emissive candle geometry from the non-emissive metal/body geometry:
- impcandle01.nif has a candle-facing shape with emissive colour (1.0, 0.804, 0.463) and emissive multiple about 0.8704; its body shape has zero emissive colour.
- impchandelliercandle01.nif has two warm-emissive candle shapes (emissive multiples about 1.1402 and 0.8704) and two zero-emissive geometry shapes.
- impwallsconcecandle01.nif has a warm-emissive candle shape with multiplier about 0.8704 and a separate zero-emissive body.
- impwallsconcecandle02.nif has a candle-facing multiplier about 1.1402 and separate non-emissive body.

Those candle NIFs also contain BSLightingShaderPropertyFloatController blocks. The NIF format enum defines controller-variable value 11 as Emissive Multiple, so the controller target/keyframe data is the next asset-level check. This may affect visible flame/glow, but it is not itself proof of the dynamic point-light flicker observed by the user.

Other selected effect NIFs (candle flame, small fire, torch fire, Dawnguard beam and Falmer brazier effects) contain BSEffectShaderProperty and particle-system blocks. The markers are visual geometry. These files do not by themselves identify the placed light radius/intensity/flicker settings.

### Remaining game-side gap

The current uploaded root path is labelled + update. Base-game plugin records are not yet confirmed in this archive. To connect static mesh assets to the actual dynamic scene lights, the next data needed is the appropriate base-game plugin records (light definitions and placed references), then to correlate their mesh paths, radii, intensities and flicker flags with the runtime resource/dispatch traces. Keep static-file results distinct from Maxwell runtime evidence.

Do not infer the root cause from the NIFs alone: the candle material animation, placed light objects and Maxwell's GPU dispatch/resource behaviour are three separate layers that still need to be connected.
