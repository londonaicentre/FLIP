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




import mime from "mime";

import { getPreSignedUrl, IPreSignedUrlBody, uploadModelFile } from "@/services/model-service";

export const uploadFile = async (
    file: File,
    uploadUrl: string
): Promise<void> => {
    await uploadModelFile(uploadUrl, file);
};

export const createPreSignedUrl = async (
    file: File,
    path: string,
    modelId: string
): Promise<string | null> => {

    const contentType = mime.getType(file.name);
    const endpoint = `${path}/${modelId}`;
    const body: IPreSignedUrlBody = {
        fileName: file.name,
        contentType: contentType ?? "application/octet-stream"
    };

    return await getPreSignedUrl(endpoint, body);
};
