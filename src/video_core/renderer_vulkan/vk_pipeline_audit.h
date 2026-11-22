// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <cstdint>
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

template <typename... Args>
void Log(fmt::format_string<Args...> format, Args&&... args) {
    auto& logger = GPU::Logging::GPULogger::GetInstance();
    if (!logger.IsDetailedAuditEnabled()) {
        return;
    }
    logger.LogPipelineAuditEvent(fmt::format(format, std::forward<Args>(args)...));
}

} // namespace Vulkan::PipelineAudit
