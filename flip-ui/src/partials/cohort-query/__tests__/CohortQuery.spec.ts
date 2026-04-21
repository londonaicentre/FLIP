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

vi.mock("@/router", () => ({
    default: { push: vi.fn() }
}));

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
    Form: {
        template: "<form @submit.prevent=\"$emit('submit', { query: 'SELECT * FROM patients' })\"><slot /></form>"
    },
    "icon-heroicons-outline-clock": { template: "<span />" },
    Transition: { template: "<div><slot /></div>" },
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
        trustsQueried: 2,
        totalCohort: 100
    }
};

const unstagedProjectWithQuery: IProject = {
    ...unstagedProject,
    query: {
        id: "query-1",
        name: "Test Query",
        query: "SELECT * FROM patients",
        trustsQueried: 2,
        totalCohort: 100
    }
};

function mountCohortQuery(options: {
    project?: IProject;
    permissions?: string[];
} = {}) {
    const { project, permissions = ["CanManageProjects"] } = options;
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
                            attributes: { sub: "1", email: "test@example.com" },
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

describe("CohortQuery", () => {
    beforeEach(() => {
        mockSendQuery.mockReset();
        mockSnackbarShow.mockReset();
        mockSnackbarError.mockReset();
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

    describe("info alert messaging", () => {
        it("shows editable query message when project is UNSTAGED", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject });
            const alert = wrapper.findComponent({ name: "AiAlert" });

            expect(alert.props("text")).toContain("This query will be sent to all participating Trusts");
        });

        it("shows locked query message when project is STAGED", () => {
            const wrapper = mountCohortQuery({ project: stagedProjectWithQuery });
            const alert = wrapper.findComponent({ name: "AiAlert" });

            expect(alert.props("text")).toContain("locked and can not be edited");
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

        it("sets readonly on AiCodeTextArea when user is observer", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject, permissions: [] });
            const codeTextArea = wrapper.findComponent({ name: "AiCodeTextArea" });

            expect(codeTextArea.props("inputProps")).toEqual({ readonly: true });
        });

        it("does not set readonly when project is UNSTAGED and user has permissions", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject, permissions: ["CanManageProjects"] });
            const codeTextArea = wrapper.findComponent({ name: "AiCodeTextArea" });

            expect(codeTextArea.props("inputProps")).toEqual({ readonly: false });
        });

        it("shows the button when project is UNSTAGED and user is not observer", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject });

            expect(wrapper.find(CohortQueryPage.runCohortQueryButton).exists()).toBe(true);
        });

        it("hides the button when project is STAGED (locked)", () => {
            const wrapper = mountCohortQuery({ project: stagedProjectWithQuery });

            expect(wrapper.find(CohortQueryPage.runCohortQueryButton).exists()).toBe(false);
        });

        it("hides the button when user is observer", () => {
            const wrapper = mountCohortQuery({ project: unstagedProject, permissions: [] });

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
                trust: [{ statusCode: 200, name: "Trust A", message: "OK" }]
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
                trust: [{ statusCode: 200, name: "Trust A", message: "OK" }]
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
                trust: [{ statusCode: 200, name: "Trust A", message: "OK" }]
            });

            const wrapper = mountCohortQuery({ project: unstagedProject });
            const form = wrapper.find("form");
            await form.trigger("submit");

            await vi.waitFor(() => expect(wrapper.emitted("UpdateProject")).toBeDefined());
        });
    });
});
