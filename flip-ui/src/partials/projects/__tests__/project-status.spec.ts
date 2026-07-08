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
import { reactive, ref } from "vue";

import { IImagingProjectStatus } from "@/services/project-service";
import { useSiteDetailsStore } from "@/store/siteDetailsStore";

import ProjectStatus from "../ProjectStatus.vue";
import { ProjectStatusComponent } from "../selectors";

const mockRoute = reactive({
    name: "ProjectView",
    fullPath: "/project/test-project-id",
    path: "/project/test-project-id",
    params: { projectId: "test-project-id" } as Record<string, string>
});

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute
    };
});

const mockSwrvData = ref<IImagingProjectStatus[] | undefined>(undefined);
const mockSwrvError = ref<Error | null>(null);

vi.mock("swrv", () => ({
    default: () => ({
        data: mockSwrvData,
        mutate: vi.fn(),
        error: mockSwrvError
    })
}));

vi.mock("@/composables/useErrorHandler", () => ({ default: vi.fn() }));

const stubs = {
    "icon-ph-check-bold": { template: "<span data-test-icon='check' />" },
    "icon-heroicons-outline-clock": { template: "<span data-test-icon='clock' />" },
    ExclamationCircleIcon: { template: "<span data-test-icon='exclamation' />" },
    RefreshIcon: { template: "<span data-test-icon='refresh' />" },
    Transition: { template: "<div><slot /></div>" }
};

const mockTrustData: IImagingProjectStatus[] = [
    {
        trustId: "trust-1",
        trustName: "Alpha Trust",
        projectCreationCompleted: true,
        importStatus: {
            successful: 10,
            failed: 2,
            processing: 3,
            queued: 5,
            queueFailed: 1
        },
        reimportCount: 2
    },
    {
        trustId: "trust-2",
        trustName: "Beta Trust",
        projectCreationCompleted: false,
        importStatus: undefined,
        reimportCount: undefined
    },
    {
        trustId: "trust-3",
        trustName: "Gamma Trust",
        projectCreationCompleted: true,
        importStatus: undefined,
        reimportCount: 5
    }
];

// Separate signature so "pass undefined explicitly" actually reaches the
// store mutation — using a default-parameter value would swallow an
// explicit `undefined` and reinstate the 5.
function mountProjectStatus(canLoad = true, ...overrides: [(number | undefined)?, number?]) {
    const maxReimportCount = overrides.length ? overrides[0] : 5;
    const cohortSize = overrides[1];
    // Seed the siteDetailsStore with the cap the component reads from,
    // mirroring what /site/details populates at runtime. Tests default to
    // 5 so the existing "2 / 5" / "5 / 5" assertions keep holding.
    const pinia = createTestingPinia({
        createSpy: vi.fn,
        stubActions: false
    });
    const store = useSiteDetailsStore(pinia);
    store.maxReimportCount = maxReimportCount;

    return mount(ProjectStatus, {
        props: {
            canLoad,
            cohortSize
        },
        global: {
            plugins: [pinia],
            stubs,
            directives: { tippy: () => {} }
        }
    });
}

beforeEach(() => {
    mockSwrvData.value = undefined;
    mockSwrvError.value = null;
});

describe("ProjectStatus", () => {
    describe("rendering", () => {
        it("mounts without errors", () => {
            const wrapper = mountProjectStatus();
            expect(wrapper.exists()).toBe(true);
        });

        it("shows the project status container when canLoad is true", () => {
            const wrapper = mountProjectStatus(true);
            expect(wrapper.find(ProjectStatusComponent.container).exists()).toBe(true);
        });

        it("hides the project status container when canLoad is false", () => {
            const wrapper = mountProjectStatus(false);
            expect(wrapper.find(ProjectStatusComponent.container).exists()).toBe(false);
        });
    });

    describe("locked state when canLoad is false", () => {
        it("shows an info alert about project approval being required", () => {
            const wrapper = mountProjectStatus(false);
            const alerts = wrapper.findAllComponents({ name: "AiAlert" });
            const approvalAlert = alerts.find(a => a.props("text")?.includes("Project approval is required"));

            expect(approvalAlert).toBeDefined();
        });

        it("shows skeleton placeholders", () => {
            const wrapper = mountProjectStatus(false);
            const skeletons = wrapper.findAllComponents({ name: "AiSkeleton" });

            expect(skeletons.length).toBeGreaterThan(0);
        });
    });

    describe("loading state", () => {
        it("shows skeleton loading indicators when data is null", () => {
            const wrapper = mountProjectStatus(true);
            const skeletons = wrapper.findAllComponents({ name: "AiSkeleton" });

            expect(skeletons.length).toBeGreaterThan(0);
        });

        it("does not show the trust list when loading", () => {
            const wrapper = mountProjectStatus(true);
            const list = wrapper.find("ul[role='list']");

            expect(list.exists()).toBe(false);
        });
    });

    describe("loaded state with data", () => {
        beforeEach(() => {
            mockSwrvData.value = mockTrustData;
        });

        it("displays trust names sorted alphabetically", () => {
            const wrapper = mountProjectStatus(true);
            const trustNames = [
                wrapper.find("[data-test=trust-name-trust-1]"),
                wrapper.find("[data-test=trust-name-trust-2]"),
                wrapper.find("[data-test=trust-name-trust-3]")
            ];

            expect(trustNames[0].text()).toBe("Alpha Trust");
            expect(trustNames[1].text()).toBe("Beta Trust");
            expect(trustNames[2].text()).toBe("Gamma Trust");
        });

        it("shows a check icon for trusts with completed project creation", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=project-creation-complete-trust-1]").exists()).toBe(true);
        });

        it("shows a clock icon for trusts with incomplete project creation", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=project-creation-incomplete-trust-2]").exists()).toBe(true);
        });

        it("displays import statistics for trusts with importStatus", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=successful-imports-trust-1]").text()).toBe("10");
            expect(wrapper.find("[data-test=processing-imports-trust-1]").text()).toBe("3");
            expect(wrapper.find("[data-test=queued-imports-trust-1]").text()).toBe("5");
            expect(wrapper.find("[data-test=failed-imports-trust-1]").text()).toBe("3");
        });

        it("renders the empty-state import bar for every card so the footer pins to the bottom", () => {
            // Cards used to short-circuit the bar when importStatus was absent,
            // which left "Awaiting creation" floating mid-card next to a tall
            // sibling in the grid. Bar is now always rendered, with the
            // "— / no imports yet" branch covering both incomplete-creation
            // and completed-but-no-imports trusts.
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=import-bar-trust-1]").exists()).toBe(true);
            expect(wrapper.find("[data-test=import-bar-trust-2]").exists()).toBe(true);
            expect(wrapper.find("[data-test=import-bar-trust-3]").exists()).toBe(true);

            const trust2Bar = wrapper.find("[data-test=import-bar-trust-2]");
            expect(wrapper.find("[data-test=pct-retrieved-trust-2]").text()).toBe("—");
            expect(trust2Bar.text()).toContain("no imports yet");

            const trust3Bar = wrapper.find("[data-test=import-bar-trust-3]");
            expect(wrapper.find("[data-test=pct-retrieved-trust-3]").text()).toBe("—");
            expect(trust3Bar.text()).toContain("no imports yet");
        });

        it("no longer renders the separate awaiting-import-status alert", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=import-status-warning-trust-3]").exists()).toBe(false);
        });

        it("displays reimport count when available and project is created", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=project-reimport-status-trust-1]").text()).toBe("2 / 5");
        });

        it("shows exclamation icon when reimport count reaches max limit", () => {
            const wrapper = mountProjectStatus(true);
            const trust3Li = wrapper.find("[data-test=project-reimport-status-trust-3]");

            expect(trust3Li.exists()).toBe(true);
            expect(trust3Li.text()).toBe("5 / 5");
        });
    });

    describe("overview sidebar", () => {
        beforeEach(() => {
            mockSwrvData.value = mockTrustData;
        });

        it("shows correct project creation count", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find(ProjectStatusComponent.overviewProjectCreation).text()).toBe("2/3");
        });

        it("shows correct study retrieval total", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find(ProjectStatusComponent.overviewImageRetrieval).text()).toBe("10");
        });

        it("floors near-complete retrieval percentages instead of rounding them up", () => {
            mockSwrvData.value = [
                {
                    trustId: "trust-floor",
                    trustName: "Floor Trust",
                    projectCreationCompleted: true,
                    importStatus: {
                        successful: 999,
                        failed: 1,
                        processing: 0,
                        queued: 0,
                        queueFailed: 0
                    },
                    reimportCount: 0
                }
            ];

            const wrapper = mountProjectStatus(true, 5, 1000);

            // The component formats the cohort size with toLocaleString() and no explicit
            // locale, so derive the expected label the same way instead of hard-coding "1,000".
            expect(wrapper.find(ProjectStatusComponent.overviewRetrievalPercent).text())
                .toBe(`99% of expected ${(1000).toLocaleString()}`);
            expect(wrapper.find("[data-test=pct-retrieved-trust-floor]").text()).toBe("99%");
        });
    });

    describe("trust list", () => {
        beforeEach(() => {
            mockSwrvData.value = mockTrustData;
        });

        it("does not render a trust filter search", () => {
            const wrapper = mountProjectStatus(true);

            expect(wrapper.findComponent({ name: "AiSearch" }).exists()).toBe(false);
            expect(wrapper.find("[data-test=trust-name-trust-1]").exists()).toBe(true);
            expect(wrapper.find("[data-test=trust-name-trust-2]").exists()).toBe(true);
            expect(wrapper.find("[data-test=trust-name-trust-3]").exists()).toBe(true);
        });
    });

    describe("conditional display logic", () => {
        beforeEach(() => {
            mockSwrvData.value = mockTrustData;
        });

        it("shows green icon when reimport count is below max", () => {
            const wrapper = mountProjectStatus(true);
            const trust1ReimportStatus = wrapper.find("[data-test=project-reimport-status-trust-1]");

            expect(trust1ReimportStatus.exists()).toBe(true);
            // The reimport icon (sibling SVG) should be green for below-max counts
            const icon = trust1ReimportStatus.element.parentElement!.querySelector("svg");
            expect(icon!.classList.contains("text-green-500")).toBe(true);
        });

        it("shows yellow icon when reimport count reaches max", () => {
            const wrapper = mountProjectStatus(true);
            const trust3ReimportStatus = wrapper.find("[data-test=project-reimport-status-trust-3]");

            expect(trust3ReimportStatus.exists()).toBe(true);
            const icon = trust3ReimportStatus.element.parentElement!.querySelector("svg");
            expect(icon!.classList.contains("text-yellow-500")).toBe(true);
        });

        it("shows 'Created' text for trusts with completed project creation", () => {
            const wrapper = mountProjectStatus(true);
            const trust1Li = wrapper.findAll("li").find(li => li.find("[data-test=trust-name-trust-1]").exists())!;

            expect(trust1Li.text()).toContain("Created");
        });

        it("shows 'Awaiting creation…' text for trusts with incomplete project creation", () => {
            const wrapper = mountProjectStatus(true);
            const trust2Li = wrapper.findAll("li").find(li => li.find("[data-test=trust-name-trust-2]").exists())!;

            expect(trust2Li.text()).toContain("Awaiting creation…");
        });

        it("does not show import-status-warning for trusts with incomplete creation", () => {
            const wrapper = mountProjectStatus(true);

            // trust-2 has projectCreationCompleted=false and importStatus=undefined
            // The alert requires BOTH !importStatus AND projectCreationCompleted
            expect(wrapper.find("[data-test=import-status-warning-trust-2]").exists()).toBe(false);
        });

        it("shows zeroed legend counts for trusts without importStatus", () => {
            // Empty-state bar still renders the legend so card heights stay
            // consistent across the grid; counts read 0 across the board.
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find("[data-test=successful-imports-trust-2]").text()).toBe("0");
            expect(wrapper.find("[data-test=processing-imports-trust-2]").text()).toBe("0");
            expect(wrapper.find("[data-test=queued-imports-trust-2]").text()).toBe("0");
            expect(wrapper.find("[data-test=failed-imports-trust-2]").text()).toBe("0");

            expect(wrapper.find("[data-test=successful-imports-trust-3]").text()).toBe("0");
        });

        it("does not show reimport section for trusts with incomplete creation", () => {
            const wrapper = mountProjectStatus(true);

            // trust-2 has projectCreationCompleted=false, reimportCount=undefined
            expect(wrapper.find("[data-test=project-reimport-status-trust-2]").exists()).toBe(false);
        });

        it("hides the reimport badge until /site/details populates maxReimportCount", () => {
            // Before the first site-details fetch lands, maxReimportCount is
            // undefined in the store. Rendering the badge would require a
            // display threshold we don't know yet, so the component must wait.
            mockSwrvData.value = mockTrustData;
            const wrapper = mountProjectStatus(true, undefined);

            expect(wrapper.find("[data-test=project-reimport-status-trust-1]").exists()).toBe(false);
            expect(wrapper.find("[data-test=project-reimport-status-trust-3]").exists()).toBe(false);
        });
    });

    describe("route change behavior", () => {
        it("clears data when route projectId changes", async () => {
            mockSwrvData.value = mockTrustData;
            const wrapper = mountProjectStatus(true);

            expect(wrapper.findAll("li").length).toBe(3);

            mockRoute.params = { projectId: "different-project-id" };
            await wrapper.vm.$nextTick();

            // Data should be cleared, showing loading skeletons
            expect(wrapper.find("ul[role='list']").exists()).toBe(false);
        });

        afterEach(() => {
            mockRoute.params = { projectId: "test-project-id" };
        });
    });

    describe("empty data", () => {
        it("shows awaiting message when data is an empty array", () => {
            mockSwrvData.value = [];
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find(ProjectStatusComponent.noProjectStatusMessage).exists()).toBe(true);
        });

        it("displays correct overview counts with empty data", () => {
            mockSwrvData.value = [];
            const wrapper = mountProjectStatus(true);

            expect(wrapper.find(ProjectStatusComponent.overviewProjectCreation).text()).toBe("0/0");
            expect(wrapper.find(ProjectStatusComponent.overviewImageRetrieval).text()).toBe("0");
        });

        it("does not show overview sidebar when data is undefined", () => {
            mockSwrvData.value = undefined;
            const wrapper = mountProjectStatus(true);

            // When data is undefined, the skeleton loading state is shown, not the data view
            expect(wrapper.find(ProjectStatusComponent.overviewProjectCreation).exists()).toBe(false);
        });
    });
});
