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
        <AiBreadcrumbs :pages="breadcrumbPages" :current="{name: project.name}" />
        <div class="flex-grow h-full overflow-y-auto">
            <div class="flex items-center justify-end flex-shrink-0 px-4 py-4 space-x-4 bg-white shadow-sm dark:bg-gray-900">
                <div class="flex flex-row items-center gap-4">
                    <div
                        class="relative flex flex-col items-center justify-end w-10 h-10 transition bg-white rounded-full dark:bg-gray-900 ring-2 ring-offset-2 dark:ring-offset-gray-900 shrink-0"
                        :class="[
                            project.status === 'APPROVED' && 'ring-green-600/70 dark:ring-green-400',
                            project.status === 'STAGED' && 'ring-primary-600/70 dark:ring-primary-400',
                            project.status === 'UNSTAGED' && 'ring-gray-400/70 dark:ring-gray-600',
                        ]"
                    >
                        <div class="relative flex items-center justify-center w-full bg-gray-100 border border-gray-300 rounded-full shadow dark:bg-gray-800 dark:text-gray-300 grow dark:border-gray-500">
                            {{ getInitials(project.name) }}
                        </div>
                    </div>
                    <div class="hidden text-lg font-semibold truncate md:flex font-heading grow">
                        <span class="max-w-lg truncate">{{ project.name }}</span>
                    </div>
                </div>

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
                        <template v-if="projectApproved">
                            <div class="flex items-center flex-shrink-0 gap-3 text-sm">
                                <AiCircledIcon>
                                    <icon-heroicons-outline-check class="w-4 h-4" />
                                </AiCircledIcon>
                                Project Approved
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
                        <AiGuard :permissions="editProjectPermissions" :bypass="isOwnerOrHasAccess()">
                            <AiButton light data-test="edit-project-btn" @click.capture="openEditProjectDrawer">
                                <icon-mdi-pencil-outline class="mr-2" />
                                Edit Project
                            </AiButton>
                        </AiGuard>
                    </div>
                </div>
            </div>

            <AiSteps :steps="steps" />

            <div class="relative grid items-start grid-cols-1 gap-4 p-4 lg:grid-cols-12 xl:p-4">
                <!-- Left column -->
                <div class="grid grid-cols-1 gap-4 lg:sticky top-16 lg:col-span-4 2xl:col-span-3">
                    <ProjectDetails />
                    <QueryDetails
                        :query-details="project.query"
                        class="order-2 2xl:order-3"
                    />
                </div>

                <!-- Middle Column -->
                <div class="sticky grid grid-cols-1 gap-4 lg:col-span-8 top-16 2xl:col-span-6">
                    <div class="grid grid-cols-1 gap-4 2xl:grid-cols-1">
                        <Transition name="slidedown" mode="out-in">
                            <template v-if="isProjectUnstaged()">
                                <ProjectStaging
                                    :has-query="!!project.query"
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
                        <div class="2xl:hidden">
                            <LatestModels />
                        </div>
                    </div>
                    <ProjectStatus :can-load="projectApproved" />
                </div>

                <!-- Right column -->
                <div class="sticky hidden grid-cols-1 gap-4 2xl:grid 2xl:col-span-3 lg:-order-none top-16">
                    <span class="hidden 2xl:block">
                        <LatestModels />
                    </span>
                </div>
            </div>
        </div>
        <EditProjectDrawer
            :id="project.id"
            :show="editDrawerOpen"
            :name="project.name"
            :users="project.users"
            :project-unstaged="isProjectUnstaged()"
            :description="project.description"
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

import AiBreadcrumbs, { IPage } from "@/components/AiBreadcrumbs/AiBreadcrumbs.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiGuard from "@/components/AiGuard/AiGuard.vue";
import AiCircledIcon from "@/components/AiIcon/AiCircledIcon.vue";
import AiConfirmModal from "@/components/AiModal/AiConfirmModal.vue";
import { IStep } from "@/components/AiSteps/AiSteps.vue";
import QueryDetails from "@/partials/cohort-query/QueryDetails.vue";
import LatestModels from "@/partials/models/LatestModels.vue";
import EditProjectDrawer, { IEditProject } from "@/partials/projects/EditProjectDrawer.vue";
import ProjectApproval from "@/partials/projects/ProjectApproval.vue";
import ProjectDetails from "@/partials/projects/ProjectDetails.vue";
import ProjectStaging from "@/partials/projects/ProjectStaging.vue";
import ProjectStatus from "@/partials/projects/ProjectStatus.vue";
import { approveProject, editProject, stageProject as stageProjectWithTrusts, unstageProject } from "@/services/project-service";
import { useAuthStore, UserPermissions } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { useProjectStore } from "@/store/project";
import { getInitials } from "@/utils/helpers";
import { Snackbar } from "@/utils/snackbar";

import AiSteps from "../../../components/AiSteps/AiSteps.vue";

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

const breadcrumbPages: IPage[] = [
    {
        name: "Projects",
        path: "/projects"
    }
];

const steps = computed((): IStep[] => [
    {
        id: "01",
        name: "Project Created",
        completed: true
    },
    {
        id: "02",
        name: "Cohort Query Created",
        completed: !!project?.value?.query?.id
    },
    {
        id: "03",
        name: "Project Staged",
        completed: project?.value?.status !== "UNSTAGED"
    },
    {
        id: "04",
        name: "Project Approved",
        completed: project?.value?.status === "APPROVED"
    }
]);

const emit = defineEmits(["UpdateProject"]);

const { project } = storeToRefs(projectStore);

const editProjectPermissions: UserPermissions[] = ["CanManageProjects"];
const unstageProjectPermissions: UserPermissions[] = ["CanUnstageProjects"];

const projectApproved = computed(() => {
    return project?.value?.status === "APPROVED";
});

const isOwnerOrHasAccess = () => {
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
    } catch (e) {
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

    } catch (e) {
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
