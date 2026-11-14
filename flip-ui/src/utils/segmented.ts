/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// The segmented filter look shared by the Projects and Models toolbars, kept in one place so they cannot drift.
// Each class name stays whole within one literal: Tailwind's extractor never sees a class split across a concatenation.
export const SEGMENTED_GROUP_CLASS = "inline-flex bg-white dark:bg-dark-canvas border border-gray-200 "
    + "dark:border-dark-border rounded-lg p-1";

export const segmentClass = (active: boolean): string[] => [
    "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-semibold transition-colors",
    active
        ? "bg-gray-100 dark:bg-dark-surface text-gray-900 dark:text-gray-100"
        : "bg-transparent text-gray-500 hover:text-gray-800 dark:text-gray-300 dark:hover:text-gray-200"
];
