# Skyrim Switch exefs/romfs source archive

The original game-data working archive is maintained separately in the owner's private repository:

**[sev-byte/Skyrim-exefs-romfs](https://github.com/sev-byte/Skyrim-exefs-romfs)**

The repository is private and is intended to contain only the owner's own Switch dump. The source data must remain unchanged and must not be copied into the Maxwell source repository or its build artifacts.

## Uploaded archive and inspection status

The owner uploaded the release [Original Skyrim dump](https://github.com/sev-byte/Skyrim-exefs-romfs/releases/tag/Skyrim) on 2026-11-14. It contains eight sequential volumes (`.001` through `.008`), totalling 13,298,584,465 bytes. The owner confirms the archive data was stored without compression.

GitHub reports all eight release assets as uploaded and provides SHA-256 digests, which are recorded in the private repository's `MANIFEST.md`. Those metadata digests have not yet been independently recomputed from downloaded bytes. The private repository now contains a manual GitHub Actions audit workflow that checks each volume one at a time, records the first/last-volume signatures, and avoids extraction or artifact publication.

**Inspection status: staged, not yet enumerated.** The binary contents have not yet been extracted or read in this environment. In particular, we have not yet verified whether the release holds base-game data, update data, or both, and have not yet inspected the executable or mesh archives. Do not treat the archive filename as proof of its contents.

## Why this source matters to the investigation

The lighting investigation has already identified the following leads:

- The Switch executable contains the string `fMsLightCount:BudgetCaps`, which still needs to be traced to active code/data.
- The original `Meshes0/1` archives were not available during the earlier investigation, preventing direct inspection of the Switch mesh/BSTriShape structures.
- No live per-mesh light count or light-index list has yet been identified.
- The Eden-side GPU captures have shader, constant-buffer, descriptor and resource-handle observations, but have not conclusively identified the source of the flicker.

Once archive enumeration and access to the relevant entries are available, use the game files as a cross-reference for the Eden/Maxwell observations: first resolve the exact executable/version, then locate the light-budget string and trace its callers/data, then inspect the relevant mesh structures and material/light references. Keep the GPU captures as independent runtime evidence rather than treating static game data as proof of runtime behavior.

## Separation of responsibilities

- **Private data archive:** original `exefs/` and `romfs/` files, a version manifest and checksums.
- **This repository:** Eden/Maxwell source changes, diagnostic instrumentation and build workflows.
- Do not copy game binaries into this public source repository, source releases, or GitHub Actions artifacts.
- Share only specific extracted metadata, hashes, structural notes or small original-code excerpts needed to explain a finding. Keep copyrighted game content in the private archive.

## Known game identifier

The earlier Eden capture recorded title ID `01000A10041EA000`. Confirm that this matches the dump and record the exact base-game/update/DLC state in the private repository's `MANIFEST.md`.
