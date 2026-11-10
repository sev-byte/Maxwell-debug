// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// SPDX-FileCopyrightText: Copyright 2019 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <variant>
#include <vector>
#include "video_core/vulkan_common/vulkan_wrapper.h"
#include "video_core/gpu_logging/native_diagnostic_recorder.h"

namespace Vulkan {

class Device;
class Scheduler;

struct DescriptorAddress {
    VkDeviceAddress address;
    VkDeviceSize range;
    VkFormat format;
};

union DescriptorUpdateEntry {
    DescriptorUpdateEntry() = default;
    DescriptorUpdateEntry(VkDescriptorImageInfo image_) : image{image_} {}
    DescriptorUpdateEntry(VkDescriptorBufferInfo buffer_) : buffer{buffer_} {}
    DescriptorUpdateEntry(VkBufferView texel_buffer_) : texel_buffer{texel_buffer_} {}
    DescriptorUpdateEntry(DescriptorAddress address_) : address{address_} {}
    std::monostate empty{};
    VkDescriptorImageInfo image;
    VkDescriptorBufferInfo buffer;
    VkBufferView texel_buffer;
    DescriptorAddress address;
};

class UpdateDescriptorQueue final {
    // This should be plenty for the vast majority of cases. Most desktop platforms only
    // provide up to 3 swapchain images.
    static constexpr size_t FRAMES_IN_FLIGHT = 8;

public:
    static constexpr size_t GUEST_FRAME_PAYLOAD_SIZE = 0x80000;
    static constexpr size_t COMPUTE_FRAME_PAYLOAD_SIZE = 0x20000;

    explicit UpdateDescriptorQueue(const Device& device_, size_t frame_payload_size_,
                                   bool supports_descriptor_buffer_ = false);
    ~UpdateDescriptorQueue();

    [[nodiscard]] bool UsesDescriptorBuffer() const noexcept {
        return use_descriptor_buffer;
    }

    void TickFrame();
    void Acquire(Scheduler& scheduler, size_t required_entries = 0,
                 bool use_descriptor_buffer_ = false);

    const DescriptorUpdateEntry* UpdateData() const noexcept {
        return upload_start;
    }

    void AddSampledImage(VkImageView image_view, VkSampler sampler) {
        GPU::Logging::NativeDiagnosticRecorder::GetInstance().Record(
            GPU::Logging::DiagnosticEventType::DescriptorImageWrite, diagnostic_frame_id,
            reinterpret_cast<u64>(image_view), reinterpret_cast<u64>(sampler), 1,
            static_cast<u64>(payload_cursor - payload_start));
        *(payload_cursor++) = VkDescriptorImageInfo{
            .sampler = sampler,
            .imageView = image_view,
            .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
        };
    }

    void AddImage(VkImageView image_view) {
        GPU::Logging::NativeDiagnosticRecorder::GetInstance().Record(
            GPU::Logging::DiagnosticEventType::DescriptorImageWrite, diagnostic_frame_id,
            reinterpret_cast<u64>(image_view), 0, 0,
            static_cast<u64>(payload_cursor - payload_start));
        *(payload_cursor++) = VkDescriptorImageInfo{
            .sampler = VK_NULL_HANDLE,
            .imageView = image_view,
            .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
        };
    }

    void AddBuffer(VkBuffer buffer, VkDeviceSize offset, VkDeviceSize size) {
        GPU::Logging::NativeDiagnosticRecorder::GetInstance().Record(
            GPU::Logging::DiagnosticEventType::DescriptorBufferWrite, diagnostic_frame_id,
            reinterpret_cast<u64>(buffer), offset, size,
            static_cast<u64>(payload_cursor - payload_start));
        *(payload_cursor++) = VkDescriptorBufferInfo{
            .buffer = buffer,
            .offset = offset,
            .range = size,
        };
    }

    void AddBuffer(VkBuffer buffer, VkDeviceAddress base_address, VkDeviceSize offset,
                   VkDeviceSize size) {
        if (!use_descriptor_buffer) {
            AddBuffer(buffer, offset, size);
            return;
        }
        GPU::Logging::NativeDiagnosticRecorder::GetInstance().Record(
            GPU::Logging::DiagnosticEventType::DescriptorAddressWrite, diagnostic_frame_id,
            base_address == 0 ? 0 : base_address + offset, size, 1,
            static_cast<u64>(payload_cursor - payload_start));
        *(payload_cursor++) = DescriptorAddress{
            .address = base_address == 0 ? 0 : base_address + offset,
            .range = base_address == 0 ? VK_WHOLE_SIZE : size,
            .format = VK_FORMAT_UNDEFINED,
        };
    }

    void AddTexelBuffer(VkBufferView texel_buffer) {
        GPU::Logging::NativeDiagnosticRecorder::GetInstance().Record(
            GPU::Logging::DiagnosticEventType::DescriptorTexelWrite, diagnostic_frame_id,
            reinterpret_cast<u64>(texel_buffer), 0, 0,
            static_cast<u64>(payload_cursor - payload_start));
        *(payload_cursor++) = texel_buffer;
    }

    void AddTexelBuffer(VkBufferView texel_buffer, VkDeviceAddress base_address,
                        VkDeviceSize offset, VkDeviceSize size, VkFormat format) {
        if (!use_descriptor_buffer) {
            AddTexelBuffer(texel_buffer);
            return;
        }
        GPU::Logging::NativeDiagnosticRecorder::GetInstance().Record(
            GPU::Logging::DiagnosticEventType::DescriptorAddressWrite, diagnostic_frame_id,
            base_address == 0 ? 0 : base_address + offset, size,
            static_cast<u64>(format),
            static_cast<u64>(payload_cursor - payload_start));
        *(payload_cursor++) = DescriptorAddress{
            .address = base_address == 0 ? 0 : base_address + offset,
            .range = base_address == 0 ? VK_WHOLE_SIZE : size,
            .format = format,
        };
    }

private:
    const Device& device;
    const size_t frame_payload_size;
    const bool supports_descriptor_buffer;
    bool use_descriptor_buffer{false};
    size_t frame_index{0};
    DescriptorUpdateEntry* payload_cursor = nullptr;
    u64 diagnostic_frame_id = 0;
    DescriptorUpdateEntry* payload_start = nullptr;
    const DescriptorUpdateEntry* upload_start = nullptr;
    std::vector<DescriptorUpdateEntry> payload;
};

// TODO: should these be separate classes instead?
using GuestDescriptorQueue = UpdateDescriptorQueue;
using ComputePassDescriptorQueue = UpdateDescriptorQueue;

} // namespace Vulkan
