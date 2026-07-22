// Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//     http://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

import { mount } from "@vue/test-utils";
import { describe, expect, test } from "vitest";

import ModelTabs from "@/partials/models/ModelTabs.vue";
import type { ModelStatus } from "@/services/model-service";

function mountTabs(status: ModelStatus, modelValue: "prepare" | "run" = "prepare") {
    return mount(ModelTabs, {
        props: {
            status,
            modelValue
        }
    });
}

describe("ModelTabs", () => {
    test("renders a Prepare and a Run chip", () => {
        const comp = mountTabs("PENDING");

        expect(comp.find("[data-test=tab-prepare]").text()).toContain("Prepare");
        expect(comp.find("[data-test=tab-run]").text()).toContain("Run");
    });

    test("marks the active chip for assistive tech", () => {
        const comp = mountTabs("RUNNING", "run");

        expect(comp.find("[data-test=tab-run]").attributes("aria-current")).toBe("page");
        expect(comp.find("[data-test=tab-prepare]").attributes("aria-current")).toBeUndefined();
    });

    test("Run is locked until the model is dispatched", async () => {
        const comp = mountTabs("PENDING");
        const run = comp.find("[data-test=tab-run]");

        expect(run.attributes("disabled")).toBeDefined();
        expect(run.text()).toContain("after start");

        await run.trigger("click");
        expect(comp.emitted("update:modelValue")).toBeUndefined();
    });

    test("Run unlocks once training has been initiated", async () => {
        const comp = mountTabs("INITIATED");
        const run = comp.find("[data-test=tab-run]");

        expect(run.attributes("disabled")).toBeUndefined();
        expect(run.text()).not.toContain("after start");

        await run.trigger("click");
        expect(comp.emitted("update:modelValue")).toEqual([["run"]]);
    });

    test("switching back to Prepare is always allowed", async () => {
        const comp = mountTabs("RUNNING", "run");

        await comp.find("[data-test=tab-prepare]").trigger("click");

        expect(comp.emitted("update:modelValue")).toEqual([["prepare"]]);
    });

    test("Prepare reads as done once the model has been dispatched", () => {
        const pending = mountTabs("PENDING");
        expect(pending.find("[data-test=tab-prepare-done]").exists()).toBe(false);

        const started = mountTabs("RUNNING", "run");
        expect(started.find("[data-test=tab-prepare-done]").exists()).toBe(true);
    });

    test("Prepare stays ticked when you open it — the stage is still done", () => {
        const comp = mountTabs("RUNNING", "prepare");

        expect(comp.find("[data-test=tab-prepare-done]").exists()).toBe(true);
    });

    test("the tick reads green on the open and closed tab alike — the active fill is light", () => {
        const open = mountTabs("RUNNING", "prepare");
        expect(open.find("[data-test=tab-prepare-done]").classes()).toContain("text-green-600");

        const closed = mountTabs("RUNNING", "run");
        expect(closed.find("[data-test=tab-prepare-done]").classes()).toContain("text-green-600");
    });

    test("Run reads as done only once the results have been uploaded", () => {
        const uploaded = mountTabs("RESULTS_UPLOADED", "run");
        expect(uploaded.find("[data-test=tab-run-done]").exists()).toBe(true);

        const live = mountTabs("RUNNING", "run");
        expect(live.find("[data-test=tab-run-done]").exists()).toBe(false);
    });

    test("a run that stopped or failed carries no tick — a tick means success", () => {
        for (const status of ["STOPPED", "ERROR", "RESULTS_UPLOAD_FAILED"] as ModelStatus[]) {
            const comp = mountTabs(status, "run");
            expect(comp.find("[data-test=tab-run-done]").exists()).toBe(false);
        }
    });

    test("no connector between the tabs — the nav is a plain narrow-gap row (design 04·A)", () => {
        const pending = mountTabs("PENDING");
        expect(pending.find("[data-test=tab-connector]").exists()).toBe(false);
        expect(pending.find("nav").classes()).toContain("gap-1");
    });

    test("tabs are flat ghost buttons: active is ink on a soft paper fill, inactive muted text", () => {
        const started = mountTabs("RUNNING", "run");
        const active = started.find("[data-test=tab-run]");
        const inactive = started.find("[data-test=tab-prepare]");

        expect(active.classes()).toContain("bg-ink/10");
        expect(active.classes()).toContain("text-ink");
        // No border, no underline, no pill — the filled rectangle is the whole
        // active indicator (design 04·A ProjectChrome ghost buttons, radius 8).
        expect(active.classes()).toContain("rounded-lg");
        expect(active.classes()).not.toContain("rounded-full");
        expect(active.classes().some(c => c.startsWith("border"))).toBe(false);

        expect(inactive.classes()).toContain("text-ink-3");
        expect(inactive.classes().some(c => c.startsWith("bg-"))).toBe(false);
        expect(inactive.classes().some(c => c.startsWith("border"))).toBe(false);
    });

    test("the Run chip pulses only while the run is in progress", () => {
        const live = mountTabs("RUNNING", "run");
        expect(live.find("[data-test=tab-run-live]").exists()).toBe(true);

        const finished = mountTabs("RESULTS_UPLOADED", "run");
        expect(finished.find("[data-test=tab-run-live]").exists()).toBe(false);

        const stopped = mountTabs("STOPPED", "run");
        expect(stopped.find("[data-test=tab-run-live]").exists()).toBe(false);
    });

    test("a stopped or errored run still lets you open the Run tab", async () => {
        const comp = mountTabs("ERROR", "prepare");

        await comp.find("[data-test=tab-run]").trigger("click");

        expect(comp.emitted("update:modelValue")).toEqual([["run"]]);
    });
});
