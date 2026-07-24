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
import { mount, VueWrapper } from "@vue/test-utils";
import { reactive, ref } from "vue";

import MainLayout from "../MainLayout.vue";

const mockRoute = reactive({
    name: "Home",
    fullPath: "/",
    path: "/",
    params: {} as Record<string, string>
});

const mockRouterPush = vi.fn();

vi.mock("vue-router", async (importOriginal) => {
    const actual = await importOriginal<typeof import("vue-router")>();

    return {
        ...actual,
        useRoute: () => mockRoute,
        useRouter: () => ({ push: mockRouterPush })
    };
});

// Mock @/router at the module level: the real module builds the app router from
// the generated page routes, whose lazy page imports (e.g. Login.vue and its
// ~icons import) can resolve AFTER this suite's environment is torn down and
// fail the run with an EnvironmentTeardownError (flaky in CI).
vi.mock("@/router", () => ({
    routeChange: {
        gotoLogin: vi.fn(),
        viewProjects: vi.fn(),
        changePassword: vi.fn(),
        notAllowed: vi.fn()
    },
    default: { push: vi.fn() }
}));

vi.mock("swrv", () => ({
    // Invoke the key function like real swrv does, so the page's key builders
    // (e.g. the `/users/${email}` lookup) are exercised rather than skipped.
    default: (keyFn?: unknown) => {
        if (typeof keyFn === "function") keyFn();

        return {
            data: ref(null),
            mutate: vi.fn(),
            error: ref(null)
        };
    }
}));

vi.mock("@vueuse/core", () => ({
    useDark: () => ref(false),
    useToggle: () => vi.fn(),
    whenever: vi.fn()
}));

vi.mock("@/services/project-service", () => ({ getProject: vi.fn() }));

vi.mock("@/utils/snackbar", () => ({
    Snackbar: {
        success: vi.fn(),
        error: vi.fn()
    }
}));

function mountMainLayout(options: {
    permissions?: string[];
    email?: string;
    bannerEnabled?: boolean;
    bannerMessage?: string;
    deploymentMode?: boolean;
    hasError?: boolean;
    routePath?: string;
    routeName?: string;
    routeParams?: Record<string, string>;
} = {}) {
    const {
        permissions = [],
        email = "test@example.com",
        bannerEnabled,
        bannerMessage = "Test banner",
        deploymentMode = false,
        hasError = false,
        routePath = "/",
        routeName = "Home",
        routeParams = {}
    } = options;

    mockRoute.name = routeName;
    mockRoute.fullPath = routePath;
    mockRoute.path = routePath;
    mockRoute.params = routeParams;

    const banner = bannerEnabled !== undefined
        ? {
            message: bannerMessage,
            link: "",
            enabled: bannerEnabled
        }
        : undefined;

    return mount(MainLayout, {
        global: {
            plugins: [
                createTestingPinia({
                    createSpy: vi.fn,
                    stubActions: false,
                    initialState: {
                        auth: {
                            user: {
                                username: "testuser",
                                userId: "1",
                                attributes: {
                                    sub: "1",
                                    email
                                },
                                permissions
                            },
                            signInStep: "DONE"
                        },
                        siteDetails: {
                            banner,
                            deploymentMode
                        },
                        error: { hasError },
                        modals: {
                            createProjectOpen: false,
                            createModelOpen: false
                        },
                        siteSettings: { darkMode: false }
                    }
                })
            ],
            stubs: {
                AiBanner: { template: "<div data-test='banner' />" },
                AiMainNavigation: true,
                AiHeader: { template: "<div data-test='header'><slot /></div>" },
                AiUserDropdown: {
                    template: "<div data-test='user-dropdown' />",
                    props: ["isDark", "emailAddress", "role"]
                },
                AiErrorAlert: { template: "<div data-test='error-alert' />" },
                AiLoader: { template: "<div data-test='loader' />" },
                DeploymentMode: { template: "<div data-test='deployment-mode' />" },
                CreateModelModal: true,
                "router-view": {
                    template: "<div><slot :Component=\"comp\" /></div>",
                    data() { return { comp: { template: "<div>stub</div>" } }; }
                },
                transition: { template: "<div><slot /></div>" },
                Transition: { template: "<div><slot /></div>" }
            }
        }
    });
}

describe("MainLayout", () => {
    beforeEach(() => {
        mockRouterPush.mockReset();
    });

    describe("rendering", () => {
        it("mounts without errors", () => {
            const wrapper = mountMainLayout();

            expect(wrapper.exists()).toBe(true);
        });
    });

    describe("userRole", () => {
        it("returns Admin when user has CanAccessAdminPanel permission", () => {
            const wrapper = mountMainLayout({ permissions: ["CanAccessAdminPanel"] });
            const dropdown = wrapper.findComponent("[data-test='user-dropdown']") as VueWrapper;

            expect((dropdown.props() as Record<string, unknown>).role).toBe("Admin");
        });

        it("returns Researcher when user has CanCreateProjects but not CanAccessAdminPanel", () => {
            const wrapper = mountMainLayout({ permissions: ["CanCreateProjects"] });
            const dropdown = wrapper.findComponent("[data-test='user-dropdown']") as VueWrapper;

            expect((dropdown.props() as Record<string, unknown>).role).toBe("Researcher");
        });

        it("returns Viewer when user has no management permissions", () => {
            const wrapper = mountMainLayout({ permissions: [] });
            const dropdown = wrapper.findComponent("[data-test='user-dropdown']") as VueWrapper;

            expect((dropdown.props() as Record<string, unknown>).role).toBe("Viewer");
        });

        it("prioritises Admin over Researcher when user has both permissions", () => {
            const wrapper = mountMainLayout({ permissions: ["CanAccessAdminPanel", "CanManageProjects"] });
            const dropdown = wrapper.findComponent("[data-test='user-dropdown']") as VueWrapper;

            expect((dropdown.props() as Record<string, unknown>).role).toBe("Admin");
        });
    });

    describe("banner visibility", () => {
        it("shows AiBanner when banner is enabled", () => {
            const wrapper = mountMainLayout({ bannerEnabled: true });

            expect(wrapper.find("[data-test='banner']").exists()).toBe(true);
        });

        it("hides AiBanner when banner is not enabled", () => {
            const wrapper = mountMainLayout({ bannerEnabled: false });

            expect(wrapper.find("[data-test='banner']").exists()).toBe(false);
        });

        it("hides AiBanner when banner is undefined", () => {
            const wrapper = mountMainLayout();

            expect(wrapper.find("[data-test='banner']").exists()).toBe(false);
        });
    });

    describe("error alert visibility", () => {
        it("shows AiErrorAlert when errorStore.hasError is true", () => {
            const wrapper = mountMainLayout({ hasError: true });

            expect(wrapper.find("[data-test='error-alert']").exists()).toBe(true);
        });

        it("hides AiErrorAlert when errorStore.hasError is false", () => {
            const wrapper = mountMainLayout({ hasError: false });

            expect(wrapper.find("[data-test='error-alert']").exists()).toBe(false);
        });
    });

    describe("deployment mode", () => {
        it("shows DeploymentMode when deploymentMode is true on non-admin route", () => {
            const wrapper = mountMainLayout({
                deploymentMode: true,
                routePath: "/"
            });

            expect(wrapper.find("[data-test='deployment-mode']").exists()).toBe(true);
        });

        it("hides DeploymentMode on admin routes even when deploymentMode is true", () => {
            const wrapper = mountMainLayout({
                deploymentMode: true,
                routePath: "/admin/users"
            });

            expect(wrapper.find("[data-test='deployment-mode']").exists()).toBe(false);
        });
    });

    describe("signOut", () => {
        it("calls authStore.signOut when sign-out is emitted", async () => {
            const wrapper = mountMainLayout();
            const dropdown = wrapper.findComponent("[data-test='user-dropdown']") as VueWrapper;

            await dropdown.vm.$emit("sign-out");
            await wrapper.vm.$nextTick();

            const { useAuthStore } = await import("@/store/auth");
            const authStore = useAuthStore();

            expect(authStore.signOut).toHaveBeenCalled();
        });
    });

    describe("project fetch reactions", () => {
        it("setProject + hasProject when data arrives (whenever data → callback)", async () => {
            // `whenever` is stubbed to vi.fn() so callbacks don't auto-fire — pull
            // the captured callback out of the mock's first call (data arm) and
            // run it manually, then assert the store side-effect.
            mountMainLayout({
                routePath: "/project/abc",
                routeParams: { projectId: "abc" }
            });

            const { whenever } = await import("@vueuse/core");
            const dataCallback = (whenever as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1];
            dataCallback();

            const { useProjectStore } = await import("@/store/project");
            const projectStore = useProjectStore();
            // setProject is a Pinia action; createTestingPinia spies on it.
            expect(projectStore.setProject).toHaveBeenCalled();
        });

        it("routes to / and shows an error snackbar when getProject errors (whenever error → callback)", async () => {
            mountMainLayout({
                routePath: "/project/abc",
                routeParams: { projectId: "abc" }
            });

            const { whenever } = await import("@vueuse/core");
            const errorCallback = (whenever as unknown as ReturnType<typeof vi.fn>).mock.calls[1][1];
            errorCallback();

            const { Snackbar } = await import("@/utils/snackbar");
            expect(Snackbar.error).toHaveBeenCalledWith(
                expect.objectContaining({ title: "Not found" })
            );
            expect(mockRouterPush).toHaveBeenCalledWith({ path: "/" });
        });

        it("clears the project store when the route leaves /project/", async () => {
            // Mount on a non-project page → the immediate watch falls into the
            // `else` branch and clears the store.
            mountMainLayout({ routePath: "/projects" });

            const { useProjectStore } = await import("@/store/project");
            const projectStore = useProjectStore();
            expect(projectStore.clearProject).toHaveBeenCalled();
        });
    });
});
