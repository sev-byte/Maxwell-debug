// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include "video_core/gpu_logging/native_diagnostic_recorder.h"

#include <chrono>
#include <filesystem>
#include <thread>

#include "common/fs/file.h"
#include "common/fs/fs.h"
#include "common/fs/path_util.h"
#include "common/zstd_compression.h"

namespace GPU::Logging {
namespace {
constexpr u32 Magic = 0x4E444544; // "DEDN" in little-endian byte order
constexpr u16 FormatVersion = 1;
constexpr std::size_t ChunkLimit = 256 * 1024;

struct FileHeader {
    u32 magic;
    u16 version;
    u16 event_size;
    u64 wall_start_ns;
    u64 monotonic_start_ns;
};

struct ChunkHeader {
    u32 magic;
    u32 uncompressed_size;
    u32 compressed_size;
    u32 checksum;
};

u64 WallNowNs() {
    return static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count());
}

u64 MonotonicNowNs() {
    return static_cast<u64>(std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}

u32 HashBytes(const std::vector<u8>& bytes) {
    // FNV-1a checksum detects truncated or corrupted chunks; it is not cryptographic.
    u32 hash = 2166136261u;
    for (const u8 byte : bytes) {
        hash = (hash ^ byte) * 16777619u;
    }
    return hash;
}
} // namespace

NativeDiagnosticRecorder& NativeDiagnosticRecorder::GetInstance() {
    static NativeDiagnosticRecorder recorder;
    return recorder;
}

NativeDiagnosticRecorder::~NativeDiagnosticRecorder() {
    Shutdown();
}

bool NativeDiagnosticRecorder::Initialize() {
    std::scoped_lock lock{mutex};
    if (initialized) {
        return true;
    }

    using namespace Common::FS;
    const auto directory = GetEdenPath(EdenPath::LogDir) / "diagnostic";
    if (!CreateDir(directory)) {
        failed = true;
        return false;
    }

    const auto path = directory / "native_events.bin.zst";
    file = std::make_unique<IOFile>(path, FileAccessMode::Write, FileType::BinaryFile);
    if (!file->IsOpen()) {
        file.reset();
        failed = true;
        return false;
    }

    chunk.clear();
    chunk.reserve(ChunkLimit);
    sequence = 0;
    dropped_events = 0;
    failed = false;

    const FileHeader header{
        .magic = Magic,
        .version = FormatVersion,
        .event_size = static_cast<u16>(sizeof(DiagnosticEvent)),
        .wall_start_ns = WallNowNs(),
        .monotonic_start_ns = MonotonicNowNs(),
    };
    if (file->Write(header) != 1) {
        file->Close();
        file.reset();
        failed = true;
        return false;
    }

    initialized = true;
    DiagnosticEvent start{
        .type = static_cast<u16>(DiagnosticEventType::SessionStart),
        .sequence = ++sequence,
        .wall_time_ns = WallNowNs(),
        .monotonic_time_ns = MonotonicNowNs(),
    };
    chunk.insert(chunk.end(), reinterpret_cast<const u8*>(&start),
                 reinterpret_cast<const u8*>(&start) + sizeof(start));
    return true;
}

void NativeDiagnosticRecorder::Record(DiagnosticEventType type, u64 frame_id,
                                      u64 object_a, u64 object_b, u64 value_a,
                                      u64 value_b, u16 flags) noexcept {
    std::scoped_lock lock{mutex};
    if (!initialized || failed) {
        ++dropped_events;
        return;
    }

    const auto now_wall = WallNowNs();
    const auto now_mono = MonotonicNowNs();
    const DiagnosticEvent event{
        .type = static_cast<u16>(type),
        .flags = flags,
        .thread_id = static_cast<u32>(std::hash<std::thread::id>{}(std::this_thread::get_id())),
        .sequence = ++sequence,
        .wall_time_ns = now_wall,
        .monotonic_time_ns = now_mono,
        .frame_id = frame_id,
        .object_a = object_a,
        .object_b = object_b,
        .value_a = value_a,
        .value_b = value_b,
    };

    const auto* begin = reinterpret_cast<const u8*>(&event);
    chunk.insert(chunk.end(), begin, begin + sizeof(event));
    if (chunk.size() >= ChunkLimit && !FlushChunkLocked()) {
        failed = true;
        ++dropped_events;
    }
}

bool NativeDiagnosticRecorder::FlushChunkLocked() noexcept {
    if (!file || chunk.empty()) {
        return true;
    }

    const auto compressed = Common::Compression::CompressDataZSTD(
        chunk.data(), chunk.size(), 3);
    if (compressed.empty()) {
        return false;
    }

    const ChunkHeader header{
        .magic = 0x4B4E4843, // "CHNK"
        .uncompressed_size = static_cast<u32>(chunk.size()),
        .compressed_size = static_cast<u32>(compressed.size()),
        .checksum = HashBytes(chunk),
    };
    if (file->Write(header) != 1 || file->Write(compressed) != compressed.size()) {
        return false;
    }
    file->Flush();
    chunk.clear();
    return true;
}

void NativeDiagnosticRecorder::Shutdown() noexcept {
    std::scoped_lock lock{mutex};
    if (!file) {
        initialized = false;
        return;
    }

    if (initialized && !failed) {
        DiagnosticEvent end{
            .type = static_cast<u16>(DiagnosticEventType::SessionEnd),
            .sequence = ++sequence,
            .wall_time_ns = WallNowNs(),
            .monotonic_time_ns = MonotonicNowNs(),
            .value_a = dropped_events,
        };
        const auto* begin = reinterpret_cast<const u8*>(&end);
        chunk.insert(chunk.end(), begin, begin + sizeof(end));
        if (!FlushChunkLocked()) {
            failed = true;
        }
    }

    file->Flush();
    file->Close();
    file.reset();
    initialized = false;
}

bool NativeDiagnosticRecorder::IsInitialized() const noexcept {
    std::scoped_lock lock{mutex};
    return initialized && !failed;
}

} // namespace GPU::Logging
