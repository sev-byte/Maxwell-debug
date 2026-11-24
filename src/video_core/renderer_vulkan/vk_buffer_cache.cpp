// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

// SPDX-FileCopyrightText: Copyright 2019 yuzu Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <algorithm>
#include <array>
#include <cstring>
#include <span>
#include <vector>

#include "video_core/buffer_cache/buffer_cache_base.h"
#include "video_core/renderer_vulkan/vk_buffer_cache.h"
#include "video_core/renderer_vulkan/vk_pipeline_audit.h"

#include "video_core/renderer_vulkan/maxwell_to_vk.h"
#include "video_core/renderer_vulkan/vk_scheduler.h"
#include "video_core/renderer_vulkan/vk_staging_buffer_pool.h"
#include "video_core/renderer_vulkan/vk_update_descriptor.h"
#include "video_core/vulkan_common/vulkan_device.h"
#include "video_core/vulkan_common/vulkan_memory_allocator.h"
#include "video_core/vulkan_common/vulkan_wrapper.h"
#include "video_core/texture_cache/util.h"

namespace Vulkan {
namespace {
VkBufferCopy MakeBufferCopy(const VideoCommon::BufferCopy& copy) {
    return VkBufferCopy{
        .srcOffset = copy.src_offset,
        .dstOffset = copy.dst_offset,
        .size = copy.size,
    };
}

VkIndexType IndexTypeFromNumElements(const Device& device, u32 num_elements) {
    if (num_elements <= 0xff && device.IsExtIndexTypeUint8Supported()) {
        return VK_INDEX_TYPE_UINT8_EXT;
    }
    if (num_elements <= 0xffff) {
        return VK_INDEX_TYPE_UINT16;
    }
    return VK_INDEX_TYPE_UINT32;
}

size_t BytesPerIndex(VkIndexType index_type) {
    switch (index_type) {
    case VK_INDEX_TYPE_UINT8_EXT:
        return 1;
    case VK_INDEX_TYPE_UINT16:
        return 2;
    case VK_INDEX_TYPE_UINT32:
        return 4;
    default:
        ASSERT_MSG(false, "Invalid index type={}", index_type);
        return 1;
    }
}

vk::Buffer CreateBuffer(const Device& device, const MemoryAllocator& memory_allocator, u64 size,
                        VkDeviceSize sparse_alignment) {
    VkBufferUsageFlags flags =
        VK_BUFFER_USAGE_TRANSFER_SRC_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT |
        VK_BUFFER_USAGE_UNIFORM_TEXEL_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_TEXEL_BUFFER_BIT |
        VK_BUFFER_USAGE_UNIFORM_BUFFER_BIT | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT |
        VK_BUFFER_USAGE_INDEX_BUFFER_BIT | VK_BUFFER_USAGE_VERTEX_BUFFER_BIT |
        VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT;
    if (device.IsExtTransformFeedbackSupported()) {
        flags |= VK_BUFFER_USAGE_TRANSFORM_FEEDBACK_BUFFER_BIT_EXT;
    }
    if (device.IsExtConditionalRendering()) {
        flags |= VK_BUFFER_USAGE_CONDITIONAL_RENDERING_BIT_EXT;
    }
    if (device.IsBufferDeviceAddressSupported()) {
        flags |= VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    }
    const VkBufferCreateInfo buffer_ci = {
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .pNext = nullptr,
        .flags = 0,
        .size = size,
        .usage = flags,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .queueFamilyIndexCount = 0,
        .pQueueFamilyIndices = nullptr,
    };
    if (sparse_alignment > 1) {
        return memory_allocator.CreateBuffer(buffer_ci, MemoryUsage::DeviceLocal, sparse_alignment);
    }
    return memory_allocator.CreateBuffer(buffer_ci, MemoryUsage::DeviceLocal);
}
} // Anonymous namespace

Buffer::Buffer(BufferCacheRuntime& runtime, VideoCommon::NullBufferParams null_params)
    : VideoCommon::BufferBase(null_params), scheduler{&runtime.scheduler}, tracker{4096} {
    if (runtime.device.HasNullDescriptor()) {
        return;
    }
    device = &runtime.device;
    buffer = runtime.CreateNullBuffer();
    is_null = true;
    if (device->IsBufferDeviceAddressSupported()) {
        device_address = device->GetLogical().GetBufferDeviceAddress(*buffer);
    }
}

Buffer::Buffer(BufferCacheRuntime& runtime, DAddr cpu_addr_, u64 size_bytes_,
               bool sparse_compatible_)
    : VideoCommon::BufferBase(cpu_addr_, size_bytes_), device{&runtime.device},
      scheduler{&runtime.scheduler},
      buffer{CreateBuffer(*device, runtime.memory_allocator, SizeBytes(),
                          runtime.SparseAlignmentFor(sparse_compatible_))},
      tracker{SizeBytes()} {
    sparse_compatible = sparse_compatible_;
    if (runtime.device.HasDebuggingToolAttached()) {
        buffer.SetObjectNameEXT(fmt::format("Buffer {:#x}", CpuAddr()).c_str());
    }
    if (device->IsBufferDeviceAddressSupported()) {
        device_address = device->GetLogical().GetBufferDeviceAddress(*buffer);
    }
    PipelineAudit::Log(
        "BUFFER_CREATE buffer={:#x} backing_cpu_addr={:#x} size={} sparse_compatible={} device_address={:#x}",
        PipelineAudit::HandleValue(Handle()), CpuAddr(), SizeBytes(), sparse_compatible,
        device_address);
}

void Buffer::MarkUsage(u64 offset, u64 size) noexcept {
    const u64 tick = scheduler->CurrentTick();
    tracker.Track(offset, size);
    last_usage_tick = tick;
    PipelineAudit::Log(
        "BUFFER_USAGE buffer={:#x} backing_cpu_addr={:#x} size={} offset={} range={} tick={}",
        PipelineAudit::HandleValue(Handle()), CpuAddr(), SizeBytes(), offset, size, tick);
}

void Buffer::ResetUsageTracking() noexcept {
    const u64 tick = scheduler->CurrentTick();
    PipelineAudit::Log(
        "BUFFER_USAGE_RESET buffer={:#x} backing_cpu_addr={:#x} size={} previous_usage_tick={} reset_tick={}",
        PipelineAudit::HandleValue(Handle()), CpuAddr(), SizeBytes(), last_usage_tick, tick);
    tracker.Reset();
}

VkBufferView Buffer::View(u32 offset, u32 size, VideoCore::Surface::PixelFormat format) {
    if (!device) {
        // Null buffer supported, return a null descriptor
        return VK_NULL_HANDLE;
    } else if (is_null) {
        // Null buffer not supported, adjust offset and size
        offset = 0;
        size = 0;
    }
    const auto it{std::ranges::find_if(views, [offset, size, format](const BufferView& view) {
        return offset == view.offset && size == view.size && format == view.format;
    })};
    if (it != views.end()) {
        return *it->handle;
    }
    views.push_back({
        .offset = offset,
        .size = size,
        .format = format,
        .handle = device->GetLogical().CreateBufferView({
            .sType = VK_STRUCTURE_TYPE_BUFFER_VIEW_CREATE_INFO,
            .pNext = nullptr,
            .flags = 0,
            .buffer = *buffer,
            .format = MaxwellToVK::SurfaceFormat(*device, FormatType::Buffer, false, format).format,
            .offset = offset,
            .range = size,
        }),
    });
    PipelineAudit::Log(
        "BUFFER_VIEW_CREATE buffer={:#x} view={:#x} backing_cpu_addr={:#x} offset={} range={} format={}",
        PipelineAudit::HandleValue(Handle()), PipelineAudit::HandleValue(*views.back().handle),
        CpuAddr(), offset, size, static_cast<u32>(format));
    return *views.back().handle;
}

class QuadIndexBuffer {
public:
    QuadIndexBuffer(const Device& device_, MemoryAllocator& memory_allocator_,
                    Scheduler& scheduler_, StagingBufferPool& staging_pool_)
        : device{device_}, memory_allocator{memory_allocator_}, scheduler{scheduler_},
          staging_pool{staging_pool_} {}

    virtual ~QuadIndexBuffer() = default;

    void UpdateBuffer(u32 num_indices_) {
        if (num_indices_ <= num_indices) {
            return;
        }

        scheduler.Finish();

        num_indices = num_indices_;
        index_type = IndexTypeFromNumElements(device, num_indices);

        const u32 num_quads = GetQuadsNum(num_indices);
        const u32 num_triangle_indices = num_quads * 6;
        const u32 num_first_offset_copies = 4;
        const size_t bytes_per_index = BytesPerIndex(index_type);
        const size_t size_bytes = num_triangle_indices * bytes_per_index * num_first_offset_copies;
        const VkBufferCreateInfo buffer_ci = {
            .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
            .pNext = nullptr,
            .flags = 0,
            .size = size_bytes,
            .usage = VK_BUFFER_USAGE_INDEX_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT,
            .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
            .queueFamilyIndexCount = 0,
            .pQueueFamilyIndices = nullptr,
        };
        buffer = memory_allocator.CreateBuffer(buffer_ci, MemoryUsage::DeviceLocal);
        if (device.HasDebuggingToolAttached()) {
            buffer.SetObjectNameEXT("Quad LUT");
        }

        const bool host_visible = buffer.IsHostVisible();
        const StagingBufferRef staging = [&] {
            if (host_visible) {
                return StagingBufferRef{};
            }
            return staging_pool.Request(size_bytes, MemoryUsage::Upload);
        }();

        u8* staging_data = host_visible ? buffer.Mapped().data() : staging.mapped_span.data();
        const size_t quad_size = bytes_per_index * 6;

        for (u32 first = 0; first < num_first_offset_copies; ++first) {
            for (u32 quad = 0; quad < num_quads; ++quad) {
                MakeAndUpdateIndices(staging_data, quad_size, quad, first);
                staging_data += quad_size;
            }
        }

        if (!host_visible) {
            scheduler.RequestOutsideRenderPassOperationContext();
            scheduler.Record([src_buffer = staging.buffer, src_offset = staging.offset,
                              dst_buffer = *buffer, size_bytes](vk::CommandBuffer cmdbuf) {
                const VkBufferCopy copy{
                    .srcOffset = src_offset,
                    .dstOffset = 0,
                    .size = size_bytes,
                };
                const VkBufferMemoryBarrier write_barrier{
                    .sType = VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER,
                    .pNext = nullptr,
                    .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
                    .dstAccessMask = VK_ACCESS_INDEX_READ_BIT,
                    .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                    .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                    .buffer = dst_buffer,
                    .offset = 0,
                    .size = size_bytes,
                };
                cmdbuf.CopyBuffer(src_buffer, dst_buffer, copy);
                cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_TRANSFER_BIT,
                                       VK_PIPELINE_STAGE_VERTEX_INPUT_BIT, 0, write_barrier);
            });
        } else {
            buffer.Flush();
        }
    }

    void BindBuffer(u32 first) {
        const VkIndexType index_type_ = index_type;
        const size_t sub_first_offset = static_cast<size_t>(first % 4) * GetQuadsNum(num_indices);
        const size_t offset =
            (sub_first_offset + GetQuadsNum(first)) * 6ULL * BytesPerIndex(index_type);
        scheduler.Record([buffer_ = *buffer, index_type_, offset](vk::CommandBuffer cmdbuf) {
            cmdbuf.BindIndexBuffer(buffer_, offset, index_type_);
        });
    }

protected:
    virtual u32 GetQuadsNum(u32 num_indices) const = 0;

    virtual void MakeAndUpdateIndices(u8* staging_data, size_t quad_size, u32 quad, u32 first) = 0;

    const Device& device;
    MemoryAllocator& memory_allocator;
    Scheduler& scheduler;
    StagingBufferPool& staging_pool;

    vk::Buffer buffer{};
    MemoryCommit memory_commit{};
    VkIndexType index_type{};
    u32 num_indices = 0;
};

class QuadArrayIndexBuffer : public QuadIndexBuffer {
public:
    QuadArrayIndexBuffer(const Device& device_, MemoryAllocator& memory_allocator_,
                         Scheduler& scheduler_, StagingBufferPool& staging_pool_)
        : QuadIndexBuffer(device_, memory_allocator_, scheduler_, staging_pool_) {}

    ~QuadArrayIndexBuffer() = default;

private:
    u32 GetQuadsNum(u32 num_indices_) const override {
        return num_indices_ / 4;
    }

    template <typename T>
    static std::array<T, 6> MakeIndices(u32 quad, u32 first) {
        std::array<T, 6> indices{0, 1, 2, 0, 2, 3};
        for (T& index : indices) {
            index = static_cast<T>(first + index + quad * 4);
        }
        return indices;
    }

    void MakeAndUpdateIndices(u8* staging_data, size_t quad_size, u32 quad, u32 first) override {
        switch (index_type) {
        case VK_INDEX_TYPE_UINT8_EXT:
            std::memcpy(staging_data, MakeIndices<u8>(quad, first).data(), quad_size);
            break;
        case VK_INDEX_TYPE_UINT16:
            std::memcpy(staging_data, MakeIndices<u16>(quad, first).data(), quad_size);
            break;
        case VK_INDEX_TYPE_UINT32:
            std::memcpy(staging_data, MakeIndices<u32>(quad, first).data(), quad_size);
            break;
        default:
            ASSERT(false);
            break;
        }
    }
};

class QuadStripIndexBuffer : public QuadIndexBuffer {
public:
    QuadStripIndexBuffer(const Device& device_, MemoryAllocator& memory_allocator_,
                         Scheduler& scheduler_, StagingBufferPool& staging_pool_)
        : QuadIndexBuffer(device_, memory_allocator_, scheduler_, staging_pool_) {}

    ~QuadStripIndexBuffer() = default;

private:
    u32 GetQuadsNum(u32 num_indices_) const override {
        return num_indices_ >= 4 ? (num_indices_ - 2) / 2 : 0;
    }

    template <typename T>
    static std::array<T, 6> MakeIndices(u32 quad, u32 first) {
        std::array<T, 6> indices{0, 3, 1, 0, 2, 3};
        for (T& index : indices) {
            index = static_cast<T>(first + index + quad * 2);
        }
        return indices;
    }

    void MakeAndUpdateIndices(u8* staging_data, size_t quad_size, u32 quad, u32 first) override {
        switch (index_type) {
        case VK_INDEX_TYPE_UINT8_EXT:
            std::memcpy(staging_data, MakeIndices<u8>(quad, first).data(), quad_size);
            break;
        case VK_INDEX_TYPE_UINT16:
            std::memcpy(staging_data, MakeIndices<u16>(quad, first).data(), quad_size);
            break;
        case VK_INDEX_TYPE_UINT32:
            std::memcpy(staging_data, MakeIndices<u32>(quad, first).data(), quad_size);
            break;
        default:
            ASSERT(false);
            break;
        }
    }
};

BufferCacheRuntime::BufferCacheRuntime(const Device& device_, MemoryAllocator& memory_allocator_,
                                       Scheduler& scheduler_, StagingBufferPool& staging_pool_,
                                       GuestDescriptorQueue& guest_descriptor_queue_,
                                       ComputePassDescriptorQueue& compute_pass_descriptor_queue,
                                       DescriptorPool& descriptor_pool)
    : device{device_}, memory_allocator{memory_allocator_}, scheduler{scheduler_},
      staging_pool{staging_pool_}, guest_descriptor_queue{guest_descriptor_queue_},
      quad_index_pass(device, scheduler, descriptor_pool, staging_pool,
                      compute_pass_descriptor_queue),
      multi_range_buffers(device_) {
    const VkDriverIdKHR driver_id = device.GetDriverID();
    limit_dynamic_storage_buffers = driver_id == VK_DRIVER_ID_QUALCOMM_PROPRIETARY ||
                                    driver_id == VK_DRIVER_ID_ARM_PROPRIETARY;
    if (limit_dynamic_storage_buffers) {
        max_dynamic_storage_buffers = device.GetMaxDescriptorSetStorageBuffersDynamic();
    }    
    if (device.SupportsUint8Indices()) {
        uint8_pass = std::make_unique<Uint8Pass>(device, scheduler, descriptor_pool, staging_pool,
                                                 compute_pass_descriptor_queue);
    }
    quad_array_index_buffer = std::make_shared<QuadArrayIndexBuffer>(device_, memory_allocator_,
                                                                     scheduler_, staging_pool_);
    quad_strip_index_buffer = std::make_shared<QuadStripIndexBuffer>(device_, memory_allocator_,
                                                                     scheduler_, staging_pool_);
}

StagingBufferRef BufferCacheRuntime::UploadStagingBuffer(size_t size) {
    return staging_pool.Request(size, MemoryUsage::Upload);
}

StagingBufferRef BufferCacheRuntime::DownloadStagingBuffer(size_t size, bool deferred) {
    return staging_pool.Request(size, MemoryUsage::Download, deferred);
}

void BufferCacheRuntime::LogReadbackBatchBegin(const StagingBufferRef& staging,
                                               size_t total_size, size_t copy_count,
                                               u64 batch_id) {
    PipelineAudit::Log(
        "READBACK_BATCH_BEGIN batch_id={} staging_buffer={:#x} allocation_index={} staging_offset={} requested_bytes={} copy_count={} deferred={}",
        batch_id, PipelineAudit::HandleValue(staging.buffer), staging.index, staging.offset,
        total_size, copy_count, true);
}

void BufferCacheRuntime::LogReadbackBarrier(const StagingBufferRef& staging, const char* edge,
                                            size_t total_size, size_t copy_count) {
    if (std::strcmp(edge, "pre") == 0) {
        PipelineAudit::Log(
            "READBACK_BARRIER edge=pre staging_buffer={:#x} allocation_index={} bytes={} copy_count={} src_stage=graphics_compute_transfer src_access=memory_write dst_stage=transfer dst_access=transfer_read|transfer_write",
            PipelineAudit::HandleValue(staging.buffer), staging.index, total_size, copy_count);
    } else {
        PipelineAudit::Log(
            "READBACK_BARRIER edge=post staging_buffer={:#x} allocation_index={} bytes={} copy_count={} src_stage=transfer src_access=transfer_write dst_stage=graphics_compute dst_access=memory_read|memory_write",
            PipelineAudit::HandleValue(staging.buffer), staging.index, total_size, copy_count);
    }
}

void BufferCacheRuntime::LogReadbackCopy(const StagingBufferRef& staging,
                                         VkBuffer source_buffer, DAddr source_cpu_addr,
                                         u64 source_offset, u64 staging_offset, u64 size,
                                         u64 batch_id) {
    PipelineAudit::Log(
        "READBACK_COPY batch_id={} source_buffer={:#x} source_cpu_addr={:#x} staging_buffer={:#x} allocation_index={} source_offset={} staging_offset={} size={} expected_submit_tick={}",
        batch_id, PipelineAudit::HandleValue(source_buffer), source_cpu_addr,
        PipelineAudit::HandleValue(staging.buffer), staging.index, source_offset,
        staging_offset, size, scheduler.CurrentTick());
}

void BufferCacheRuntime::LogHostReadback(const StagingBufferRef& staging, DAddr guest_cpu_addr,
                                         u64 staging_offset, u64 size, u64 batch_id) {
    PipelineAudit::Log(
        "HOST_READBACK_WRITEBACK batch_id={} staging_buffer={:#x} allocation_index={} staging_offset={} guest_cpu_addr={:#x} size={} observed_completed_tick={} next_logical_tick={}",
        batch_id, PipelineAudit::HandleValue(staging.buffer), staging.index, staging_offset,
        guest_cpu_addr, size, scheduler.GetMasterSemaphore().KnownGpuTick(),
        scheduler.CurrentTick());
}

void BufferCacheRuntime::SplitTargetSegment(DAddr address) {
    auto next = target_ledger.upper_bound(address);
    if (next == target_ledger.begin()) {
        return;
    }
    auto current = std::prev(next);
    if (current->first >= address || current->second.end <= address) {
        return;
    }
    TargetLedgerSegment right = current->second;
    current->second.end = address;
    target_ledger.emplace(address, std::move(right));
}

u64 BufferCacheRuntime::EnsureTargetRange(DAddr start, DAddr end) {
    if (end <= start) {
        return 0;
    }
    SplitTargetSegment(start);
    SplitTargetSegment(end);
    u64 newly_tracked_bytes = 0;
    DAddr cursor = start;
    while (cursor < end) {
        auto it = target_ledger.lower_bound(cursor);
        if (it != target_ledger.end() && it->first == cursor) {
            cursor = it->second.end;
            continue;
        }
        const DAddr gap_end = it == target_ledger.end() ? end : std::min(end, it->first);
        TargetLedgerSegment segment{};
        segment.end = gap_end;
        target_ledger.emplace(cursor, std::move(segment));
        newly_tracked_bytes += gap_end - cursor;
        cursor = gap_end;
    }
    return newly_tracked_bytes;
}

void BufferCacheRuntime::RegisterTargetRange(DAddr address, u64 size,
                                             GPUVAddr guest_gpu_address, const Buffer& buffer,
                                             u32 buffer_offset, size_t descriptor_index,
                                             bool is_written) {
    if (size == 0 ||
        !GPU::Logging::GPULogger::GetInstance().IsDetailedAuditEnabled()) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const u64 newly_tracked_bytes = EnsureTargetRange(address, address + size);
    target_tracking_active.store(true, std::memory_order_relaxed);
    if (newly_tracked_bytes != 0) {
        PipelineAudit::Log(
            "TARGET_RANGE_DISCOVERED shader_hash={:#x} descriptor_index={} guest_gpu_addr={:#x} backing_cpu_addr={:#x} size={} newly_tracked_bytes={} buffer={:#x} buffer_base_cpu_addr={:#x} buffer_offset={} is_written={} source=live_storage_binding",
            0x4577AED68DD75ECCULL, descriptor_index, guest_gpu_address, address, size,
            newly_tracked_bytes, PipelineAudit::HandleValue(buffer.Handle()), buffer.CpuAddr(),
            buffer_offset, is_written);
    }
}

u64 BufferCacheRuntime::NextTargetReadbackBatchId() {
    std::scoped_lock lock{target_ledger_mutex};
    return next_target_readback_batch_id++;
}

u64 BufferCacheRuntime::CurrentTargetWriteSequence() const {
    if (!target_tracking_active.load(std::memory_order_relaxed)) {
        return 0;
    }
    std::scoped_lock lock{target_ledger_mutex};
    return target_write_sequence;
}

void BufferCacheRuntime::RecordTargetGpuWrite(DAddr address, u64 size, u64 frame_tick) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    auto it = target_ledger.upper_bound(address);
    if (it != target_ledger.begin()) {
        --it;
    }
    SplitTargetSegment(address);
    SplitTargetSegment(end);
    it = target_ledger.lower_bound(address);
    std::vector<std::pair<DAddr, DAddr>> overlaps;
    for (; it != target_ledger.end() && it->first < end; ++it) {
        const DAddr overlap_start = std::max(address, it->first);
        const DAddr overlap_end = std::min(end, it->second.end);
        if (overlap_start < overlap_end) {
            overlaps.emplace_back(overlap_start, overlap_end);
        }
    }
    if (overlaps.empty()) {
        return;
    }
    const u64 sequence = ++target_write_sequence;
    const u64 tick = scheduler.CurrentTick();
    for (const auto& [start, finish] : overlaps) {
        auto segment = target_ledger.upper_bound(start);
        if (segment != target_ledger.begin()) {
            --segment;
        }
        for (; segment != target_ledger.end() && segment->first < finish; ++segment) {
            if (segment->second.end <= start) {
                continue;
            }
            segment->second.latest_write_sequence = sequence;
            segment->second.latest_write_frame = frame_tick;
            segment->second.latest_write_tick = tick;
            segment->second.gpu_owned = true;
        }
        PipelineAudit::Log(
            "TARGET_GPU_WRITE sequence={} address={:#x} size={} frame={} scheduler_tick={} source=buffer_cache_mark_written",
            sequence, start, finish - start, frame_tick, tick);
    }
}

void BufferCacheRuntime::RecordTargetOwnershipClear(DAddr address, u64 size,
                                                    const char* reason, u64 batch_id,
                                                    u64 schedule_write_sequence,
                                                    u64 frame_tick) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    SplitTargetSegment(address);
    SplitTargetSegment(end);
    auto it = target_ledger.lower_bound(address);
    const u64 tick = scheduler.CurrentTick();
    for (; it != target_ledger.end() && it->first < end; ++it) {
        const DAddr start = std::max(address, it->first);
        const DAddr finish = std::min(end, it->second.end);
        if (start >= finish) {
            continue;
        }
        const u64 bytes = finish - start;
        const bool newer_write = batch_id != 0 &&
                                 it->second.latest_write_sequence > schedule_write_sequence;
        PipelineAudit::Log(
            "TARGET_OWNERSHIP_CLEAR reason={} address={:#x} size={} batch_id={} schedule_write_sequence={} latest_write_sequence={} newer_write={} newer_write_bytes={} was_gpu_owned={} latest_write_frame={} clear_frame={} scheduler_tick={}",
            reason, start, bytes, batch_id, schedule_write_sequence,
            it->second.latest_write_sequence, newer_write, newer_write ? bytes : 0,
            it->second.gpu_owned, it->second.latest_write_frame, frame_tick, tick);
        it->second.gpu_owned = false;
    }
}

void BufferCacheRuntime::RecordTargetOwnershipDecision(const char* operation, DAddr address,
                                                       u64 size, bool cache_gpu_owned,
                                                       bool tracker_gpu_owned, bool preflushable,
                                                       u64 frame_tick) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    auto it = target_ledger.upper_bound(address);
    if (it != target_ledger.begin()) {
        --it;
    }
    const u64 tick = scheduler.CurrentTick();
    for (; it != target_ledger.end() && it->first < end; ++it) {
        const DAddr start = std::max(address, it->first);
        const DAddr finish = std::min(end, it->second.end);
        if (start >= finish) {
            continue;
        }
        const u64 age_frames = frame_tick >= it->second.latest_write_frame
                                   ? frame_tick - it->second.latest_write_frame
                                   : 0;
        PipelineAudit::Log(
            "TARGET_OWNERSHIP_DECISION operation={} address={:#x} size={} cache_gpu_owned={} tracker_gpu_owned={} preflushable={} ledger_gpu_owned={} latest_write_sequence={} latest_write_frame={} data_age_frames={} frame={} scheduler_tick={}",
            operation, start, finish - start, cache_gpu_owned, tracker_gpu_owned,
            preflushable, it->second.gpu_owned, it->second.latest_write_sequence,
            it->second.latest_write_frame, age_frames, frame_tick, tick);
    }
}

void BufferCacheRuntime::RegisterTargetReadback(u64 batch_id, DAddr address, u64 size,
                                                u64 schedule_write_sequence,
                                                u64 schedule_frame, u64 schedule_tick,
                                                size_t total_size, size_t copy_count) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    SplitTargetSegment(address);
    SplitTargetSegment(end);
    auto it = target_ledger.lower_bound(address);
    bool touched_target = false;
    for (; it != target_ledger.end() && it->first < end; ++it) {
        const DAddr start = std::max(address, it->first);
        const DAddr finish = std::min(end, it->second.end);
        if (start >= finish) {
            continue;
        }
        touched_target = true;
        it->second.active_readback_batches.push_back(batch_id);
        PipelineAudit::Log(
            "TARGET_ASYNC_REGISTRATION_ADD batch_id={} address={:#x} size={} active_registration_count={} latest_add_batch_id={} schedule_write_sequence={} latest_write_sequence={} frame={} scheduler_tick={}",
            batch_id, start, finish - start, it->second.active_readback_batches.size(),
            it->second.active_readback_batches.back(), schedule_write_sequence,
            it->second.latest_write_sequence, schedule_frame, schedule_tick);
    }
    if (touched_target && target_readback_batches.insert(batch_id).second) {
        PipelineAudit::Log(
            "TARGET_ASYNC_BATCH_SCHEDULED batch_id={} requested_bytes={} copy_count={} schedule_frame={} schedule_tick={} schedule_write_sequence={}",
            batch_id, total_size, copy_count, schedule_frame, schedule_tick,
            schedule_write_sequence);
    }
}

void BufferCacheRuntime::CancelTargetReadbacks(DAddr address, u64 size, const char* caller,
                                               u64 frame_tick) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    SplitTargetSegment(address);
    SplitTargetSegment(end);
    auto it = target_ledger.lower_bound(address);
    const u64 tick = scheduler.CurrentTick();
    for (; it != target_ledger.end() && it->first < end; ++it) {
        if (it->second.active_readback_batches.empty()) {
            continue;
        }
        PipelineAudit::Log(
            "TARGET_ASYNC_CANCEL caller={} address={:#x} size={} cancelled_registration_count={} oldest_batch_id={} latest_batch_id={} frame={} scheduler_tick={}",
            caller, it->first, it->second.end - it->first,
            it->second.active_readback_batches.size(),
            it->second.active_readback_batches.front(),
            it->second.active_readback_batches.back(), frame_tick, tick);
        it->second.active_readback_batches.clear();
    }
}

void BufferCacheRuntime::InspectTargetReadbackPop(
    u64 batch_id, DAddr start, DAddr end, s32 overlap_count,
    u64 schedule_write_sequence, u64 schedule_frame, u64 schedule_tick, u64 pop_frame) {
    if (end <= start || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    auto it = target_ledger.upper_bound(start);
    if (it != target_ledger.begin()) {
        --it;
    }
    const u64 pop_tick = scheduler.CurrentTick();
    for (; it != target_ledger.end() && it->first < end; ++it) {
        const DAddr overlap_start = std::max(start, it->first);
        const DAddr overlap_end = std::min(end, it->second.end);
        if (overlap_start >= overlap_end) {
            continue;
        }
        const auto& registrations = it->second.active_readback_batches;
        const u64 latest_add = registrations.empty() ? 0 : registrations.back();
        const bool contains_popping_batch =
            std::find(registrations.begin(), registrations.end(), batch_id) != registrations.end();
        const bool batch_mismatch = !contains_popping_batch || latest_add != batch_id;
        const u64 newer_bytes =
            it->second.latest_write_sequence > schedule_write_sequence
                ? overlap_end - overlap_start
                : 0;
        const u64 latency_frames =
            pop_frame >= schedule_frame ? pop_frame - schedule_frame : 0;
        PipelineAudit::Log(
            "TARGET_ASYNC_POP_RANGE popping_batch_id={} address={:#x} size={} overlap_count={} active_registration_count={} oldest_registered_batch_id={} latest_add_batch_id={} contains_popping_batch={} batch_mismatch={} schedule_write_sequence={} latest_write_sequence={} newer_write_bytes={} schedule_frame={} pop_frame={} pop_latency_frames={} schedule_tick={} pop_tick={} pop_latency_ticks={}",
            batch_id, overlap_start, overlap_end - overlap_start, overlap_count,
            registrations.size(), registrations.empty() ? 0 : registrations.front(),
            latest_add, contains_popping_batch, batch_mismatch,
            schedule_write_sequence, it->second.latest_write_sequence, newer_bytes,
            schedule_frame, pop_frame, latency_frames, schedule_tick, pop_tick,
            pop_tick >= schedule_tick ? pop_tick - schedule_tick : 0);
    }
}

void BufferCacheRuntime::ConsumeTargetReadbackRegistration(u64 batch_id, DAddr address,
                                                            u64 size) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    SplitTargetSegment(address);
    SplitTargetSegment(end);
    auto it = target_ledger.lower_bound(address);
    for (; it != target_ledger.end() && it->first < end; ++it) {
        auto& registrations = it->second.active_readback_batches;
        if (registrations.empty()) {
            continue;
        }
        const u64 consumed_batch = registrations.front();
        registrations.erase(registrations.begin());
        PipelineAudit::Log(
            "TARGET_ASYNC_REGISTRATION_CONSUME popping_batch_id={} consumed_registration_batch_id={} address={:#x} size={} remaining_registration_count={} ownership_registration_match={}",
            batch_id, consumed_batch, it->first, it->second.end - it->first,
            registrations.size(), consumed_batch == batch_id);
    }
}

void BufferCacheRuntime::LogTargetReadbackBatchPop(u64 batch_id, u64 schedule_frame,
                                                   u64 schedule_tick, u64 pop_frame) {
    if (!target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    if (target_readback_batches.erase(batch_id) == 0) {
        return;
    }
    const u64 pop_tick = scheduler.CurrentTick();
    PipelineAudit::Log(
        "TARGET_ASYNC_BATCH_POP batch_id={} schedule_frame={} pop_frame={} pop_latency_frames={} schedule_tick={} pop_tick={} pop_latency_ticks={} fence_driven=true",
        batch_id, schedule_frame, pop_frame,
        pop_frame >= schedule_frame ? pop_frame - schedule_frame : 0,
        schedule_tick, pop_tick, pop_tick >= schedule_tick ? pop_tick - schedule_tick : 0);
}

void BufferCacheRuntime::RecordTargetSynchronousDownload(const char* phase, DAddr address,
                                                         u64 size, u64 readback_id,
                                                         u64 write_sequence,
                                                         u64 frame_tick) {
    if (size == 0 || !target_tracking_active.load(std::memory_order_relaxed)) {
        return;
    }
    std::scoped_lock lock{target_ledger_mutex};
    const DAddr end = address + size;
    auto it = target_ledger.upper_bound(address);
    if (it != target_ledger.begin()) {
        --it;
    }
    const u64 tick = scheduler.CurrentTick();
    for (; it != target_ledger.end() && it->first < end; ++it) {
        const DAddr start = std::max(address, it->first);
        const DAddr finish = std::min(end, it->second.end);
        if (start >= finish) {
            continue;
        }
        PipelineAudit::Log(
            "TARGET_SYNC_READBACK phase={} readback_id={} address={:#x} size={} scheduled_write_sequence={} latest_write_sequence={} ledger_gpu_owned={} frame={} scheduler_tick={} fence_driven=false",
            phase, readback_id, start, finish - start, write_sequence,
            it->second.latest_write_sequence, it->second.gpu_owned, frame_tick, tick);
    }
}

VkFormat BufferCacheRuntime::TexelBufferFormat(VideoCore::Surface::PixelFormat format) const {
    return MaxwellToVK::SurfaceFormat(device, FormatType::Buffer, false, format).format;
}

void BufferCacheRuntime::FreeDeferredStagingBuffer(StagingBufferRef& ref) {
    const u64 release_tick = scheduler.CurrentTick();
    staging_pool.FreeDeferred(ref);
    if (ref.usage == MemoryUsage::Download) {
        PipelineAudit::Log(
            "READBACK_STAGING_RELEASE staging_buffer={:#x} allocation_index={} assigned_tick={} observed_completed_tick={}",
            PipelineAudit::HandleValue(ref.buffer), ref.index, release_tick,
            scheduler.GetMasterSemaphore().KnownGpuTick());
    }
}

u64 BufferCacheRuntime::GetDeviceLocalMemory() const {
    return device.GetDeviceLocalMemory();
}

u64 BufferCacheRuntime::GetDeviceMemoryUsage() const {
    return device.GetDeviceMemoryUsage();
}

bool BufferCacheRuntime::CanReportMemoryUsage() const {
    return device.CanReportMemoryUsage();
}

u32 BufferCacheRuntime::GetUniformBufferAlignment() const {
    return static_cast<u32>(device.GetUniformBufferAlignment());
}

u32 BufferCacheRuntime::GetStorageBufferAlignment() const {
    return static_cast<u32>(device.GetStorageBufferAlignment());
}

void BufferCacheRuntime::TickFrame(Common::SlotVector<Buffer>& slot_buffers) noexcept {
    for (auto it = slot_buffers.begin(); it != slot_buffers.end(); it++) {
        if (scheduler.IsFree(it->LastUsageTick())) {
            it->ResetUsageTracking();
        }
    }
}

u64 BufferCacheRuntime::CurrentTick() {
    return scheduler.CurrentTick();
}

bool BufferCacheRuntime::IsFree(u64 tick) {
    return scheduler.IsFree(tick);
}

void BufferCacheRuntime::Wait(u64 tick) {
    scheduler.Wait(tick);
}

void BufferCacheRuntime::Finish() {
    scheduler.Finish();
}

bool BufferCacheRuntime::CanReorderUpload(const Buffer& buffer,
                                          std::span<const VideoCommon::BufferCopy> copies) {
    if (Settings::values.disable_buffer_reorder) {
        PipelineAudit::Log(
            "UPLOAD_REORDER_DECISION buffer={:#x} backing_cpu_addr={:#x} reason=setting_disabled result=serial tick={} last_usage_tick={}",
            PipelineAudit::HandleValue(buffer.Handle()), buffer.CpuAddr(), CurrentTick(),
            buffer.LastUsageTick());
        return false;
    }
    const bool can_use_upload_cmdbuf =
        std::ranges::all_of(copies, [&](const VideoCommon::BufferCopy& copy) {
            const bool region_used = buffer.IsRegionUsed(copy.dst_offset, copy.size);
            PipelineAudit::Log(
                "UPLOAD_REORDER_RANGE buffer={:#x} backing_cpu_addr={:#x} dst_offset={} size={} region_used={} tick={} last_usage_tick={}",
                PipelineAudit::HandleValue(buffer.Handle()), buffer.CpuAddr(), copy.dst_offset,
                copy.size, region_used, CurrentTick(), buffer.LastUsageTick());
            return !region_used;
        });
    PipelineAudit::Log(
        "UPLOAD_REORDER_DECISION buffer={:#x} backing_cpu_addr={:#x} copy_count={} decision={} expected_submit_tick={} last_usage_tick={}",
        PipelineAudit::HandleValue(buffer.Handle()), buffer.CpuAddr(), copies.size(),
        can_use_upload_cmdbuf ? "upload_command_buffer" : "main_command_buffer",
        CurrentTick(), buffer.LastUsageTick());
    return can_use_upload_cmdbuf;
}

void BufferCacheRuntime::CopyBuffer(VkBuffer dst_buffer, VkBuffer src_buffer,
                                    std::span<const VideoCommon::BufferCopy> copies, bool barrier,
                                    bool can_reorder_upload) {
    if (dst_buffer == VK_NULL_HANDLE || src_buffer == VK_NULL_HANDLE) {
        return;
    }
    static constexpr VkMemoryBarrier READ_BARRIER{
        .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .pNext = nullptr,
        .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT,
    };
    static constexpr VkMemoryBarrier WRITE_BARRIER{
        .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .pNext = nullptr,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
    };

    // Measuring a popular game, this number never exceeds the specified size once data is warmed up
    boost::container::small_vector<VkBufferCopy, 8> vk_copies(copies.size());
    std::ranges::transform(copies, vk_copies.begin(), MakeBufferCopy);
    const bool uses_upload_command_buffer =
        src_buffer == staging_pool.StreamBuf() && can_reorder_upload;
    u64 total_bytes = 0;
    for (size_t index = 0; index < copies.size(); ++index) {
        total_bytes += copies[index].size;
        PipelineAudit::Log(
            "BUFFER_TRANSFER copy_index={} src={:#x} dst={:#x} src_offset={} dst_offset={} size={} path={} barrier={} expected_submit_tick={}",
            index, PipelineAudit::HandleValue(src_buffer), PipelineAudit::HandleValue(dst_buffer),
            copies[index].src_offset, copies[index].dst_offset, copies[index].size,
            uses_upload_command_buffer ? "upload_command_buffer" : "main_command_buffer", barrier,
            scheduler.CurrentTick());
    }
    PipelineAudit::Log(
        "BUFFER_TRANSFER_BATCH src={:#x} dst={:#x} copy_count={} total_bytes={} path={} barrier={} can_reorder={} expected_submit_tick={}",
        PipelineAudit::HandleValue(src_buffer), PipelineAudit::HandleValue(dst_buffer),
        copies.size(), total_bytes,
        uses_upload_command_buffer ? "upload_command_buffer" : "main_command_buffer", barrier,
        can_reorder_upload, scheduler.CurrentTick());
    if (src_buffer == staging_pool.StreamBuf() && can_reorder_upload) {
        scheduler.RecordWithUploadBuffer([src_buffer, dst_buffer, vk_copies](
                                             vk::CommandBuffer, vk::CommandBuffer upload_cmdbuf) {
            upload_cmdbuf.CopyBuffer(src_buffer, dst_buffer, vk_copies);
        });
        return;
    }

    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([src_buffer, dst_buffer, vk_copies, barrier](vk::CommandBuffer cmdbuf) {
        if (barrier) {
            cmdbuf.PipelineBarrier(vk::PIPELINE_STAGE_GRAPHICS_COMPUTE_TRANSFER,
                                   VK_PIPELINE_STAGE_TRANSFER_BIT, 0, READ_BARRIER);
        }
        cmdbuf.CopyBuffer(src_buffer, dst_buffer, vk_copies);
        if (barrier) {
            cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_TRANSFER_BIT,
                                   vk::PIPELINE_STAGE_GRAPHICS_COMPUTE, 0, WRITE_BARRIER);
        }
    });
}

void BufferCacheRuntime::PreCopyBarrier() {
    static constexpr VkMemoryBarrier READ_BARRIER{
        .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .pNext = nullptr,
        .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT,
    };
    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([](vk::CommandBuffer cmdbuf) {
        cmdbuf.PipelineBarrier(vk::PIPELINE_STAGE_GRAPHICS_COMPUTE_TRANSFER, VK_PIPELINE_STAGE_TRANSFER_BIT,
                               0, READ_BARRIER);
    });
}

void BufferCacheRuntime::PostCopyBarrier() {
    static constexpr VkMemoryBarrier WRITE_BARRIER{
        .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .pNext = nullptr,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
    };
    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([](vk::CommandBuffer cmdbuf) {
        cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_TRANSFER_BIT, vk::PIPELINE_STAGE_GRAPHICS_COMPUTE,
                               0, WRITE_BARRIER);
    });
}

void BufferCacheRuntime::ClearBuffer(VkBuffer dest_buffer, u32 offset, size_t size, u32 value) {
    if (dest_buffer == VK_NULL_HANDLE) {
        return;
    }
    PipelineAudit::Log(
        "BUFFER_CLEAR dst={:#x} offset={} size={} value={:#x} expected_submit_tick={}",
        PipelineAudit::HandleValue(dest_buffer), offset, size, value, scheduler.CurrentTick());
    static constexpr VkMemoryBarrier READ_BARRIER{
        .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .pNext = nullptr,
        .srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT | VK_ACCESS_TRANSFER_WRITE_BIT,
    };
    static constexpr VkMemoryBarrier WRITE_BARRIER{
        .sType = VK_STRUCTURE_TYPE_MEMORY_BARRIER,
        .pNext = nullptr,
        .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .dstAccessMask = VK_ACCESS_MEMORY_READ_BIT | VK_ACCESS_MEMORY_WRITE_BIT,
    };

    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([dest_buffer, offset, size, value](vk::CommandBuffer cmdbuf) {
        cmdbuf.PipelineBarrier(vk::PIPELINE_STAGE_GRAPHICS_COMPUTE_TRANSFER, VK_PIPELINE_STAGE_TRANSFER_BIT,
                               0, READ_BARRIER);
        cmdbuf.FillBuffer(dest_buffer, offset, size, value);
        cmdbuf.PipelineBarrier(VK_PIPELINE_STAGE_TRANSFER_BIT, vk::PIPELINE_STAGE_GRAPHICS_COMPUTE,
                               0, WRITE_BARRIER);
    });
}

bool BufferCacheRuntime::BindMultiRangeStorageBuffer(u64 key, bool is_written,
                                                     GPUVAddr guest_gpu_addr,
                                                     u32 guest_size) {
    if (multi_range_sources.empty() || multi_range_total == 0) {
        return false;
    }
    const MultiRangeRef ref = multi_range_buffers.Get(device, scheduler, memory_allocator, key,
                                                     multi_range_sources, multi_range_total);
    if (ref.handle == VK_NULL_HANDLE) {
        return false;
    }
    if (is_written && !ref.sparse) {
        return false;
    }
    if (ref.needs_gather) {
        PreCopyBarrier();
        VkDeviceSize dst_offset = 0;
        for (const MultiRangeSource& source : multi_range_sources) {
            const std::array<VideoCommon::BufferCopy, 1> copy{VideoCommon::BufferCopy{
                .src_offset = u64(source.offset),
                .dst_offset = u64(dst_offset),
                .size = size_t(source.size),
            }};
            CopyBuffer(ref.handle, source.handle, copy, false);
            dst_offset += source.size;
        }
        PostCopyBarrier();
        multi_range_buffers.MarkGathered(key);
    }
    PipelineAudit::ScopedResource resource{
        "multi_range_storage_buffer", guest_gpu_addr, 0,
        PipelineAudit::HandleValue(ref.handle), 0, guest_size == 0 ? ref.size : guest_size};
    PipelineAudit::Log(
        "COMPUTE_MULTI_RANGE_DESCRIPTOR key={:#x} guest_gpu_addr={:#x} guest_range={} descriptor_buffer={:#x} descriptor_address={:#x} descriptor_range={} sparse={} needs_gather={}",
        key, guest_gpu_addr, guest_size, PipelineAudit::HandleValue(ref.handle), ref.address,
        ref.size, ref.sparse, ref.needs_gather);
    guest_descriptor_queue.AddBuffer(ref.handle, ref.address, 0, ref.size);
    return true;
}

void BufferCacheRuntime::BindIndexBuffer(PrimitiveTopology topology, IndexFormat index_format,
                                         u32 base_vertex, u32 num_indices, VkBuffer buffer,
                                         u32 offset, [[maybe_unused]] u32 size) {
    VkIndexType vk_index_type = MaxwellToVK::IndexFormat(index_format);
    VkDeviceSize vk_offset = offset;
    VkBuffer vk_buffer = buffer;
    if (topology == PrimitiveTopology::Quads || topology == PrimitiveTopology::QuadStrip) {
        vk_index_type = VK_INDEX_TYPE_UINT32;
        std::tie(vk_buffer, vk_offset) =
            quad_index_pass.Assemble(index_format, num_indices, base_vertex, buffer, offset,
                                     topology == PrimitiveTopology::QuadStrip);
    } else if (vk_index_type == VK_INDEX_TYPE_UINT8_EXT && !device.IsExtIndexTypeUint8Supported()) {
        vk_index_type = VK_INDEX_TYPE_UINT16;
        if (uint8_pass) {
            std::tie(vk_buffer, vk_offset) = uint8_pass->Assemble(num_indices, buffer, offset);
        } else if (device.GetDriverID() == VK_DRIVER_ID_QUALCOMM_PROPRIETARY) {
            ReserveNullBuffer();
            vk_buffer = *null_buffer;
            vk_offset = 0;
        }
    }
    if (vk_buffer == VK_NULL_HANDLE) {
        // Vulkan doesn't support null index buffers. Replace it with our own null buffer.
        ReserveNullBuffer();
        vk_buffer = *null_buffer;
    }
    scheduler.Record([vk_buffer, vk_offset, vk_index_type](vk::CommandBuffer cmdbuf) {
        cmdbuf.BindIndexBuffer(vk_buffer, vk_offset, vk_index_type);
    });
}

void BufferCacheRuntime::BindQuadIndexBuffer(PrimitiveTopology topology, u32 first, u32 count) {
    if (count == 0) {
        ReserveNullBuffer();
        scheduler.Record([this](vk::CommandBuffer cmdbuf) {
            cmdbuf.BindIndexBuffer(*null_buffer, 0, VK_INDEX_TYPE_UINT32);
        });
        return;
    }

    if (topology == PrimitiveTopology::Quads) {
        quad_array_index_buffer->UpdateBuffer(first + count);
        quad_array_index_buffer->BindBuffer(first);
    } else if (topology == PrimitiveTopology::QuadStrip) {
        quad_strip_index_buffer->UpdateBuffer(first + count);
        quad_strip_index_buffer->BindBuffer(first);
    }
}

void BufferCacheRuntime::BindVertexBuffer(u32 index, VkBuffer buffer, u32 offset, u32 size, u32 stride) {
    if (index >= device.GetMaxVertexInputBindings()) {
        return;
    }
    if (device.IsExtExtendedDynamicStateSupported()) {
        scheduler.Record([index, buffer, offset, size, stride](vk::CommandBuffer cmdbuf) {
            const VkDeviceSize vk_offset = buffer != VK_NULL_HANDLE ? offset : 0;
            const VkDeviceSize vk_size = buffer != VK_NULL_HANDLE ? size : VK_WHOLE_SIZE;
            const VkDeviceSize vk_stride = stride;
            cmdbuf.BindVertexBuffers2EXT(index, 1, &buffer, &vk_offset, &vk_size, &vk_stride);
        });
    } else {
        if (!device.HasNullDescriptor() && buffer == VK_NULL_HANDLE) {
            ReserveNullBuffer();
            buffer = *null_buffer;
            offset = 0;
        }
        scheduler.Record([index, buffer, offset](vk::CommandBuffer cmdbuf) {
            cmdbuf.BindVertexBuffer(index, buffer, offset);
        });
    }
}

void BufferCacheRuntime::BindVertexBuffers(VideoCommon::HostBindings<Buffer>& bindings) {
    boost::container::static_vector<VkBuffer, VideoCommon::NUM_VERTEX_BUFFERS> buffer_handles(bindings.buffers.size());
    for (u32 i = 0; i < bindings.buffers.size(); ++i) {
        auto handle = bindings.buffers[i]->Handle();
        if (handle == VK_NULL_HANDLE) {
            bindings.offsets[i] = 0;
            bindings.sizes[i] = VK_WHOLE_SIZE;
            if (!device.HasNullDescriptor()) {
                ReserveNullBuffer();
                handle = *null_buffer;
            }
        }
        buffer_handles[i] = handle;
    }
    const u32 device_max = device.GetMaxVertexInputBindings();
    const u32 min_binding = (std::min)(bindings.min_index, device_max);
    const u32 max_binding = (std::min)(bindings.max_index, device_max);
    const u32 binding_count = max_binding - min_binding;
    if (binding_count == 0) {
        return;
    }
    if (device.IsExtExtendedDynamicStateSupported()) {
        scheduler.Record([bindings_ = std::move(bindings), buffer_handles_ = std::move(buffer_handles), binding_count](vk::CommandBuffer cmdbuf) {
            cmdbuf.BindVertexBuffers2EXT(bindings_.min_index, binding_count, buffer_handles_.data(), bindings_.offsets.data(), bindings_.sizes.data(), bindings_.strides.data());
        });
    } else {
        scheduler.Record([bindings_ = std::move(bindings), buffer_handles_ = std::move(buffer_handles), binding_count](vk::CommandBuffer cmdbuf) {
            cmdbuf.BindVertexBuffers(bindings_.min_index, binding_count, buffer_handles_.data(), bindings_.offsets.data());
        });
    }
}

void BufferCacheRuntime::BindTransformFeedbackBuffer(u32 index, VkBuffer buffer, u32 offset,
                                                     u32 size) {
    if (!device.IsExtTransformFeedbackSupported()) {
        // Already logged in the rasterizer
        return;
    }
    if (buffer == VK_NULL_HANDLE) {
        // Vulkan doesn't support null transform feedback buffers.
        // Replace it with our own null buffer.
        ReserveNullBuffer();
        buffer = *null_buffer;
        offset = 0;
        size = 0;
    }
    scheduler.Record([index, buffer, offset, size](vk::CommandBuffer cmdbuf) {
        const VkDeviceSize vk_offset = offset;
        const VkDeviceSize vk_size = size;
        cmdbuf.BindTransformFeedbackBuffersEXT(index, 1, &buffer, &vk_offset, &vk_size);
    });
}

void BufferCacheRuntime::BindTransformFeedbackBuffers(VideoCommon::HostBindings<Buffer>& bindings) {
    if (!device.IsExtTransformFeedbackSupported()) {
        // Already logged in the rasterizer
        return;
    }
    boost::container::static_vector<VkBuffer, VideoCommon::NUM_VERTEX_BUFFERS> buffer_handles(bindings.buffers.size());
    for (u32 i = 0; i < bindings.buffers.size(); ++i) {
        auto handle = bindings.buffers[i]->Handle();
        if (handle == VK_NULL_HANDLE) {
            ReserveNullBuffer();
            handle = *null_buffer;
            bindings.offsets[i] = 0;
            bindings.sizes[i] = 0;
        }
        buffer_handles[i] = handle;
    }
    scheduler.Record([bindings_ = std::move(bindings), buffer_handles_ = std::move(buffer_handles)](vk::CommandBuffer cmdbuf) {
        cmdbuf.BindTransformFeedbackBuffersEXT(0, u32(buffer_handles_.size()), buffer_handles_.data(), bindings_.offsets.data(), bindings_.sizes.data());
    });
}

void BufferCacheRuntime::ReserveNullBuffer() {
    if (!null_buffer) {
        null_buffer = CreateNullBuffer();
    }
}

vk::Buffer BufferCacheRuntime::CreateNullBuffer() {
    VkBufferCreateInfo create_info{
        .sType = VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO,
        .pNext = nullptr,
        .flags = 0,
        .size = 4,
        .usage = VK_BUFFER_USAGE_VERTEX_BUFFER_BIT | VK_BUFFER_USAGE_INDEX_BUFFER_BIT |
                 VK_BUFFER_USAGE_TRANSFER_DST_BIT | VK_BUFFER_USAGE_INDIRECT_BUFFER_BIT,
        .sharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .queueFamilyIndexCount = 0,
        .pQueueFamilyIndices = nullptr,
    };
    if (device.IsExtTransformFeedbackSupported()) {
        create_info.usage |= VK_BUFFER_USAGE_TRANSFORM_FEEDBACK_BUFFER_BIT_EXT;
    }
    if (device.IsBufferDeviceAddressSupported()) {
        create_info.usage |= VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    }
    vk::Buffer ret = memory_allocator.CreateBuffer(create_info, MemoryUsage::DeviceLocal);
    if (device.HasDebuggingToolAttached()) {
        ret.SetObjectNameEXT("Null buffer");
    }

    scheduler.RequestOutsideRenderPassOperationContext();
    scheduler.Record([buffer = *ret](vk::CommandBuffer cmdbuf) {
        cmdbuf.FillBuffer(buffer, 0, VK_WHOLE_SIZE, 0);
    });

    return ret;
}

} // namespace Vulkan
