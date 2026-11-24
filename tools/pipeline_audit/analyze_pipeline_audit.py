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
    "DESCRIPTOR_BUFFER_FRAME", "DESCRIPTOR_BUFFER_TOUCH", "DESCRIPTOR_CLASS_BIND",
    "WALL_CLOCK_ANCHOR",
    "READBACK_STAGING_CREATE", "READBACK_STAGING_REUSE", "READBACK_STAGING_RELEASE",
    "READBACK_BATCH_BEGIN", "READBACK_BARRIER", "READBACK_COPY",
    "HOST_READBACK_WRITEBACK",
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
    "BUFFER_CREATE", "BUFFER_VIEW_CREATE", "IMAGE_VIEW_CREATE",
    "CAPTURE_CONFIG", "WALL_CLOCK_ANCHOR",
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
    wall_clock_anchors: list[tuple[int, dict[str, str]]] = []
    buffer_metadata: dict[int, dict[str, str]] = {}
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
            elif event == "WALL_CLOCK_ANCHOR":
                wall_clock_anchors.append((seq, fields))
            elif event == "BUFFER_CREATE":
                handle = parse_int(fields.get("buffer"))
                if handle is not None:
                    buffer_metadata[handle] = fields
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
    target_dispatch_ticks: Counter[int] = Counter()
    target_dispatch_records: list[tuple[int, int, dict[str, str]]] = []

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
                    # Descriptor payloads themselves carry guest GPUVA and VkBuffer.
                    # Use that direct mapping as a resource-identity seed for the
                    # subsequent usage/reset/transfer timeline pass.
                    if handle is not None and kind in ("buffer", "buffer_address"):
                        target_handles.add(handle)

            elif event == "COMPUTE_DISPATCH":
                shader = safe_value(fields, "shader_hash", "shader")
                if shader == target_hash:
                    target_dispatches += 1
                    tick = parse_int(fields.get("expected_submit_tick"))
                    if tick is not None:
                        target_dispatch_ticks[tick] += 1
                        target_dispatch_records.append((seq, tick, fields))
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

    # Pass 2b: re-scan from the beginning now that descriptor writes have
    # resolved the target VkBuffer handle. This avoids missing BUFFER_USAGE,
    # reset or transfer records that happened before the first matching descriptor.
    timeline.clear()
    expected_ticks.clear()
    outbound_transfer_records: list[tuple[int, dict[str, str]]] = []
    destination_handles: set[int] = set()
    readback_copy_records: list[tuple[int, dict[str, str]]] = []
    readback_staging_handles: set[int] = set()
    timeline_event_counts: Counter[str] = Counter()
    resource_events = {
        "BUFFER_CREATE", "BUFFER_USAGE", "BUFFER_USAGE_RESET",
        "UPLOAD_REORDER_RANGE", "UPLOAD_REORDER_DECISION",
        "BUFFER_TRANSFER", "BUFFER_TRANSFER_BATCH", "BUFFER_CLEAR", "READBACK_COPY",
    }
    descriptor_events = {
        "DESCRIPTOR_SET_SLOT", "DESCRIPTOR_SET_COMMIT",
        "DESCRIPTOR_BUFFER_ALLOC", "DESCRIPTOR_BUFFER_FRAME",
        "DESCRIPTOR_BUFFER_TOUCH", "DESCRIPTOR_BUFFER_REUSE_WAIT_BEGIN",
        "DESCRIPTOR_BUFFER_REUSE_WAIT_END",
        "DESCRIPTOR_BUFFER_CHUNK_STALL_BEGIN", "DESCRIPTOR_BUFFER_CHUNK_STALL_END",
    }
    with args.log.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parsed = audit_parts(line)
            if parsed is None:
                continue
            seq, event, body = parsed
            if event not in resource_events and event not in descriptor_events:
                continue
            fields = parse_fields(body)
            relevant = False
            if event in resource_events:
                handle = parse_int(fields.get("buffer"))
                src = parse_int(fields.get("src"))
                dst = parse_int(fields.get("dst"))
                readback_src = parse_int(fields.get("source_buffer"))
                relevant = (handle in target_handles or
                            src in target_handles or dst in target_handles or
                            (event == "READBACK_COPY" and readback_src in target_handles))
            elif event in descriptor_events:
                relevant = parse_int(fields.get("target_hash")) == target_hash
            if not relevant:
                continue
            timeline.append((seq, event, body))
            timeline_event_counts[event] += 1
            if event == "BUFFER_TRANSFER":
                src = parse_int(fields.get("src"))
                dst = parse_int(fields.get("dst"))
                if src in target_handles and dst is not None:
                    outbound_transfer_records.append((seq, fields))
                    destination_handles.add(dst)
            elif event == "READBACK_COPY":
                src = parse_int(fields.get("source_buffer"))
                staging = parse_int(fields.get("staging_buffer"))
                if src in target_handles and staging is not None:
                    readback_copy_records.append((seq, fields))
                    readback_staging_handles.add(staging)
            for key in ("expected_submit_tick", "tick", "reset_tick",
                        "last_usage_tick", "previous_use_tick", "assigned_use_tick"):
                tick = parse_int(fields.get(key))
                if tick is not None and tick > 0:
                    expected_ticks.add(tick)

    # Pass 2c: resolve the readback staging lifecycle and CPU writeback
    # for any staging buffer populated from the target Vulkan buffer.
    readback_events: list[tuple[int, str, dict[str, str]]] = []
    readback_event_counts: Counter[str] = Counter()
    readback_host_bytes = 0
    readback_guest_ranges: Counter[str] = Counter()
    readback_guest_intervals: list[tuple[int, int, int, int]] = []
    if readback_staging_handles:
        readback_names = {
            "READBACK_STAGING_CREATE", "READBACK_STAGING_REUSE",
            "READBACK_STAGING_RELEASE", "READBACK_BATCH_BEGIN",
            "READBACK_BARRIER", "READBACK_COPY", "HOST_READBACK_WRITEBACK",
        }
        with args.log.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                parsed = audit_parts(line)
                if parsed is None:
                    continue
                seq, event, body = parsed
                if event not in readback_names:
                    continue
                fields = parse_fields(body)
                staging = parse_int(fields.get("staging_buffer"))
                if staging not in readback_staging_handles:
                    continue
                readback_events.append((seq, event, fields))
                readback_event_counts[event] += 1
                if event == "HOST_READBACK_WRITEBACK":
                    size = parse_int(fields.get("size")) or 0
                    guest_text = fields.get("guest_cpu_addr", "unknown")
                    guest = parse_int(guest_text)
                    readback_host_bytes += size
                    readback_guest_ranges[guest_text] += size
                    if guest is not None and size > 0:
                        readback_guest_intervals.append((seq, guest, guest + size, staging))

    # Pass 2c: derive the next hop by finding every descriptor/bind/usage
    # reference to buffers that received copies from the target buffer.
    consumer_events = {"DESCRIPTOR_WRITE", "DESCRIPTOR_CLASS_BIND", "BUFFER_USAGE"}
    destination_bdas: list[tuple[int, int, int]] = []
    for handle in destination_handles:
        metadata = buffer_metadata.get(handle, {})
        base = safe_value(metadata, "device_address", "vk_device_address")
        size = parse_int(metadata.get("size"))
        if base is not None and base != 0 and size is not None and size > 0:
            destination_bdas.append((base, size, handle))

    consumer_groups: dict[tuple[str, str, str, str, str, str], dict[str, object]] = {}
    consumer_unresolved_context = 0
    with args.log.open("r", encoding="utf-8", errors="replace") as stream:
        for line in stream:
            parsed = audit_parts(line)
            if parsed is None:
                continue
            seq, event, body = parsed
            if event not in consumer_events:
                continue
            fields = parse_fields(body)
            kind = fields.get("kind", event)
            matched_handles: set[int] = set()
            if event == "DESCRIPTOR_WRITE":
                handle = safe_value(fields, "handle", "buffer")
                view = parse_int(fields.get("view"))
                address = parse_int(fields.get("address"))
                if handle in destination_handles:
                    matched_handles.add(handle)
                if view in buffer_views and buffer_views[view] in destination_handles:
                    matched_handles.add(buffer_views[view])
                if address is not None:
                    for base, size, dst_handle in destination_bdas:
                        if base <= address < base + size:
                            matched_handles.add(dst_handle)
            else:
                handle = safe_value(fields, "buffer", "handle")
                if handle in destination_handles:
                    matched_handles.add(handle)

            if not matched_handles:
                continue

            shader = fields.get("target_hash", "unknown")
            target_kind = fields.get("target_kind", "unknown")
            stage = fields.get("stage", "unknown")
            resource_kind = fields.get("resource_kind", kind)
            access = fields.get("is_written", "not_recorded")
            if shader == "unknown" or target_kind == "unknown":
                consumer_unresolved_context += 1

            for dst_handle in matched_handles:
                key = (hex(dst_handle), target_kind, shader, stage, resource_kind, access)
                entry = consumer_groups.setdefault(
                    key, {"count": 0, "first_seq": seq, "last_seq": seq,
                          "events": Counter(), "kinds": Counter(),
                          "offsets": set(), "ranges": set()}
                )
                entry["count"] = int(entry["count"]) + 1
                entry["last_seq"] = seq
                entry["events"][event] += 1
                entry["kinds"][kind] += 1
                if fields.get("offset") is not None:
                    entry["offsets"].add(fields["offset"])
                if fields.get("range") is not None:
                    entry["ranges"].add(fields["range"])

    # Pass 2d: after each host writeback, find later descriptor and usage
    # records whose backing CPU ranges overlap the guest-memory bytes written.
    # This follows the data beyond the temporary Download staging buffer.
    guest_consumer_events = {
        "DESCRIPTOR_WRITE", "DESCRIPTOR_CLASS_BIND", "COMPUTE_BUFFER_BIND",
        "BUFFER_USAGE",
    }
    guest_consumer_groups: dict[
        tuple[str, str, str, str, str, str], dict[str, object]
    ] = {}
    guest_consumer_unresolved_context = 0
    if readback_guest_intervals:
        with args.log.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                parsed = audit_parts(line)
                if parsed is None:
                    continue
                seq, event, body = parsed
                if event not in guest_consumer_events:
                    continue
                fields = parse_fields(body)
                backing = parse_int(fields.get("backing_cpu_addr"))
                if backing is None:
                    continue
                resource_offset = safe_value(fields, "resource_offset", "offset") or 0
                resource_range = safe_value(fields, "resource_range", "range")
                resource_start = backing + resource_offset
                resource_end = resource_start + (resource_range if resource_range and resource_range > 0 else 1)
                matched_intervals = [
                    interval for interval in readback_guest_intervals
                    if seq > interval[0] and resource_start < interval[2] and interval[1] < resource_end
                ]
                if not matched_intervals:
                    continue

                target_kind = fields.get("target_kind", "unknown")
                shader = fields.get("target_hash", "unknown")
                stage = fields.get("stage", "unknown")
                resource_kind = fields.get("resource_kind", fields.get("kind", event))
                access = fields.get("is_written", "not_recorded")
                if target_kind == "unknown" or shader == "unknown":
                    guest_consumer_unresolved_context += 1
                key = (target_kind, shader, stage, event, resource_kind, access)
                entry = guest_consumer_groups.setdefault(
                    key, {"count": 0, "first_seq": seq, "last_seq": seq,
                          "backing_cpu_addrs": set(), "guest_gpu_addrs": set(),
                          "ranges": set(), "readback_intervals": set(),
                          "buffer_handles": set()}
                )
                entry["count"] = int(entry["count"]) + 1
                entry["last_seq"] = seq
                entry["backing_cpu_addrs"].add(hex(backing))
                if fields.get("guest_gpu_addr") is not None:
                    entry["guest_gpu_addrs"].add(fields["guest_gpu_addr"])
                if resource_range is not None:
                    entry["ranges"].add(str(resource_range))
                handle = safe_value(fields, "buffer", "handle")
                if handle is not None:
                    entry["buffer_handles"].add(hex(handle))
                for interval in matched_intervals:
                    entry["readback_intervals"].add(
                        f"{hex(interval[1])}-{hex(interval[2])}"
                    )

    # Group target-buffer outbound copies by their scheduler tick, then pair
    # the groups with target dispatches by global sequence rank (k-th to k-th).
    outbound_by_tick: dict[int, dict[str, object]] = {}
    for seq, fields in outbound_transfer_records:
        tick = parse_int(fields.get("expected_submit_tick"))
        if tick is None:
            continue
        group = outbound_by_tick.setdefault(
            tick, {"count": 0, "sizes": [], "destinations": set(),
                   "sequences": [], "records": []}
        )
        group["count"] = int(group["count"]) + 1
        group["sizes"].append(parse_int(fields.get("size")) or 0)
        group["destinations"].add(fields.get("dst", "?"))
        group["sequences"].append(seq)
        group["records"].append((seq, fields))

    dispatch_tick_set = set(target_dispatch_ticks)
    transfer_tick_set = set(outbound_by_tick)
    common_transfer_dispatch_ticks = dispatch_tick_set & transfer_tick_set
    ordered_copy_groups = sorted(
        outbound_by_tick.items(), key=lambda item: min(item[1]["sequences"])
    )
    ordered_dispatches = sorted(target_dispatch_records, key=lambda item: item[0])
    pair_count = min(len(ordered_dispatches), len(ordered_copy_groups))
    pair_delta_histogram: Counter[int] = Counter()
    pair_seq_distance_histogram: Counter[str] = Counter()
    pair_rows: list[tuple[int, int, int, int, int]] = []
    for index in range(pair_count):
        dispatch_seq, dispatch_tick, _ = ordered_dispatches[index]
        copy_tick, group = ordered_copy_groups[index]
        first_copy_seq = min(group["sequences"])
        delta = copy_tick - dispatch_tick
        pair_delta_histogram[delta] += 1
        pair_seq_distance_histogram[
            "copy_after_dispatch" if first_copy_seq > dispatch_seq else "copy_before_or_at_dispatch"
        ] += 1
        pair_rows.append((index + 1, dispatch_seq, dispatch_tick, first_copy_seq, copy_tick))

    exact_pairs = 0
    contiguous_source_pairs = 0
    contiguous_destination_pairs = 0
    for _, group in ordered_copy_groups:
        records = sorted(group["records"], key=lambda item: item[0])
        if len(records) != 2:
            continue
        f0, f1 = records[0][1], records[1][1]
        if sorted(group["sizes"]) != [2953216, 3600384]:
            continue
        exact_pairs += 1
        src0 = parse_int(f0.get("src_offset"))
        src1 = parse_int(f1.get("src_offset"))
        dst0 = parse_int(f0.get("dst_offset"))
        dst1 = parse_int(f1.get("dst_offset"))
        size0 = parse_int(f0.get("size"))
        size1 = parse_int(f1.get("size"))
        if None not in (src0, src1, size0) and src1 == src0 + size0:
            contiguous_source_pairs += 1
        if None not in (dst0, dst1, size0) and dst1 == dst0 + size0:
            contiguous_destination_pairs += 1

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
    print(f"Target buffer bindings resolved from COMPUTE_BUFFER_BIND: {len(target_bindings)}")
    print(f"Target VkBuffer handles resolved from matching descriptor writes: {len(target_handles)}")
    for handle in sorted(target_handles):
        print(f"  VkBuffer={hex(handle)}")
    for seq, kind, fields in target_bindings:
        print(
            f"  seq={seq} kind={kind} guest_gpu_addr="
            f"{fields.get('guest_gpu_addr', fields.get('guest_base_gpu_addr', '?'))} "
            f"backing_cpu_addr={fields.get('backing_cpu_addr', '?')} "
            f"VkBuffer={fields.get('buffer', fields.get('descriptor_buffer', '?'))} "
            f"VkDeviceAddress={fields.get('vk_device_address', '?')} "
            f"offset={fields.get('offset', fields.get('source_offset', '?'))} "
            f"range={fields.get('range', fields.get('segment_size', fields.get('guest_total_range', '?')))}"
        )
    print(f"Descriptor writes for target shader: {target_compute_writes}")
    print(f"Descriptor writes resolved to a resource identity: {resolved_writes}")
    print(f"Descriptor writes unresolved: {unresolved_writes}")
    print(f"Descriptor writes overlapping target buffer: {len(target_descriptor_writes)}")
    if target_descriptor_writes:
        target_seqs = [seq for seq, _ in target_descriptor_writes]
        print(f"Target descriptor sequence range: first={min(target_seqs)} last={max(target_seqs)}")
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
    print(f"Target compute dispatch ticks: {len(dispatch_tick_set)}")
    print(f"Target-buffer outbound transfer ticks: {len(transfer_tick_set)}")
    print(f"Exact same-tick intersections (not sequence pairing): {len(common_transfer_dispatch_ticks)}")
    print(f"Dispatches paired by sequence rank: {pair_count}")
    print("K-th dispatch to k-th outbound copy-group tick delta histogram (copy_tick - dispatch_tick):")
    for delta, count in sorted(pair_delta_histogram.items()):
        print(f"  delta={delta}: {count}")
    print("K-th pair sequence ordering:")
    for ordering, count in sorted(pair_seq_distance_histogram.items()):
        print(f"  {ordering}: {count}")
    print(f"Two-chunk groups with exact 3600384 + 2953216 byte sizes: {exact_pairs}")
    print(f"Exact groups with contiguous source offsets: {contiguous_source_pairs}")
    print(f"Exact groups with contiguous destination offsets: {contiguous_destination_pairs}")
    print("First 20 sequence-rank dispatch/copy pairs:")
    for rank, dispatch_seq, dispatch_tick, copy_seq, copy_tick in pair_rows[:20]:
        print(f"  pair={rank} dispatch_seq={dispatch_seq} dispatch_tick={dispatch_tick} "
              f"copy_seq={copy_seq} copy_tick={copy_tick} delta={copy_tick - dispatch_tick}")
    print(f"Target buffer timeline records: {len(timeline)}")
    print(f"Readback staging buffers sourced from target: {len(readback_staging_handles)}")
    print(f"Target-sourced readback copy records: {len(readback_copy_records)}")
    print(f"Host readback guest-memory writeback records: {readback_event_counts.get('HOST_READBACK_WRITEBACK', 0)}")
    print(f"Host readback bytes written to guest memory: {readback_host_bytes}")
    print("Readback lifecycle event counts:")
    for name, count in sorted(readback_event_counts.items()):
        print(f"  {name}={count}")
    print("Readback staging handles:")
    for handle in sorted(readback_staging_handles):
        print(f"  staging_buffer={hex(handle)}")
    print("Readback guest destination ranges (CPU address -> bytes written):")
    for guest, byte_count in sorted(readback_guest_ranges.items()):
        print(f"  guest_cpu_addr={guest} bytes={byte_count}")
    print(f"Later shader/buffer usage records overlapping readback ranges: {sum(int(v['count']) for v in guest_consumer_groups.values())}")
    print(f"Later consumer records without shader context: {guest_consumer_unresolved_context}")
    print("Shaders and buffer-usage events referencing guest-memory ranges after readback:")
    for key, value in sorted(guest_consumer_groups.items()):
        target_kind, shader, stage, event, resource_kind, access = key
        print(
            f"  target_kind={target_kind} shader={shader} stage={stage} event={event} "
            f"resource_kind={resource_kind} is_written={access} count={value['count']} "
            f"first_seq={value['first_seq']} last_seq={value['last_seq']} "
            f"backing_cpu_addrs={','.join(sorted(value['backing_cpu_addrs']))} "
            f"guest_gpu_addrs={','.join(sorted(value['guest_gpu_addrs']))} "
            f"ranges={','.join(sorted(value['ranges']))} "
            f"buffers={','.join(sorted(value['buffer_handles']))} "
            f"readback_ranges={','.join(sorted(value['readback_intervals']))}"
        )
    for seq, event, fields in readback_events[:2000]:
        print(f"  seq={seq} {event} " +
              " ".join(f"{key}={value}" for key, value in fields.items()))
    if len(readback_events) > 2000:
        print(f"  ... {len(readback_events)-2000} additional readback lifecycle events omitted")
    print(f"Outbound destination VkBuffers: {len(destination_handles)}")
    for dst_handle in sorted(destination_handles):
        meta = buffer_metadata.get(dst_handle, {})
        print(f"  destination={hex(dst_handle)} size={meta.get('size','unknown')} "
              f"backing_cpu_addr={meta.get('backing_cpu_addr','unknown')}")
    print(f"Destination consumer records matched: {sum(int(v['count']) for v in consumer_groups.values())}")
    print(f"Destination consumer records without shader context: {consumer_unresolved_context}")
    print("Destination buffer shader consumers (descriptor/bind/usage records):")
    for key, value in sorted(consumer_groups.items()):
        dst, target_kind, shader, stage, resource_kind, access = key
        events = ",".join(f"{name}:{count}" for name, count in sorted(value["events"].items()))
        kinds = ",".join(f"{name}:{count}" for name, count in sorted(value["kinds"].items()))
        print(f"  destination={dst} target_kind={target_kind} shader={shader} stage={stage} "
              f"resource_kind={resource_kind} is_written={access} count={value['count']} "
              f"first_seq={value['first_seq']} last_seq={value['last_seq']} "
              f"events={events} kinds={kinds}")
    if wall_clock_anchors:
        print(f"Wall-clock anchors: {len(wall_clock_anchors)} (100 ms cadence)")
        for seq, fields in wall_clock_anchors:
            print(f"  seq={seq} steady_us={fields.get('steady_us','?')} "
                  f"wall_epoch_us={fields.get('wall_epoch_us','?')}")
    print("Target timeline event counts:")
    for name, count in sorted(timeline_event_counts.items()):
        print(f"  {name}={count}")
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
