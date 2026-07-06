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



import { mount } from "@vue/test-utils";
import { describe, expect, test, vi } from "vitest";

import Timeline from "../Timeline.vue";

// Two logs (Hub + a trust) so the log list renders, including the connector
// line between non-final entries.
const mockLogs = vi.hoisted(() => ({
    logs: [
        {
            id: "1",
            modelId: "m1",
            logDate: "2026-01-01T10:00:00Z",
            success: true,
            trustName: null,
            log: "Training started"
        },
        {
            id: "2",
            modelId: "m1",
            logDate: "2026-01-02T10:00:00Z",
            success: false,
            trustName: "Trust A",
            log: "Round failed"
        }
    ]
}));

vi.mock("swrv", async () => {
    const { ref } = await import("vue");

    return {
        default: () => ({
            data: ref(mockLogs.logs),
            isValidating: ref(false)
        })
    };
});

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({ params: { modelId: "m1" } })
    };
});

describe("Timeline", () => {
    test("renders a list entry per log with the Hub/trust label", () => {
        const comp = mount(Timeline, {
            props: { complete: true },
            global: { stubs: { AiLoader: true } }
        });

        expect(comp.findAll("li")).toHaveLength(mockLogs.logs.length);
        expect(comp.text()).toContain("Training started");
        expect(comp.text()).toContain("Hub");
        expect(comp.text()).toContain("Trust A");
    });
});
