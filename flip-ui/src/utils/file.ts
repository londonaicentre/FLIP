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




import mime from "mime";

import { getPreSignedUrl,
    IPreSignedUploadPolicy,
    IPreSignedUrlBody,
    uploadModelFile } from "@/services/model-service";

/**
 * Thrown when a file fails the client-side size guard. The component layer
 * detects this via `instanceof FileTooLargeError` to surface a user-friendly
 * snackbar instead of the generic upload-error message.
 */
export class FileTooLargeError extends Error {
    public readonly limitBytes: number;
    public readonly actualBytes: number;

    constructor(limitBytes: number, actualBytes: number) {
        super(`File is ${actualBytes} bytes, which exceeds the ${limitBytes}-byte limit.`);
        this.name = "FileTooLargeError";
        this.limitBytes = limitBytes;
        this.actualBytes = actualBytes;
    }
}

export const uploadFile = async (
    file: File,
    policy: IPreSignedUploadPolicy
): Promise<void> => {
    if (file.size > policy.maxBytes) {
        throw new FileTooLargeError(policy.maxBytes, file.size);
    }
    await uploadModelFile(policy, file);
};

export const createPreSignedUrl = async (
    file: File,
    path: string,
    modelId: string
): Promise<IPreSignedUploadPolicy | null> => {

    const contentType = mime.getType(file.name);
    const endpoint = `${path}/${modelId}`;
    const body: IPreSignedUrlBody = {
        fileName: file.name,
        contentType: contentType ?? "application/octet-stream"
    };

    return await getPreSignedUrl(endpoint, body);
};
