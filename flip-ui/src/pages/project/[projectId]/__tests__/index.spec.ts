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
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { IProject } from "@/services/project-service";

import ProjectPage from "../index.vue";

const mockRoute = { params: { projectId: "p-123" } as Record<string, string> };
vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

// All service helpers are mocked — we drive the state with the project store
// and only assert on what the page renders / which mock got called.
const stageProjectWithTrusts = vi.fn().mockResolvedValue(undefined);
const approveProject = vi.fn().mockResolvedValue(undefined);
const unstageProject = vi.fn().mockResolvedValue(undefined);
const editProject = vi.fn().mockResolvedValue(undefined);
vi.mock("@/services/project-service", () => ({
    stageProject: (...args: unknown[]) => stageProjectWithTrusts(...args),
    approveProject: (...args: unknown[]) => approveProject(...args),
    unstageProject: (...args: unknown[]) => unstageProject(...args),
    editProject: (...args: unknown[]) => editProject(...args)
}));

const snackbarSuccess = vi.fn();
const snackbarError = vi.fn();
vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: (...args: unknown[]) => snackbarSuccess(...args),
        error: (...args: unknown[]) => snackbarError(...args)
    }
}));

// Stub the heavy children — we want to test the page's own logic, not its
// composition with all the partials.
const stubs = {
    AiCard: { template: "<div><slot /></div>" },
    AiButton: {
        template: "<button v-bind=\"$attrs\" @click=\"$emit('click')\"><slot /></button>",
        inheritAttrs: false
    },
    AiCircledIcon: { template: "<i><slot /></i>" },
    AiConfirmModal: {
        // Expose the continue-action so tests can drive the modal flow without
        // needing to render its internals.
        template: "<div v-if=\"dialog\" data-test=\"stubbed-confirm-modal\"><button data-test=\"stubbed-confirm-continue\" @click=\"continueAction\">go</button></div>",
        props: ["dialog", "continueAction", "title", "description", "continueButtonText", "submitting"]
    },
    AiGuard: { template: "<div><slot /></div>" },
    QueryDetails: { template: "<div data-test=\"stub-query-details\" />" },
    LatestModels: { template: "<div data-test=\"stub-latest-models\" />" },
    EditProjectDrawer: { template: "<div />" },
    LifecycleTrack: {
        template: "<ol data-test=\"stub-lifecycle\"><li v-for=\"s in steps\" :key=\"s.id\" :data-test=\"`step-${s.id}`\" :data-completed=\"s.completed\" :data-date=\"s.date\">{{ s.name }}</li></ol>",
        props: ["steps"]
    },
    ProjectApproval: { template: "<div data-test=\"stub-project-approval\" />" },
    ProjectStaging: {
        // Expose the computed stageable set so tests can assert the parent's
        // exclusion logic (errored / never-responded / empty trusts).
        template: "<div data-test=\"stub-project-staging\" :data-stageable=\"(stageableTrustIds || []).join(',')\" />",
        props: ["stageableTrustIds", "hasQuery", "projectStaged", "staging"]
    },
    ProjectStatus: { template: "<div data-test=\"stub-project-status\" />" },
    Transition: { template: "<div><slot /></div>" },
    // Render the slot so breadcrumb copy is exercised (the real RouterLink is
    // never registered in these tests).
    "router-link": { template: "<a><slot /></a>" }
};

interface MountOptions {
    project?: IProject | null;
    permissions?: string[];
    userId?: string;
}

const baseProject = (): IProject => ({
    id: "p-123",
    name: "Stroke triage",
    description: "Test",
    ownerId: "owner-1",
    ownerEmail: "owner@example.com",
    creationtimestamp: "2026-01-01T00:00:00Z",
    status: "UNSTAGED",
    users: [],
    approvedTrusts: []
});

function mountProjectPage({
    project = baseProject(),
    permissions = ["CanCreateProjects", "CanUnstageProjects"],
    userId = "owner-1"
}: MountOptions = {}) {
    return mount(ProjectPage, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false,
                initialState: {
                    project: { project },
                    auth: {
                        user: {
                            userId,
                            permissions
                        }
                    }
                }
            })],
            stubs
        }
    });
}

beforeEach(() => {
    stageProjectWithTrusts.mockClear();
    approveProject.mockClear();
    unstageProject.mockClear();
    editProject.mockClear();
    snackbarSuccess.mockClear();
    snackbarError.mockClear();
});

describe("Project page (/project/[id]/index.vue)", () => {
    test("hides the page chrome when projectStore.project is null", () => {
        const wrapper = mountProjectPage({ project: null as unknown as IProject });
        expect(wrapper.find("[data-test=stub-lifecycle]").exists()).toBe(false);
    });

    test("renders the Projects breadcrumb link back to the project list", () => {
        const wrapper = mountProjectPage();
        expect(wrapper.find("a").exists()).toBe(true);
        expect(wrapper.text()).toContain("Projects");
    });

    test("pins the Latest Models card to the row height the imaging-status card defines", () => {
        const wrapper = mountProjectPage();

        // The card is absolutely positioned inside a stretched grid cell at lg, so
        // its model list can't inflate the row past the imaging project status card.
        const card = wrapper.find("[data-test=stub-latest-models]");
        expect(card.classes()).toContain("lg:absolute");
        expect(card.classes()).toContain("lg:inset-0");
        const cell = card.element.parentElement;
        expect(cell?.className).toContain("relative");
        expect(cell?.className).toContain("lg:self-stretch");
        expect(cell?.className).not.toContain("sticky");
    });

    test("renders 4 lifecycle steps and marks only step 01 complete on a fresh UNSTAGED project", () => {
        const wrapper = mountProjectPage();
        const steps = wrapper.findAll("[data-test^=step-]");
        expect(steps).toHaveLength(4);
        expect(steps[0].text()).toBe("Project Created");
        expect(steps[0].attributes("data-completed")).toBe("true");
        // Cohort Query / Staged / Approved are all unchecked until they happen.
        expect(steps[1].attributes("data-completed")).toBe("false");
        expect(steps[2].attributes("data-completed")).toBe("false");
        expect(steps[3].attributes("data-completed")).toBe("false");
    });

    test("marks Cohort Query step complete when project.query is present", () => {
        const project = baseProject();
        project.query = {
            id: "q1",
            name: "q",
            query: "SELECT 1",
            queriedTrustIds: [],
            respondedTrustIds: [],
            erroredTrustIds: []
        } as unknown as IProject["query"];
        const wrapper = mountProjectPage({ project });
        const step02 = wrapper.find("[data-test=step-02]");
        expect(step02.attributes("data-completed")).toBe("true");
    });

    test("excludes errored and empty (0-record/suppressed) trusts from the stageable set", async () => {
        const project = baseProject();
        project.query = {
            id: "q1",
            name: "q",
            query: "SELECT 1",
            queriedTrustIds: ["t1", "t2", "t3", "t4"],
            respondedTrustIds: ["t1", "t2", "t3"],
            erroredTrustIds: ["t2"],
            emptyTrustIds: ["t3"]
        } as unknown as IProject["query"];
        const wrapper = mountProjectPage({ project });
        await flushPromises();

        // t1 only: t2 errored, t3 returned 0/suppressed, t4 never responded (#519).
        const staging = wrapper.find("[data-test=stub-project-staging]");
        expect(staging.attributes("data-stageable")).toBe("t1");
    });

    test("marks Project Staged when status moves off UNSTAGED, and Project Approved when status is APPROVED", () => {
        const stagedProject = baseProject();
        stagedProject.status = "STAGED";
        const stagedWrapper = mountProjectPage({ project: stagedProject });
        expect(stagedWrapper.find("[data-test=step-03]").attributes("data-completed")).toBe("true");
        expect(stagedWrapper.find("[data-test=step-04]").attributes("data-completed")).toBe("false");

        const approvedProject = baseProject();
        approvedProject.status = "APPROVED";
        approvedProject.approvedTrusts = [
            {
                id: "t1",
                name: "KCH",
                approved: true,
                approvedAt: "2026-05-01T10:00:00Z"
            },
            {
                id: "t2",
                name: "UCLH",
                approved: true,
                approvedAt: "2026-05-15T10:00:00Z"
            }
        ];
        const approvedWrapper = mountProjectPage({ project: approvedProject });
        const step04 = approvedWrapper.find("[data-test=step-04]");
        expect(step04.attributes("data-completed")).toBe("true");
    });

    test("surfaces the staged date on the Staged step once the project is staged", () => {
        const staged = baseProject();
        staged.status = "STAGED";
        staged.stagedAt = "2026-03-01T10:00:00.000Z";
        const wrapper = mountProjectPage({ project: staged });
        expect(wrapper.find("[data-test=step-03]").attributes("data-date")).toBe("2026-03-01T10:00:00.000Z");
    });

    test("does not surface a stale staged date on the Staged step when the project is UNSTAGED", () => {
        // staged_at is the most-recent STAGE audit and persists after an unstage. Once a project
        // is back to UNSTAGED the Staged step is incomplete again, so it must not show the old date.
        const project = baseProject();
        project.status = "UNSTAGED";
        project.stagedAt = "2026-03-01T10:00:00.000Z";
        const wrapper = mountProjectPage({ project });
        const step03 = wrapper.find("[data-test=step-03]");
        expect(step03.attributes("data-completed")).toBe("false");
        expect(step03.attributes("data-date")).toBeFalsy();
    });

    test("shows ProjectStaging while UNSTAGED and switches to ProjectApproval once staged", () => {
        const wrapper = mountProjectPage();
        expect(wrapper.find("[data-test=stub-project-staging]").exists()).toBe(true);
        expect(wrapper.find("[data-test=stub-project-approval]").exists()).toBe(false);

        const staged = baseProject();
        staged.status = "STAGED";
        const stagedWrapper = mountProjectPage({ project: staged });
        expect(stagedWrapper.find("[data-test=stub-project-staging]").exists()).toBe(false);
        expect(stagedWrapper.find("[data-test=stub-project-approval]").exists()).toBe(true);
    });

    test("shows the Awaiting Approval badge while STAGED", () => {
        const staged = baseProject();
        staged.status = "STAGED";
        const wrapper = mountProjectPage({ project: staged });
        expect(wrapper.text()).toContain("Awaiting Approval");
    });

    test("user-count pill pluralises: 1 'User' when no extra users, 'Users' when one or more", () => {
        const wrapper = mountProjectPage();
        // owner counts as +1 → "1 User"
        expect(wrapper.text()).toContain("1 User");
        expect(wrapper.text()).not.toContain("1 Users");

        const project = baseProject();
        project.users = [
            {
                id: "u1",
                email: "a@x",
                isDisabled: false
            },
            {
                id: "u2",
                email: "b@x",
                isDisabled: false
            }
        ];
        const wrapper2 = mountProjectPage({ project });
        // owner + 2 = 3 Users
        expect(wrapper2.text()).toContain("3 Users");
    });

    describe("header actions align with the title and collapse to icons on narrow screens", () => {
        // Mirrors the model page header: one vertically-centred row (title
        // left, actions right) whose labels hide below lg so they stop
        // squashing the truncating project name.
        const labelSelector = "span.hidden.lg\\:inline";

        test("title and actions sit in one items-center row with a shrink-0 actions cluster", () => {
            const wrapper = mountProjectPage();

            const h1 = wrapper.find("h1");
            expect(h1.exists()).toBe(true);
            const row = h1.element.parentElement;
            expect(row?.className).toContain("items-center");
            expect(row?.className).not.toContain("flex-wrap");

            const actions = h1.element.nextElementSibling;
            expect(actions?.className).toContain("items-center");
            expect(actions?.className).toContain("shrink-0");
            expect(actions?.className).not.toContain("flex-col");
        });

        test("hides the Edit Project label below lg and keeps an aria-label", () => {
            const wrapper = mountProjectPage();

            const btn = wrapper.find("[data-test=edit-project-btn]");
            expect(btn.exists()).toBe(true);
            expect(btn.attributes("aria-label")).toBe("Edit Project");
            const label = btn.find(labelSelector);
            expect(label.exists()).toBe(true);
            expect(label.text()).toBe("Edit Project");
        });

        test("hides the Unstage Project label and the Awaiting Approval text below lg when STAGED", () => {
            const staged = baseProject();
            staged.status = "STAGED";
            const wrapper = mountProjectPage({ project: staged });

            const unstage = wrapper.find("[data-test=unstage-project-btn]");
            expect(unstage.exists()).toBe(true);
            expect(unstage.attributes("aria-label")).toBe("Unstage Project");
            expect(unstage.find(labelSelector).text()).toBe("Unstage Project");

            const badge = wrapper.find("[data-test=awaiting-approval-badge]");
            expect(badge.exists()).toBe(true);
            expect(badge.attributes("title")).toBe("Awaiting Approval");
            expect(badge.find(labelSelector).text()).toBe("Awaiting Approval");
        });

        test("hides the word 'Users' below lg but keeps the count visible with a full-title tooltip", () => {
            const project = baseProject();
            project.users = [
                {
                    id: "u1",
                    email: "a@x",
                    isDisabled: false
                },
                {
                    id: "u2",
                    email: "b@x",
                    isDisabled: false
                }
            ];
            const wrapper = mountProjectPage({ project });

            const pill = wrapper.find("[data-test=user-count-pill]");
            expect(pill.exists()).toBe(true);
            expect(pill.attributes("title")).toBe("3 Users");
            const word = pill.find(labelSelector);
            expect(word.exists()).toBe(true);
            // Only the noun hides; the count stays visible at every width.
            expect(word.text()).toBe("Users");
            expect(pill.text()).toContain("3");
            // The count lives inside the icon chip itself: one rounded pill
            // carrying both the user icon and the number (no separate disc).
            expect(pill.classes()).toContain("rounded-full");
            expect(pill.classes()).toContain("bg-primary-100");
            expect(pill.find("svg").exists()).toBe(true);
            expect(pill.find("i").exists()).toBe(false);
        });
    });

    test("the Unstage button only renders when the project is STAGED", () => {
        const wrapper = mountProjectPage();
        expect(wrapper.find("[data-test=unstage-project-btn]").exists()).toBe(false);

        const staged = baseProject();
        staged.status = "STAGED";
        const stagedWrapper = mountProjectPage({ project: staged });
        expect(stagedWrapper.find("[data-test=unstage-project-btn]").exists()).toBe(true);
    });

    test("clicking Unstage and confirming calls the unstageProject service for this project", async () => {
        const staged = baseProject();
        staged.status = "STAGED";
        const wrapper = mountProjectPage({ project: staged });

        await wrapper.find("[data-test=unstage-project-btn]").trigger("click");
        await wrapper.find("[data-test=stubbed-confirm-continue]").trigger("click");
        await flushPromises();

        expect(unstageProject).toHaveBeenCalledTimes(1);
        expect(unstageProject).toHaveBeenCalledWith("/projects/p-123/unstage");
        expect(snackbarSuccess).toHaveBeenCalled();
    });

    test("Edit Project button label flips to 'View Project' for viewers", () => {
        // Viewer = no CanCreateProjects. canCreateProjects=false → isViewer=true.
        const wrapper = mountProjectPage({ permissions: [] });
        const editBtn = wrapper.find("[data-test=edit-project-btn]");
        expect(editBtn.text()).toContain("View Project");
        expect(editBtn.text()).not.toContain("Edit Project");
    });
});
