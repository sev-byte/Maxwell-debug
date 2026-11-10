#!/usr/bin/env python3
"""Decode Eden native diagnostic recorder .edlog files to CSV.

Requires: python -m pip install zstandard
Usage: python decode_native_events.py input.edlog [output.csv]
"""
import argparse
import csv
import struct
import sys
from pathlib import Path

FILE_HEADER = struct.Struct("<IHHQQ")
CHUNK_HEADER = struct.Struct("<IIII")
EVENT = struct.Struct("<HHIQQQQQQQQ")
FILE_MAGIC = 0x4E444544
CHUNK_MAGIC = 0x4B4E4843

EVENT_NAMES = {
    1: "SessionStart", 2: "SessionEnd", 3: "RenderPassBegin",
    4: "RenderPassEnd", 5: "ComputeDispatch", 6: "GraphicsDraw",
    7: "ResourceUpdate", 8: "DescriptorUpdate", 9: "Synchronization",
    10: "Presentation", 11: "FrameAcquire", 12: "FramePresentQueued",
    13: "FramePresentSubmitted", 14: "DescriptorBufferWrite",
    15: "DescriptorImageWrite", 16: "DescriptorTexelWrite",
    17: "DescriptorAddressWrite", 18: "GraphicsPipelineBind",
    19: "ComputePipelineBind", 20: "DescriptorSetBind",
    21: "DescriptorBufferBind", 22: "PushDescriptorBind",
    255: "DiagnosticError",
}
COLUMNS = [
    "type", "type_name", "flags", "thread_id", "sequence",
    "wall_time_ns", "monotonic_time_ns", "frame_id", "object_a",
    "object_b", "value_a", "value_b",
]


def fnv1a(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


def decode(source):
    try:
        import zstandard
    except ImportError as exc:
        raise SystemExit("Missing dependency: python -m pip install zstandard") from exc

    rows = []
    with source.open("rb") as stream:
        raw = stream.read(FILE_HEADER.size)
        if len(raw) != FILE_HEADER.size:
            raise ValueError("File is shorter than the diagnostic header")
        magic, version, event_size, wall_start, mono_start = FILE_HEADER.unpack(raw)
        if magic != FILE_MAGIC:
            raise ValueError(f"Bad file magic: 0x{magic:08x}")
        if version != 1:
            raise ValueError(f"Unsupported format version: {version}")
        if event_size != EVENT.size:
            raise ValueError(f"Unexpected event size {event_size}; decoder expects {EVENT.size}")

        chunk_number = 0
        while True:
            raw = stream.read(CHUNK_HEADER.size)
            if not raw:
                break
            if len(raw) != CHUNK_HEADER.size:
                raise ValueError(f"Truncated chunk header at chunk {chunk_number}")
            chunk_magic, raw_size, compressed_size, checksum = CHUNK_HEADER.unpack(raw)
            if chunk_magic != CHUNK_MAGIC:
                raise ValueError(f"Bad chunk magic at chunk {chunk_number}: 0x{chunk_magic:08x}")
            compressed = stream.read(compressed_size)
            if len(compressed) != compressed_size:
                raise ValueError(f"Truncated compressed data at chunk {chunk_number}")
            payload = zstandard.ZstdDecompressor().decompress(compressed, max_output_size=raw_size)
            if len(payload) != raw_size:
                raise ValueError(f"Wrong uncompressed size at chunk {chunk_number}")
            if fnv1a(payload) != checksum:
                raise ValueError(f"Checksum mismatch at chunk {chunk_number}")
            if len(payload) % EVENT.size:
                raise ValueError(f"Misaligned event data at chunk {chunk_number}")
            for offset in range(0, len(payload), EVENT.size):
                values = EVENT.unpack_from(payload, offset)
                rows.append({
                    "type": values[0], "type_name": EVENT_NAMES.get(values[0], "Unknown"),
                    "flags": values[1], "thread_id": values[2], "sequence": values[3],
                    "wall_time_ns": values[4], "monotonic_time_ns": values[5],
                    "frame_id": values[6], "object_a": values[7], "object_b": values[8],
                    "value_a": values[9], "value_b": values[10],
                })
            chunk_number += 1
    return rows, (version, event_size, wall_start, mono_start)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    args = parser.parse_args()
    rows, header = decode(args.input)
    output = args.output or args.input.with_suffix(".csv")
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Decoded {len(rows):,} events to {output}")
    print(f"Format v{header[0]}, event size {header[1]}, start wall ns {header[2]}, monotonic ns {header[3]}")


if __name__ == "__main__":
    main()
