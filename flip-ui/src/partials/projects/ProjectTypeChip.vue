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
    <!-- Project type (FLIP#1071) as icons that add up the way the data does: every project has OMOP
         records (register book), imaging projects add an x-ray. The name is kept for screen readers
         and as a hover tooltip. Informational only. -->
    <span
        class="inline-flex items-center gap-1 shrink-0"
        :class="hasImaging ? IMAGING_CLASS : OMOP_ONLY_CLASS"
        :title="label"
        :data-test="`project-type-chip-${projectTypeId(hasImaging)}`"
    >
        <icon-healthicons-xray-outline v-if="hasImaging" :class="SIZE_CLASS[size]" aria-hidden="true" />
        <icon-healthicons-register-book-outline :class="SIZE_CLASS[size]" aria-hidden="true" />
        <span class="sr-only">{{ label }}</span>
    </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { IMAGING_LABEL, OMOP_ONLY_LABEL, projectTypeId } from "@/partials/projects/projectType";

interface IProjectTypeChipProps {
    // Resolved by the caller (see `projectHasImaging`), so the "absent means imaging" rule lives in one place.
    hasImaging: boolean;
    size?: "sm" | "md" | "lg";
}

const props = withDefaults(defineProps<IProjectTypeChipProps>(), { size: "md" });

const label = computed(() => (props.hasImaging ? IMAGING_LABEL : OMOP_ONLY_LABEL));

const SIZE_CLASS = {
    sm: "w-[18px] h-[18px]",
    md: "w-5 h-5",
    lg: "w-6 h-6"
} as const;

// Design tokens from tailwind.config: plum for imaging, the steel-blue accent for data-only.
const IMAGING_CLASS = "text-primary-500 dark:text-primary-300";
const OMOP_ONLY_CLASS = "text-steel-700 dark:text-steel-100";
</script>
