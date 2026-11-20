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

import { ref } from "vue";

import { ITrustKitDownload } from "@/services/trust-service";

export type IIssuedTrustKit = ITrustKitDownload & { trustLabel: string };

// The kit just issued, held only while its passphrase dialog is open. It lives here and the dialog in MainLayout:
// the table row that asked for the kit can leave the page before the passphrase is saved.
export const issuedKit = ref<IIssuedTrustKit | null>(null);

// Same anchor-click approach as the model files zip (ModelUpload.vue).
export const saveKitFile = ({ file_name, content_base64 }: ITrustKitDownload) => {
    const bytes = Uint8Array.from(atob(content_base64), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = file_name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
};
