# Skyrim Switch exefs/romfs source archive

The original game-data working archive is maintained separately in the owner's private repository:

**[sev-byte/Skyrim-exefs-romfs](https://github.com/sev-byte/Skyrim-exefs-romfs)**

That repository is private and is intended to contain only the owner's own Switch dump. Keep the original files unchanged and preserve the extracted directory layout.

## Why this source matters to the investigation

The lighting investigation has already identified the following leads:

- The Switch executable contains the string `fMsLightCount:BudgetCaps`, which still needs to be traced to active code/data.
- The original `Meshes0/1` archives were not available during the earlier investigation, preventing direct inspection of the Switch mesh/BSTriShape structures.
- No live per-mesh light count or light-index list has yet been identified.
- The Eden-side GPU captures have shader, constant-buffer, descriptor and resource-handle observations, but have not conclusively identified the source of the flicker.

The private source archive gives us a route to inspect the actual game-side executable and RomFS assets, verify the mesh archive structure, and connect those findings to the shader/resource evidence captured by Maxwell.

## Separation of responsibilities

- **Private data archive:** original `exefs/` and `romfs/` files, a version manifest and checksums.
- **This repository:** Eden/Maxwell source changes, diagnostic instrumentation and build workflows.
- Do not copy game binaries into this public source repository, source releases, or GitHub Actions artifacts.
- Share only the specific extracted metadata, hashes, structural notes or small original-code excerpts needed to explain a finding. Keep copyrighted game content in the private archive.

## Known game identifier

The earlier Eden capture recorded title ID `01000A10041EA000`. Confirm that this matches the dump and record the exact base-game/update/DLC state in the private repository's `MANIFEST.md`.
