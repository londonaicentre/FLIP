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

const DESCRIPTION = "All CT scans of women aged over 60, please.";

async function mountClosed(options: { initial?: string; used?: number } = {}) {
    const { drafter, calls } = controlledDrafter(options.used);
    const wrapper = mount(Harness, {
        props: {
            drafter,
            initial: options.initial ?? ""
        },
        global: { stubs }
    });
    await flushPromises();

    return {
        wrapper,
        calls
    };
}

async function setup(options: { initial?: string; used?: number } = {}) {
    const { wrapper, calls } = await mountClosed(options);
    await wrapper.get("[data-test='draft-assistant-open']").trigger("click");
    await wrapper.get("[data-test='draft-description']").setValue(DESCRIPTION);

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
        expect(wrapper.emitted("run")).toBeUndefined();
    });

    it("is closed by default, and opening and closing it leaves the SQL alone", async () => {
        const { wrapper } = await mountClosed({ initial: "SELECT 1" });
        const editor = () => (wrapper.get("[data-test='editor']").element as HTMLTextAreaElement).value;

        expect(wrapper.find("[data-test='draft-assistant']").exists()).toBe(false);
        expect(wrapper.find("[data-test='draft-summary']").exists()).toBe(false);
        expect(wrapper.get("button[data-test='draft-assistant-open']").text()).toContain("Draft from a description");
        expect(wrapper.get("button[data-test='draft-assistant-open']").text()).toContain("Beta");

        await wrapper.get("button[data-test='draft-assistant-open']").trigger("click");
        const panel = wrapper.get("[data-test='draft-assistant']");
        expect(panel.get("h2").text()).toContain("Beta");
        expect(panel.get("[data-test='draft-beta-note']").text()).toContain("Beta: drafts can be wrong.");

        await wrapper.get("[data-test='draft-assistant-close']").trigger("click");
        expect(wrapper.find("[data-test='draft-assistant']").exists()).toBe(false);
        expect(wrapper.find("button[data-test='draft-assistant-open']").exists()).toBe(true);
        expect(editor()).toBe("SELECT 1");
    });

    it("collapses to a one-line summary after a draft, and Redraft expands the card again", async () => {
        const { wrapper, generate, respond } = await setup();

        await generate();
        await respond(CT_WOMEN_OVER_60_DRAFT);

        expect(wrapper.find("[data-test='draft-assistant']").exists()).toBe(false);
        const summary = wrapper.get("[data-test='draft-summary']");
        expect(summary.text()).toContain("Drafted from");
        expect(summary.get("[data-test='draft-summary-description']").text()).toBe(DESCRIPTION);
        expect(summary.get("button[data-test='draft-redraft']").text()).toBe("Redraft");

        await summary.get("button[data-test='draft-redraft']").trigger("click");
        expect(wrapper.find("[data-test='draft-summary']").exists()).toBe(false);
        expect((wrapper.get("[data-test='draft-description']").element as HTMLTextAreaElement).value)
            .toBe(DESCRIPTION);
        expect(wrapper.get("button[data-test='draft-generate']").text()).toContain("Regenerate SQL");
    });

    it("folds the assumptions under the summary only once a draft has landed", async () => {
        const { wrapper, generate, respond } = await setup();

        // Manual SQL, no draft: no assumptions anywhere.
        await wrapper.get("[data-test='editor']").setValue("SELECT 1");
        expect(wrapper.find("[data-test='draft-assumptions']").exists()).toBe(false);

        await generate();
        await wrapper.get("[data-test='replace-sql']").trigger("click");
        await flushPromises();
        expect(wrapper.find("[data-test='draft-assumptions']").exists()).toBe(false);
        await respond(CT_WOMEN_OVER_60_DRAFT);

        const fold = wrapper.get("[data-test='draft-assumptions']");
        expect(fold.element.tagName).toBe("DETAILS");
        const n = CT_WOMEN_OVER_60_DRAFT.assumptions.length;
        expect(fold.get("[data-test='draft-assumptions-title']").text()).toBe(`${n} assumptions behind this draft`);
        expect(fold.findAll("li")).toHaveLength(n + CT_WOMEN_OVER_60_DRAFT.conceptsUsed.length);
        expect(fold.find("[data-test='draft-assumptions-stale']").exists()).toBe(false);
    });

    it("marks the assumptions as possibly out of date once the SQL is edited", async () => {
        const { wrapper, generate, respond } = await setup();

        await generate();
        await respond(CT_WOMEN_OVER_60_DRAFT);
        await wrapper.get("[data-test='editor']").setValue(`${CT_WOMEN_OVER_60_DRAFT.sql}\n-- tweak`);

        const fold = wrapper.get("[data-test='draft-assumptions']");
        expect(fold.get("[data-test='draft-assumptions-title']").text()).toContain("(may be out of date)");
        expect(fold.get("[data-test='draft-assumptions-stale']").text())
            .toBe("You have edited the SQL since the draft, so some assumptions may no longer apply.");

        // Back to the draft's text: current again.
        await wrapper.get("[data-test='editor']").setValue(CT_WOMEN_OVER_60_DRAFT.sql);
        expect(wrapper.get("[data-test='draft-assumptions-title']").text()).not.toContain("out of date");
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

        // The collapsed summary's controls too.
        await wrapper.get("button[data-test='draft-clarification-option']").trigger("click");
        await respond(CT_WOMEN_OVER_60_DRAFT);
        const summaryButtons = wrapper.get("[data-test='draft-summary']").findAll("button");
        expect(summaryButtons).toHaveLength(2);
        summaryButtons.forEach((b) => expect(b.attributes("type")).toBe("button"));
        await wrapper.get("[data-test='draft-assumptions-title']").trigger("click");
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
