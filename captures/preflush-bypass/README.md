# Preflush bypass test capture upload

This page is the handoff point for the Skyrim preflush-bypass diagnostic capture.

## Tested build

- Repository: [sev-byte/Maxwell-debug](https://github.com/sev-byte/Maxwell-debug)
- Branch: `skyrim-target-preflush-test`
- GitHub Actions run: [#125 — Log targeted Skyrim shader output ranges](https://github.com/sev-byte/Maxwell-debug/actions/runs/43753544882)
- Commit: [`d2c853cd0d6e09600816860810d399ac2737e9cf`](https://github.com/sev-byte/Maxwell-debug/commit/d2c853cd0d6e09600816860810d399ac2737e9cf)

## Upload the capture

**Do not commit the large binary into the Git source tree.** Attach it to a GitHub Release instead.

[Open the prefilled release draft for this capture](https://github.com/sev-byte/Maxwell-debug/releases/new?tag=preflush-capture-2026-11-15)

Use:
- Tag: `preflush-capture-2026-11-15`
- Target branch: `skyrim-target-preflush-test`
- Release title: `Preflush bypass test capture — 2026-11-15`

Drag the diagnostic ZIP or binary into the release asset attachment area, then publish the release. GitHub currently permits release assets below 2 GiB each.

**Privacy:** this repository is public. Publishing the release makes the attached binary publicly downloadable. Check the capture for personal information before publishing.

## What to inspect

Search the capture for these messages:

- `MAXWELL-PREFLUSH matched target lighting compute shader`
- `MAXWELL-PREFLUSH target SSBO write`
- `MAXWELL-PREFLUSH bypass`

The three checks answer:
1. Did the identified 320×6×1 lighting compute dispatch match?
2. Did the shader's output SSBO range get marked for targeted readback?
3. Did a CPU read actually reach the preflush bypass?

A missing message is useful evidence too; it tells us which part of the path was not reached.
