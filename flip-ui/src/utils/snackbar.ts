/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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




import { ISnackbar, notify } from "@/components/AiSnackbar/notify";

export const Snackbar = {
    show: (snackbar: ISnackbar, timeout = 6_000): void => {

        const notification: ISnackbar = {
            group: "top-right",
            type: "info",
            ...snackbar
        };

        notify(notification, timeout);
    },
    error: (snackbar: ISnackbar, timeout = 12_000): void => {

        const notification: ISnackbar = {
            group: "top-right",
            type: "error",
            ...snackbar
        };

        notify(notification, timeout);
    },
    success: (snackbar: ISnackbar, timeout = 6_000): void => {

        const notification: ISnackbar = {
            group: "top-right",
            type: "success",
            ...snackbar
        };

        notify(notification, timeout);
    },
    warning: (snackbar: ISnackbar, timeout = 6_000): void => {

        const notification: ISnackbar = {
            group: "top-right",
            type: "warning",
            ...snackbar
        };

        notify(notification, timeout);
    }
};
