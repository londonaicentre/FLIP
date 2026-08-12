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




import { format } from "date-fns";

import { ILog } from "@/services/model-service";

export const stringArrayEquals = (a: string[], b: string[]): boolean => {
    // Both inputs are Arrays
    return Array.isArray(a) && Array.isArray(b) &&
        // Both have the same length
        a.length === b.length &&
        // Everything in a is in b
        a.every((val) => b.includes(val));
};

/**
 * Everything in B is in A
 * @param a array of strings. Must contain everything in B.
 * @param b array of string. All must be contained in A.
 * @returns boolean
 */
export const stringArrayContainsAll = (a: string[], b: string[]): boolean => {
    // Both inputs are Arrays
    return Array.isArray(a) && Array.isArray(b) &&
        // Everything in b is in a
        b.every((val) => a.includes(val));
};

export const formatBytes = (bytes: number, decimals = 2): string => {
    if (bytes === 0) return "0 Bytes";

    const k = 1024;
    const dm = decimals < 0 ? 0 : decimals;
    const sizes = ["Bytes", "KB", "MB", "GB", "TB"];

    const i = Math.floor(Math.log2(bytes) / Math.log2(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
};

export const getShortDateFromString = (dateString: string): string => {
    return format(new Date(dateString), "dd/MM/yyyy, HH:mm:ss");
};

/**
 * Epoch milliseconds of an API timestamp, or null when missing/unparseable.
 *
 * The hub serialises naive-UTC datetimes with no offset suffix
 * ("2026-06-01T12:00:00"), which `new Date()` would otherwise read as
 * browser-local time — skewing displayed ages by the viewer's UTC offset.
 * Offset-less date-times are therefore pinned to UTC before parsing.
 */
export const apiTimestampMs = (timestamp?: string | null): number | null => {
    if (!timestamp) return null;
    const hasOffset = /(Z|[+-]\d{2}:?\d{2})$/.test(timestamp);
    const ms = new Date(timestamp.includes("T") && !hasOffset ? `${timestamp}Z` : timestamp).getTime();

    return Number.isFinite(ms) ? ms : null;
};

/**
 * "created 2h ago"-style meta label for a list row, from an API timestamp.
 *
 * Shared by the Projects and Models lists (owner · created … ago). Falls back
 * to an em-dash when the timestamp is missing or unparseable; a future
 * (clock-skewed) timestamp clamps to "created 0s ago".
 */
export const relativeCreatedLabel = (timestamp?: string | null): string => {
    const created = apiTimestampMs(timestamp);
    if (created === null) return "—";
    const sec = (Date.now() - created) / 1000;
    if (sec < 60) return `created ${Math.max(0, Math.floor(sec))}s ago`;
    if (sec < 3_600) return `created ${Math.floor(sec / 60)}m ago`;
    if (sec < 86_400) return `created ${Math.floor(sec / 3_600)}h ago`;

    return `created ${Math.floor(sec / 86_400)}d ago`;
};

export const getOrderedLogs = (logs: ILog[] | undefined, reverse = false): ILog[] => {
    if (!logs) {
        return [];
    }

    const orderedLogs = logs.map(t => t).sort((a, b) => {
        const dateA = new Date(a.logDate);
        const dateB = new Date(b.logDate);

        return +dateA - +dateB;
    });

    if (reverse) {
        return orderedLogs.reverse();
    }

    return orderedLogs;
};

export const capatilizeString = (value: string): string => {
    return value.replace(/\w\S*/g,
        (w) => (
            w.replace(/^\w/,
                (c) => c.toUpperCase())
        )
    );
};

/**
 * Get a cryptographically random identifier (UUID v4)
 */
export const getRandomId = (): string => crypto.randomUUID();


export const getInitials = (name: string): string => {
    const rgx = /(\p{L}{1})\p{L}+/gu;

    const initials = [...name.matchAll(rgx)];

    const firstInitial = initials.shift()?.[1] ?? "";
    const lastInitial = initials.pop()?.[1] ?? "";
    const response = firstInitial + lastInitial;

    return response.toUpperCase();
};
