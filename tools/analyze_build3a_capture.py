#!/usr/bin/env python3
"""Stream the Build 3A release ZIP and extract a compact diagnostic event ledger."""
from __future__ import annotations

import csv
import gzip
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

ARCHIVE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
BT = chr(96)

MARKERS = [
    "TARGET_RANGE_DISCOVERED",
    "TARGET_DISPATCH",
    "TARGET_GPU_WRITE_MARK",
    "TARGET_OWNERSHIP_CLEAR",
    "TARGET_OWNERSHIP_DECISION",
    "TARGET_ASYNC_REGISTRATION_ADD",
    "TARGET_ASYNC_BATCH_SCHEDULED",
    "TARGET_ASYNC_CANCEL",
    "TARGET_ASYNC_POP_RANGE",
    "TARGET_ASYNC_REGISTRATION_CONSUME",
    "TARGET_ASYNC_BATCH_POP",
    "TARGET_SYNC_READBACK",
    "TARGET_DISCOVERY",
    "PIPELINE_BIND",
    "DESCRIPTOR_CLASS_BIND",
    "COMPUTE_DISPATCH",
    "[Shader] Compiled:",
    "vkCmdDraw(",
    "vkCmdDrawIndexed(",
    "vkCmdDrawIndirect(",
    "vkCmdDrawIndexedIndirect(",
    "vkCmdDrawIndirectCount(",
    "vkCmdDrawIndexedIndirectCount(",
    "COMPUTE_BUFFER_BIND",
    "COMPUTE_MULTI_RANGE_SEGMENT",
    "FRAME_METRICS",
    "FENCE_SIGNAL_BEGIN",
    "FENCE_SIGNAL_END",
    "FENCE_WAIT_REQUEST",
    "FENCE_WAIT_RETURN",
    "RELEASE_FENCES",
]
TS_RE = re.compile(r"\[(\d+\.\d+)\]")
KV_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s,]+)")
SHADER_RE = re.compile(
    r"\[Shader\] Compiled: shader_([0-9a-fA-F]+)_(\w+) "
    r"\(SPIR-V size: (\d+) bytes, hash: ([0-9a-fA-F]+)\)"
)
DRAW_RE = re.compile(r"\[Thread:(\d+)\] (vkCmdDraw\w*)\((.*)\) -> (.*)$")

def is_log_name(name: str) -> bool:
    low = name.lower()
    return low.endswith((".log", ".txt", ".out", ".log.gz", ".txt.gz"))

def stream_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo):
    raw = zf.open(info, "r")
    if info.filename.lower().endswith(".gz"):
        return gzip.GzipFile(fileobj=raw, mode="rb")
    return raw

counts = Counter()
samples = defaultdict(lambda: {"first": None, "last": None})
total_lines = 0
total_bytes = 0
first_ts = None
last_ts = None
source_files = []
frame_rows = 0
dispatch_rows = 0
shader_rows = 0
draw_rows = 0

with zipfile.ZipFile(ARCHIVE, "r") as zf:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    with (OUT / "zip_inventory.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["member", "uncompressed_bytes", "compressed_bytes"])
        for info in infos:
            w.writerow([info.filename, info.file_size, info.compress_size])

    log_infos = [i for i in infos if is_log_name(i.filename)]
    if not log_infos:
        log_infos = [
            i for i in sorted(infos, key=lambda x: x.file_size, reverse=True)[:10]
            if not i.filename.lower().endswith((".png", ".jpg", ".jpeg", ".mp4", ".zip"))
        ]
    if not log_infos:
        raise SystemExit("No log/text members found in the uploaded ZIP. See zip_inventory.csv.")

    with gzip.open(OUT / "build3a_relevant_events.log.gz", "wt",
                   encoding="utf-8", errors="replace", compresslevel=6) as event_out, \
         (OUT / "frame_metrics.csv").open("w", newline="", encoding="utf-8") as frame_file, \
         (OUT / "dispatches.csv").open("w", newline="", encoding="utf-8") as dispatch_file, \
         (OUT / "shader_compilation.csv").open("w", newline="", encoding="utf-8") as shader_file, \
         (OUT / "draw_calls.csv").open("w", newline="", encoding="utf-8") as draw_file:

        fw = csv.writer(frame_file)
        dw = csv.writer(dispatch_file)
        sw = csv.writer(shader_file)
        vw = csv.writer(draw_file)

        fw.writerow(["timestamp_monotonic", "frame", "elapsed_us", "scheduler_tick", "raw_line"])
        dw.writerow(["timestamp_monotonic", "frame", "dispatch_mode", "groups_x", "groups_y",
                     "groups_z", "scheduler_tick", "raw_line"])
        sw.writerow(["timestamp_monotonic", "shader_name", "shader_hash", "stage",
                     "spirv_bytes", "logged_hash", "raw_line"])
        vw.writerow(["timestamp_monotonic", "call_name", "thread", "parameters", "result", "raw_line"])

        for info in log_infos:
            source_files.append((info.filename, info.file_size))
            with stream_member(zf, info) as stream:
                for raw_line in stream:
                    total_lines += 1
                    total_bytes += len(raw_line)
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    hits = [marker for marker in MARKERS if marker in line]
                    if not hits:
                        continue

                    ts_match = TS_RE.search(line)
                    ts = ts_match.group(1) if ts_match else ""

                    for marker in hits:
                        counts[marker] += 1
                        if samples[marker]["first"] is None:
                            samples[marker]["first"] = line[:700]
                        samples[marker]["last"] = line[:700]

                    event_out.write(line + "\n")
                    kv = dict(KV_RE.findall(line))

                    if "FRAME_METRICS" in hits:
                        fw.writerow([ts, kv.get("frame", ""), kv.get("elapsed_us", ""),
                                     kv.get("scheduler_tick", ""), line[:1000]])
                        frame_rows += 1

                    if "TARGET_DISPATCH" in hits:
                        dw.writerow([ts, kv.get("frame", ""), kv.get("mode", ""),
                                     kv.get("groups", "").split("x")[0],
                                     kv.get("groups", "").split("x")[1] if "x" in kv.get("groups", "") else "",
                                     kv.get("groups", "").split("x")[2] if kv.get("groups", "").count("x") >= 2 else "",
                                     kv.get("expected_submit_tick", ""), line[:1000]])
                        dispatch_rows += 1

                    if "[Shader] Compiled:" in line:
                        match = SHADER_RE.search(line)
                        if match:
                            sw.writerow([ts, "shader_" + match.group(1) + "_" + match.group(2),
                                         "0x" + match.group(1), match.group(2), match.group(3),
                                         match.group(4), line[:1000]])
                            shader_rows += 1

                    if "[Vulkan]" in line and "vkCmdDraw" in line:
                        match = DRAW_RE.search(line)
                        if match:
                            vw.writerow([ts, match.group(2), match.group(1), match.group(3),
                                         match.group(4), line[:1200]])
                            draw_rows += 1

summary = []
summary.append("# Build 3A capture — streamed triage")
summary.append("")
summary.append(f"- Archive: {BT}{ARCHIVE.name}{BT}")
summary.append(f"- Log members scanned: **{len(source_files)}**")
summary.append(f"- Total uncompressed log lines scanned: **{total_lines:,}**")
summary.append(f"- Total uncompressed bytes read: **{total_bytes:,}**")
summary.append(f"- Frame metric rows: **{frame_rows:,}**")
summary.append(f"- Target dispatch rows: **{dispatch_rows:,}**")
summary.append(f"- Shader compilation records: **{shader_rows:,}**")
summary.append(f"- Vulkan draw-call records: **{draw_rows:,}**")
summary.append("")
summary.append("## ZIP members scanned")
for name, size in source_files:
    summary.append(f"- {BT}{name}{BT} — {size:,} bytes")
summary.append("")
summary.append("## Event counts")
summary.append("| Event | Count |")
summary.append("|---|---:|")
for marker in MARKERS:
    summary.append(f"| {BT}{marker}{BT} | {counts[marker]:,} |")
summary.append("")
summary.append("## First and last examples")
for marker in MARKERS:
    if counts[marker]:
        summary.append(f"### {BT}{marker}{BT}")
        summary.append("First: " + BT + (samples[marker]["first"] or "").replace(BT, "'") + BT)
        summary.append("")
        summary.append("Last: " + BT + (samples[marker]["last"] or "").replace(BT, "'") + BT)
        summary.append("")
(OUT / "capture_summary.md").write_text("\n".join(summary), encoding="utf-8")

with (OUT / "event_counts.csv").open("w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["event", "count"])
    for marker in MARKERS:
        writer.writerow([marker, counts[marker]])

print((OUT / "capture_summary.md").read_text(encoding="utf-8"))
