# Native diagnostic log format (EDEN)

The Android native diagnostic recorder writes one `.edlog` file per session under Eden's log directory, in the `diagnostic` subdirectory. Files are append-only during a session and use Zstandard-compressed chunks.

## Container version 1

All current Android targets are little-endian. The decoder uses explicit little-endian layouts.

### File header (24 bytes)

| Field | Type | Meaning |
|---|---:|---|
| magic | u32 | `0x4E444544` (bytes `DEDN`) |
| version | u16 | 1 |
| event_size | u16 | 72 bytes |
| wall_start_ns | u64 | Unix wall-clock start time in nanoseconds |
| monotonic_start_ns | u64 | steady-clock start time in nanoseconds |

### Chunk header (16 bytes)

| Field | Type | Meaning |
|---|---:|---|
| magic | u32 | `0x4B4E4843` (bytes `CHNK`) |
| uncompressed_size | u32 | Size of raw event bytes |
| compressed_size | u32 | Size of following Zstandard frame |
| checksum | u32 | FNV-1a 32-bit checksum of raw event bytes |

The chunk payload is a sequence of fixed-size events. A chunk must decompress to exactly `uncompressed_size`, and its byte count must be divisible by 72.

### Event record (72 bytes)

| Field | Type | Meaning |
|---|---:|---|
| type | u16 | Diagnostic event type |
| flags | u16 | Event-specific flags |
| thread_id | u32 | Hash of the recording thread identifier |
| sequence | u64 | Session event sequence |
| wall_time_ns | u64 | System-clock timestamp |
| monotonic_time_ns | u64 | Steady-clock timestamp |
| frame_id | u64 | Frame correlation ID, or zero if not assigned |
| object_a | u64 | Event-specific object/handle |
| object_b | u64 | Event-specific object/handle |
| value_a | u64 | Event-specific value |
| value_b | u64 | Event-specific value |

## Current event-specific conventions

These are recorder conventions, not Vulkan API guarantees. Treat raw values as unsigned integers and decode handles as opaque identifiers.

### ResourceUpdate flags

| flags | Meaning | object_a | object_b | value_a | value_b |
|---:|---|---|---|---|---|
| 0x10 | Buffer created | VkBuffer | guest CPU address | byte size | device address |
| 0x11 | Buffer destroyed | VkBuffer | guest CPU address | byte size | device address |
| 0x20 | Image created | VkImage | guest GPU address | width | height in high 32 bits, depth in low 32 bits |
| 0x21 | Image destroyed | VkImage | guest GPU address | width | height/depth packed as above |
| 0x22 | Buffer-to-image upload | source VkBuffer | destination VkImage | copy region count | source offset |

Other ResourceUpdate flag values are defined at their individual call sites.

### Synchronization flags

For the following events, `object_a` is the source pipeline-stage mask, `object_b` is the destination pipeline-stage mask, `value_a` is the source access mask and `value_b` is the destination access mask.

| flags | Operation |
|---:|---|
| 3 | Compute pre-dispatch memory dependency |
| 4 | Upload transfer-to-general-work dependency |
| 5 | Transform-feedback output dependency |
| 6 | Image layout transition; object_a is VkImage, object_b packs old layout (high 32 bits) and new layout (low 32 bits) |

Older buffer-cache copy/fill records use their original field layout; consult the call site before interpreting those records as stage masks.

### Frame presentation

`FramePresentQueued` means Eden queued the frame for presentation. `FramePresentSubmitted` means the present work was submitted through Eden's present path. It is **not** a measurement of when the display physically scanned out the frame.

## Decoder

Run:

```sh
python -m pip install zstandard
python tools/decode_native_events.py path/to/native_events.edlog
```

The decoder validates chunk magic, decompressed length, event alignment and FNV-1a checksum before writing CSV. A successful decode validates the container, not the semantic correctness of every instrumentation hook.
