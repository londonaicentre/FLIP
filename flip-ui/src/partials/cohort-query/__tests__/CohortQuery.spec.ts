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
import { mount } from "@vue/test-utils";
import { reactive } from "vue";

import CohortQueryPageWrapper from "@/pages/project/[projectId]/cohort-query/index.vue";
import { IProject } from "@/services/project-service";

import CohortQuery from "../CohortQuery.vue";
import { CohortQueryPage } from "./selectors";

const mockRoute = reactive({
    name: "CohortQuery",
    fullPath: "/project/test-project-id/cohort",
    path: "/project/test-project-id/cohort",
    params: { projectId: "test-project-id" } as Record<string, string>
});

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

const mockRouterPush = vi.fn();

vi.mock("@/router", () => ({ default: { push: (...args: unknown[]) => mockRouterPush(...args) } }));

const mockSendQuery = vi.fn();

vi.mock("@/services/cohort-query-service", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/services/cohort-query-service")>();

    return {
        ...actual,
        sendQuery: (...args: unknown[]) => mockSendQuery(...args)
    };
});

const mockSnackbarShow = vi.fn();
const mockSnackbarError = vi.fn();

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        show: (...args: unknown[]) => mockSnackbarShow(...args),
        error: (...args: unknown[]) => mockSnackbarError(...args)
    }
}));

const stubs = {
    AiLoader: { template: "<div data-test='loader' />" },
    AiCodeTextArea: {
        name: "AiCodeTextArea",
        template: "<div data-test='cohort-query'><slot /></div>",
        props: ["initialValue", "inputProps", "name", "label"]
    },
    QueryResultCharts: { template: "<div data-test='query-result-charts' />" },
    Form: { template: "<form @submit.prevent=\"$emit('submit', { query: 'SELECT * FROM patients' })\"><slot /></form>" },
    "icon-ph-clock": { template: "<span />" },
    Transition: { template: "<div><slot /></div>" }
};

const unstagedProject: IProject = {
    id: "test-project-id",
    name: "Test Project",
    description: "A test project",
    ownerId: "owner-1",
    ownerEmail: "owner@example.com",
    creationtimestamp: "2026-01-01",
    status: "UNSTAGED",
    users: [],
    query: undefined
};

const stagedProjectWithQuery: IProject = {
    ...unstagedProject,
    status: "STAGED",
    query: {
        id: "query-1",
        name: "Test Query",
        query: "SELECT * FROM patients",
        queriedTrustIds: ["trust-1", "trust-2"],
        pendingTrustIds: [],
        cancelledTrustIds: [],
        respondedTrustIds: [],
        erroredTrustIds: [],
        emptyTrustIds: [],
        totalCohort: 100
    }
};

const unstagedProjectWithQuery: IProject = {
    ...unstagedProject,
    query: {
        id: "query-1",
        name: "Test Query",
        query: "SELECT * FROM patients",
        queriedTrustIds: ["trust-1", "trust-2"],
        pendingTrustIds: [],
        cancelledTrustIds: [],
        respondedTrustIds: [],
        erroredTrustIds: [],
        emptyTrustIds: [],
        totalCohort: 100
    }
};

function mountCohortQuery(options: {
    project?: IProject;
    permissions?: string[];
} = {}) {
    const { project, permissions = ["CanCreateProjects"] } = options;

    return mount(CohortQuery, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    auth: {
                        user: {
                            username: "testuser",
                            userId: "1",
                            attributes: {
                                sub: "1",
                                email: "test@example.com"
                            },
                            permissions
                        },
                        signInStep: "DONE"
                    },
                    project: { project }
                }
            })],
            stubs
        }
    });
}

// Page-wrapper mount — used for tests that exercise markup the page owns
// (description copy, "Run on all trusts" button) rather than the partial.
function mountCohortQueryPage(options: {
    project?: IProject;
    permissions?: string[];
} = {}) {
    const { project, permissions = ["CanCreateProjects"] } = options;
    const pageStubs = {
        ...stubs,
        CohortQuery: {
            name: "CohortQuery",
            emits: ["update-project", "submitting-change"],
            template: "<div data-test='cohort-query-partial-stub' />"
        },
        "router-link": { template: "<a><slot /></a>" }
    };

    return mount(CohortQueryPageWrapper, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    auth: {
                        user: {
                            username: "testuser",
                            userId: "1",
                            attributes: {
                                sub: "1",
                                email: "test@example.com"
                            },
                            permissions
                        },
                        signInStep: "DONE"
                    },
                    project: { project }
                }
            })],
            stubs: pageStubs
        }
    });
}

describe("CohortQuery", () => {
    beforeEach(() => {
        mockSendQuery.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarError.mockReset();
        mockRouterPush.mockReset();
    });

    describe("rendering", () => {
        it("mounts without errors", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject });
            expect(wrapper.exists()).toBe(true);
        });

        it("shows loader when project is null", () => {
            const wrapper = mountCohortQuery({ project: undefined });

            expect(wrapper.find("[data-test='loader']").exists()).toBe(true);
            expect(wrapper.find("form").exists()).toBe(false);
        });

        it("shows the form when project is loaded", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject });

            expect(wrapper.find("form").exists()).toBe(true);
            expect(wrapper.find("[data-test='loader']").exists()).toBe(false);
        });
    });

    // The header copy + run button live on the page wrapper, not the partial.
    describe("page wrapper header copy + run button", () => {
        it("shows editable query message when project is UNSTAGED", () => {
            const wrapper = mountCohortQueryPage({ project: unstagedProject });

            expect(wrapper.text()).toContain("Edit the OMOP query");
        });

        it("shows locked query message when project is STAGED", () => {
            const wrapper = mountCohortQueryPage({ project: stagedProjectWithQuery });

            expect(wrapper.text()).toContain("locked and can not be edited");
        });

        it("disables the Run button while CohortQuery reports a submission in flight", async () => {
            const wrapper = mountCohortQueryPage({ project: unstagedProject });
            const runButton = wrapper.find("[data-test='view-cohort-query-results-btn']");
            // Idle: the button is enabled.
            expect(runButton.attributes("disabled")).toBeUndefined();

            const partial = wrapper.findComponent({ name: "CohortQuery" });
            await partial.vm.$emit("submitting-change", true);
            expect(runButton.attributes("disabled")).toBeDefined();

            await partial.vm.$emit("submitting-change", false);
            expect(runButton.attributes("disabled")).toBeUndefined();
        });

        it("re-emits UpdateProject when CohortQuery emits update-project", async () => {
            const wrapper = mountCohortQueryPage({ project: unstagedProject });

            const partial = wrapper.findComponent({ name: "CohortQuery" });
            await partial.vm.$emit("update-project");

            expect(wrapper.emitted("UpdateProject")).toBeTruthy();
        });
    });

    describe("query editor & button visibility", () => {
        it("renders AiCodeTextArea with the project query value", () => {
            const wrapper = mountCohortQuery({ project: unstagedProjectWithQuery });
            const codeTextArea = wrapper.findComponent({ name: "AiCodeTextArea" });

            expect(codeTextArea.props("initialValue")).toBe("SELECT * FROM patients");
        });

        it("sets readonly on AiCodeTextArea when query is locked (STAGED)", () => {
            const wrapper = mountCohortQuery({ project: stagedProjectWithQuery });
            const codeTextArea = wrapper.findComponent({ name: "AiCodeTextArea" });

            expect(codeTextArea.props("inputProps")).toEqual({ readonly: true });
        });

        it("sets readonly on AiCodeTextArea when user is viewer", () => {
            const wrapper = mountCohortQuery({
                project: unstagedProject,
                permissions: []
            });
            const codeTextArea = wrapper.findComponent({ name: "AiCodeTextArea" });

            expect(codeTextArea.props("inputProps")).toEqual({ readonly: true });
        });

        it("does not set readonly when project is UNSTAGED and user has permissions", () => {
            const wrapper = mountCohortQuery({
                project: unstagedProject,
                permissions: ["CanCreateProjects"]
            });
            const codeTextArea = wrapper.findComponent({ name: "AiCodeTextArea" });

            expect(codeTextArea.props("inputProps")).toEqual({ readonly: false });
        });

        // Run button lives on the page wrapper, not the partial.
        it("shows the run button when project is UNSTAGED and user is not viewer", () => {
            const wrapper = mountCohortQueryPage({ project: unstagedProject });

            expect(wrapper.find(CohortQueryPage.runCohortQueryButton).exists()).toBe(true);
        });

        it("hides the run button when project is STAGED (locked)", () => {
            const wrapper = mountCohortQueryPage({ project: stagedProjectWithQuery });

            expect(wrapper.find(CohortQueryPage.runCohortQueryButton).exists()).toBe(false);
        });

        it("hides the run button when user is viewer", () => {
            const wrapper = mountCohortQueryPage({
                project: unstagedProject,
                permissions: []
            });

            expect(wrapper.find(CohortQueryPage.runCohortQueryButton).exists()).toBe(false);
        });
    });

    describe("query results display", () => {
        it("shows QueryResultCharts when project has a query", () => {
            const wrapper = mountCohortQuery({ project: unstagedProjectWithQuery });

            expect(wrapper.find("[data-test='query-result-charts']").exists()).toBe(true);
        });

        it("does not show QueryResultCharts when project has no query", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject });

            expect(wrapper.find("[data-test='query-result-charts']").exists()).toBe(false);
        });
    });

    describe("runCohortQuery", () => {
        it("calls sendQuery with correct parameters on form submit", async () => {
            mockSendQuery.mockResolvedValue({
                queryId: "new-query-id",
                trust: [{
                    statusCode: 200,
                    name: "Trust A",
                    message: "OK"
                }]
            });

            const wrapper = mountCohortQuery({ project: unstagedProject });
            const form = wrapper.find("form");
            await form.trigger("submit");

            await vi.waitFor(() => expect(mockSendQuery).toHaveBeenCalled());

            expect(mockSendQuery).toHaveBeenCalledWith("/step/cohort", {
                query: "SELECT * FROM patients",
                name: "Test Project: Cohort Query",
                projectId: "test-project-id"
            });
        });

        it("shows success snackbar on successful query", async () => {
            mockSendQuery.mockResolvedValue({
                queryId: "new-query-id",
                trust: [{
                    statusCode: 200,
                    name: "Trust A",
                    message: "OK"
                }]
            });

            const wrapper = mountCohortQuery({ project: unstagedProject });
            const form = wrapper.find("form");
            await form.trigger("submit");

            await vi.waitFor(() => expect(mockSnackbarShow).toHaveBeenCalled());

            expect(mockSnackbarShow).toHaveBeenCalledWith(
                expect.objectContaining({ type: "success" })
            );
        });

        it("shows error snackbar when sendQuery throws", async () => {
            mockSendQuery.mockRejectedValue(new Error("Network error"));

            const wrapper = mountCohortQuery({ project: unstagedProject });
            const form = wrapper.find("form");
            await form.trigger("submit");

            await vi.waitFor(() => expect(mockSnackbarError).toHaveBeenCalled());

            expect(mockSnackbarError).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Error running cohort query" })
            );
        });

        it("emits UpdateProject on successful query", async () => {
            mockSendQuery.mockResolvedValue({
                queryId: "new-query-id",
                trust: [{
                    statusCode: 200,
                    name: "Trust A",
                    message: "OK"
                }]
            });

            const wrapper = mountCohortQuery({ project: unstagedProject });
            const form = wrapper.find("form");
            await form.trigger("submit");

            await vi.waitFor(() => expect(wrapper.emitted("UpdateProject")).toBeDefined());
        });

        it("aggregates per-trust statusCode>=300 into the error snackbar text", async () => {
            // The server resolves the POST but reports failures per-trust. The
            // submit handler maps each non-2xx into a "Trust: NAME (Error CODE):
            // MESSAGE" line and throws the joined message — which then surfaces
            // through the catch-block snackbar.
            mockSendQuery.mockResolvedValue({
                queryId: "qid-broken",
                trust: [
                    {
                        statusCode: 400,
                        name: "Trust A",
                        message: "Bad SQL"
                    },
                    {
                        statusCode: 500,
                        name: "Trust B",
                        message: "Internal"
                    }
                ]
            });

            const wrapper = mountCohortQuery({ project: unstagedProject });
            await wrapper.find("form").trigger("submit");

            await vi.waitFor(() => expect(mockSnackbarError).toHaveBeenCalled());

            const [snackbarPayload] = mockSnackbarError.mock.calls[0];
            expect(snackbarPayload.title).toBe("Error running cohort query");
            expect(snackbarPayload.text).toContain("Trust: Trust A (Error 400): Bad SQL");
            expect(snackbarPayload.text).toContain("Trust: Trust B (Error 500): Internal");
        });

        it("flips formSubmitting back to false (re-emits submittingChange) when sendQuery errors", async () => {
            mockSendQuery.mockRejectedValue(new Error("boom"));

            const wrapper = mountCohortQuery({ project: unstagedProject });
            await wrapper.find("form").trigger("submit");

            // submittingChange fires twice: true on submit start, false on error catch.
            await vi.waitFor(() => {
                const events = wrapper.emitted("submittingChange") ?? [];
                expect(events.length).toBeGreaterThanOrEqual(2);
            });
            const events = wrapper.emitted("submittingChange")!;
            // The final emission must be `false` — without that, the parent's
            // submit-button stays in its loading state forever after an error.
            expect(events.at(-1)).toEqual([false]);
        });

        it("does not re-issue sendQuery while a previous submit is still in flight", async () => {
            // Resolve the first call slowly so the second submit lands while
            // formSubmitting is still true.
            let resolveFirst: (v: unknown) => void = () => {};
            mockSendQuery.mockReturnValueOnce(new Promise(resolve => { resolveFirst = resolve; }));

            const wrapper = mountCohortQuery({ project: unstagedProject });
            await wrapper.find("form").trigger("submit");
            await wrapper.find("form").trigger("submit");

            // Only the first submit issued a request.
            expect(mockSendQuery).toHaveBeenCalledTimes(1);

            // Resolve so the test doesn't leak an unfinished promise.
            resolveFirst({
                queryId: "q",
                trust: [{
                    statusCode: 200,
                    name: "A",
                    message: "OK"
                }]
            });
        });

        it("wires the success snackbar's View Project action to router.push", async () => {
            mockSendQuery.mockResolvedValue({
                queryId: "qid-ok",
                trust: [{
                    statusCode: 200,
                    name: "Trust A",
                    message: "OK"
                }]
            });

            const wrapper = mountCohortQuery({ project: unstagedProject });
            await wrapper.find("form").trigger("submit");
            await vi.waitFor(() => expect(mockSnackbarShow).toHaveBeenCalled());

            const payload = mockSnackbarShow.mock.calls[0][0];
            payload.action();

            expect(mockRouterPush).toHaveBeenCalledWith({ path: `/project/${unstagedProject.id}` });
        });
    });

    describe("lastRunLine", () => {
        // Find the LAST `lastRunLine`-style cell — the partial repeats the
        // class on other muted-mono labels, but the run-stamp is the only one
        // whose text starts with "Last run".
        const lastRunCell = (wrapper: ReturnType<typeof mountCohortQuery>) => {
            const candidates = wrapper.findAll(".uppercase.font-mono");

            return candidates.find(c => c.text().startsWith("Last run")) ?? null;
        };

        it("renders nothing when the persisted query has no created timestamp", () => {
            const wrapper = mountCohortQuery({ project: unstagedProjectWithQuery });
            expect(lastRunCell(wrapper)).toBeNull();
        });

        it("renders 'today' when the query was created earlier in the day", () => {
            const now = new Date();
            now.setHours(14, 32, 0, 0);
            const project: IProject = {
                ...unstagedProjectWithQuery,
                query: {
                    ...unstagedProjectWithQuery.query!,
                    created: now.toISOString(),
                    createdBy: "R. Patel"
                }
            };
            const wrapper = mountCohortQuery({ project });
            const cell = lastRunCell(wrapper);
            expect(cell).not.toBeNull();
            expect(cell!.text()).toContain("today");
            expect(cell!.text()).toContain("by R. Patel");
        });

        it("shows the SQL file icon next to the run stamp", () => {
            const project: IProject = {
                ...unstagedProjectWithQuery,
                query: {
                    ...unstagedProjectWithQuery.query!,
                    created: new Date().toISOString()
                }
            };
            const wrapper = mountCohortQuery({ project });
            const cell = lastRunCell(wrapper);
            expect(cell).not.toBeNull();
            expect(cell!.find("[data-test=last-run-sql-icon]").exists()).toBe(true);
        });

        it("renders 'yesterday' when the query was created on the previous calendar day", () => {
            const ts = new Date();
            ts.setDate(ts.getDate() - 1);
            const project: IProject = {
                ...unstagedProjectWithQuery,
                query: {
                    ...unstagedProjectWithQuery.query!,
                    created: ts.toISOString()
                }
            };
            const wrapper = mountCohortQuery({ project });
            const cell = lastRunCell(wrapper);
            expect(cell).not.toBeNull();
            expect(cell!.text()).toContain("yesterday");
            // No createdBy → no " by …" suffix.
            expect(cell!.text()).not.toContain(" by ");
        });

        it("renders an absolute date when the query is older than yesterday", () => {
            const ts = new Date();
            ts.setDate(ts.getDate() - 7);
            const project: IProject = {
                ...unstagedProjectWithQuery,
                query: {
                    ...unstagedProjectWithQuery.query!,
                    created: ts.toISOString()
                }
            };
            const wrapper = mountCohortQuery({ project });
            const cell = lastRunCell(wrapper);
            expect(cell).not.toBeNull();
            expect(cell!.text()).toContain("on ");
            expect(cell!.text()).not.toContain("today");
            expect(cell!.text()).not.toContain("yesterday");
        });

        it("renders nothing when created is an unparseable date string", () => {
            const project: IProject = {
                ...unstagedProjectWithQuery,
                query: {
                    ...unstagedProjectWithQuery.query!,
                    created: "not-a-date"
                }
            };
            const wrapper = mountCohortQuery({ project });
            expect(lastRunCell(wrapper)).toBeNull();
        });
    });

    describe("project-store reactivity", () => {
        it("syncs the local project ref when the store's project changes after mount", async () => {
            const { useProjectStore } = await import("@/store/project");
            const wrapper = mountCohortQuery({ project: unstagedProject });
            // Before the store updates, AiCodeTextArea sees the initial project's
            // (undefined) query.
            const code = wrapper.findComponent({ name: "AiCodeTextArea" });
            expect(code.props("initialValue")).toBeUndefined();

            // Mutate the store; the local project ref must follow.
            useProjectStore().project = unstagedProjectWithQuery;
            await wrapper.vm.$nextTick();

            expect(code.props("initialValue")).toBe("SELECT * FROM patients");
        });

        it("flips submittingChange back to false once the project store carries the just-submitted query id", async () => {
            mockSendQuery.mockResolvedValue({
                queryId: "qid-handshake",
                trust: [{
                    statusCode: 200,
                    name: "Trust A",
                    message: "OK"
                }]
            });
            const { useProjectStore } = await import("@/store/project");

            const wrapper = mountCohortQuery({ project: unstagedProject });
            await wrapper.find("form").trigger("submit");
            await vi.waitFor(() => expect(mockSendQuery).toHaveBeenCalled());

            // After the success branch, formSubmitting stays `true` waiting for
            // the project store to surface the new query id. Inject it and the
            // watch should flip submitting → false.
            useProjectStore().project = {
                ...unstagedProject,
                query: {
                    ...unstagedProjectWithQuery.query!,
                    id: "qid-handshake"
                }
            };
            await wrapper.vm.$nextTick();
            await vi.waitFor(() => {
                const events = wrapper.emitted("submittingChange") ?? [];
                expect(events.at(-1)).toEqual([false]);
            });
        });
    });
});
