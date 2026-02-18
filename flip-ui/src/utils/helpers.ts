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
 * Get a random number
 */
export const getRandomId = (): string => {
    return Math.random().toString(36).substring(2, 15) + Math.random().toString(36).substring(2, 15);
};


export const getInitials = (name: string): string => {
    const rgx = new RegExp(/(\p{L}{1})\p{L}+/, "gu");

    const initials = [...name.matchAll(rgx)] || [];

    const firstInitial = initials.shift()?.[1] ?? "";
    const lastInitial = initials.pop()?.[1] ?? "";
    const response = firstInitial + lastInitial;

    return response.toUpperCase();
};
