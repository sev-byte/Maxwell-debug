// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#include "video_core/gpu_logging/native_diagnostic_recorder.h"

#include <chrono>
#include <functional>
#include <string>

#include "common/fs/file.h"
#include "common/fs/fs.h"
#include "common/fs/path_util.h"
#include "common/zstd_compression.h"

namespace GPU::Logging {
namespace {
constexpr u32 Magic = 0x4E444544;
constexpr u16 FormatVersion = 1;
constexpr std::size_t ChunkLimit = 256 * 1024;
constexpr auto WriterWakeInterval = std::chrono::milliseconds(250);

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

    // A unique file avoids overwriting a previous diagnostic session.
    const auto path = directory / ("native_events_" + std::to_string(WallNowNs()) + ".edlog");
    file = std::make_unique<IOFile>(path, FileAccessMode::Write, FileType::BinaryFile);
    if (!file->IsOpen()) {
        file.reset();
        failed = true;
        return false;
    }

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

    queue.clear();
    sequence = 0;
    dropped_events = 0;
    stopping = false;
    failed = false;
    initialized = true;

    DiagnosticEvent start{
        .type = static_cast<u16>(DiagnosticEventType::SessionStart),
        .sequence = ++sequence,
        .wall_time_ns = WallNowNs(),
        .monotonic_time_ns = MonotonicNowNs(),
    };
    queue.push_back(start);

    try {
        writer = std::thread(&NativeDiagnosticRecorder::WriterLoop, this);
    } catch (...) {
        initialized = false;
        file->Close();
        file.reset();
        failed = true;
        queue.clear();
        return false;
    }
    return true;
}

void NativeDiagnosticRecorder::Record(DiagnosticEventType type, u64 frame_id,
                                      u64 object_a, u64 object_b, u64 value_a,
                                      u64 value_b, u16 flags) noexcept {
    {
        std::scoped_lock lock{mutex};
        if (!initialized || stopping || failed) {
            ++dropped_events;
            return;
        }
        if (queue.size() >= MaxQueuedEvents) {
            ++dropped_events;
            return;
        }

        queue.push_back(DiagnosticEvent{
            .type = static_cast<u16>(type),
            .flags = flags,
            .thread_id = static_cast<u32>(
                std::hash<std::thread::id>{}(std::this_thread::get_id())),
            .sequence = ++sequence,
            .wall_time_ns = WallNowNs(),
            .monotonic_time_ns = MonotonicNowNs(),
            .frame_id = frame_id,
            .object_a = object_a,
            .object_b = object_b,
            .value_a = value_a,
            .value_b = value_b,
        });
    }
    wake_writer.notify_one();
}

bool NativeDiagnosticRecorder::WriteChunk(const std::vector<u8>& events) noexcept {
    if (!file || events.empty()) {
        return true;
    }

    const auto compressed =
        Common::Compression::CompressDataZSTD(events.data(), events.size(), 3);
    if (compressed.empty()) {
        return false;
    }

    const ChunkHeader header{
        .magic = 0x4B4E4843,
        .uncompressed_size = static_cast<u32>(events.size()),
        .compressed_size = static_cast<u32>(compressed.size()),
        .checksum = HashBytes(events),
    };
    if (file->Write(header) != 1 || file->Write(compressed) != compressed.size()) {
        return false;
    }
    file->Flush();
    return true;
}

void NativeDiagnosticRecorder::WriterLoop() noexcept {
    std::vector<u8> chunk;
    chunk.reserve(ChunkLimit);

    for (;;) {
        std::deque<DiagnosticEvent> batch;
        bool should_stop = false;
        {
            std::unique_lock lock{mutex};
            wake_writer.wait_for(lock, WriterWakeInterval,
                                 [this] { return !queue.empty() || stopping; });
            while (!queue.empty() &&
                   batch.size() < ChunkLimit / sizeof(DiagnosticEvent)) {
                batch.push_back(queue.front());
                queue.pop_front();
            }
            should_stop = stopping && queue.empty();
        }

        for (const auto& event : batch) {
            const auto* begin = reinterpret_cast<const u8*>(&event);
            chunk.insert(chunk.end(), begin, begin + sizeof(event));
        }

        if (chunk.size() >= ChunkLimit || (should_stop && !chunk.empty())) {
            if (!WriteChunk(chunk)) {
                std::scoped_lock lock{mutex};
                failed = true;
                dropped_events += queue.size();
                queue.clear();
                chunk.clear();
            } else {
                chunk.clear();
            }
        }

        if (should_stop) {
            break;
        }
    }

    if (file) {
        file->Flush();
        file->Close();
        file.reset();
    }
}

void NativeDiagnosticRecorder::Shutdown() noexcept {
    {
        std::scoped_lock lock{mutex};
        if (!initialized && !writer.joinable()) {
            return;
        }
        if (initialized && !failed) {
            if (queue.size() < MaxQueuedEvents) {
                queue.push_back(DiagnosticEvent{
                    .type = static_cast<u16>(DiagnosticEventType::SessionEnd),
                    .sequence = ++sequence,
                    .wall_time_ns = WallNowNs(),
                    .monotonic_time_ns = MonotonicNowNs(),
                    .value_a = dropped_events,
                });
            } else {
                ++dropped_events;
            }
        }
        initialized = false;
        stopping = true;
    }
    wake_writer.notify_one();
    if (writer.joinable()) {
        writer.join();
    }
}

bool NativeDiagnosticRecorder::IsInitialized() const noexcept {
    std::scoped_lock lock{mutex};
    return initialized && !failed;
}

} // namespace GPU::Logging
