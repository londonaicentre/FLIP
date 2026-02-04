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



/* eslint-disable @typescript-eslint/no-explicit-any */
import { mountComponent } from "@test/helper";
import { createPinia } from "pinia";

import FileUpload from "@/partials/models/FileUpload.vue";
import ModelUpload from "@/partials/models/ModelUpload.vue";

vi.mock("@/utils/file", () => {
    return { uploadFile: () => Promise.resolve(void 0) };
});

vi.mock("@/services/api", () => {
    return {
        _http: {
            get: () => Promise.resolve(
                "{\"body\": {status\": \"COMPLETED\",}"
            ),
            post: () => Promise.resolve({ id: "some-id" }
            )
        }
    };
});

function mockFileCreator({
    name = "file.txt",
    size = 1024,
    type
}: {
    name?: string;
    size?: number;
    type?: string;
    lastModified?: Date;
}): File {
    const blob = new Blob(["a".repeat(size)], { type });

    return new File(
        [blob], name, { type }
    );
}

function fileListFromArray(files: File[]): FileList {
    const fileListMock = files.reduce(
        (accumulator: File[], curr, i: number) => {
            accumulator[i] = curr;

            return accumulator;
        },
        []
    ) as any;

    // eslint-disable-next-line @typescript-eslint/no-empty-function
    fileListMock.item = () => { };

    return fileListMock as FileList;
}

describe.skip("components/ModelUpload", () => {

    const wrapper = mountComponent(ModelUpload, { global: { plugins: [createPinia()] } });

    it("renders the correct markups", () => {
        expect(wrapper.element).toMatchSnapshot();
    });

    it.skip("Adds file to table with valid properties", async () => {
        const sizeInKB = 2.5;
        const name = "New awesome model.py";
        const type = "text/script";
        const fileList: FileList = fileListFromArray([
            mockFileCreator({
                name,
                size: sizeInKB * 1024,
                type
            })
        ]);

        wrapper.findComponent(FileUpload).vm.$emit("newFiles", fileList);

        // const uploadingStatusLabel =
        //     wrapper.find(ModelUploadModal.uploadingStatusLabel);
        // const scanningStatusLabel =
        //     wrapper.find(ModelUploadModal.scanningStatusLabel);
        // const completedStatusLabel =
        //     wrapper.find(ModelUploadModal.completedStatusLabel);

        const files = (wrapper.vm.$data as any).files;
        expect(files.length).toBe(1);
        const file = files[0];
        // expect(file.status).toBe(FileUploadStatus.UPLOADING);
        // expect(uploadingStatusLabel.isVisible).toBeTruthy();
        expect(file.size).toBe(sizeInKB.toFixed(2) + " KB");
        expect(file.name).toBe(name);
        expect(file.type).toBe(type.split("/")[1].toUpperCase() + " File");

        // expect(file.status).toBe(FileUploadStatus.SCANNING);
        // expect(uploadingStatusLabel.exists()).toBeFalsy();
        // expect(scanningStatusLabel.isVisible).toBeTruthy();
    });

    it.skip("Adds multiple file to table with valid properties", async () => {
        const firstSizeInKB = 2.5;
        const firstName = "New awesome model.py";
        const firstType = "text/script";
        const secondSizeInKB = 2.504543;
        const secondName = "New awesome model.py";
        const secondType = "text/script";
        const fileList: FileList = fileListFromArray([
            mockFileCreator({
                name: firstName,
                size: firstSizeInKB * 1024,
                type: firstType
            }),
            mockFileCreator({
                name: secondName,
                size: secondSizeInKB * 1024,
                type: secondType
            })
        ]);

        wrapper.findComponent(FileUpload).vm.$emit("newFiles", fileList);

        // const uploadingStatusLabel =
        //     wrapper.find(ModelUploadModal.uploadingStatusLabel);
        // const scanningStatusLabel =
        //     wrapper.find(ModelUploadModal.scanningStatusLabel);
        // const completedStatusLabel =
        //     wrapper.find(ModelUploadModal.completedStatusLabel);

        const files = (wrapper.vm.$data as any).files;
        const firstFile = files[0];
        expect(firstFile.size).toBe(firstSizeInKB.toFixed(2) + " KB");
        expect(firstFile.name).toBe(firstName);
        expect(firstFile.type).toBe(firstType.split("/")[1].toUpperCase() + " File");
        // expect(firstFile.status).toBe(FileUploadStatus.UPLOADING);
        // expect(uploadingStatusLabel.isVisible).toBeTruthy();

        expect(files.length).toBe(2);
        // expect(firstFile.status).toBe(FileUploadStatus.SCANNING);
        // expect(uploadingStatusLabel.exists()).toBeFalsy();
        // expect(scanningStatusLabel.isVisible).toBeTruthy();

        const secondFile = files[1];
        expect(secondFile.size).toBe(secondSizeInKB.toFixed(2) + " KB");
        expect(secondFile.name).toBe(secondName);
        expect(secondFile.type).toBe(secondType.split("/")[1].toUpperCase() + " File");
        // expect(secondFile.status).toBe(FileUploadStatus.UPLOADING);
        // expect(uploadingStatusLabel.isVisible).toBeTruthy();
        // expect(scanningStatusLabel.isVisible).toBeTruthy();

        expect(files.length).toBe(2);
    });
});
