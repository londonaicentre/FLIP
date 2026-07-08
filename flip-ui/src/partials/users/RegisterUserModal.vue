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

<template>
    <TransitionRoot as="template" :show="dialog">
        <Dialog as="div" class="fixed inset-0 z-10" @close.capture="close">
            <div class="flex items-center justify-center h-screen min-h-screen p-4 text-center sm:block sm:p-0">
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0"
                    enter-to="opacity-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100"
                    leave-to="opacity-0"
                >
                    <AiDialogOverlay />
                </TransitionChild>
                <!-- This element is to trick the browser into centering the modal contents. -->
                <span class="inline-block align-middle sm:h-screen" aria-hidden="true">&#8203;</span>
                <TransitionChild
                    as="template"
                    enter="ease-out duration-300"
                    enter-from="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                    enter-to="opacity-100 translate-y-0 sm:scale-100"
                    leave="ease-in duration-200"
                    leave-from="opacity-100 translate-y-0 sm:scale-100"
                    leave-to="opacity-0 translate-y-4 sm:translate-y-0 sm:scale-95"
                >
                    <div
                        class="inline-flex flex-col w-full max-w-lg max-h-screen p-4 text-left align-middle rounded-lg"
                    >
                        <div
                            class="inline-flex flex-col w-full transition-all transform bg-white rounded-lg shadow-xl dark:bg-dark-surface"
                        >
                            <DialogTitle
                                as="h3"
                                class="px-8 py-4 text-lg font-bold leading-6 text-left text-gray-700 dark:text-gray-300"
                            >
                                {{ title }}
                            </DialogTitle>
                            <div class="flex flex-grow overflow-y-auto bg-white dark:bg-dark-surface">
                                <div class="flex flex-col items-start w-full">
                                    <div class="w-full text-left">
                                        <div class="w-full px-8 py-4 space-y-4 overflow-y-auto text-sm font-normal leading-5 dark:text-gray-300">
                                            <p>The new user will be sent a temporary password.</p>
                                            <AiInput
                                                v-if="!lockIdentity"
                                                class="mt-2"
                                                data-test="name-field"
                                                type="text"
                                                name="name"
                                                placeholder="Name"
                                            />
                                            <div v-else class="mt-2">
                                                <label class="block mb-1 text-xs font-semibold text-gray-500 dark:text-gray-300">Name</label>
                                                <input
                                                    data-test="name-field"
                                                    type="text"
                                                    :value="initialName"
                                                    readonly
                                                    disabled
                                                    class="block w-full text-sm text-gray-500 bg-gray-100 border-gray-300 rounded-md shadow-sm cursor-not-allowed dark:bg-gray-900 dark:text-gray-300 dark:border-gray-700"
                                                >
                                            </div>
                                            <AiInput
                                                class="mt-2"
                                                data-test="organisation-field"
                                                type="text"
                                                name="organisation"
                                                placeholder="Organisation"
                                            />
                                            <AiInput
                                                v-if="!lockIdentity"
                                                class="mt-2"
                                                data-test="email-field"
                                                type="email"
                                                name="email"
                                                placeholder="Email Address"
                                            />
                                            <div v-else class="mt-2">
                                                <label class="block mb-1 text-xs font-semibold text-gray-500 dark:text-gray-300">Email</label>
                                                <input
                                                    data-test="email-field"
                                                    type="email"
                                                    :value="initialEmail"
                                                    readonly
                                                    disabled
                                                    class="block w-full text-sm text-gray-500 bg-gray-100 border-gray-300 rounded-md shadow-sm cursor-not-allowed dark:bg-gray-900 dark:text-gray-300 dark:border-gray-700"
                                                >
                                            </div>
                                            <Listbox v-model="selectedOption">
                                                <ListboxButton
                                                    class="relative w-full py-2 pl-3 pr-10 mt-2 text-left bg-white dark:bg-dark-raised border border-gray-300 dark:border-dark-border rounded-md cursor-default focus:border-primary-500 focus:ring-primary-500 dark:focus:ring-primary-400 dark:focus:border-primary-400 focus:ring-1"
                                                    :class="[
                                                        !!errors?.role &&
                                                            'ring-1 ring-red-500 focus:ring-red-500 text-red-500 dark:text-red-400 dark:focus:ring-red-400',
                                                        !!errors?.role &&
                                                            'focus:border-red-500 border-red-500 dark:focus:border-red-400',
                                                    ]"
                                                >
                                                    <span
                                                        class="block truncate"
                                                        :class="!selectedOption && 'text-gray-500 dark:text-gray-300'"
                                                        data-test="role-select"
                                                    >
                                                        {{ selectedOption?.description ?? "Please select a role" }}
                                                    </span>
                                                    <span class="absolute inset-y-0 right-0 flex items-center pr-2 pointer-events-none">
                                                        <icon-mdi-chevron-down class="w-5 h-5 text-gray-400 dark:text-gray-300" />
                                                    </span>
                                                </ListboxButton>
                                                <transition
                                                    enter-active-class="transition duration-100 ease-out"
                                                    enter-from-class="transform scale-95 opacity-0"
                                                    enter-to-class="transform scale-100 opacity-100"
                                                    leave-active-class="transition duration-75 ease-in"
                                                    leave-from-class="transform scale-100 opacity-100"
                                                    leave-to-class="transform scale-95 opacity-0"
                                                >
                                                    <ListboxOptions
                                                        class="fixed z-10 py-2 origin-top-left bg-white dark:bg-dark-canvas dark:ring-white/20 rounded-md shadow-2xl w-60 ring-1 ring-black ring-opacity-5 focus:outline-none"
                                                    >
                                                        <ListboxOption
                                                            v-for="option in roleOptions"
                                                            v-slot="{ active, selected }"
                                                            :key="option.id"
                                                            data-test="role-select-option"
                                                            :value="option"
                                                        >
                                                            <li
                                                                class="relative px-4 py-2 pl-10 select-none transition"
                                                                :class="[
                                                                    (selected || active)
                                                                        && 'text-primary-500 bg-primary-100 dark:bg-dark-surface dark:text-primary-200'
                                                                ]"
                                                            >
                                                                <span>{{ option.description }}</span>
                                                                <span class="absolute inset-y-0 left-0 flex items-center pl-3">
                                                                    <icon-mdi-check v-if="selected" />
                                                                </span>
                                                            </li>
                                                        </ListboxOption>
                                                    </ListboxOptions>
                                                </transition>
                                            </Listbox>
                                            <div v-if="!!errors?.role" class="mt-1 text-sm text-red-500 dark:text-red-400">
                                                {{ errors.role }}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                            <div class="px-4 py-3 bg-gray-100 rounded-b-lg dark:bg-dark-canvas sm:px-6 sm:flex sm:flex-row-reverse sm:flex-shrink-0">
                                <AiButton
                                    data-test="register-user-confirm-btn"
                                    primary
                                    class="w-full sm:ml-2 sm:w-auto"
                                    :loading="isSubmitting"
                                    @click="submitAction"
                                >
                                    Register User
                                </AiButton>
                                <AiButton
                                    class="w-full mt-2 sm:mt-0 sm:w-auto"
                                    data-test="close-modal-btn"
                                    @click="close"
                                >
                                    Cancel
                                </AiButton>
                            </div>
                        </div>
                    </div>
                </TransitionChild>
            </div>
        </Dialog>
    </TransitionRoot>
</template>

<script setup lang="ts">
import { Dialog,
    DialogTitle,
    Listbox,
    ListboxButton,
    ListboxOption,
    ListboxOptions,
    TransitionChild,
    TransitionRoot } from "@headlessui/vue";
import { useField, useForm } from "vee-validate";
import { computed, ref, watch } from "vue";
import { object, string } from "yup";

import AiDialogOverlay from "@/components/AiDialogOverlay/AiDialogOverlay.vue";
import AiInput from "@/components/AiInput/AiInput.vue";
import { IOption } from "@/components/AiSelect/interfaces";
import { IRole } from "@/services/role-service";
import { IRegisterUserDto, registerUser } from "@/services/user-service";
import { useErrorStore } from "@/store/error";
import { extractErrorDetail } from "@/utils/api-errors";
import { Snackbar } from "@/utils/snackbar";

interface IRegisterUserModalProps {
    dialog?: boolean,
    title?: string,
    roles: IRole[],
    // Optional pre-fill (e.g. "Enroll" from an access request passes the
    // requester's name + email); organisation + role are still admin-chosen.
    initialName?: string,
    initialEmail?: string
}

interface RegisterUserForm {
    name: string;
    organisation: string;
    email: string;
    role: string;
}

const props = withDefaults(
    defineProps<IRegisterUserModalProps>(), {
        title: "Register User",
        dialog: false,
        initialName: "",
        initialEmail: ""
    }
);

const emit = defineEmits(["closeModal", "onSuccess"]);

const schema = object().shape({
    name: string()
        .required("A name is required"),
    organisation: string()
        .required("An organisation is required"),
    email: string()
        .required("An email address is required")
        .email("Please enter a valid email address"),
    role: string()
        .required("Please select a role")
});

const errorStore = useErrorStore();
const selectedOption = ref<IOption>();
const isSubmitting = ref(false);

const { errors, resetForm, setFieldValue, validate, validateField } =
    useForm<RegisterUserForm>({ validationSchema: schema });
const name = useField("name");
const organisation = useField("organisation");
const email = useField("email");
const role = useField<string>("role");

// When enrolling from an access request the requester's name + email are fixed
// (passed as initial* props); lock them read-only so the admin cannot alter the
// identity they approved. The plain Register-User flow passes neither, so it
// stays fully editable.
const lockIdentity = computed(() => props.initialEmail !== "");

const roleOptions = computed<IOption[]>(() =>
    props.roles.map((item) => ({
        id: item.id,
        description: item.rolename
    }))
);

watch(selectedOption, async (current) => {
    role.value.value = current?.id ?? "";
    await validateField("role");
});

// Pre-fill name + email whenever the modal opens, so a reused instance
// re-populates on each open (e.g. enrolling different access requests in turn).
// Use setFieldValue (the form's canonical setter) so the value also reaches the
// AiInput's own useField binding, not just the parent's copy. `immediate` also
// covers an instance mounted with dialog already open — otherwise vee-validate
// would still see empty name/email and block submit in the locked-identity flow,
// where the visible (disabled) inputs are not bound to the form.
watch(() => props.dialog, (isOpen) => {
    if (isOpen) {
        setFieldValue("name", props.initialName);
        setFieldValue("email", props.initialEmail);
    }
}, { immediate: true });

const close = () => {
    emit("closeModal", false);
    selectedOption.value = undefined;
    resetForm();
};

const submitAction = async () => {
    isSubmitting.value = true;

    const { valid } = await validate();

    if (valid) {
        const user: IRegisterUserDto = {
            name: lockIdentity.value ? props.initialName : (name.value.value as string),
            organisation: organisation.value.value as string,
            email: lockIdentity.value ? props.initialEmail : (email.value.value as string),
            roles: [role.value.value as string]
        };

        try {
            const result = await registerUser(user);

            if (!result.email) {
                Snackbar.error({
                    text: "There was an error, please try again.",
                    title: "User not registered"
                });
                errorStore.setError();
            } else {
                emit("onSuccess", false);
                Snackbar.success({
                    text: "The user has been registered successfully",
                    title: "User registered"
                });
            }
        } catch (e) {
            Snackbar.error({
                text: extractErrorDetail(e, "There was an error, please try again."),
                title: "User not registered"
            });
            errorStore.setError();
        }
        close();
    }

    isSubmitting.value = false;
};
</script>
