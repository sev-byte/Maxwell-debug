// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <cstdint>
#include <string>
#include <type_traits>
#include <utility>

#include <fmt/format.h>

#include "common/common_types.h"
#include "video_core/gpu_logging/gpu_logging.h"

namespace Vulkan::PipelineAudit {

template <typename Handle>
u64 HandleValue(Handle handle) noexcept {
    if constexpr (std::is_pointer_v<Handle>) {
        return static_cast<u64>(reinterpret_cast<std::uintptr_t>(handle));
    } else {
        return static_cast<u64>(handle);
    }
}

struct AuditContext {
    const char* target_kind{};
    u64 target_hash{};
    const char* stage{};
    const char* resource_kind{};
    GPUVAddr guest_gpu_addr{};
    DAddr backing_cpu_addr{};
    u64 buffer_handle{};
    u64 resource_offset{};
    u64 resource_range{};
};

inline thread_local AuditContext current_context{};

class ScopedContext {
public:
    ScopedContext(const char* target_kind, u64 target_hash, const char* stage) noexcept
        : previous{current_context} {
        current_context = {target_kind, target_hash, stage};
    }
    ~ScopedContext() {
        current_context = previous;
    }
    ScopedContext(const ScopedContext&) = delete;
    ScopedContext& operator=(const ScopedContext&) = delete;

private:
    AuditContext previous;
};

class ScopedResource {
public:
    ScopedResource(const char* resource_kind, GPUVAddr guest_gpu_addr,
                   DAddr backing_cpu_addr, u64 buffer_handle, u64 offset,
                   u64 range) noexcept
        : previous{current_context} {
        current_context.resource_kind = resource_kind;
        current_context.guest_gpu_addr = guest_gpu_addr;
        current_context.backing_cpu_addr = backing_cpu_addr;
        current_context.buffer_handle = buffer_handle;
        current_context.resource_offset = offset;
        current_context.resource_range = range;
    }
    ~ScopedResource() {
        current_context = previous;
    }
    ScopedResource(const ScopedResource&) = delete;
    ScopedResource& operator=(const ScopedResource&) = delete;

private:
    AuditContext previous;
};

template <typename... Args>
void Log(fmt::format_string<Args...> format, Args&&... args) noexcept {
    try {
        auto& logger = GPU::Logging::GPULogger::GetInstance();
        if (!logger.IsDetailedAuditEnabled()) {
            return;
        }
        std::string message = fmt::format(format, std::forward<Args>(args)...);
        if (current_context.target_kind != nullptr) {
            message = fmt::format("target_kind={} target_hash={:#x} stage={} {}",
                                  current_context.target_kind, current_context.target_hash,
                                  current_context.stage, message);
        }
        if (current_context.resource_kind != nullptr) {
            message = fmt::format(
                "{} resource_kind={} guest_gpu_addr={:#x} backing_cpu_addr={:#x} buffer={:#x} resource_offset={} resource_range={}",
                message, current_context.resource_kind, current_context.guest_gpu_addr,
                current_context.backing_cpu_addr, current_context.buffer_handle,
                current_context.resource_offset, current_context.resource_range);
        }
        logger.LogPipelineAuditEvent(message);
    } catch (...) {
        try {
            GPU::Logging::GPULogger::GetInstance().NotePipelineAuditDrop();
        } catch (...) {
            // The diagnostic path must never affect emulation.
        }
    }
}

} // namespace Vulkan::PipelineAudit
