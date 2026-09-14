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

/**
 * Shared presentation for the trust chips on the Projects and Models tables.
 *
 * The two pages chip *different things* — Projects lists the trusts linked to a project and
 * what each has agreed to, Models lists the trusts a training run was dispatched to — so the
 * dot's meaning stays with each caller. Only the look is shared, which is why this module
 * exports class strings and a label rather than a component: what a chip means is the page's
 * business, what a chip looks like is the platform's.
 */

/** The fields a chip renders. Satisfied by both `IProjectTrust` and `IModelSummaryTrust`. */
export interface ITrustChipTrust {
    name: string;
    code?: string | null;
}

/** Base chip: fill, radius, type. Combine with one of the padding constants below. */
export const TRUST_CHIP_CLASS = "inline-flex items-center py-1 rounded text-xs font-medium "
    + "font-mono bg-gray-100 dark:bg-dark-surface text-gray-700 dark:text-gray-300";

/** Padding for a chip carrying a status dot — tighter on the dot side. */
export const TRUST_CHIP_DOTTED_PADDING = "gap-1.5 pl-2 pr-2.5";

/** Padding for a chip with no dot. */
export const TRUST_CHIP_PLAIN_PADDING = "px-2.5";

/** The dot itself. The caller adds its own `bg-*`, which is where the meaning lives. */
export const TRUST_CHIP_DOT_CLASS = "w-1.5 h-1.5 rounded-full shrink-0";

/**
 * Label a chip as compactly as the data allows.
 *
 * Prefers the trust's short code (e.g. "GSTT") when the backend supplies one. Falls back to the
 * full name with common bloat ("NHS Foundation Trust" etc.) stripped, truncated to 16 chars so
 * chips stay compact.
 *
 * Args:
 *     trust (ITrustChipTrust): The trust to label.
 *
 * Returns:
 *     str: The chip's visible text; empty when the trust has neither code nor name.
 */
export const trustChipLabel = (trust: ITrustChipTrust): string => {
    if (trust.code) return trust.code;
    if (!trust.name) return "";
    const stripped = trust.name
        .replace(/\bNHS Foundation Trust\b/gi, "")
        .replace(/\bNHS Trust\b/gi, "")
        .replace(/\bTrust\b/gi, "")
        .trim();
    if (stripped.length <= 16) return stripped;

    return stripped.slice(0, 14) + "…";
};
