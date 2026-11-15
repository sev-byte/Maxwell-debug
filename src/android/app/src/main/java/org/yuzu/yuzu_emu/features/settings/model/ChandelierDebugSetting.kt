// SPDX-FileCopyrightText: Copyright 2026 Eden Emulator Project
// SPDX-License-Identifier: GPL-3.0-or-later

package org.yuzu.yuzu_emu.features.settings.model

/**
 * Named Android switches backed by dedicated bits in the existing debug_knobs setting.
 * The candle-cap switches are mutually exclusive.
 */
object ChandelierDebugSetting {
    private class DebugKnob(
        override val key: String,
        private val bit: Int,
        private val mutuallyExclusiveBit: Int? = null
    ) : AbstractBooleanSetting {
        override val defaultValue: Boolean = false
        override val isRuntimeModifiable: Boolean = true
        override val isSwitchable: Boolean = true
        override val pairedSettingKey: String = ""
        override val isSaveable: Boolean
            get() = UShortSetting.DEBUG_KNOBS.isSaveable

        override var global: Boolean
            get() = UShortSetting.DEBUG_KNOBS.global
            set(value) {
                UShortSetting.DEBUG_KNOBS.global = value
            }

        override fun getBoolean(needsGlobal: Boolean): Boolean {
            val mask = 1 shl bit
            return (UShortSetting.DEBUG_KNOBS.getInt(needsGlobal) and mask) != 0
        }

        override fun setBoolean(value: Boolean) {
            val mask = 1 shl bit
            var flags = UShortSetting.DEBUG_KNOBS.getInt()
            flags = if (value) flags or mask else flags and mask.inv()
            if (value && mutuallyExclusiveBit != null) {
                flags = flags and (1 shl mutuallyExclusiveBit).inv()
            }
            UShortSetting.DEBUG_KNOBS.setInt(flags)
        }

        override fun getValueAsString(needsGlobal: Boolean): String =
            getBoolean(needsGlobal).toString()

        override fun reset() = setBoolean(defaultValue)
    }

    val DISABLE_ANIMATION_A: AbstractBooleanSetting =
        DebugKnob("chandelier_disable_animation_a", bit = 0)

    val DISABLE_ANIMATION_B: AbstractBooleanSetting =
        DebugKnob("chandelier_disable_animation_b", bit = 1)

    val CAP_TO_4: AbstractBooleanSetting =
        DebugKnob("chandelier_cap_to_4", bit = 2, mutuallyExclusiveBit = 3)

    val CAP_TO_5: AbstractBooleanSetting =
        DebugKnob("chandelier_cap_to_5", bit = 3, mutuallyExclusiveBit = 2)
}
