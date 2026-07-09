// Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//     http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

import { mount } from "@vue/test-utils";
import { describe, expect, test, vi } from "vitest";

import RoundProgress from "@/partials/models/RoundProgress.vue";
import type { IModelProgress } from "@/services/model-service";

// Reactive holders the keyed swrv mock serves; tests overwrite per scenario.
const mockData = vi.hoisted(() => ({
    progress: null as unknown,
    keys: [] as string[]
}));

vi.mock("swrv", async () => {
    const { computed } = await import("vue");

    return {
        default: (key: string) => {
            mockData.keys.push(key);

            return {
                data: computed(() => (key.includes("/progress") ? mockData.progress : null)),
                isValidating: { value: false }
            };
        }
    };
});

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => ({
            params: {
                projectId: "p1",
                modelId: "m1"
            }
        })
    };
});

const runningProgress: IModelProgress = {
    currentRound: 7,
    totalRounds: 15,
    startedAt: "2026-07-09T13:00:00Z",
    avgRoundSeconds: 534,
    estRemainingSeconds: 4260,
    trusts: [
        {
            id: "t1",
            name: "Guy's & St Thomas'",
            code: "GSTT",
            lastRound: 7,
            state: "returned",
            avgRoundSeconds: 400,
            lastEventAt: "2026-07-09T14:32:08Z"
        },
        {
            id: "t2",
            name: "Royal Free London",
            code: "RFL",
            lastRound: 6,
            state: "training",
            avgRoundSeconds: 538,
            lastEventAt: "2026-07-09T14:30:12Z"
        },
        {
            id: "t3",
            name: "Manchester Foundation",
            code: "MFT",
            lastRound: 5,
            state: "dropped",
            avgRoundSeconds: 552,
            lastEventAt: "2026-07-09T13:47:00Z"
        }
    ]
};

const emptyProgress: IModelProgress = {
    currentRound: null,
    totalRounds: null,
    startedAt: null,
    avgRoundSeconds: null,
    estRemainingSeconds: null,
    trusts: []
};

function mountCard(status = "TRAINING_STARTED", progress: unknown = runningProgress) {
    mockData.progress = progress;
    mockData.keys = [];

    return mount(RoundProgress, {
        props: { status },
        global: { stubs: { AiCard: { template: "<div><slot /></div>" } } }
    });
}

describe("RoundProgress", () => {
    test("hidden while the model is still being prepared", () => {
        const comp = mountCard("PENDING");

        expect(comp.find("[data-test=round-progress-card]").exists()).toBe(false);
    });

    test("hidden when a finished model has no round telemetry (pre-events runs)", () => {
        const comp = mountCard("RESULTS_UPLOADED", emptyProgress);

        expect(comp.find("[data-test=round-progress-card]").exists()).toBe(false);
    });

    test("a just-dispatched run shows the roster with no round position yet", () => {
        const comp = mountCard("INITIATED", {
            ...emptyProgress,
            trusts: runningProgress.trusts
        });

        expect(comp.find("[data-test=round-progress-card]").exists()).toBe(true);
        expect(comp.find("[data-test=round-current]").text()).toBe("0");
        expect(comp.find("[data-test=round-total]").text()).toContain("—");
    });

    test("renders the round position and total", () => {
        const comp = mountCard();

        expect(comp.find("[data-test=round-current]").text()).toBe("7");
        expect(comp.find("[data-test=round-total]").text()).toContain("15");
    });

    test("renders one segment per round with done, current and upcoming states", () => {
        const comp = mountCard();

        const segments = comp.findAll("[data-test=round-segment]");
        expect(segments).toHaveLength(15);
        expect(segments.filter((s) => s.attributes("data-state") === "done")).toHaveLength(6);
        expect(segments[6].attributes("data-state")).toBe("current");
        expect(segments.filter((s) => s.attributes("data-state") === "upcoming")).toHaveLength(8);
    });

    test("summarises returned trusts and who the round is waiting on", () => {
        const comp = mountCard();

        const meta = comp.find("[data-test=round-meta-right]").text();
        // MFT is dropped, so 1 of 2 live trusts returned; RFL is still computing.
        expect(meta).toContain("1 of 2 returned");
        expect(meta).toContain("waiting on RFL");
    });

    test("shows avg round and remaining estimate while running", () => {
        const comp = mountCard();

        const meta = comp.find("[data-test=round-meta-left]").text();
        expect(meta).toContain("avg round 8m 54s");
        expect(meta).toContain("est. remaining ~1h 11m");
    });

    test("the per-trust ladder is collapsed by default and opens on toggle", async () => {
        const comp = mountCard();

        expect(comp.find("[data-test=ladder-panel]").exists()).toBe(false);

        await comp.find("[data-test=ladder-toggle]").trigger("click");

        expect(comp.find("[data-test=ladder-panel]").exists()).toBe(true);
        expect(comp.findAll("[data-test=ladder-row]")).toHaveLength(3);
    });

    test("ladder rows carry per-state pills", async () => {
        const comp = mountCard();
        await comp.find("[data-test=ladder-toggle]").trigger("click");

        const pills = comp.findAll("[data-test=ladder-pill]").map((p) => p.text());
        expect(pills[0]).toContain("returned · waiting");
        expect(pills[1]).toContain("gating round 7");
        expect(pills[2]).toContain("out since r5");
    });

    test("the live dot pulses only while the run is in progress", () => {
        const live = mountCard("TRAINING_STARTED");
        expect(live.find("[data-test=round-live-dot]").classes().join(" ")).toContain("animate-ping");

        const done = mountCard("RESULTS_UPLOADED");
        expect(done.find("[data-test=round-live-dot]").exists()).toBe(false);
    });

    test("a stopped run freezes at the last round reached without the remaining estimate", () => {
        const comp = mountCard("STOPPED");

        expect(comp.find("[data-test=round-current]").text()).toBe("7");
        expect(comp.find("[data-test=round-meta-left]").text()).not.toContain("est. remaining");
    });

    test("shows no metric values and never fetches metrics", () => {
        // Metric labels are chosen freely by the training code (TRAIN_LOSS, TEST_DICE,
        // "GAN loss (G)", …), so the card cannot know which are headline-worthy nor
        // what units they carry — a VAL_DICE_LOSS would render as a percentage. The
        // metrics card below owns that, with a tab and axis per series.
        mountCard();

        expect(mockData.keys).toEqual(["/model/m1/progress"]);
    });
});
