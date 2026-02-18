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




import { computed, ComputedRef } from "vue";

import { IAIHeaderProps } from "@/components/AiHeader/AiHeader.vue";
import { IMainNavigationProps } from "@/components/AiMainNavigation/AiMainNavigation.vue";
import { useAuthStore } from "@/store/auth";
import AdminIcon from "~icons/heroicons-outline/finger-print";
import projectsIcon from "~icons/ic/twotone-library-books";
import ConnectionIcon from "~icons/ph/plug-duotone";


export default function useNavigation(props: IMainNavigationProps | IAIHeaderProps): ComputedRef {
    const authStore = useAuthStore();

    return computed(() => [
        {
            name: "Projects",
            href: "/projects",
            current: props.currentPage.startsWith("/project"),
            icon: projectsIcon,
            canAccess: true
        },
        {
            name: "Connection Status",
            href: "/connectionstatus",
            current: props.currentPage === "/connectionstatus",
            icon: ConnectionIcon,
            canAccess: true
        },
        {
            name: "Admin",
            href: "/admin",
            current: props.currentPage.startsWith("/admin"),
            icon: AdminIcon,
            canAccess: authStore.hasPermissions(["CanAccessAdminPanel"])
        }
    ].filter(item => item.canAccess));
}
