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
    name: Model
</route>

<template>
    <template v-if="!modelData">
        <AiLoader />
    </template>
    <div v-else class="relative flex flex-col h-full overflow-hidden">
        <div class="flex-grow h-full overflow-y-auto">
            <header class="px-6 pt-4">
                <router-link
                    to="/projects"
                    class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 hover:text-primary-500 dark:text-gray-400 dark:hover:text-primary-300"
                >
                    Projects
                </router-link>
                <template v-if="project">
                    <span class="mx-1 text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 dark:text-gray-400">/</span>
                    <router-link
                        :to="`/project/${project.id}`"
                        class="text-xs font-semibold tracking-wider uppercase font-mono text-gray-500 hover:text-primary-500 dark:text-gray-400 dark:hover:text-primary-300"
                    >
                        {{ project.name }}
                    </router-link>
                </template>
                <div class="flex items-center justify-between gap-4 mt-2">
                    <h1 class="text-3xl font-semibold font-heading mt-1 text-gray-900 dark:text-gray-100 truncate">
                        <span class="max-w-2xl truncate">{{ modelData.modelName }}</span>
                    </h1>
                    <div class="flex items-center gap-3 shrink-0">
                        <AiGuard v-if="!isViewer" :permissions="editProjectPermissions" :bypass="isOwnerOrHasAccess()">
                            <AiButton light data-test="edit-model-btn" @click="openEditModelDrawer">
                                <icon-mdi-pencil-outline class="mr-2" />
                                Edit Model
                            </AiButton>
                        </AiGuard>
                        <AiButton
                            v-if="!isViewer && isTrainingPending()"
                            primary
                            data-test="initiate-training-btn"
                            :disabled="!readyToTrain"
                            :loading="trainingRef?.isSubmitting ?? false"
                            @click="trainingRef?.initiateTraining()"
                        >
                            Initiate Training
                        </AiButton>
                        <TrainingActionsMenu v-if="!isViewer && !isTrainingPending()" :status="getStatusEnumValue(modelData?.status)" />
                    </div>
                </div>
            </header>

            <div class="flex flex-col gap-4 p-4">
                <LifecycleTrack :steps="steps" />

                <div class="flex flex-col gap-4 lg:flex-row lg:gap-4">
                    <aside class="lg:w-80 2xl:min-w-[30rem] shrink-0">
                        <ModelUpload
                            :files="modelData.files ?? []"
                            :loading="!modelData"
                            :can-upload="!trainingStartedOrStopped && !isViewer"
                            :model-id="modelData.modelId"
                            :required-files="requiredFiles"
                            :job-type="currentJobType"
                            @uploaded="update"
                            @deleted-file="onFileDeleted"
                        />
                    </aside>

                    <div class="flex-1 min-w-0">
                        <Training
                            ref="trainingRef"
                            :can-train="readyToTrain"
                            :status="modelData?.status"
                            :all-files-uploaded="allFilesUploaded"
                            :required-files="requiredFiles"
                            :uploaded-file-names="modelData?.files?.map(f => f.name) ?? []"
                            :job-type="currentJobType"
                            :fl-backend-label="flBackendLabel"
                            @started="trainingInitialised"
                        />
                    </div>
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

import AiButton from "@/components/AiButton/AiButton.vue";
import AiGuard from "@/components/AiGuard/AiGuard.vue";
import AiLoader from "@/components/AiLoader/AiLoader.vue";
import { IStep } from "@/components/AiSteps/AiSteps.vue";
import useErrorHandler from "@/composables/useErrorHandler";
import { usePermissions } from "@/composables/usePermissions";
import { FileUploadStatus } from "@/interfaces/model/types";
import EditModelDrawer, { IEditModel } from "@/partials/models/EditModelDrawer.vue";
import ModelUpload from "@/partials/models/ModelUpload.vue";
import Training from "@/partials/models/Training.vue";
import TrainingActionsMenu from "@/partials/models/TrainingActionsMenu.vue";
import LifecycleTrack from "@/partials/projects/LifecycleTrack.vue";
import { routeChange } from "@/router";
import { resolveModelConfigState } from "@/services/file-service";
import { getFLStatus } from "@/services/fl-service";
import { DEFAULT_JOB_TYPE, editModel, fetchJobTypes, getModel, getRequiredFilesForJobType, type JobType, type JobTypesResponse, ModelStatusEnum } from "@/services/model-service";
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
const { isViewer } = usePermissions();

// Bridge to Training.vue: the form lives there (vee-validate context wraps
// TrainingOptions) but the submit button lives in the page header. Page calls
// `initiateTraining()` on this ref to fire the form's native submit, which
// vee-validate intercepts and routes through the existing schema + handler.
const trainingRef = ref<InstanceType<typeof Training> | null>(null);

const allFilesUploaded = ref(false);
const allFilesPassScan = ref(false);
const jobTypes = ref<JobTypesResponse>({});
const currentJobType = ref<JobType>(DEFAULT_JOB_TYPE);
const resolvedConfigFileStatus = ref<FileUploadStatus | null>(null);
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

const { data: flStatus } = useSWRV(
    "fl/status",
    getFLStatus,
    {
        dedupingInterval: 5_000,
        shouldRetryOnError: false
    }
);

const formatBackend = (backend: "nvflare" | "flower") =>
    backend === "nvflare" ? "NVFlare" : "Flower";

const flBackendLabel = computed(() => {
    const backend = Array.isArray(flStatus.value)
        ? flStatus.value.find(net => net.fl_backend)?.fl_backend
        : undefined;

    return backend ? formatBackend(backend) : undefined;
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

    // @ts-expect-error indexing the enum by a string key already guarded by the `status in ModelStatusEnum` check above
    return ModelStatusEnum[status];
}

const steps = computed((): IStep[] => {
    const statusValue = getStatusEnumValue(modelData.value?.status);
    const isStopped = statusValue === ModelStatusEnum.STOPPED;
    const isError = statusValue === ModelStatusEnum.ERROR;

    // When training is stopped or errors, prior completed steps should
    // remain marked as completed (✅) rather than showing 🚫.
    // A stopped/errored model must have been at least PREPARED, so
    // "Model Prepared" stays completed and only later steps show the
    // stopped/error indicator.  See issue #29.
    return [
        {
            id: "01",
            name: "Model Created",
            completed: true,
            date: modelData.value?.creationTimestamp ?? null
        },
        {
            id: "02",
            name: "Model Prepared",
            description: statusValue === ModelStatusEnum.INITIATED ? "Model Queued" : undefined,
            inProgress: statusValue === ModelStatusEnum.INITIATED,
            completed: statusValue >= ModelStatusEnum.PREPARED || isStopped || isError,
            date: modelData.value?.preparedAt ?? null
        },
        {
            id: "03",
            name: "Training",
            description:
                (statusValue >= ModelStatusEnum.PREPARED && statusValue < ModelStatusEnum.RESULTS_UPLOADED)
                    ? "In Progress" : undefined,
            inProgress: statusValue >= ModelStatusEnum.PREPARED && !isStopped && !isError,
            completed: statusValue > ModelStatusEnum.TRAINING_STARTED,
            error: isError,
            stopped: isStopped,
            date: modelData.value?.trainingStartedAt ?? null
        },
        {
            id: "04",
            name: "Results Uploaded",
            completed: statusValue === ModelStatusEnum.RESULTS_UPLOADED,
            date: modelData.value?.resultsUploadedAt ?? null
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
        const resolved = await resolveModelConfigState(
            modelData.value.files,
            resolvedConfigFileStatus.value,
            jobTypes.value,
            modelData.value.modelId
        );
        if (resolved.changed) {
            resolvedConfigFileStatus.value = resolved.configStatus;
            currentJobType.value = resolved.jobType;
            requiredFiles.value = resolved.requiredFiles;
        }

        allFilesUploaded.value = stringArrayContainsAll(
            modelData.value.files.map((f: { name: string }) => f.name),
            requiredFiles.value
        );
        allFilesPassScan.value = modelData.value.files.every(
            (f: { status: string }) => f.status === FileUploadStatus.COMPLETED
        );
    }
}, { immediate: true });


const update = () => {
    mutate();
};

const onFileDeleted = () => {
    // A new config.json after deletion may declare a different job_type;
    // clear the cached status so the next poll re-resolves required files.
    resolvedConfigFileStatus.value = null;
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
