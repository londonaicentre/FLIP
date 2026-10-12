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
    <!-- Project-type chip (FLIP#1071): every project gets one — imaging projects use OMOP data too,
         so the labels are "Imaging + OMOP" (primary tint, scan icon) and "OMOP only" (steel blue,
         database icon). Informational only. -->
    <span
        class="inline-flex items-center gap-1 rounded-full px-2 py-px text-[10.5px] font-semibold whitespace-nowrap shrink-0"
        :class="hasImaging ? IMAGING_CLASS : OMOP_ONLY_CLASS"
        :data-test="`project-type-chip-${projectTypeId(hasImaging)}`"
    >
        <icon-ph-scan v-if="hasImaging" class="w-3 h-3 shrink-0" aria-hidden="true" />
        <icon-ph-database v-else class="w-3 h-3 shrink-0" aria-hidden="true" />
        {{ hasImaging ? IMAGING_LABEL : OMOP_ONLY_LABEL }}
    </span>
</template>

<script setup lang="ts">
import { IMAGING_LABEL, OMOP_ONLY_LABEL, projectTypeId } from "@/partials/projects/projectType";

interface IProjectTypeChipProps {
    // Resolved by the caller (see `projectHasImaging`), so the "absent means imaging" rule lives in one place.
    hasImaging: boolean;
}

defineProps<IProjectTypeChipProps>();

// Design tokens from tailwind.config: the primary tint for imaging, the steel-blue accent for data-only.
const IMAGING_CLASS = "bg-primary-100 text-primary-500 dark:bg-primary-900/60 dark:text-primary-200";
const OMOP_ONLY_CLASS = "bg-steel-100 text-steel-700 dark:bg-steel-700/40 dark:text-steel-100";
</script>
