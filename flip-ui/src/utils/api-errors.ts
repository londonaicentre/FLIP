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

import { isAxiosError } from "axios";

/**
 * Extract the human-readable error message from a thrown value, preferring
 * the FastAPI-style ``{detail: "..."}`` body that flip-api returns on 4xx.
 * Falls back to ``fallback`` when the value is not an axios error or carries
 * no usable detail, so callers always have something to show the user.
 *
 * A request-validation failure is a **list** of per-field objects
 * (``detail: [{loc, msg, type}]``), not a string, so a pydantic error used to
 * fall through to the generic fallback and the user never saw which field was
 * wrong. Flatten those into one sentence.
 */
export const extractErrorDetail = (error: unknown, fallback: string): string => {
    if (!isAxiosError(error)) {
        return fallback;
    }
    const detail = (error.response?.data as { detail?: unknown } | undefined)?.detail;

    if (typeof detail === "string" && detail.length > 0) {
        return detail;
    }
    if (Array.isArray(detail)) {
        const messages = detail
            .map(item => (typeof item === "object" && item !== null ? (item as { msg?: unknown }).msg : undefined))
            .filter((msg): msg is string => typeof msg === "string" && msg.length > 0);

        if (messages.length > 0) {
            return [...new Set(messages)].join(". ");
        }
    }

    return fallback;
};
