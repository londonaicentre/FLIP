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
        class="inline-flex items-center font-semibold rounded-full gap-1.5 whitespace-nowrap"
        :class="dense ? 'px-2 py-px text-[11px]' : 'px-2.5 py-0.5 text-[13px]'"
        :style="{ color: tone.fg, backgroundColor: tone.bg }"
        data-test="role-badge"
    >
        <span class="w-1.5 h-1.5 rounded-full shrink-0" :style="{ backgroundColor: tone.fg }" />
        {{ roleName }}
    </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { roleTone } from "@/utils/role-appearance";

/**
 * Role pill used in the admin user list and role selector. Colour-coded by role
 * name via the shared role-appearance tones.
 */
const props = withDefaults(
    defineProps<{
        roleName: string;
        /** Tighter padding/font for list rows and tables. */
        dense?: boolean;
    }>(),
    { dense: false }
);

const tone = computed(() => roleTone(props.roleName));
</script>
