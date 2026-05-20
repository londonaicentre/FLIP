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
import { beforeEach, describe, expect, test, vi } from "vitest";
import { ref } from "vue";

import { IProject, IProjectTrust, ProjectStatus } from "@/services/project-service";

import Page from "../projects.vue";

interface ProjectsResponse {
    data: IProject[];
    totalPages: number;
    page: number;
}

const mockSwrvData = ref<ProjectsResponse | undefined>(undefined);

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: vi.fn(),
        error: ref(null)
    })
}));

const stubs = {
    AiCard: { template: "<div><slot /></div>" },
    AiButton: { template: "<button><slot /></button>" },
    AiLoader: { template: "<div />" },
    AiSearch: { template: "<div />" },
    AiPagination: { template: "<div />" },
    CreateProjectModal: { template: "<div />" },
    // The global test setup stubs router-link with an empty (no-slot) stub;
    // override it so project rows actually render their content.
    "router-link": {
        template: "<a><slot /></a>",
        props: ["to"]
    },
    "icon-ph-list-bullets-duotone": { template: "<span />" },
    "icon-ph-squares-four-duotone": { template: "<span />" },
    "icon-ph-archive-duotone": { template: "<span />" },
    "icon-ph-users-three-duotone": { template: "<span />" }
};

const trust = (id: string, code: string, approved: boolean): IProjectTrust => ({
    id,
    name: `${code} NHS Foundation Trust`,
    code,
    approved
});

const makeProject = (status: ProjectStatus, trusts: IProjectTrust[]): IProject => ({
    id: `p-${status}`,
    name: "Stroke triage from CT",
    description: "Multi-trust evaluation of an acute-stroke triage model.",
    ownerId: "u1",
    ownerEmail: "r.patel@example.com",
    creationtimestamp: new Date().toISOString(),
    status,
    users: [],
    approvedTrusts: trusts
});

const setProject = (project: IProject): void => {
    mockSwrvData.value = {
        data: [project],
        totalPages: 1,
        page: 1
    };
};

function mountPage() {
    return mount(Page, {
        global: {
            plugins: [createTestingPinia({
                createSpy: vi.fn,
                stubActions: false
            })],
            stubs
        }
    });
}

beforeEach(() => {
    mockSwrvData.value = undefined;
});

describe("Projects Page", () => {
    test("Renders Component", () => {
        expect(mountPage().exists()).toBe(true);
    });

    test("a staged-but-unapproved trust still shows as a chip on the card", async () => {
        // Regression: trusts were filtered to approved=true, so a freshly
        // staged trust vanished and the card claimed "No trusts staged".
        setProject(makeProject("STAGED", [trust("t1", "KCH", false)]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='trust-chip']")).toHaveLength(1);
        expect(wrapper.text()).not.toContain("No trusts staged");
    });

    test("staged projects render plain trust chips with no approval dot", async () => {
        setProject(makeProject("STAGED", [trust("t1", "KCH", true), trust("t2", "GSTT", false)]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        // Both linked trusts render; the project is not APPROVED, so even the
        // trust that has signed off keeps a plain chip.
        expect(wrapper.findAll("[data-test='trust-chip']")).toHaveLength(2);
        expect(wrapper.findAll("[data-test='trust-approved-dot']")).toHaveLength(0);
    });

    test("an approved project shows a green dot on each approved trust chip", async () => {
        setProject(makeProject("APPROVED", [trust("t1", "KCH", true), trust("t2", "GSTT", true)]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        const dots = wrapper.findAll("[data-test='trust-approved-dot']");
        expect(dots).toHaveLength(2);
        dots.forEach(dot => expect(dot.classes()).toContain("bg-emerald-500"));
    });

    test("a non-approved trust on an approved project keeps a plain chip", async () => {
        setProject(makeProject("APPROVED", [trust("t1", "KCH", true), trust("t2", "GSTT", false)]));
        const wrapper = mountPage();
        await wrapper.vm.$nextTick();

        expect(wrapper.findAll("[data-test='trust-chip']")).toHaveLength(2);
        expect(wrapper.findAll("[data-test='trust-approved-dot']")).toHaveLength(1);
    });

    test("renders approved trust dots in the grid view too", async () => {
        setProject(makeProject("APPROVED", [trust("t1", "KCH", true), trust("t2", "GSTT", true)]));
        const wrapper = mountPage();
        await wrapper.find("[data-test='view-mode-grid']").trigger("click");

        const grid = wrapper.find("[data-test='projects-grid-view']");
        expect(grid.exists()).toBe(true);
        expect(grid.findAll("[data-test='trust-approved-dot']")).toHaveLength(2);
    });
});
