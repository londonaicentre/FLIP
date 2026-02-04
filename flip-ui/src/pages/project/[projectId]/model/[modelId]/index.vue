<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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
    name: Model
</route>

<template>
    <template v-if="!modelData">
        <AiLoader />
    </template>
    <div v-else class="relative flex flex-col h-full lg:overflow-hidden">
        <AiBreadcrumbs :pages="breadcrumbPages" :current="{ name: modelData.modelName }" />

        <div class="flex items-center flex-shrink-0 px-4 py-4 space-x-4 bg-white shadow-sm dark:bg-gray-900">
            <div class="flex items-center text-lg font-semibold truncate font-heading grow">
                <span class="max-w-lg truncate">{{ modelData.modelName }}</span>
            </div>
            <div class="flex items-center space-x-8">
                <AiGuard :permissions="editProjectPermissions" :bypass="isOwnerOrHasAccess()">
                    <AiButton light data-test="edit-model-btn" @click="openEditModelDrawer">
                        <icon-mdi-pencil-outline class="mr-2" />
                        Edit Model
                    </AiButton>
                </AiGuard>
            </div>
        </div>

        <AiSteps :steps="steps" />
        <div
            class="flex flex-col flex-1 min-w-0 space-y-4 lg:overflow-hidden grow lg:flex-row lg:space-x-2 lg:space-y-0"
        >
            <aside class="pt-4 pl-4 pr-4 lg:py-4 lg:pr-2 lg:overflow-y-auto">
                <div class="flex flex-col w-full space-y-4 lg:w-80 2xl:min-w-[30rem]">
                    <ModelDetails :model="modelData" />
                    <ModelUpload
                        :files="modelData.files ?? []"
                        :loading="!modelData"
                        :can-upload="!trainingStartedOrStopped"
                        :model-id="modelData.modelId"
                        :required-files="requiredFiles"
                        :job-type="currentJobType"
                        @uploaded="update"
                        @deleted-file="onFileDeleted"
                    />
                    <QueryDetails :query-details="project?.query" />
                </div>
            </aside>

            <div class="flex flex-col flex-1 h-full min-w-0 px-4 pb-4 lg:p-0">
                <div class="h-full space-y-4 lg:py-4 lg:pr-4 lg:pl-0 grow">
                    <Training
                        :can-train="readyToTrain"
                        :status="modelData?.status"
                        :all-files-uploaded="allFilesUploaded"
                        :required-files="requiredFiles"
                        :uploaded-file-names="modelData?.files?.map(f => f.name) ?? []"
                        :job-type="currentJobType"
                        @started="trainingInitialised"
                    />
                </div>
            </div>

            <EditModelDrawer
                :id="modelData.modelId"
                :show="editDrawerOpen"
                :name="modelData.modelName"
                :model-pending="isTrainingPending()"
                :description="modelData.modelDescription"
                :updating="modelUpdating"
                :owner-id="project?.ownerId || ''"
                @close="closeEditModelDrawer"
                @save="updateModelEvent"
            />
        </div>
    </div>
</template>

<script lang="ts" setup>
import useSWRV from "swrv";
import { computed, onBeforeMount, ref, watch } from "vue";
import { useRoute } from "vue-router";

import AiBreadcrumbs, { IPage } from "@/components/AiBreadcrumbs/AiBreadcrumbs.vue";
import AiButton from "@/components/AiButton/AiButton.vue";
import AiGuard from "@/components/AiGuard/AiGuard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import AiSteps, { IStep } from "@/components/AiSteps/AiSteps.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { FileUploadStatus } from "@/interfaces/model/types";
import QueryDetails from "@/partials/cohort-query/QueryDetails.vue";
import EditModelDrawer, { IEditModel } from "@/partials/models/EditModelDrawer.vue";
import ModelDetails from "@/partials/models/ModelDetails.vue";
import ModelUpload from "@/partials/models/ModelUpload.vue";
import Training from "@/partials/models/Training.vue";
import { routeChange } from "@/router";
import { getJobTypeFromConfig } from "@/services/file-service";
import { DEFAULT_JOB_TYPE, editModel, fetchJobTypes, getModel, getRequiredFilesForJobType, ModelStatusEnum, type JobType, type JobTypesResponse } from "@/services/model-service";
import { useAuthStore, UserPermissions } from "@/store/auth";
import { useErrorStore } from "@/store/error";
import { useProjectStore } from "@/store/project";
import { stringArrayContainsAll } from "@/utils/helpers";
import { Snackbar } from "@/utils/snackbar";

const route = useRoute();
const routeParams = route.params;
const modelId = routeParams["modelId"];
const projectStore = useProjectStore();
const project = projectStore.project;
const authStore = useAuthStore();
const errorStore = useErrorStore();

const allFilesUploaded = ref(false);
const allFilesPassScan = ref(false);
const jobTypes = ref<JobTypesResponse>({});
const currentJobType = ref<JobType>(DEFAULT_JOB_TYPE);
const requiredFiles = ref<string[]>([]);
const editProjectPermissions = ref(["CanManageProjects"] as UserPermissions[]);
const editDrawerOpen = ref(false);
const modelUpdating = ref(false);


onBeforeMount(async () => {
    if (!projectStore.isApproved) {
        Snackbar.error({
            title: "Requires Project Approval",
            text: "Unable to view this model as this project is not yet approved."
        });
        routeChange.viewProject(projectStore.getProject?.id ?? "");
        return;
    }
    // Fetch job types from API
    jobTypes.value = await fetchJobTypes();
    // Set default required files
    requiredFiles.value = getRequiredFilesForJobType(jobTypes.value, DEFAULT_JOB_TYPE);
});

const breadcrumbPages: IPage[] = [
    {
        name: "Projects",
        path: "/projects"
    },
    {
        name: projectStore.project?.name ?? "",
        path: `/project/${projectStore.project?.id ?? ""}`
    },
    {
        name: "Models",
        path: `/project/${projectStore.project?.id ?? ""}/models`
    }
];

const { data: modelData, error, mutate } = useSWRV(
    `/step/model/${modelId}`,
    getModel,
    {
        refreshInterval: 5_000,
        dedupingInterval: 5_000,
        shouldRetryOnError: true,
        revalidateOnFocus: false,
        errorRetryCount: 3
    });

useErrorHandler(error);

/**
 * Watch the error state of the project store.
 * If the error state is true, then it will route to the project page.
 */
watch(error, () => {
    if (error.value) {
        if (projectStore.project?.id) {
            routeChange.viewProject(projectStore.project.id);

            Snackbar.warning({
                title: "Model doesn't exist",
                text: "We can not find the requested model. "
            });
        }
    }
});


function getStatusEnumValue(status: string | undefined): number {
    // Map string status (e.g. "PENDING") to ModelStatusEnum value
    if (!status || !(status in ModelStatusEnum)) return ModelStatusEnum.ERROR;
    // @ts-ignore
    return ModelStatusEnum[status];
}

const steps = computed((): IStep[] => {
    const statusValue = getStatusEnumValue(modelData.value?.status);
    return [
        {
            id: "01",
            name: "Model Created",
            completed: true
        },
        {
            id: "02",
            name: "Model Prepared",
            description: statusValue === ModelStatusEnum.INITIATED ? "Model Queued" : undefined,
            inProgress: statusValue === ModelStatusEnum.INITIATED,
            completed: statusValue >= ModelStatusEnum.PREPARED,
            error: statusValue === ModelStatusEnum.ERROR,
            stopped: statusValue === ModelStatusEnum.STOPPED
        },
        {
            id: "03",
            name: "Training Started",
            description:
                (statusValue >= ModelStatusEnum.PREPARED && statusValue < ModelStatusEnum.RESULTS_UPLOADED)
                    ? "In Progress" : undefined,
            inProgress: statusValue >= ModelStatusEnum.PREPARED,
            completed: statusValue > ModelStatusEnum.TRAINING_STARTED,
            error: statusValue === ModelStatusEnum.ERROR,
            stopped: statusValue === ModelStatusEnum.STOPPED
        },
        {
            id: "04",
            name: "Results Uploaded",
            completed: statusValue === ModelStatusEnum.RESULTS_UPLOADED,
            error: statusValue === ModelStatusEnum.ERROR,
            stopped: statusValue === ModelStatusEnum.STOPPED
        }
    ];
});


const readyToTrain = computed(() => {
    return !trainingStartedOrStopped.value
        && allFilesUploaded.value
        && allFilesPassScan.value
        && !!modelData.value?.query;
});

const trainingStartedOrStopped = computed(() => {
    const statusValue = getStatusEnumValue(modelData.value?.status);
    return statusValue > ModelStatusEnum.PENDING ||
        statusValue === ModelStatusEnum.ERROR ||
        statusValue === ModelStatusEnum.STOPPED;
});

watch([modelData, jobTypes], async () => {
    if (!modelData.value || !Object.keys(jobTypes.value).length) return;
    if (modelData.value?.files?.length) {
        // Check if config.json exists in uploaded files and is fully scanned
    const configFile = modelData.value.files.find((f: { name: string; status: string }) => f.name === "config.json");
        const hasCompletedConfigJson = configFile && configFile.status === FileUploadStatus.COMPLETED;
        let jobType = DEFAULT_JOB_TYPE;
        if (hasCompletedConfigJson) {
            // Fetch job type from config.json (only if file is ready)
            jobType = await getJobTypeFromConfig(modelData.value.modelId, jobTypes.value);
        }
        currentJobType.value = jobType;
        requiredFiles.value = getRequiredFilesForJobType(jobTypes.value, jobType);
        allFilesUploaded.value = stringArrayContainsAll(
            modelData.value.files.map((f: { name: string }) => f.name),
            requiredFiles.value
        );
        allFilesPassScan.value = modelData.value.files.every((f: { status: string }) => f.status === FileUploadStatus.COMPLETED);
    }
}, { immediate: true });


const update = () => {
    mutate();
};

const onFileDeleted = () => {
    // Re-fetch model data and trigger job type/required files logic
    update();
};

const trainingInitialised = () => {
    if (modelData.value?.status) {
        modelData.value.status = "INITIATED";
    }
};

const isTrainingPending = () => {
    return modelData.value?.status === "PENDING";
};

const isOwnerOrHasAccess = () => {
    const projectOwner = project?.ownerId;
    const currentUserId = authStore.user?.userId;

    return projectOwner === currentUserId ||
        project?.users?.map((u: { id: string }) => u.id).includes(currentUserId as string);
};

const openEditModelDrawer = () => {
    editDrawerOpen.value = true;
};

const closeEditModelDrawer = () => {
    editDrawerOpen.value = false;
};

const updateModelEvent = async (updated: IEditModel) => {
    modelUpdating.value = true;
    try {
        await editModel(`/model/${modelData.value?.modelId}`, updated);
        await update();

        Snackbar.success({
            title: "Model Updated",
            text: "This model has been updated."
        });
    } catch {
        Snackbar.error({
            title: "Unable to update model",
            text: `${modelData.value?.modelName} has not been updated.`
        });

        errorStore.setError();
    }
    modelUpdating.value = false;
    editDrawerOpen.value = false;
};
</script>
