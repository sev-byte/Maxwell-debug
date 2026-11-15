#!/usr/bin/env python3
"""Stream-inspect the preflush capture ZIP on a GitHub Actions runner."""
from __future__ import annotations
import argparse, collections, io, re, tempfile, zipfile
from pathlib import Path

TARGET_HASH = "4577aed68dd75ecc"
VOLUMETRIC_HASH = "8c0d043b68e7a6d1"
ASSET_URL = "https://github.com/sev-byte/Maxwell-debug/releases/download/preflush-capture-2026-11-15/Eden.preflushable.log.zip"
RELEASE_URL = "https://github.com/sev-byte/Maxwell-debug/releases/tag/preflush-capture-2026-11-15"
BUILD_URL = "https://github.com/sev-byte/Maxwell-debug/actions/runs/43753544882"
TEXT_EXTS = {".log", ".txt", ".json", ".jsonl", ".csv", ".trace", ".xml"}
BINARY_EXTS = {".bin", ".spv", ".ash", ".apk", ".so", ".png", ".jpg", ".jpeg", ".mp4"}
PATTERNS = {
    "shader_match": re.compile(r"MAXWELL-PREFLUSH matched target lighting compute shader", re.I),
    "ssbo_write": re.compile(r"MAXWELL-PREFLUSH target SSBO write", re.I),
    "bypass": re.compile(r"MAXWELL-PREFLUSH bypass", re.I),
    "target_hash": re.compile(TARGET_HASH, re.I),
    "volumetric_hash": re.compile(VOLUMETRIC_HASH, re.I),
    "preflushable_true": re.compile(r"preflushable_before\s*=\s*true", re.I),
    "preflushable_false": re.compile(r"preflushable_before\s*=\s*false", re.I),
}
ADDR_RE = re.compile(r"addr\s*=\s*(0x[0-9a-f]+).*?size\s*=\s*(0x[0-9a-f]+)", re.I)
WALL_RE = re.compile(r"wall_ms\s*=\s*(\d+)", re.I)

def human(n):
    x = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if x < 1024 or unit == "TB":
            return f"{x:.1f} {unit}"
        x /= 1024

def choose_text_files(infos):
    files = [x for x in infos if not x.is_dir()]
    chosen = [x for x in files if Path(x.filename).suffix.lower() in TEXT_EXTS]
    return chosen or [x for x in files if Path(x.filename).suffix.lower() not in BINARY_EXTS and x.file_size]

def scan_stream(stream, name, counts, samples, ranges, wall):
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
    line_no = 0
    replacements = 0
    try:
        for raw in text:
            line_no += 1
            if "\ufffd" in raw:
                replacements += 1
            line = raw.rstrip("\r\n")
            for key, pattern in PATTERNS.items():
                if pattern.search(line):
                    counts[key] += 1
                    if len(samples[key]) < 30:
                        samples[key].append((name, line_no, line[:1600]))
            if "MAXWELL-PREFLUSH target SSBO write" in line:
                m = ADDR_RE.search(line)
                if m: ranges["writes"].add((m.group(1).lower(), m.group(2).lower()))
            if "MAXWELL-PREFLUSH bypass" in line:
                m = ADDR_RE.search(line)
                if m: ranges["bypasses"].add((m.group(1).lower(), m.group(2).lower()))
            m = WALL_RE.search(line)
            if m: wall.append(int(m.group(1)))
    finally:
        try: text.detach()
        except Exception: pass
    counts["text_lines"] += line_no
    counts["replacement_lines"] += replacements
    counts["files_scanned"] += 1

def inspect_zip(path):
    manifest, counts, samples = [], collections.Counter(), collections.defaultdict(list)
    ranges, wall, notes = {"writes": set(), "bypasses": set()}, [], []
    def walk(zpath, label, depth=0):
        if depth > 2:
            notes.append("Nested ZIP depth exceeded: " + label)
            return
        try:
            with zipfile.ZipFile(zpath) as zf:
                infos = zf.infolist()
                files = [i for i in infos if not i.is_dir()]
                manifest.append((label, len(infos), sum(i.file_size for i in files),
                                 [(i.filename, i.file_size, i.compress_size) for i in infos[:100]]))
                nested = [i for i in files if Path(i.filename).suffix.lower() == ".zip"]
                for info in nested:
                    with tempfile.NamedTemporaryFile(prefix="nested-preflush-", suffix=".zip") as tmp:
                        with zf.open(info) as src:
                            while True:
                                data = src.read(8 * 1024 * 1024)
                                if not data: break
                                tmp.write(data)
                        tmp.flush()
                        walk(Path(tmp.name), label + "!" + info.filename, depth + 1)
                selected = choose_text_files(infos)
                for info in selected:
                    if not info.file_size or Path(info.filename).suffix.lower() == ".zip": continue
                    try:
                        scan_stream(zf.open(info), label + "!" + info.filename,
                                    counts, samples, ranges, wall)
                    except Exception as exc:
                        notes.append(f"Scan error in {info.filename}: {type(exc).__name__}: {exc}")
                if not selected and not nested:
                    notes.append("No likely text/log members in " + label)
        except zipfile.BadZipFile:
            notes.append("Invalid ZIP: " + label)
    walk(path, path.name)
    return manifest, counts, samples, ranges, wall, notes

def make_report(path):
    manifest, counts, samples, ranges, wall, notes = inspect_zip(path)
    get = lambda k: counts.get(k, 0)
    writes, bypasses = ranges["writes"], ranges["bypasses"]
    overlap = writes & bypasses
    if not get("shader_match"):
        conclusion = ("No target-dispatch match marker was found. The capture does not demonstrate that "
                      "the identified 320x6x1 shader predicate matched while diagnostics were active.")
    elif not get("ssbo_write"):
        conclusion = ("The target dispatch matched, but no target SSBO-write marker was found. The shader "
                      "was recognised, but its instrumented writable SSBO path did not record an output range.")
    elif not get("bypass"):
        conclusion = ("Target SSBO writes were recorded, but no preflush-bypass marker was found. This "
                      "capture does not show a CPU read reaching the bypass path for those tracked outputs.")
    elif overlap:
        conclusion = ("Shader match, target SSBO writes and preflush bypasses are all present, with at least "
                      "one identical address/size pair in write and bypass records. The bypass was reached "
                      "for a tracked range. Since the user reports no visual change, this weakens the idea "
                      "that this preflush shortcut is the sole cause of the flicker.")
    else:
        conclusion = ("Shader match, SSBO-write and bypass markers all appear, but no exact address/size "
                      "pair matches between write and bypass records. The bypass ran, but range identity "
                      "is not established by the parsed log lines.")
    out = [
        "# Skyrim preflush bypass capture inspection", "",
        f"Capture: {ASSET_URL}",
        f"Release: {RELEASE_URL}",
        f"Test build: {BUILD_URL}",
        f"Archive size: {human(path.stat().st_size)}", "",
        "## Archive manifest"
    ]
    if manifest:
        for label, entries, size, members in manifest:
            out.append(f"- {label}: {entries} entries; uncompressed files total {human(size)}")
            for name, n, c in members:
                out.append(f"  - {name}: {human(n)} (compressed {human(c)})")
    else: out.append("- No readable archive manifest.")
    out += ["", "## Marker counts", "| Marker | Count |", "|---|---:|",
            f"| Target lighting compute shader match | {get('shader_match')} |",
            f"| Target SSBO write | {get('ssbo_write')} |",
            f"| Preflush bypass | {get('bypass')} |",
            f"| Target shader hash {TARGET_HASH} | {get('target_hash')} |",
            f"| 3D-image compute lead {VOLUMETRIC_HASH} | {get('volumetric_hash')} |",
            f"| preflushable_before=true | {get('preflushable_true')} |",
            f"| preflushable_before=false | {get('preflushable_false')} |", "",
            f"- Text files scanned: {get('files_scanned')}",
            f"- Text lines scanned: {get('text_lines'):,}",
            f"- Lines with UTF-8 replacement characters: {get('replacement_lines'):,}"]
    if wall: out.append(f"- wall_ms timestamps found: {len(wall):,}; min={min(wall)}; max={max(wall)}")
    out += ["", "## Address/range comparison",
            f"- Unique target write address/size pairs: {len(writes)}",
            f"- Unique bypass address/size pairs: {len(bypasses)}",
            f"- Exact address/size intersections: {len(overlap)}"]
    for addr, size in sorted(overlap)[:100]: out.append(f"  - {addr} size {size}")
    out += ["", "## Interpretation", conclusion, "",
            "The user-reported visual outcome for this capture was no visible change. "
            "The log establishes whether the instrumented path ran; it does not alone prove the visual cause.", ""]
    for key, label in [("shader_match", "Shader match samples"), ("ssbo_write", "Target SSBO write samples"),
                       ("bypass", "Bypass samples")]:
        out.append("## " + label)
        entries = samples.get(key, [])
        if not entries: out.append("- No matching lines found.")
        else:
            for filename, line_no, line in entries:
                out.append(f"- {filename}, line {line_no}: {line}")
        out.append("")
    out += ["## Secondary compute-shader lead",
            f"The separate compute hash {VOLUMETRIC_HASH} is counted as a lead because earlier analysis "
            "found it writes a 3D storage image. Hash presence alone does not identify it as volumetric "
            "lighting; its dispatch and bound resource data must be correlated.", "",
            "## Scanner notes"]
    out.extend(["- " + x for x in notes] if notes else ["- No archive/parser errors recorded."])
    report = "\n".join(out) + "\n"
    summary = (f"Scanned {get('files_scanned')} text files / {get('text_lines'):,} lines. "
               f"Markers match={get('shader_match')}, SSBO-write={get('ssbo_write')}, "
               f"bypass={get('bypass')}; unique write ranges={len(writes)}, bypass ranges={len(bypasses)}, "
               f"exact intersections={len(overlap)}. {conclusion}")
    return report, summary

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--zip", required=True, type=Path)
    p.add_argument("--report", type=Path, default=Path("docs/preflush-bypass-capture-report.md"))
    p.add_argument("--notes", type=Path, default=Path("docs/skyrim-target-preflush-test.md"))
    a = p.parse_args()
    report, summary = make_report(a.zip)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(report, encoding="utf-8")
    old = a.notes.read_text(encoding="utf-8") if a.notes.exists() else "# Skyrim target-shader preflush test\n"
    heading = "## Capture inspection — 15 November 2026"
    if heading not in old:
        addition = ("\n\n" + heading + "\n\n" + summary +
                    "\n\nFull evidence and marker samples: [preflush capture report](preflush-bypass-capture-report.md).\n")
        a.notes.write_text(old.rstrip() + addition, encoding="utf-8")
    print(summary)
    print("REPORT_WRITTEN=" + str(a.report))
    print("NOTES_UPDATED=" + str(a.notes))

if __name__ == "__main__":
    main()
