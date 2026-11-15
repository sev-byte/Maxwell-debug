# Skyrim preflush bypass capture inspection

Capture: https://github.com/sev-byte/Maxwell-debug/releases/download/preflush-capture-2026-11-15/Eden.preflushable.log.zip
Release: https://github.com/sev-byte/Maxwell-debug/releases/tag/preflush-capture-2026-11-15
Test build: https://github.com/sev-byte/Maxwell-debug/actions/runs/43753544882
Archive size: 237.6 MB

## Archive manifest
- Eden.preflushable.log.zip: 2 entries; uncompressed files total 1.6 GB
  - Eden preflushable log/: 0.0 B (compressed 2.0 B)
  - Eden preflushable log/eden_gpu.log: 1.6 GB (compressed 237.6 MB)

## Marker counts
| Marker | Count |
|---|---:|
| Target lighting compute shader match | 0 |
| Target SSBO write | 0 |
| Preflush bypass | 0 |
| Target shader hash 4577aed68dd75ecc | 2821 |
| 3D-image compute lead 8c0d043b68e7a6d1 | 1 |
| preflushable_before=true | 0 |
| preflushable_before=false | 0 |

- Text files scanned: 1
- Text lines scanned: 3,540,084
- Lines with UTF-8 replacement characters: 0
- wall_ms timestamps found: 3,532,832; min=1794774869900; max=1794774940914

## Address/range comparison
- Unique target write address/size pairs: 0
- Unique bypass address/size pairs: 0
- Exact address/size intersections: 0

## Interpretation
No target-dispatch match marker was found. The capture does not demonstrate that the identified 320x6x1 shader predicate matched while diagnostics were active.

The user-reported visual outcome for this capture was no visible change. The log establishes whether the instrumented path ran; it does not alone prove the visual cause.

## Shader match samples
- No matching lines found.

## Target SSBO write samples
- No matching lines found.

## Bypass samples
- No matching lines found.

## Secondary compute-shader lead
The separate compute hash 8c0d043b68e7a6d1 is counted as a lead because earlier analysis found it writes a 3D storage image. Hash presence alone does not identify it as volumetric lighting; its dispatch and bound resource data must be correlated.

## Scanner notes
- No archive/parser errors recorded.
