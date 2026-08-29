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

import { _http } from "./api";

export interface ICreatedTrust {
    id: string;
    name: string;
    code: string | null;
    region: string | null;
    created_at: string | null;
    // Plaintext keys returned once by POST /admin/trusts — never persisted on
    // the hub past the response. Surface to the admin in a one-time modal.
    trust_api_key: string;
    trust_internal_service_key: string;
    // Payload-encryption key + key-id for this trust. Also returned once and stored
    // nowhere hub-side: unlike the api key, a symmetric key cannot be reduced to a
    // hash, so an admin who does not record it here must re-register to get another.
    trust_aes_key: string;
    trust_aes_kid: string;
    // FL kit slot the hub claimed for this trust from the pre-provisioned pool.
    // The operator's fl-clients mount the matching workspace/net-N/services/<slot>
    // provisioned kit dirs; fl_kit_slot_number picks the Flower supernode key.
    fl_kit_slot: string;
    fl_kit_slot_number: number;
}

export interface ICreateTrustPayload {
    name: string;
    code: string;
    region?: string;
}

export async function createAdminTrust(payload: ICreateTrustPayload): Promise<ICreatedTrust> {
    const response = await _http.post<ICreatedTrust>("/admin/trusts", payload);

    return response.data;
}
