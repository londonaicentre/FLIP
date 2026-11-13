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

import { flushPromises, mount } from "@vue/test-utils";
import { Field, Form } from "vee-validate";
import { defineComponent, PropType } from "vue";

import { CT_WOMEN_OVER_60_DRAFT } from "@/services/cohort-draft/canned-drafts";
import type { DraftResult, ICohortDrafter, IDraftRequest, IDraftUsage } from "@/services/cohort-draft/types";

import CohortDraftAssistant from "../CohortDraftAssistant.vue";

const RESETS = "2026-12-01T00:00:00.000Z";

/** A drafter whose responses the test releases by hand. */
function controlledDrafter(used = 12) {
    const usage: IDraftUsage = {
        limit: 30,
        used,
        resetsAt: RESETS
    };
    const calls: { request: IDraftRequest; resolve: (r: DraftResult) => void; reject: (e: Error) => void }[] = [];
    const drafter: ICohortDrafter = {
        getUsage: async () => usage,
        draft: (request) => new Promise((resolve, reject) => calls.push({
            request,
            resolve: (result) => resolve({
                result,
                usage
            }),
            reject
        }))
    };

    return {
        drafter,
        calls
    };
}

// The real cohort form shape: a vee-validate <Form> holding a `query` field and a submit
// button standing in for "Run on all trusts".
const Harness = defineComponent({
    components: {
        CohortDraftAssistant,
        Field,
        VeeForm: Form
    },
    props: {
        drafter: {
            type: Object as PropType<ICohortDrafter>,
            required: true
        },
        initial: {
            type: String,
            default: ""
        }
    },
    emits: ["run"],
    template: `
        <VeeForm :initial-values="{ query: initial }" @submit="(v) => $emit('run', v)">
            <CohortDraftAssistant :has-imaging="true" :drafter="drafter" />
            <Field name="query" as="textarea" data-test="editor" />
            <button type="submit" data-test="run">Run</button>
        </VeeForm>`
});

const stubs = {
    // Renders inline (no teleport) with the two choices the real dialog offers.
    AiConfirmModal: {
        props: ["dialog", "continueAction"],
        emits: ["closeModal"],
        template: `
            <div v-if="dialog" data-test="replace-confirm">
                <button type="button" data-test="keep-sql" @click="$emit('closeModal')" />
                <button type="button" data-test="replace-sql" @click="continueAction()" />
            </div>`
    }
};

async function setup(options: { initial?: string; used?: number } = {}) {
    const { drafter, calls } = controlledDrafter(options.used);
    const wrapper = mount(Harness, {
        props: {
            drafter,
            initial: options.initial ?? ""
        },
        global: { stubs }
    });
    await flushPromises();
    await wrapper.get("[data-test='draft-assistant-open']").trigger("click");
    await wrapper.get("[data-test='draft-description']").setValue("All CT scans of women aged over 60.");

    const editor = () => (wrapper.get("[data-test='editor']").element as HTMLTextAreaElement).value;
    const generate = async () => {
        await wrapper.get("button[data-test='draft-generate']").trigger("click");
        await flushPromises();
    };
    const respond = async (result: DraftResult, index = calls.length - 1) => {
        calls[index].resolve(result);
        await flushPromises();
    };

    return {
        wrapper,
        calls,
        editor,
        generate,
        respond
    };
}

describe("CohortDraftAssistant", () => {
    it("puts a valid draft straight into the editable SQL input, without submitting", async () => {
        const { wrapper, calls, editor, generate, respond } = await setup();

        await generate();
        expect(wrapper.get("[data-test='draft-progress']").text()).toBe("Drafting SQL…");
        await respond(CT_WOMEN_OVER_60_DRAFT);

        expect(calls[0].request).toMatchObject({
            hasImaging: true,
            clarifications: []
        });
        expect(editor()).toBe(CT_WOMEN_OVER_60_DRAFT.sql);
        expect(wrapper.find("[data-test='draft-inserted']").exists()).toBe(true);
        expect(wrapper.findComponent(CohortDraftAssistant).emitted("notes")?.at(-1)).toEqual([CT_WOMEN_OVER_60_DRAFT]);
        expect(wrapper.emitted("run")).toBeUndefined();
    });

    it("asks before replacing existing SQL, and keeping it starts no draft", async () => {
        const { wrapper, calls, editor, generate, respond } = await setup({ initial: "SELECT 1" });

        await generate();
        expect(wrapper.find("[data-test='replace-confirm']").exists()).toBe(true);
        expect(calls).toHaveLength(0);

        await wrapper.get("[data-test='keep-sql']").trigger("click");
        expect(wrapper.find("[data-test='replace-confirm']").exists()).toBe(false);
        expect(calls).toHaveLength(0);
        expect(editor()).toBe("SELECT 1");

        await generate();
        await wrapper.get("[data-test='replace-sql']").trigger("click");
        await flushPromises();
        expect(calls).toHaveLength(1);
        await respond(CT_WOMEN_OVER_60_DRAFT);
        expect(editor()).toBe(CT_WOMEN_OVER_60_DRAFT.sql);
    });

    it("never overwrites SQL edited while the draft was in flight", async () => {
        const { wrapper, editor, generate, respond } = await setup();

        await generate();
        await wrapper.get("[data-test='editor']").setValue("SELECT my_edit");
        await respond(CT_WOMEN_OVER_60_DRAFT);

        expect(editor()).toBe("SELECT my_edit");
        expect(wrapper.find("[data-test='draft-kept-edits']").exists()).toBe(true);

        // Only the researcher's explicit choice replaces their edits.
        await wrapper.get("button[data-test='draft-use-held']").trigger("click");
        await flushPromises();
        expect(editor()).toBe(CT_WOMEN_OVER_60_DRAFT.sql);
    });

    it("keeps the SQL on failure and after cancelling, ignoring the late response", async () => {
        const { wrapper, calls, editor, generate, respond } = await setup();

        await generate();
        calls[0].reject(new Error("The drafting service did not respond in time."));
        await flushPromises();
        expect(editor()).toBe("");
        expect(wrapper.get("[data-test='draft-failed']").text()).toContain("Your SQL is unchanged");

        await generate();
        await wrapper.get("button[data-test='draft-cancel']").trigger("click");
        await respond(CT_WOMEN_OVER_60_DRAFT, 1);
        expect(editor()).toBe("");
        expect(wrapper.find("[data-test='draft-progress']").exists()).toBe(false);
    });

    it("answers a clarification question with a follow-up request, leaving the editor alone", async () => {
        const { wrapper, calls, editor, generate, respond } = await setup();

        await generate();
        await respond({
            status: "needs_clarification",
            question: "Which modality: CT or MRI?",
            options: ["CT", "MRI"]
        });
        expect(wrapper.get("[data-test='draft-clarification']").text()).toContain("Which modality: CT or MRI?");
        expect(editor()).toBe("");

        await wrapper.findAll("button[data-test='draft-clarification-option']")[0].trigger("click");
        await flushPromises();
        expect(calls[1].request.clarifications).toEqual(["CT"]);
    });

    it("shows a decline and drafts nothing", async () => {
        const { wrapper, editor, generate, respond } = await setup();

        await generate();
        await respond({
            status: "declined",
            explanation: "Cohort queries must be a single read-only SELECT."
        });

        expect(wrapper.get("[data-test='draft-declined']").text()).toContain("read-only SELECT");
        expect(editor()).toBe("");
    });

    it("leaves Run as the only submit: every assistant control is type=button", async () => {
        const { wrapper, generate, respond } = await setup();

        await generate();
        await respond({
            status: "needs_clarification",
            question: "Which modality: CT or MRI?",
            options: ["CT", "MRI"]
        });

        const buttons = wrapper.get("[data-test='draft-assistant']").findAll("button");
        expect(buttons.length).toBeGreaterThan(4);
        buttons.forEach((b) => expect(b.attributes("type")).toBe("button"));
        expect(wrapper.emitted("run")).toBeUndefined();

        await wrapper.get("[data-test='editor']").setValue("SELECT 1");
        await wrapper.get("form").trigger("submit");
        await flushPromises();
        expect(wrapper.emitted("run")?.[0][0]).toEqual({ query: "SELECT 1" });
    });

    it("disables generation when the allowance is used up, but not manual SQL or Run", async () => {
        const { wrapper, calls, generate } = await setup({ used: 30 });

        expect(wrapper.get("button[data-test='draft-generate']").attributes("disabled")).toBeDefined();
        expect(wrapper.get("[data-test='draft-exhausted']").text()).toContain("Drafting resets 1 December");
        expect(wrapper.get("[data-test='draft-usage-indicator']").attributes("aria-label"))
            .toBe("0 of 30 drafts remaining. Resets 1 December.");

        await generate();
        expect(calls).toHaveLength(0);

        await wrapper.get("[data-test='editor']").setValue("SELECT 2");
        await wrapper.get("form").trigger("submit");
        await flushPromises();
        expect(wrapper.emitted("run")?.[0][0]).toEqual({ query: "SELECT 2" });
    });
});
