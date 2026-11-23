#!/usr/bin/env python3
"""Summarize a Maxwell PipelineAudit log for one compute shader and guest GPU address.

No third-party dependencies. Sequence numbers, not file line numbers or timestamps,
are used for event ordering.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

EVENT_NAMES = (
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
)
KV_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=(".*?"|\S+)')
AUDIT_RE = re.compile(r"\[PipelineAudit\]\s+seq=(\d+)\s+(.*)$")
SUMMARY_RE = re.compile(
    r"\[PipelineAuditSummary\]\s+attempted=(\d+)\s+written=(\d+)\s+"
    r"dropped=(\d+)\s+last_sequence=(\d+)"
)


def number(value: str | None) -> int | None:
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


def clean(value: str | None) -> str:
    return (value or "").strip().strip('"')


def parse_line(line: str) -> dict[str, Any] | None:
    match = AUDIT_RE.search(line)
    if not match:
        return None
    seq = int(match.group(1))
    body = match.group(2)
    event = next((name for name in EVENT_NAMES
                  if re.search(rf"\b{re.escape(name)}\b", body)), "UNKNOWN")
    fields = {key: clean(value) for key, value in KV_RE.findall(body)}
    return {"seq": seq, "event": event, "fields": fields, "raw": body}


def addr_in_range(address: int | None, base: int | None, size: int | None) -> bool:
    if address is None or base is None:
        return False
    if size is None or size <= 0:
        return address == base
    return base <= address < base + size


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="eden_gpu.log or extracted GPU log")
    parser.add_argument("--shader-hash", default="4577aed68dd75ecc")
    parser.add_argument("--gpu-addr", default="0x508380000")
    args = parser.parse_args()

    target_hash = number(args.shader_hash)
    target_gpu_addr = number(args.gpu_addr)
    if target_hash is None or target_gpu_addr is None:
        parser.error("shader hash and GPU address must be numeric hexadecimal values")

    records: list[dict[str, Any]] = []
    summary: dict[str, int] | None = None
    total_lines = 0
    with args.log.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            total_lines += 1
            parsed = parse_line(line)
            if parsed:
                records.append(parsed)
                continue
            sm = SUMMARY_RE.search(line)
            if sm:
                summary = {
                    "attempted": int(sm.group(1)),
                    "written": int(sm.group(2)),
                    "dropped": int(sm.group(3)),
                    "last_sequence": int(sm.group(4)),
                }

    records.sort(key=lambda item: item["seq"])
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_event[record["event"]].append(record)

    compute_targets = []
    expected_descriptors = 0
    for event in by_event["TARGET_DISCOVERY"]:
        f = event["fields"]
        if clean(f.get("kind")) != "compute":
            continue
        shader = number(f.get("shader"))
        if shader == target_hash:
            compute_targets.append(event)
            expected_descriptors = max(expected_descriptors, number(f.get("descriptor_entries")) or 0)

    target_bindings: list[dict[str, Any]] = []
    target_handles: set[int] = set()
    target_bdas: list[tuple[int, int]] = []
    backing_addresses: set[int] = set()
    for event in by_event["COMPUTE_BUFFER_BIND"]:
        f = event["fields"]
        guest = number(f.get("guest_gpu_addr"))
        size = number(f.get("range"))
        if not addr_in_range(target_gpu_addr, guest, size):
            continue
        handle = number(f.get("buffer"))
        backing = number(f.get("backing_cpu_addr"))
        bda = number(f.get("vk_device_address"))
        if handle is not None:
            target_handles.add(handle)
        if backing is not None:
            backing_addresses.add(backing)
        if bda is not None:
            target_bdas.append((bda + (number(f.get("offset")) or 0), size or 0))
        target_bindings.append(event)

    for event in by_event["COMPUTE_MULTI_RANGE_SEGMENT"]:
        f = event["fields"]
        guest_base = number(f.get("guest_base_gpu_addr"))
        guest_range = number(f.get("guest_total_range"))
        if not addr_in_range(target_gpu_addr, guest_base, guest_range):
            continue
        handle = number(f.get("buffer"))
        backing = number(f.get("backing_cpu_addr"))
        bda = number(f.get("vk_device_address"))
        if handle is not None:
            target_handles.add(handle)
        if backing is not None:
            backing_addresses.add(backing)
        if bda is not None:
            target_bdas.append((bda + (number(f.get("source_offset")) or 0),
                                number(f.get("segment_size")) or 0))
        target_bindings.append(event)

    for event in by_event["COMPUTE_MULTI_RANGE_DESCRIPTOR"]:
        f = event["fields"]
        guest = number(f.get("guest_gpu_addr"))
        size = number(f.get("resource_range")) or number(f.get("guest_range"))
        if not addr_in_range(target_gpu_addr, guest, size):
            continue
        handle = number(f.get("buffer")) or number(f.get("descriptor_buffer"))
        if handle is not None:
            target_handles.add(handle)
        target_bindings.append(event)

    # Resolve image and texel-buffer views back to their backing objects.
    image_views: dict[int, tuple[int | None, int | None]] = {}
    for event in by_event["IMAGE_VIEW_CREATE"]:
        f = event["fields"]
        view, image = number(f.get("view")), number(f.get("image"))
        guest = number(f.get("guest_gpu_addr"))
        if view is not None:
            image_views[view] = (image, guest)

    buffer_views: dict[int, int] = {}
    for event in by_event["BUFFER_VIEW_CREATE"]:
        f = event["fields"]
        view, buffer = number(f.get("view")), number(f.get("buffer"))
        if view is not None and buffer is not None:
            buffer_views[view] = buffer

    target_descriptor_writes: list[dict[str, Any]] = []
    resolved_target_writes: list[dict[str, Any]] = []
    unresolved_target_writes: list[dict[str, Any]] = []
    target_compute_writes: list[dict[str, Any]] = []
    for event in by_event["DESCRIPTOR_WRITE"]:
        f = event["fields"]
        if number(f.get("target_hash")) != target_hash:
            continue
        target_compute_writes.append(event)
        kind = clean(f.get("kind"))
        handle = number(f.get("handle"))
        view = number(f.get("view"))
        address = number(f.get("address"))
        guest_context = number(f.get("guest_gpu_addr"))
        backing_context = number(f.get("backing_cpu_addr"))

        matches_target = (
            guest_context is not None and addr_in_range(target_gpu_addr, guest_context,
                                                        number(f.get("resource_range")))
        ) or (handle is not None and handle in target_handles)

        if kind in ("buffer_address", "texel_address") and address is not None:
            matches_target = matches_target or any(
                addr_in_range(address, base, size) for base, size in target_bdas
            )
        if kind == "texel_buffer" and view in buffer_views:
            matches_target = matches_target or buffer_views[view] in target_handles
        if kind in ("image", "sampled_image") and view in image_views:
            mapped_guest = image_views[view][1]
            matches_target = matches_target or mapped_guest == target_gpu_addr

        if matches_target:
            target_descriptor_writes.append(event)

        resolved = False
        if guest_context is not None or backing_context is not None:
            resolved = True
        elif kind in ("buffer", "buffer_address") and (handle is not None or address is not None):
            resolved = True
        elif kind in ("texel_buffer", "texel_address") and (
            view in buffer_views or address is not None
        ):
            resolved = True
        elif kind in ("image", "sampled_image") and view in image_views:
            resolved = True
        elif (handle == 0 or view == 0) and kind:
            resolved = True  # Explicit null descriptor.
        if resolved:
            resolved_target_writes.append(event)
        else:
            unresolved_target_writes.append(event)

    # Build the target buffer's use/reset/hoist/transfer timeline, then join scheduler
    # events by the actual semaphore value that completes that command-buffer submission.
    timeline: list[dict[str, Any]] = []
    expected_ticks: set[int] = set()
    for event in records:
        f, kind = event["fields"], event["event"]
        handle = number(f.get("buffer"))
        if kind in ("BUFFER_USAGE", "BUFFER_USAGE_RESET", "UPLOAD_REORDER_RANGE",
                    "UPLOAD_REORDER_DECISION"):
            relevant = handle in target_handles
        elif kind in ("BUFFER_TRANSFER", "BUFFER_TRANSFER_BATCH", "BUFFER_CLEAR"):
            relevant = number(f.get("src")) in target_handles or number(f.get("dst")) in target_handles
        else:
            relevant = False
        if relevant:
            timeline.append(event)
            tick = number(f.get("expected_submit_tick"))
            if tick is not None:
                expected_ticks.add(tick)

    for event in records:
        f, kind = event["fields"], event["event"]
        if kind in ("SUBMISSION_SCHEDULE", "SUBMISSION_ACCEPTED"):
            tick = number(f.get("semaphore_value"))
            if tick in expected_ticks:
                timeline.append(event)
        elif kind == "GPU_TICK_COMPLETE":
            tick = number(f.get("tick"))
            if tick in expected_ticks:
                timeline.append(event)
    timeline.sort(key=lambda item: item["seq"])

    # Flag only evidence of premature reuse: descriptor slots explicitly selected before
    # their previous-use tick was observed complete, or a ring wait that returned incomplete.
    reuse_findings = []
    for event in by_event["DESCRIPTOR_SET_SLOT"]:
        f = event["fields"]
        if number(f.get("target_hash")) != target_hash:
            continue
        safe = clean(f.get("reuse_safe")).lower() == "true"
        previous_tick = number(f.get("previous_use_tick")) or 0
        completed_tick = number(f.get("observed_completed_tick")) or 0
        if previous_tick > 0 and (not safe or completed_tick < previous_tick):
            reuse_findings.append(event)
    for event in by_event["DESCRIPTOR_BUFFER_REUSE_WAIT_END"]:
        f = event["fields"]
        if number(f.get("target_hash")) != target_hash:
            continue
        if clean(f.get("completed_after_wait")).lower() != "true":
            reuse_findings.append(event)

    print(f"Log: {args.log}")
    print(f"Parsed audit records: {len(records)} across {total_lines} log lines")
    print(f"Shader hash: 0x{target_hash:x}; target guest GPU address: 0x{target_gpu_addr:x}")
    print(f"Compute target discovery records: {len(compute_targets)}")
    if compute_targets:
        print(f"Expected descriptor entries (maximum seen): {expected_descriptors}")
    print(f"Target buffer bindings resolved: {len(target_bindings)}")
    for event in target_bindings:
        f = event["fields"]
        print(
            "  seq={seq} kind={kind} guest_gpu_addr={gpu} backing_cpu_addr={cpu} "
            "VkBuffer={buf} VkDeviceAddress={bda} offset={off} range={size}".format(
                seq=event["seq"], kind=f.get("kind", "?"),
                gpu=f.get("guest_gpu_addr", f.get("guest_base_gpu_addr", "?")),
                cpu=f.get("backing_cpu_addr", "?"),
                buf=f.get("buffer", f.get("descriptor_buffer", "?")),
                bda=f.get("vk_device_address", "?"),
                off=f.get("offset", f.get("source_offset", f.get("guest_range_offset", "?"))),
                size=f.get("range", f.get("segment_size", f.get("guest_total_range", "?")))
            )
        )
    print(f"Descriptor writes for shader: {len(target_compute_writes)}")
    print(f"Descriptor writes resolved to a resource identity: {len(resolved_target_writes)}")
    print(f"Descriptor writes unresolved: {len(unresolved_target_writes)}")
    print(f"Descriptor writes overlapping target buffer: {len(target_descriptor_writes)}")
    grouped_writes: dict[tuple[int | None, str], int] = defaultdict(int)
    for event in target_descriptor_writes:
        f = event["fields"]
        grouped_writes[(number(f.get("target_hash")), clean(f.get("stage")) or "?")] += 1
    print("Target descriptor writes grouped by shader hash/stage:")
    for (shader, stage), count in sorted(grouped_writes.items(),
                                         key=lambda item: (item[0][0] or 0, item[0][1])):
        shader_text = "unknown" if shader is None else f"0x{shader:x}"
        print(f"  shader={shader_text} stage={stage} writes={count}")
    for event in target_descriptor_writes:
        f = event["fields"]
        print(
            f"  seq={event['seq']} stage={f.get('stage','?')} kind={f.get('kind','?')} "
            f"resource_kind={f.get('resource_kind','?')} guest_gpu_addr={f.get('guest_gpu_addr','?')} "
            f"buffer={f.get('buffer',f.get('address',f.get('view','?')))}"
        )
    print(f"Target buffer timeline records: {len(timeline)}")
    for event in timeline:
        print(f"  seq={event['seq']} {event['event']} {event['raw']}")
    print(f"Descriptor/ring premature-reuse findings: {len(reuse_findings)}")
    for event in reuse_findings:
        print(f"  seq={event['seq']} {event['raw']}")
    if summary:
        print(
            "Audit coverage: attempted={attempted} written={written} dropped={dropped} "
            "last_sequence={last_sequence}".format(**summary)
        )
        if summary["written"] != len(records):
            print(f"WARNING: written count differs from parsed records by {summary['written'] - len(records)}")
    else:
        print("WARNING: PipelineAuditSummary was not found; the log may be truncated or logging may not have shut down.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
