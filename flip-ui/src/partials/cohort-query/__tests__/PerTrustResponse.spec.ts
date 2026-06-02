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

import PerTrustResponse from "@/partials/cohort-query/PerTrustResponse.vue";

// Pre-mount swrv data is read by the hoisted swrv mock when useSWRV is called.
const swrvMock = vi.hoisted(() => ({ value: null as unknown }));

vi.mock("swrv", async () => {
    const { ref } = await import("vue");

    return { default: () => ({ data: ref(swrvMock.value) }) };
});

const stubs = { AiCard: { template: "<div data-test='ai-card'><slot /></div>" } };

interface ITrust {
    id: string;
    name: string;
    code?: string | null;
}

const TRUSTS: ITrust[] = [
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

function mountPerTrustResponse(options: {
    data?: unknown;
    submitting?: boolean;
} = {}) {
    const { data = null, submitting = false } = options;
    swrvMock.value = data;

    return mount(PerTrustResponse, {
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
                            query: { id: "q-1" }
                        }
                    }
                }
            })],
            stubs
        }
    });
}

function rowFor(wrapper: ReturnType<typeof mount>, code: string) {
    const rows = wrapper.findAll("div.grid");

    return rows.find(r => r.text().includes(code));
}

describe("PerTrustResponse", () => {
    beforeEach(() => {
        swrvMock.value = null;
    });

    it("shows every trust as running before any results arrive", () => {
        const wrapper = mountPerTrustResponse();

        for (const trust of TRUSTS) {
            const row = rowFor(wrapper, trust.code as string);
            expect(row, `row for ${trust.code} missing`).toBeDefined();
            expect(row!.text()).toContain("running");
        }
    });

    it("shows 0 (not 'running') for a trust that responded with 0 records", async () => {
        // trust-1 had real results, trust-2 returned 0 (privacy-suppressed),
        // trust-3 has not responded yet.
        const data = {
            recordCount: 7,
            trustsResults: [
                {
                    name: "age",
                    results: [
                        {
                            trustId: "trust-1",
                            trustName: "Trust One",
                            data: [{
                                value: "<50",
                                count: 7
                            }]
                        }
                    ]
                }
            ],
            trustRecordCounts: {
                "trust-1": 7,
                "trust-2": 0
            }
        };
        const wrapper = mountPerTrustResponse({ data });
        await nextTick();
        await flushPromises();

        const t1 = rowFor(wrapper, "T1")!;
        const t2 = rowFor(wrapper, "T2")!;
        const t3 = rowFor(wrapper, "T3")!;

        expect(t1.text()).toContain("7");
        expect(t1.text()).not.toContain("running");

        expect(t2.text()).toContain("0");
        expect(t2.text()).not.toContain("running");

        expect(t3.text()).toContain("running");
    });

    it("shows a 'suppressed' chip (not a literal 0) for a privacy-suppressed trust", async () => {
        // trust-1 returned a real count; trust-2 matched some patients but the
        // count fell below the privacy threshold and was suppressed by the trust.
        // It must not read as a bare "0" / "no data available" (#519).
        const data = {
            recordCount: 7,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 7,
                "trust-2": 0
            },
            trustSuppressed: ["trust-2"]
        };
        const wrapper = mountPerTrustResponse({ data });
        await nextTick();
        await flushPromises();

        const t1 = rowFor(wrapper, "T1")!;
        const t2 = rowFor(wrapper, "T2")!;

        expect(t1.text()).toContain("7");

        expect(t2.text()).toContain("suppressed");
        expect(t2.text()).not.toContain("running");
    });

    it("falls back to per-field aggregation when trustRecordCounts is absent (back-compat)", async () => {
        // Pre-upgrade QueryStats rows lack trustRecordCounts; the component
        // must still mark trust-1 as responded based on the field results.
        const data = {
            recordCount: 7,
            trustsResults: [
                {
                    name: "age",
                    results: [
                        {
                            trustId: "trust-1",
                            trustName: "Trust One",
                            data: [{
                                value: "<50",
                                count: 7
                            }]
                        }
                    ]
                }
            ]
        };
        const wrapper = mountPerTrustResponse({ data });
        await nextTick();
        await flushPromises();

        const t1 = rowFor(wrapper, "T1")!;
        const t2 = rowFor(wrapper, "T2")!;

        expect(t1.text()).toContain("7");
        expect(t1.text()).not.toContain("running");
        expect(t2.text()).toContain("running");
    });

    it("shows every trust as queued while submitting, regardless of cached results", async () => {
        // Mid-submit the hub hasn't queued the TrustTasks yet — showing a
        // pink "running" pulse would lie. "queued" is the right state until
        // the POST returns and pendingTrustIds populates for real.
        const data = {
            recordCount: 7,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 7,
                "trust-2": 0,
                "trust-3": 3
            }
        };
        const wrapper = mountPerTrustResponse({
            data,
            submitting: true
        });
        await nextTick();
        await flushPromises();

        for (const trust of TRUSTS) {
            const row = rowFor(wrapper, trust.code as string)!;
            expect(row.text()).toContain("queued");
            expect(row.text()).not.toContain("running");
        }
    });

    it("shows an 'error' chip for a trust whose query failed", async () => {
        // trust-1 ok, trust-2 errored, trust-3 still pending.
        const data = {
            recordCount: 5,
            trustsResults: [
                {
                    name: "age",
                    results: [
                        {
                            trustId: "trust-1",
                            trustName: "Trust One",
                            data: [{
                                value: "<50",
                                count: 5
                            }]
                        }
                    ]
                }
            ],
            trustRecordCounts: { "trust-1": 5 },
            trustErrors: { "trust-2": "Database connection failed" }
        };
        const wrapper = mountPerTrustResponse({ data });
        await nextTick();
        await flushPromises();

        const t1 = rowFor(wrapper, "T1")!;
        const t2 = rowFor(wrapper, "T2")!;
        const t3 = rowFor(wrapper, "T3")!;

        expect(t1.text()).toContain("5");
        expect(t1.text()).not.toContain("error");

        expect(t2.text()).toContain("error");
        expect(t2.text()).not.toContain("running");
        // The error chip should be styled red, not the pink "running" colour.
        expect(t2.html()).toContain("bg-red-600");

        expect(t3.text()).toContain("running");
    });

    it("hides trusts that joined after the cohort query ran (not in queriedTrustIds)", async () => {
        // trust-1 + trust-2 participated in the original query; trust-3 joined
        // the platform afterwards. Once the query has finished (submitting=false)
        // trust-3 must not render as "running" — it never received the query.
        const data = {
            recordCount: 12,
            trustsResults: [],
            trustRecordCounts: {
                "trust-1": 7,
                "trust-2": 5
            }
        };
        swrvMock.value = data;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: false },
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
                                    queriedTrustIds: ["trust-1", "trust-2"]
                                }
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        expect(rowFor(wrapper, "T1")!.text()).toContain("7");
        expect(rowFor(wrapper, "T2")!.text()).toContain("5");
        expect(rowFor(wrapper, "T3")).toBeUndefined();
    });

    it("renders every trust as queued during the initial submission", async () => {
        // While the user is mid-submit, the fan-out targets the live trust
        // list — including trust-3 — so the in-flight UI shows them all as
        // "queued" (the hub is about to create the TrustTasks).
        swrvMock.value = null;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: true },
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
                                    queriedTrustIds: ["trust-1", "trust-2"]
                                }
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        for (const trust of TRUSTS) {
            const row = rowFor(wrapper, trust.code as string)!;
            expect(row.text()).toContain("queued");
            expect(row.text()).not.toContain("running");
        }
    });

    it("renders the 'skipped' chip for a trust whose task was cancelled", async () => {
        // Project was approved without trust-2; the hub cancelled trust-2's
        // PENDING task. The per-trust panel renders "skipped" — the operator
        // can see the trust was dispatched but the work was abandoned.
        const data = {
            recordCount: 5,
            trustsResults: [],
            trustRecordCounts: { "trust-1": 5 }
        };
        swrvMock.value = data;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: false },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        trust: { trusts: TRUSTS },
                        project: {
                            project: {
                                id: "p-1",
                                status: "APPROVED",
                                query: {
                                    id: "q-1",
                                    queriedTrustIds: ["trust-1", "trust-2"],
                                    pendingTrustIds: [],
                                    cancelledTrustIds: ["trust-2"],
                                    respondedTrustIds: ["trust-1"],
                                    erroredTrustIds: []
                                }
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        expect(rowFor(wrapper, "T1")!.text()).toContain("5");
        expect(rowFor(wrapper, "T2")!.text()).toContain("skipped");
        expect(rowFor(wrapper, "T2")!.text()).not.toContain("running");
        expect(rowFor(wrapper, "T2")!.text()).not.toContain("queued");
    });

    it("renders the 'queued' chip for a trust whose TrustTask is still PENDING", async () => {
        // Distinct from 'running': the task is queued at the hub but the
        // trust hasn't polled for it yet, so it's not even being processed.
        const data = {
            recordCount: 0,
            trustsResults: [],
            trustRecordCounts: {}
        };
        swrvMock.value = data;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: false },
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
                                    queriedTrustIds: ["trust-1", "trust-2"],
                                    pendingTrustIds: ["trust-2"],
                                    respondedTrustIds: [],
                                    erroredTrustIds: []
                                }
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        // trust-1: pending=false → running chip; trust-2: pending=true → queued chip
        expect(rowFor(wrapper, "T1")!.text()).toContain("running");
        expect(rowFor(wrapper, "T2")!.text()).toContain("queued");
        expect(rowFor(wrapper, "T2")!.text()).not.toContain("running");
    });

    it("shows a dispatched-but-never-responded trust as 'running'", async () => {
        // trust-3 was dispatched but never posted a result (offline, hub
        // rejected its error report, etc). Must stay visible with the
        // running pulse rather than vanish.
        const data = {
            recordCount: 7,
            trustsResults: [],
            trustRecordCounts: { "trust-1": 7 }
        };
        swrvMock.value = data;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: false },
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
                                    queriedTrustIds: ["trust-1", "trust-3"],
                                    erroredTrustIds: []
                                }
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        const t1 = rowFor(wrapper, "T1")!;
        const t3 = rowFor(wrapper, "T3")!;
        expect(t1.text()).toContain("7");
        expect(t3.text()).toContain("running");
    });

    it("classifies an errored trust via project.query.erroredTrustIds even when the QueryStats trustErrors map is empty", async () => {
        // Simulates the timing window where the trust posted an error and
        // the QueryResult.data captured it, but the QueryStats aggregator
        // hasn't re-run yet so trustErrors is empty.
        const data = {
            recordCount: 0,
            trustsResults: [],
            trustRecordCounts: {},
            trustErrors: {}  // empty — aggregator hasn't re-run
        };
        swrvMock.value = data;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: false },
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
                                    queriedTrustIds: ["trust-2"],
                                    erroredTrustIds: ["trust-2"]
                                }
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        const t2 = rowFor(wrapper, "T2")!;
        expect(t2.text()).toContain("error");
        expect(t2.text()).not.toContain("running");
    });

    it("shows the submit-a-query empty state when no query exists yet", async () => {
        // Project has no query — without an explicit branch the panel would
        // render every current trust as "running" (filter helper passes
        // undefined through to the whole list, no counts/errors land).
        swrvMock.value = null;
        const wrapper = mount(PerTrustResponse, {
            props: { submitting: false },
            global: {
                plugins: [createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        trust: { trusts: TRUSTS },
                        project: {
                            project: {
                                id: "p-1",
                                status: "UNSTAGED"
                            }
                        }
                    }
                })],
                stubs
            }
        });
        await nextTick();
        await flushPromises();

        for (const trust of TRUSTS) {
            expect(rowFor(wrapper, trust.code as string)).toBeUndefined();
        }
        expect(wrapper.text()).toContain("Submit a query to see per-trust responses");
    });

    it("prefers error over running when a trust both errored and is missing from counts", async () => {
        const data = {
            recordCount: 0,
            trustsResults: [],
            trustRecordCounts: {},
            trustErrors: { "trust-2": "Invalid SQL" }
        };
        const wrapper = mountPerTrustResponse({ data });
        await nextTick();
        await flushPromises();

        expect(rowFor(wrapper, "T2")!.text()).toContain("error");
        expect(rowFor(wrapper, "T2")!.text()).not.toContain("running");
    });
});
