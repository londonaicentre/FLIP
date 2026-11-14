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

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { nextTick, ref } from "vue";

import useErrorHandler from "@/composables/useErrorHandler";
import { useErrorStore } from "@/store/error";

describe("useErrorHandler", () => {
    beforeEach(() => {
        setActivePinia(createPinia());
    });

    it("raises the error state at once for a source that already holds an error", () => {
        useErrorHandler(ref(new Error("boom")));

        expect(useErrorStore().getError.hasError).toBe(true);
    });

    it("follows the source: set when an error arrives, cleared when it goes", async () => {
        const error = ref<Error | undefined>();
        useErrorHandler(error);
        expect(useErrorStore().hasError).toBe(false);

        error.value = new Error("boom");
        await nextTick();
        expect(useErrorStore().hasError).toBe(true);

        error.value = undefined;
        await nextTick();
        expect(useErrorStore().hasError).toBe(false);
    });
});
