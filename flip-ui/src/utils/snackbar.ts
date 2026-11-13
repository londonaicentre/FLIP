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




import { ISnackbar, notify } from "@/components/AiSnackbar/notify";

// The four variants differ only in their default type and timeout; a caller's own `type` still wins.
const notifier = (type: ISnackbar["type"], defaultTimeout: number) =>
    (snackbar: ISnackbar, timeout = defaultTimeout): void =>
        notify({
            group: "top-right",
            type,
            ...snackbar
        }, timeout);

export const Snackbar = {
    show: notifier("info", 6_000),
    error: notifier("error", 12_000),
    success: notifier("success", 6_000),
    warning: notifier("warning", 6_000)
};
