// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <cstdint>
#include <memory>
#include <mutex>
#include <vector>

#include "common/common_types.h"

namespace Common::FS { class IOFile; }

namespace GPU::Logging {

// Compact, versioned binary event stream. All timestamps are nanoseconds.
enum class DiagnosticEventType : u16 {
    SessionStart = 1,
    SessionEnd = 2,
    RenderPassBegin = 3,
    RenderPassEnd = 4,
    ComputeDispatch = 5,
    GraphicsDraw = 6,
    ResourceUpdate = 7,
    DescriptorUpdate = 8,
    Synchronization = 9,
    Presentation = 10,
    DiagnosticError = 255,
};

struct DiagnosticEvent {
    u16 type{};
    u16 flags{};
    u32 thread_id{};
    u64 sequence{};
    u64 wall_time_ns{};
    u64 monotonic_time_ns{};
    u64 frame_id{};
    u64 object_a{};
    u64 object_b{};
    u64 value_a{};
    u64 value_b{};
};
static_assert(sizeof(DiagnosticEvent) == 72);

class NativeDiagnosticRecorder {
public:
    static NativeDiagnosticRecorder& GetInstance();
    NativeDiagnosticRecorder(const NativeDiagnosticRecorder&) = delete;
    NativeDiagnosticRecorder& operator=(const NativeDiagnosticRecorder&) = delete;

    // Opens a versioned binary stream beneath EdenPath::LogDir/diagnostic.
    // Returns false if the directory or file cannot be created.
    bool Initialize();
    void Record(DiagnosticEventType type, u64 frame_id = 0, u64 object_a = 0,
                u64 object_b = 0, u64 value_a = 0, u64 value_b = 0,
                u16 flags = 0) noexcept;
    void Shutdown() noexcept;
    [[nodiscard]] bool IsInitialized() const noexcept;

private:
    NativeDiagnosticRecorder() = default;
    ~NativeDiagnosticRecorder();

    bool FlushChunkLocked() noexcept;
    mutable std::mutex mutex;
    std::unique_ptr<Common::FS::IOFile> file;
    std::vector<u8> chunk;
    u64 sequence = 0;
    u64 dropped_events = 0;
    bool initialized = false;
    bool failed = false;
};

} // namespace GPU::Logging
