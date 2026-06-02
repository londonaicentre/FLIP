<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

<template>
    <span
        class="inline-flex items-center justify-center font-bold rounded-full shrink-0 font-heading"
        :style="avatarStyle"
        data-test="user-avatar"
        aria-hidden="true"
    >
        {{ initials }}
    </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { roleTone, userInitials } from "@/utils/role-appearance";

/**
 * Circular initials avatar, tinted by the user's role. Decorative only — the
 * user's name is always rendered as text alongside it, so it is aria-hidden.
 */
const props = withDefaults(
    defineProps<{
        name?: string;
        email?: string;
        roleName?: string;
        /** Diameter in pixels. */
        size?: number;
    }>(),
    {
        name: "",
        email: "",
        roleName: "",
        size: 36
    }
);

const initials = computed(() => userInitials(props.name, props.email));

const avatarStyle = computed(() => {
    const tone = roleTone(props.roleName);

    return {
        width: `${props.size}px`,
        height: `${props.size}px`,
        fontSize: `${Math.round(props.size * 0.33)}px`,
        letterSpacing: "0.02em",
        color: tone.fg,
        backgroundColor: tone.bg,
        boxShadow: `inset 0 0 0 2px ${tone.ring}`
    };
});
</script>
