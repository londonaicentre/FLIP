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

import { describe, expect, it, vi } from "vitest";

import { notify } from "@/components/AiSnackbar/notify";
import { Snackbar } from "@/utils/snackbar";

vi.mock("@/components/AiSnackbar/notify", () => ({ notify: vi.fn() }));

const message = {
    title: "Title",
    text: "Text"
};

describe("Snackbar", () => {
    it.each([
        ["show", "info", 6_000],
        ["success", "success", 6_000],
        ["warning", "warning", 6_000],
        ["error", "error", 12_000]
    ] as const)("%s notifies top-right as %s for %i ms by default", (method, type, timeout) => {
        Snackbar[method](message);

        expect(notify).toHaveBeenCalledWith({
            group: "top-right",
            type,
            ...message
        }, timeout);
    });

    it("lets the caller override the type, group and timeout", () => {
        Snackbar.show({
            ...message,
            type: "error",
            group: "bottom"
        }, 8_000);

        expect(notify).toHaveBeenCalledWith({
            group: "bottom",
            type: "error",
            ...message
        }, 8_000);
    });
});
