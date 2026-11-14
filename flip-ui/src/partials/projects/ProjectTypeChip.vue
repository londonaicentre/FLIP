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
    <!-- Project type (FLIP#1071) as icons that add up the way the data does: every project has
         OMOP records (register book, always first); imaging projects add an x-ray after it. The
         name is kept for screen readers and as a hover tooltip. The icons take the caller's text
         colour, so the Type filter can mute them. Informational only. -->
    <span
        class="inline-flex items-center gap-1 shrink-0"
        :title="label"
        :data-test="`project-type-chip-${projectTypeId(hasImaging)}`"
    >
        <icon-healthicons-register-book-outline :class="SIZE_CLASS[size]" aria-hidden="true" />
        <icon-healthicons-xray-outline v-if="hasImaging" :class="SIZE_CLASS[size]" aria-hidden="true" />
        <span class="sr-only">{{ label }}</span>
    </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { IMAGING_LABEL, OMOP_ONLY_LABEL, projectTypeId } from "@/partials/projects/projectType";

interface IProjectTypeChipProps {
    // Resolved by the caller (see `projectHasImaging`), so the "absent means imaging" rule lives in one place.
    hasImaging: boolean;
    size?: "sm" | "md" | "lg" | "xl";
}

const props = withDefaults(defineProps<IProjectTypeChipProps>(), { size: "md" });

const label = computed(() => (props.hasImaging ? IMAGING_LABEL : OMOP_ONLY_LABEL));

const SIZE_CLASS = {
    sm: "w-[18px] h-[18px]",
    md: "w-5 h-5",
    lg: "w-6 h-6",
    xl: "w-8 h-8"
} as const;
</script>
