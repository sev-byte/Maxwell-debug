#!/usr/bin/env python3
"""Streaming Maxwell PipelineAudit analyser for a target shader and guest GPU address.

Reads the log in passes and retains only target bindings, descriptor matches and
submission events. Audit rows are already serialized by their global sequence ID,
so no full-log sort or in-memory copy is needed.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

EVENT_NAMES = {
    "TARGET_DISCOVERY", "PIPELINE_BIND", "COMPUTE_BUFFER_BIND", "DESCRIPTOR_WRITE",
    "BUFFER_CREATE", "BUFFER_VIEW_CREATE", "BUFFER_USAGE", "BUFFER_USAGE_RESET",
    "UPLOAD_REORDER_RANGE", "UPLOAD_REORDER_DECISION", "BUFFER_TRANSFER",
    "BUFFER_TRANSFER_BATCH", "BUFFER_CLEAR", "IMAGE_CREATE", "IMAGE_VIEW_CREATE",
    "IMAGE_BLIT_REQUEST", "IMAGE_COPY_REQUEST", "BUFFER_TO_IMAGE_REQUEST",
    "IMAGE_TO_BUFFER_REQUEST", "IMAGE_TO_BUFFER_DESTINATION", "COMPUTE_DISPATCH",
    "COMPUTE_MULTI_RANGE_SEGMENT", "COMPUTE_MULTI_RANGE_DESCRIPTOR",
    "DESCRIPTOR_SET_SLOT", "DESCRIPTOR_SET_COMMIT", "DESCRIPTOR_BUFFER_ALLOC",
    "DESCRIPTOR_BUFFER_REUSE_WAIT_BEGIN", "DESCRIPTOR_BUFFER_REUSE_WAIT_END",
    "DESCRIPTOR_BUFFER_CHUNK_STALL_BEGIN", "DESCRIPTOR_BUFFER_CHUNK_STALL_END",
    "SUBMISSION_SCHEDULE", "SUBMISSION_ACCEPTED", "GPU_TICK_COMPLETE",
    "CAPTURE_CONFIG",
}

AUDIT_MARKER = "[PipelineAudit]"
SUMMARY_RE = re.compile(
    r"\[PipelineAuditSummary\]\s+attempted=(\d+)\s+written=(\d+)\s+"
    r"dropped=(\d+)\s+last_sequence=(\d+)"
)
EVENT_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=(".*?"|\S+)')

FIRST_PASS = {
    "TARGET_DISCOVERY", "COMPUTE_BUFFER_BIND",
    "COMPUTE_MULTI_RANGE_SEGMENT", "COMPUTE_MULTI_RANGE_DESCRIPTOR",
    "BUFFER_VIEW_CREATE", "IMAGE_VIEW_CREATE", "CAPTURE_CONFIG",
}
SECOND_PASS = {
    "DESCRIPTOR_WRITE", "BUFFER_USAGE", "BUFFER_USAGE_RESET",
    "UPLOAD_REORDER_RANGE", "UPLOAD_REORDER_DECISION",
    "BUFFER_TRANSFER", "BUFFER_TRANSFER_BATCH", "BUFFER_CLEAR",
    "DESCRIPTOR_SET_SLOT", "DESCRIPTOR_BUFFER_REUSE_WAIT_END",
    "COMPUTE_DISPATCH", "CAPTURE_CONFIG",
}
THIRD_PASS = {"SUBMISSION_SCHEDULE", "SUBMISSION_ACCEPTED", "GPU_TICK_COMPLETE"}


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip().strip('"')
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        try:
            return int(value, 16)
        except ValueError:
            return None


def parse_fields(body: str) -> dict[str, str]:
    return {key: value.strip().strip('"') for key, value in KV_RE.findall(body)}


def audit_parts(line: str):
    marker = line.find(AUDIT_MARKER)
    if marker < 0:
        return None
    tail = line[marker + len(AUDIT_MARKER):].lstrip()
    if not tail.startswith("seq="):
        return None
    seq_text, separator, body = tail[4:].partition(" ")
    if not separator:
        body = ""
    try:
        seq = int(seq_text)
    except ValueError:
        return None
    event = None
    for match in EVENT_RE.finditer(body):
        candidate = match.group(1)
        if candidate in EVENT_NAMES:
            event = candidate
            break
    return seq, event, body


def in_range(address: int | None, base: int | None, size: int | None) -> bool:
    if address is None or base is None:
        return False
    if size is None or size <= 0:
        return address == base
    return base <= address < base + size


def safe_value(fields: dict[str, str], *keys: str) -> int | None:
    for key in keys:
        value = parse_int(fields.get(key))
        if value is not None:
            return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    parser.add_argument("--shader-hash", default="4577aed68dd75ecc")
    parser.add_argument("--gpu-addr", default="0x508380000")
    args = parser.parse_args()

    target_hash = parse_int(args.shader_hash)
    target_gpu_addr = parse_int(args.gpu_addr)
    if target_hash is None or target_gpu_addr is None:
        parser.error("shader hash and GPU address must be numeric hexadecimal values")
    if not args.log.is_file():
        parser.error(f"log does not exist: {args.log}")

    # Pass 1: establish the target's resource identity and view mappings.
    event_counts: Counter[str] = Counter()
    capture_configs: list[dict[str, str]] = []
    target_discoveries: list[tuple[int, dict[str, str]]] = []
    target_bindings: list[tuple[int, str, dict[str, str]]] = []
    target_handles: set[int] = set()
    target_backing_addresses: set[int] = set()
    target_bdas: list[tuple[int, int]] = []
    buffer_views: dict[int, int] = {}
    image_views: dict[int, tuple[int | None, int | None]] = {}
    total_lines = audit_count = 0
    first_seq = last_seq = None
    sequence_gaps = 0
    summary = None

    with args.log.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            total_lines += 1
            sm = SUMMARY_RE.search(line)
            if sm:
                summary = {
                    "attempted": int(sm.group(1)),
                    "written": int(sm.group(2)),
                    "dropped": int(sm.group(3)),
                    "last_sequence": int(sm.group(4)),
                }
            parsed = audit_parts(line)
            if parsed is None:
                continue
            seq, event, body = parsed
            audit_count += 1
            if first_seq is None:
                first_seq = seq
            if last_seq is not None and seq > last_seq + 1:
                sequence_gaps += seq - last_seq - 1
            last_seq = seq
            if event is None:
                continue
            event_counts[event] += 1
            if event not in FIRST_PASS:
                continue
            fields = parse_fields(body)

            if event == "CAPTURE_CONFIG":
                capture_configs.append(fields)
            elif event == "TARGET_DISCOVERY":
                if fields.get("kind") == "compute" and parse_int(fields.get("shader")) == target_hash:
                    target_discoveries.append((seq, fields))
            elif event == "COMPUTE_BUFFER_BIND":
                guest = parse_int(fields.get("guest_gpu_addr"))
                size = parse_int(fields.get("range"))
                if in_range(target_gpu_addr, guest, size):
                    target_bindings.append((seq, event, fields))
                    handle = parse_int(fields.get("buffer"))
                    backing = parse_int(fields.get("backing_cpu_addr"))
                    bda = parse_int(fields.get("vk_device_address"))
                    if handle is not None:
                        target_handles.add(handle)
                    if backing is not None:
                        target_backing_addresses.add(backing)
                    if bda is not None:
                        target_bdas.append((bda + (parse_int(fields.get("offset")) or 0), size or 0))
            elif event == "COMPUTE_MULTI_RANGE_SEGMENT":
                guest = parse_int(fields.get("guest_base_gpu_addr"))
                size = parse_int(fields.get("guest_total_range"))
                if in_range(target_gpu_addr, guest, size):
                    target_bindings.append((seq, event, fields))
                    handle = parse_int(fields.get("buffer"))
                    backing = parse_int(fields.get("backing_cpu_addr"))
                    bda = parse_int(fields.get("vk_device_address"))
                    if handle is not None:
                        target_handles.add(handle)
                    if backing is not None:
                        target_backing_addresses.add(backing)
                    if bda is not None:
                        target_bdas.append((
                            bda + (parse_int(fields.get("source_offset")) or 0),
                            parse_int(fields.get("segment_size")) or 0,
                        ))
            elif event == "COMPUTE_MULTI_RANGE_DESCRIPTOR":
                guest = parse_int(fields.get("guest_gpu_addr"))
                size = safe_value(fields, "resource_range", "guest_range")
                if in_range(target_gpu_addr, guest, size):
                    target_bindings.append((seq, event, fields))
                    handle = safe_value(fields, "buffer", "descriptor_buffer")
                    if handle is not None:
                        target_handles.add(handle)
            elif event == "BUFFER_VIEW_CREATE":
                view, buffer = parse_int(fields.get("view")), parse_int(fields.get("buffer"))
                if view is not None and buffer is not None:
                    buffer_views[view] = buffer
            elif event == "IMAGE_VIEW_CREATE":
                view = parse_int(fields.get("view"))
                if view is not None:
                    image_views[view] = (
                        parse_int(fields.get("image")),
                        parse_int(fields.get("guest_gpu_addr")),
                    )

    # Pass 2: resolve shader descriptor writes and collect target-buffer timeline.
    target_compute_writes = resolved_writes = unresolved_writes = 0
    target_descriptor_writes: list[tuple[int, dict[str, str]]] = []
    grouped_writes: Counter[tuple[int | None, str]] = Counter()
    timeline: list[tuple[int, str, str]] = []
    expected_ticks: set[int] = set()
    reuse_findings: list[tuple[int, str]] = []
    target_dispatches = 0
    target_dispatch_groups: Counter[tuple[str, str]] = Counter()

    with args.log.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parsed = audit_parts(line)
            if parsed is None:
                continue
            seq, event, body = parsed
            if event not in SECOND_PASS:
                continue
            fields = parse_fields(body)

            if event == "DESCRIPTOR_WRITE":
                if parse_int(fields.get("target_hash")) != target_hash:
                    continue
                target_compute_writes += 1
                kind = fields.get("kind", "")
                handle = parse_int(fields.get("handle"))
                view = parse_int(fields.get("view"))
                address = parse_int(fields.get("address"))
                guest_context = parse_int(fields.get("guest_gpu_addr"))
                backing_context = parse_int(fields.get("backing_cpu_addr"))

                match_target = (
                    guest_context is not None and in_range(
                        target_gpu_addr, guest_context,
                        parse_int(fields.get("resource_range"))
                    )
                ) or (handle is not None and handle in target_handles)
                if kind in ("buffer_address", "texel_address") and address is not None:
                    match_target = match_target or any(
                        in_range(address, base, size) for base, size in target_bdas
                    )
                if kind == "texel_buffer" and view in buffer_views:
                    match_target = match_target or buffer_views[view] in target_handles
                if kind in ("image", "sampled_image") and view in image_views:
                    match_target = match_target or image_views[view][1] == target_gpu_addr

                resolved = (
                    guest_context is not None or backing_context is not None
                    or (kind in ("buffer", "buffer_address") and
                        (handle is not None or address is not None))
                    or (kind in ("texel_buffer", "texel_address") and
                        (view in buffer_views or address is not None))
                    or (kind in ("image", "sampled_image") and view in image_views)
                    or ((handle == 0 or view == 0) and bool(kind))
                )
                if resolved:
                    resolved_writes += 1
                else:
                    unresolved_writes += 1
                if match_target:
                    target_descriptor_writes.append((seq, fields))
                    grouped_writes[(parse_int(fields.get("target_hash")),
                                    fields.get("stage", "?"))] += 1

            elif event == "COMPUTE_DISPATCH":
                shader = safe_value(fields, "shader_hash", "shader")
                if shader == target_hash:
                    target_dispatches += 1
                    target_dispatch_groups[(fields.get("groups", "?"),
                                            fields.get("expected_submit_tick", "?"))] += 1

            elif event in ("BUFFER_USAGE", "BUFFER_USAGE_RESET",
                           "UPLOAD_REORDER_RANGE", "UPLOAD_REORDER_DECISION"):
                handle = parse_int(fields.get("buffer"))
                if handle in target_handles:
                    timeline.append((seq, event, body))
                    tick = parse_int(fields.get("expected_submit_tick"))
                    if tick is not None:
                        expected_ticks.add(tick)

            elif event in ("BUFFER_TRANSFER", "BUFFER_TRANSFER_BATCH", "BUFFER_CLEAR"):
                src = parse_int(fields.get("src"))
                dst = parse_int(fields.get("dst"))
                if src in target_handles or dst in target_handles:
                    timeline.append((seq, event, body))
                    tick = parse_int(fields.get("expected_submit_tick"))
                    if tick is not None:
                        expected_ticks.add(tick)

            elif event == "DESCRIPTOR_SET_SLOT":
                if parse_int(fields.get("target_hash")) == target_hash:
                    previous = parse_int(fields.get("previous_use_tick")) or 0
                    complete = parse_int(fields.get("observed_completed_tick")) or 0
                    safe = fields.get("reuse_safe", "").lower() == "true"
                    if previous > 0 and (not safe or complete < previous):
                        reuse_findings.append((seq, body))

            elif event == "DESCRIPTOR_BUFFER_REUSE_WAIT_END":
                if parse_int(fields.get("target_hash")) == target_hash:
                    if fields.get("completed_after_wait", "").lower() != "true":
                        reuse_findings.append((seq, body))

    # Pass 3: join only the submissions referenced by the target transfer timeline.
    if expected_ticks:
        with args.log.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                parsed = audit_parts(line)
                if parsed is None:
                    continue
                seq, event, body = parsed
                if event not in THIRD_PASS:
                    continue
                fields = parse_fields(body)
                if event in ("SUBMISSION_SCHEDULE", "SUBMISSION_ACCEPTED"):
                    tick = parse_int(fields.get("semaphore_value"))
                    if tick in expected_ticks:
                        timeline.append((seq, event, body))
                elif event == "GPU_TICK_COMPLETE":
                    tick = parse_int(fields.get("tick"))
                    if tick in expected_ticks:
                        timeline.append((seq, event, body))

    timeline.sort(key=lambda item: item[0])
    target_hash_text = f"0x{target_hash:x}"
    target_addr_text = f"0x{target_gpu_addr:x}"

    print(f"Log: {args.log}")
    print(f"Log size bytes: {args.log.stat().st_size}")
    print(f"Parsed audit records: {audit_count} across {total_lines} log lines")
    print(f"Audit sequence first={first_seq} last={last_seq} gaps={sequence_gaps}")
    print(f"Shader hash: {target_hash_text}; target guest GPU address: {target_addr_text}")
    print(f"Target compute discovery records: {len(target_discoveries)}")
    if target_discoveries:
        expected = max((parse_int(f.get("descriptor_entries")) or 0)
                       for _, f in target_discoveries)
        print(f"Expected descriptor entries (maximum seen): {expected}")
    print(f"Target buffer bindings resolved: {len(target_bindings)}")
    for seq, kind, fields in target_bindings:
        print(
            f"  seq={seq} kind={kind} guest_gpu_addr="
            f"{fields.get('guest_gpu_addr', fields.get('guest_base_gpu_addr', '?'))} "
            f"backing_cpu_addr={fields.get('backing_cpu_addr', '?')} "
            f"VkBuffer={fields.get('buffer', fields.get('descriptor_buffer', '?'))} "
            f"VkDeviceAddress={fields.get('vk_device_address', '?')} "
            f"offset={fields.get('offset', fields.get('source_offset', '?'))} "
            f"range={fields.get('range', fields.get('segment_size', fields.get('guest_total_range', '?'))}"
        )
    print(f"Descriptor writes for target shader: {target_compute_writes}")
    print(f"Descriptor writes resolved to a resource identity: {resolved_writes}")
    print(f"Descriptor writes unresolved: {unresolved_writes}")
    print(f"Descriptor writes overlapping target buffer: {len(target_descriptor_writes)}")
    print("Target descriptor writes grouped by shader hash/stage:")
    for (shader, stage), count in sorted(grouped_writes.items(),
                                         key=lambda item: (item[0][0] or 0, item[0][1])):
        print(f"  shader={('unknown' if shader is None else hex(shader))} stage={stage} writes={count}")
    for seq, fields in target_descriptor_writes[:500]:
        print(
            f"  seq={seq} stage={fields.get('stage','?')} kind={fields.get('kind','?')} "
            f"resource_kind={fields.get('resource_kind','?')} "
            f"guest_gpu_addr={fields.get('guest_gpu_addr','?')} "
            f"buffer={fields.get('buffer', fields.get('address', fields.get('view','?')))}"
        )
    if len(target_descriptor_writes) > 500:
        print(f"  ... {len(target_descriptor_writes)-500} additional matching descriptor writes omitted")
    print(f"Target compute dispatches: {target_dispatches}")
    print(f"Target buffer timeline records: {len(timeline)}")
    for seq, event, body in timeline:
        print(f"  seq={seq} {event} {body}")
    print(f"Descriptor/ring premature-reuse findings: {len(reuse_findings)}")
    for seq, body in reuse_findings:
        print(f"  seq={seq} {body}")
    if capture_configs:
        print("Capture configuration records:")
        for fields in capture_configs:
            print("  " + " ".join(f"{k}={v}" for k, v in fields.items()))
    if summary:
        print(
            "Audit coverage: attempted={attempted} written={written} dropped={dropped} "
            "last_sequence={last_sequence}".format(**summary)
        )
        if summary["written"] != audit_count:
            print(f"WARNING: written count differs from parsed audit lines by {summary['written'] - audit_count}")
        if summary["dropped"]:
            print("WARNING: some audit events were dropped; missing events must be treated as unresolved")
    else:
        print("WARNING: PipelineAuditSummary was not found; log may be truncated or logger may not have shut down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
