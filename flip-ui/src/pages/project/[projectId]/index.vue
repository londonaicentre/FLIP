<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

<!-- eslint-disable vue/multi-word-component-names -->
<route lang="yaml">
    name: Project
</route>

<template>
    <div v-if="project" class="relative flex flex-col h-full overflow-hidden">
        <div class="flex-grow h-full overflow-y-auto">
            <header class="px-6 pt-4">
                <router-link
                    to="/projects"
                    class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 hover:text-primary-500 dark:text-gray-300 dark:hover:text-primary-300"
                >
                    Projects
                </router-link>
                <div class="flex flex-wrap items-center justify-between gap-4 mt-2">
                    <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100 truncate">
                        <span class="max-w-2xl truncate">{{ project.name }}</span>
                    </h1>
                    <div class="flex flex-row items-center md:gap-2 shrink-0 grow md:justify-end">
                        <div class="flex flex-col gap-4 md:flex-row">
                            <template v-if="isProjectStaged()">
                                <div class="flex items-center flex-shrink-0 gap-3 text-sm">
                                    <AiCircledIcon>
                                        <icon-heroicons-outline-clock class="w-4 h-4" />
                                    </AiCircledIcon>
                                    Awaiting Approval
                                </div>
                            </template>
                            <div class="flex items-center flex-shrink-0 gap-3 mr-2 text-sm">
                                <AiCircledIcon>
                                    <icon-ic-twotone-person-outline class="w-4 h-4" />
                                </AiCircledIcon>
                                {{ getUserCountMessage() }}
                            </div>
                        </div>
                        <div class="flex flex-col items-end justify-end gap-2 grow md:grow-0 md:flex-row">
                            <AiGuard :permissions="unstageProjectPermissions">
                                <template v-if="isProjectStaged()">
                                    <AiButton primary data-test="unstage-project-btn" @click="openUnstagingModal">
                                        <icon-heroicons-outline-clipboard class="mr-2" />
                                        Unstage Project
                                    </AiButton>
                                    <AiConfirmModal
                                        :dialog="unstageProjectOpen"
                                        small
                                        title="Unstage Project"
                                        description="Are you sure you want to unstage the project?"
                                        continue-button-text="Unstage Project"
                                        :submitting="unstagingProject"
                                        :continue-action="unstageCurrentProject"
                                        @close-modal="closeUnstageModal"
                                    />
                                </template>
                            </AiGuard>
                            <AiGuard :permissions="editProjectPermissions" :bypass="isOwnerOrHasAccess() || isViewer">
                                <AiButton light data-test="edit-project-btn" @click.capture="openEditProjectDrawer">
                                    <icon-mdi-pencil-outline v-if="!isViewer" class="mr-2" />
                                    <icon-mdi-eye-outline v-else class="mr-2" />
                                    {{ isViewer ? "View Project" : "Edit Project" }}
                                </AiButton>
                            </AiGuard>
                        </div>
                    </div>
                </div>
            </header>

            <div class="relative grid items-start grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(17rem,0.75fr)_minmax(22rem,1.5fr)_minmax(17rem,0.75fr)] xl:p-4">
                <div class="lg:col-span-3">
                    <LifecycleTrack :steps="steps" />
                </div>

                <div class="grid grid-cols-1 gap-4 lg:sticky top-16">
                    <QueryDetails :query-details="project.query" />

                    <Transition name="slidedown" mode="out-in">
                        <template v-if="isProjectUnstaged()">
                            <ProjectStaging
                                :has-query="!!project.query"
                                :stageable-trust-ids="stageableTrustIds"
                                :project-staged="isProjectStaged()"
                                :staging="stagingProject"
                                @staged="stageProject"
                            />
                        </template>
                        <template v-else>
                            <ProjectApproval
                                :approved-trusts="project.approvedTrusts ?? []"
                                :has-query="!!project.query"
                                :project-approved="projectApproved"
                                :approving="approvingProject"
                                :can-approve="isProjectStaged()"
                                @approve-project="approveProjectEvent"
                            />
                        </template>
                    </Transition>
                </div>

                <div class="lg:sticky top-16">
                    <ProjectStatus
                        :can-load="projectApproved"
                        :cohort-size="project.query?.totalCohort"
                    />
                </div>

                <div class="lg:sticky top-16">
                    <LatestModels />
                </div>
            </div>
        </div>
        <EditProjectDrawer
            :id="project.id"
            :show="editDrawerOpen"
            :name="project.name"
            :users="project.users"
            :project-unstaged="isProjectUnstaged() && !isViewer"
            :description="project.description"
            :dicom-to-nifti="project.dicom_to_nifti ?? true"
            :updating="projectUpdating"
            :owner-id="project.ownerId"
            @close="closeEditProjectDrawer"
            @save="updateProjectEvent"
        />
    </div>
</template>

<script setup lang="ts">
import { storeToRefs } from "pinia";
import { computed, ref } from "vue";
import { useRoute } from "vue-router";

import AiButton from "@/components/AiButton/AiButton.vue";
import AiGuard from "@/components/AiGuard/AiGuard.vue";
import AiCircledIcon from "@/components/AiIcon/AiCircledIcon.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { usePermissions } from "@/composables/usePermissions";
import { IStep } from "@/interfaces/steps";
import QueryDetails from "@/partials/cohort-query/QueryDetails.vue";
import LatestModels from "@/partials/models/LatestModels.vue";
import EditProjectDrawer, { IEditProject } from "@/partials/projects/EditProjectDrawer.vue";
import LifecycleTrack from "@/partials/projects/LifecycleTrack.vue";
import ProjectApproval from "@/partials/projects/ProjectApproval.vue";
import ProjectStaging from "@/partials/projects/ProjectStaging.vue";
import ProjectStatus from "@/partials/projects/ProjectStatus.vue";
import { approveProject, editProject, stageProject as stageProjectWithTrusts, unstageProject } from "@/services/project-service";
import { useAuthStore, UserPermissions } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { useProjectStore } from "@/store/project";
import { Snackbar } from "@/utils/snackbar";

export interface ITrustToStage {
    id: string;
    name: string;
    staged: boolean;
}

const route = useRoute();
const authStore = useAuthStore();
const errorStore = useErrorStore();
const projectStore = useProjectStore();

const editDrawerOpen = ref(false);
const stageProjectOpen = ref(false);
const stagingProject = ref(false);
const unstageProjectOpen = ref(false);
const projectUpdating = ref(false);
const approvingProject = ref(false);
const unstagingProject = ref(false);

// Most-recent per-trust approval (sourced server-side from
// project_trust_intersect.approved_at) doubles as the project-level
// approval date in the lifecycle bar.
const latestTrustApprovalAt = computed<string | null>(() => {
    const trusts = (project?.value?.approvedTrusts ?? []).filter(t => t.approved && t.approvedAt);
    if (!trusts.length) return null;

    return trusts
        .map(t => t.approvedAt as string)
        .sort()
        .at(-1) ?? null;
});

const steps = computed((): IStep[] => [
    {
        id: "01",
        name: "Project Created",
        completed: true,
        date: project?.value?.creationtimestamp ?? null
    },
    {
        id: "02",
        name: "Cohort Query",
        completed: !!project?.value?.query?.id,
        date: project?.value?.query?.created ?? null
    },
    {
        id: "03",
        name: "Project Staged",
        completed: project?.value?.status !== "UNSTAGED",
        // stagedAt persists after an unstage (it's the most-recent STAGE audit), so only surface it
        // while the project is actually staged — otherwise the now-incomplete step shows a stale date.
        date: project?.value?.status !== "UNSTAGED" ? (project?.value?.stagedAt ?? null) : null
    },
    {
        id: "04",
        name: "Project Approved",
        completed: project?.value?.status === "APPROVED",
        date: latestTrustApprovalAt.value
    }
]);

const emit = defineEmits(["UpdateProject"]);

const { project } = storeToRefs(projectStore);

// Researchers (CanCreateProjects) can edit projects they own — backend
// per-project access checks gate the actual writes. CanManageProjects is
// Admin-only and bypasses the per-project check on the server.
const editProjectPermissions: UserPermissions[] = ["CanCreateProjects"];
const unstageProjectPermissions: UserPermissions[] = ["CanUnstageProjects"];
const { isViewer, canCreateProjects } = usePermissions();

const projectApproved = computed(() => {
    return project?.value?.status === "APPROVED";
});

// Stageable = trusts that responded with a usable cohort. Excludes
// late-joiners (not dispatched), never-responded (dispatched but no
// QueryResult), errored (responded with an error blob), and empty
// (responded with 0 records — genuine zero or privacy-suppressed, #519).
// Mirrors the server-side guards in stage_project.py.
const stageableTrustIds = computed<string[] | undefined>(() => {
    const q = project?.value?.query;
    if (!q) return undefined;
    const excluded = new Set([...(q.erroredTrustIds ?? []), ...(q.emptyTrustIds ?? [])]);

    return (q.respondedTrustIds ?? []).filter((id) => !excluded.has(id));
});

const isOwnerOrHasAccess = () => {
    if (!canCreateProjects.value) return false;
    const projectOwner = project?.value?.ownerId;
    const currentUserId = authStore.user?.userId;

    return projectOwner === currentUserId || project?.value?.users?.map(u => u.id).includes(currentUserId as string);
};

const getUserCountMessage = () => {
    const userCount = project?.value?.users?.length ?? 0;

    return `${userCount + 1} ${userCount + 1 === 1 ? "User" : "Users"}`;
};

const openEditProjectDrawer = () => {
    editDrawerOpen.value = true;
};

const openUnstagingModal = () => {
    unstageProjectOpen.value = true;
};

const closeUnstageModal = () => {
    unstageProjectOpen.value = false;
};

const closeEditProjectDrawer = () => {
    editDrawerOpen.value = false;
};

const updateProjectEvent = async (update: IEditProject) => {
    projectUpdating.value = true;

    const { name } = { ...project?.value };

    try {
        await editProject(`/projects/${project?.value?.id}`, update);

        Snackbar.success({
            title: "Project Updated",
            text: "This project has been updated."
        });

        emit("UpdateProject");
    }
    catch {
        Snackbar.error({
            title: "Unable to update project",
            text: `${name} has not been updated.`
        });

        errorStore.setError();
    }
    editDrawerOpen.value = false;
    projectUpdating.value = false;
};

const approveProjectEvent = async (ids: string[]) => {
    approvingProject.value = true;

    const { name } = { ...project?.value };

    try {
        // If it is only one trust, add to an array
        const arr: string[] = [];
        const trustList = arr.concat(ids);

        await approveProject(`/step/project/${route.params.projectId}/approve`, trustList);

        Snackbar.success({
            title: "Project Approved",
            text: `${name} has been approved.`
        });

        emit("UpdateProject");
    }
    catch {
        Snackbar.error({
            title: "Unable to approve project",
            text: `${name} has not been approved.`
        });

        errorStore.setError();
    }

    approvingProject.value = false;
};

const stageProject = async (trustIds: string[]) => {

    if(stagingProject.value) {
        return;
    }

    stagingProject.value = true;

    if (!trustIds.length) {
        return;
    }

    try {
        await stageProjectWithTrusts(`/projects/${project?.value?.id}/stage`, trustIds);
        emit("UpdateProject");
    } catch {
        Snackbar.error({
            title: "Unable to stage project",
            text: "There was a problem staging this project, please try again."
        });
    }

    stageProjectOpen.value = false;
    stagingProject.value = false;

};

const unstageCurrentProject = async () => {
    try {
        if (unstagingProject.value) {
            return;
        }

        unstagingProject.value = true;

        await unstageProject(`/projects/${project?.value?.id}/unstage`);

        emit("UpdateProject");

        Snackbar.success({
            title: "Project Unstaged",
            text: "The project has been unstaged."
        });

    } catch {
        Snackbar.error({
            title: "Unable to unstage project",
            text: "There was a problem unstaging this project, please try again."
        });
    }

    unstagingProject.value = false;
    unstageProjectOpen.value = false;
};

const isProjectStaged = () => {
    return project?.value?.status === "STAGED";
};

const isProjectUnstaged = () => {
    return project?.value?.status === "UNSTAGED";
};
</script>
