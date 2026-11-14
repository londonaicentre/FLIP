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

import { computed, onBeforeUnmount, ref } from "vue";

import type { ICohortDraft, ICohortDrafter, IDraftAnswer, IDraftClarification, IDraftUsage } from "@/services/cohort-draft/types";

export type DraftPhase =
    | "idle"
    | "confirming" // the editor has SQL: waiting for an explicit choice to replace it
    | "drafting"
    | "drafted" // a draft is in the editor
    | "clarifying"
    | "declined"
    | "failed"
    | "kept-edits"; // the editor changed while drafting, so the draft was held back

/** "1 December": the allowance resets at 00:00 UTC on the 1st, so the date is read in UTC. */
export const formatDraftReset = (resetsAt: string): string =>
    new Date(resetsAt).toLocaleDateString("en-GB", {
        day: "numeric",
        month: "long",
        timeZone: "UTC"
    });

interface IEditor {
    get: () => string;
    set: (sql: string) => void;
}

/**
 * Drafting state for the cohort SQL assistant (FLIP#1556). The editor is only ever written by
 * a draft that arrives for the latest request, while the editor still holds what it held when
 * that request started. Cancel, failure, a stale response and an unmount all leave it alone.
 */
export function useCohortDraft(drafter: ICohortDrafter, editor: IEditor, hasImaging: () => boolean) {
    const phase = ref<DraftPhase>("idle");
    const usage = ref<IDraftUsage | null>(null);
    const clarification = ref<IDraftClarification | null>(null);
    const answers = ref<IDraftAnswer[]>([]);
    const message = ref("");
    // The draft whose SQL was last placed in the editor, for its assumptions.
    const inserted = ref<ICohortDraft | null>(null);
    const heldDraft = ref<ICohortDraft | null>(null);

    const exhausted = computed(() => !!usage.value && usage.value.used >= usage.value.limit);

    let latest = 0;
    let controller: AbortController | null = null;
    let pendingRequest: { description: string; clarifications: IDraftAnswer[] } | null = null;
    let phaseBeforeConfirm: DraftPhase = "idle";

    void drafter.getUsage().then((u) => { usage.value = u; }).catch(() => undefined);

    const start = async (description: string, clarifications: IDraftAnswer[]) => {
        const token = ++latest;
        controller?.abort();
        controller = new AbortController();
        const before = editor.get();

        phase.value = "drafting";
        message.value = "";
        heldDraft.value = null;
        answers.value = clarifications;

        try {
            const { result, usage: u } = await drafter.draft(
                {
                    description,
                    hasImaging: hasImaging(),
                    clarifications
                },
                controller.signal
            );
            if (token !== latest) return;
            usage.value = u;

            switch (result.status) {
                case "draft":
                    if (editor.get() !== before) {
                        heldDraft.value = result;
                        phase.value = "kept-edits";
                    } else {
                        editor.set(result.sql);
                        inserted.value = result;
                        phase.value = "drafted";
                    }
                    break;
                case "needs_clarification":
                    clarification.value = result;
                    phase.value = "clarifying";
                    break;
                case "declined":
                    message.value = result.explanation;
                    phase.value = "declined";
                    break;
                case "quota_exhausted":
                    phase.value = "idle";
                    break;
            }
        } catch (e) {
            if (token !== latest) return;
            message.value = (e as Error).message;
            phase.value = "failed";
        }
    };

    /** Generate, asking first when the editor already holds SQL. */
    const generate = (description: string, clarifications: IDraftAnswer[] = []) => {
        if (exhausted.value || phase.value === "drafting" || !description.trim()) return;

        if (editor.get().trim()) {
            pendingRequest = {
                description,
                clarifications
            };
            phaseBeforeConfirm = phase.value;
            phase.value = "confirming";

            return;
        }
        void start(description, clarifications);
    };

    const confirmReplace = () => {
        if (phase.value !== "confirming" || !pendingRequest) return;
        const { description, clarifications } = pendingRequest;
        pendingRequest = null;
        void start(description, clarifications);
    };

    const cancelReplace = () => {
        pendingRequest = null;
        if (phase.value === "confirming") phase.value = phaseBeforeConfirm;
    };

    const answer = (description: string, reply: string) => {
        if (!reply.trim()) return;
        generate(description, [
            ...answers.value,
            {
                question: clarification.value?.question ?? "",
                answer: reply.trim()
            }
        ]);
    };

    /** Abandon the in-flight request; its response, if it still arrives, is ignored. */
    const cancel = () => {
        latest++;
        controller?.abort();
        controller = null;
        if (phase.value === "drafting") phase.value = "idle";
    };

    /** The researcher's explicit choice to replace their in-flight edits with the held draft. */
    const acceptHeldDraft = () => {
        if (!heldDraft.value) return;
        editor.set(heldDraft.value.sql);
        inserted.value = heldDraft.value;
        heldDraft.value = null;
        phase.value = "drafted";
    };

    onBeforeUnmount(cancel);

    return {
        phase,
        usage,
        exhausted,
        clarification,
        message,
        inserted,
        heldDraft,
        generate,
        confirmReplace,
        cancelReplace,
        answer,
        cancel,
        acceptHeldDraft
    };
}
