// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// SPDX-FileCopyrightText: Copyright 2019 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <variant>
#include <vector>
#include "video_core/renderer_vulkan/vk_pipeline_audit.h"
#include "video_core/vulkan_common/vulkan_wrapper.h"

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
                                   bool supports_descriptor_buffer_ = false,
                                   const char* audit_name_ = "descriptor_queue");
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
        *(payload_cursor++) = VkDescriptorImageInfo{
            .sampler = sampler,
            .imageView = image_view,
            .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
        };
        PipelineAudit::Log("DESCRIPTOR_WRITE queue={} kind=sampled_image view={:#x} sampler={:#x}",
                           audit_name, PipelineAudit::HandleValue(image_view),
                           PipelineAudit::HandleValue(sampler));
    }

    void AddImage(VkImageView image_view) {
        *(payload_cursor++) = VkDescriptorImageInfo{
            .sampler = VK_NULL_HANDLE,
            .imageView = image_view,
            .imageLayout = VK_IMAGE_LAYOUT_GENERAL,
        };
        PipelineAudit::Log("DESCRIPTOR_WRITE queue={} kind=image view={:#x}", audit_name,
                           PipelineAudit::HandleValue(image_view));
    }

    void AddBuffer(VkBuffer buffer, VkDeviceSize offset, VkDeviceSize size) {
        *(payload_cursor++) = VkDescriptorBufferInfo{
            .buffer = buffer,
            .offset = offset,
            .range = size,
        };
        PipelineAudit::Log(
            "DESCRIPTOR_WRITE queue={} kind=buffer handle={:#x} offset={} range={}", audit_name,
            PipelineAudit::HandleValue(buffer), offset, size);
    }

    void AddBuffer(VkBuffer buffer, VkDeviceAddress base_address, VkDeviceSize offset,
                   VkDeviceSize size) {
        if (!use_descriptor_buffer) {
            AddBuffer(buffer, offset, size);
            return;
        }
        *(payload_cursor++) = DescriptorAddress{
            .address = base_address == 0 ? 0 : base_address + offset,
            .range = base_address == 0 ? VK_WHOLE_SIZE : size,
            .format = VK_FORMAT_UNDEFINED,
        };
        PipelineAudit::Log(
            "DESCRIPTOR_WRITE queue={} kind=buffer_address address={:#x} base={:#x} offset={} range={}",
            audit_name, base_address == 0 ? 0 : base_address + offset, base_address, offset,
            base_address == 0 ? VK_WHOLE_SIZE : size);
    }

    void AddTexelBuffer(VkBufferView texel_buffer) {
        *(payload_cursor++) = texel_buffer;
        PipelineAudit::Log("DESCRIPTOR_WRITE queue={} kind=texel_buffer view={:#x}", audit_name,
                           PipelineAudit::HandleValue(texel_buffer));
    }

    void AddTexelBuffer(VkBufferView texel_buffer, VkDeviceAddress base_address,
                        VkDeviceSize offset, VkDeviceSize size, VkFormat format) {
        if (!use_descriptor_buffer) {
            AddTexelBuffer(texel_buffer);
            return;
        }
        *(payload_cursor++) = DescriptorAddress{
            .address = base_address == 0 ? 0 : base_address + offset,
            .range = base_address == 0 ? VK_WHOLE_SIZE : size,
            .format = format,
        };
        PipelineAudit::Log(
            "DESCRIPTOR_WRITE queue={} kind=texel_address address={:#x} base={:#x} offset={} range={} format={}",
            audit_name, base_address == 0 ? 0 : base_address + offset, base_address, offset,
            base_address == 0 ? VK_WHOLE_SIZE : size, static_cast<u32>(format));
    }

private:
    const Device& device;
    const size_t frame_payload_size;
    const bool supports_descriptor_buffer;
    const char* audit_name;
    bool use_descriptor_buffer{false};
    size_t frame_index{0};
    DescriptorUpdateEntry* payload_cursor = nullptr;
    DescriptorUpdateEntry* payload_start = nullptr;
    const DescriptorUpdateEntry* upload_start = nullptr;
    std::vector<DescriptorUpdateEntry> payload;
};

// TODO: should these be separate classes instead?
using GuestDescriptorQueue = UpdateDescriptorQueue;
using ComputePassDescriptorQueue = UpdateDescriptorQueue;

} // namespace Vulkan
