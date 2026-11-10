// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <condition_variable>
#include <cstdint>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

#include "common/common_types.h"

namespace Common::FS { class IOFile; }

namespace GPU::Logging {

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
    FrameAcquire = 11,
    FramePresentQueued = 12,
    FramePresentSubmitted = 13,
    DescriptorBufferWrite = 14,
    DescriptorImageWrite = 15,
    DescriptorTexelWrite = 16,
    DescriptorAddressWrite = 17,
    GraphicsPipelineBind = 18,\n    ComputePipelineBind = 19,\n    DescriptorSetBind = 20,\n    DescriptorBufferBind = 21,\n    PushDescriptorBind = 22,
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

    bool Initialize();
    void Record(DiagnosticEventType type, u64 frame_id = 0, u64 object_a = 0,
                u64 object_b = 0, u64 value_a = 0, u64 value_b = 0,
                u16 flags = 0) noexcept;
    void Shutdown() noexcept;
    [[nodiscard]] bool IsInitialized() const noexcept;

private:
    NativeDiagnosticRecorder() = default;
    ~NativeDiagnosticRecorder();

    void WriterLoop() noexcept;
    bool WriteChunk(const std::vector<u8>& events) noexcept;

    static constexpr std::size_t MaxQueuedEvents = 65536;
    mutable std::mutex mutex;
    std::condition_variable wake_writer;
    std::deque<DiagnosticEvent> queue;
    std::unique_ptr<Common::FS::IOFile> file;
    std::thread writer;
    u64 sequence = 0;
    u64 dropped_events = 0;
    bool initialized = false;
    bool stopping = false;
    bool failed = false;
};

} // namespace GPU::Logging
