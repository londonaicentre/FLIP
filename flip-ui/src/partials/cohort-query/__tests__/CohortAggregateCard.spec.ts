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

import { createTestingPinia } from "@pinia/testing";
import { flushPromises, mount } from "@vue/test-utils";
import { nextTick } from "vue";

import CohortAggregateCard from "@/partials/cohort-query/CohortAggregateCard.vue";

const swrvMock = vi.hoisted(() => ({ value: null as unknown }));

vi.mock("swrv", async () => {
    const { ref } = await import("vue");

    return { default: () => ({ data: ref(swrvMock.value) }) };
});

const stubs = { AiCard: { template: "<div data-test='ai-card'><slot /></div>" } };

const TRUSTS = [
    {
        id: "trust-1",
        name: "Trust One",
        code: "T1"
    },
    {
        id: "trust-2",
        name: "Trust Two",
        code: "T2"
    },
    {
        id: "trust-3",
        name: "Trust Three",
        code: "T3"
    }
];

function mountCard(options: { data?: unknown; submitting?: boolean; queriedTrustIds?: string[] } = {}) {
    const { data = null, submitting = false, queriedTrustIds } = options;
    swrvMock.value = data;

    return mount(CohortAggregateCard, {
        props: { submitting },
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    trust: { trusts: TRUSTS },
                    project: {
                        project: {
                            id: "p-1",
                            status: "UNSTAGED",
                            query: {
                                id: "q-1",
                                ...(queriedTrustIds ? { queriedTrustIds } : {})
                            }
                        }
                    }
                }
            })],
            stubs
        }
    });
}

describe("CohortAggregateCard", () => {
    beforeEach(() => {
        swrvMock.value = null;
    });

    it("frames itself as a flat border box, not a shadowed card", () => {
        const wrapper = mountCard();

        expect(wrapper.classes()).toContain("border");
        expect(wrapper.classes()).toContain("rounded-xl");
        expect(wrapper.find("[data-test='ai-card']").exists()).toBe(false);
    });

    it("shows total 0 and all trusts running before any results arrive", () => {
        const wrapper = mountCard();
        expect(wrapper.text()).toContain("0");
        expect(wrapper.text()).toContain("Aggregated cohort (live)");
        expect(wrapper.text()).toContain("0 trusts complete");
        expect(wrapper.text()).toContain("3 running");
    });

    it("sums per-trust counts into the headline total", async () => {
        const data = {
            recordCount: 10,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 7,
                "trust-2": 3
            }
        };
        const wrapper = mountCard({ data });
        await nextTick();
        await flushPromises();

        // 7 + 3 = 10
        expect(wrapper.text()).toContain("10");
        expect(wrapper.text()).toContain("2 trusts complete");
        expect(wrapper.text()).toContain("1 running");
    });

    it("includes an errored segment when any trust failed", async () => {
        const data = {
            recordCount: 5,
            trustsResults: [],
            trustRecordCounts: { "trust-1": 5 },
            trustErrors: { "trust-2": "boom" }
        };
        const wrapper = mountCard({ data });
        await nextTick();
        await flushPromises();

        expect(wrapper.text()).toContain("1 trusts complete");
        expect(wrapper.text()).toContain("1 running");
        expect(wrapper.text()).toContain("1 errored");
    });

    it("includes a suppressed segment when any trust privacy-suppressed its count", async () => {
        // trust-2's cohort fell below the privacy threshold and was suppressed (its
        // count may be 1-9 or zero); the subtitle calls it out so a suppressed trust
        // isn't silently dropped from the picture (#519).
        const data = {
            recordCount: 5,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 5,
                "trust-2": 0
            },
            trustSuppressed: ["trust-2"]
        };
        const wrapper = mountCard({ data });
        await nextTick();
        await flushPromises();

        expect(wrapper.text()).toContain("2 trusts complete");
        expect(wrapper.text()).toContain("1 running");
        expect(wrapper.text()).toContain("1 suppressed");
    });

    it("drops the '(live)' marker and patients-so-far suffix once all trusts have responded", async () => {
        const data = {
            recordCount: 12,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 5,
                "trust-2": 4,
                "trust-3": 3
            }
        };
        const wrapper = mountCard({ data });
        await nextTick();
        await flushPromises();

        expect(wrapper.text()).toContain("12");
        expect(wrapper.text()).toContain("Aggregated cohort");
        expect(wrapper.text()).not.toContain("(live)");
        expect(wrapper.text()).not.toContain("so far");
        expect(wrapper.text()).toContain("3 trusts complete");
        expect(wrapper.text()).not.toContain("running");
    });

    it("treats every trust as running while submitting, even if cached counts exist", async () => {
        const data = {
            recordCount: 12,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 5,
                "trust-2": 4,
                "trust-3": 3
            }
        };
        const wrapper = mountCard({
            data,
            submitting: true
        });
        await nextTick();
        await flushPromises();

        expect(wrapper.text()).toContain("0 trusts complete");
        expect(wrapper.text()).toContain("3 running");
    });

    it("restricts the subtitle counts to the queried trust set when queriedTrustIds is present", async () => {
        // trust-3 is in the store but was not queried for this project. With
        // queriedTrustIds = [trust-1, trust-2] it must not inflate the running count:
        // denominator is 2 (trust-1 complete, trust-2 suppressed → both complete), 0 running.
        const data = {
            recordCount: 5,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 5,
                "trust-2": 0
            },
            trustSuppressed: ["trust-2"]
        };
        const wrapper = mountCard({
            data,
            queriedTrustIds: ["trust-1", "trust-2"]
        });
        await nextTick();
        await flushPromises();

        expect(wrapper.text()).toContain("2 trusts complete");
        expect(wrapper.text()).toContain("1 suppressed");
        // trust-3 (un-queried) is excluded, so nothing is counted as running.
        expect(wrapper.text()).not.toContain("running");
    });

    it("falls back to the legacy trustsResults shape when trustRecordCounts is absent", async () => {
        // Older responses didn't carry trustRecordCounts — the card walks
        // trustsResults instead, picks the first field with results, and sums
        // each trust's data counts. Verifies the fallback branch (lines 85-92).
        const data = {
            trustsResults: [
                {
                    name: "empty-field",
                    results: []
                },
                {
                    name: "age",
                    results: [
                        {
                            trustId: "trust-1",
                            data: [{ count: 4 }, { count: 3 }]
                        },
                        {
                            trustId: "trust-2",
                            data: [{ count: 5 }]
                        }
                    ]
                }
            ]
        };
        const wrapper = mountCard({ data });
        await nextTick();
        await flushPromises();

        // trust-1: 4 + 3 = 7; trust-2: 5; total 12.
        expect(wrapper.text()).toContain("12");
        expect(wrapper.text()).toContain("2 trusts complete");
        expect(wrapper.text()).toContain("1 running");
    });
});
