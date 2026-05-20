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
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getPreSignedUrl, uploadModelFile } from "@/services/model-service";
import { createPreSignedUrl, FileTooLargeError, uploadFile } from "@/utils/file";

vi.mock("@/services/model-service", () => ({
    getPreSignedUrl: vi.fn(),
    uploadModelFile: vi.fn()
}));

describe("FileTooLargeError", () => {
    it("captures the limit and actual sizes and a descriptive message", () => {
        const err = new FileTooLargeError(100, 250);

        expect(err).toBeInstanceOf(Error);
        expect(err).toBeInstanceOf(FileTooLargeError);
        expect(err.name).toBe("FileTooLargeError");
        expect(err.limitBytes).toBe(100);
        expect(err.actualBytes).toBe(250);
        // The component layer surfaces this string in a snackbar, so we
        // pin it to make sure either number change is visible to the user.
        expect(err.message).toContain("250");
        expect(err.message).toContain("100");
    });
});

describe("uploadFile", () => {
    beforeEach(() => {
        vi.mocked(uploadModelFile).mockReset();
    });

    it("delegates to uploadModelFile when the file fits the policy", async () => {
        vi.mocked(uploadModelFile).mockResolvedValue(undefined);
        const file = new File(["payload"], "trainer.py", { type: "text/x-python" });
        const policy = {
            url: "https://signed.example/upload",
            fields: { key: "models/m-1/trainer.py" },
            maxBytes: 100
        };

        await uploadFile(file, policy);

        expect(uploadModelFile).toHaveBeenCalledWith(policy, file);
    });

    it("throws FileTooLargeError before calling the network when file exceeds policy maxBytes", async () => {
        // Fail-fast guard: avoids a wasted multipart POST + S3 reject cycle
        // when the client already knows the policy will reject the file.
        const file = new File(["x".repeat(200)], "big.py");
        const policy = {
            url: "https://signed.example/upload",
            fields: { key: "models/m-1/big.py" },
            maxBytes: 100
        };

        await expect(uploadFile(file, policy)).rejects.toBeInstanceOf(FileTooLargeError);
        expect(uploadModelFile).not.toHaveBeenCalled();
    });

    it("populates FileTooLargeError with the policy limit and actual size", async () => {
        const file = new File(["x".repeat(200)], "big.py");
        const policy = {
            url: "https://signed.example/upload",
            fields: { key: "models/m-1/big.py" },
            maxBytes: 100
        };

        await expect(uploadFile(file, policy)).rejects.toMatchObject({
            limitBytes: 100,
            actualBytes: 200
        });
    });

    it("permits files exactly at the maxBytes boundary", async () => {
        // The check is strict `>`, not `>=`. Exact-size files must go
        // through; flipping the comparator would silently block legitimate
        // uploads at the cap.
        vi.mocked(uploadModelFile).mockResolvedValue(undefined);
        const file = new File(["x".repeat(100)], "boundary.py");
        const policy = {
            url: "https://signed.example/upload",
            fields: { key: "models/m-1/boundary.py" },
            maxBytes: 100
        };

        await uploadFile(file, policy);

        expect(uploadModelFile).toHaveBeenCalledTimes(1);
    });
});

describe("createPreSignedUrl", () => {
    beforeEach(() => {
        vi.mocked(getPreSignedUrl).mockReset();
    });

    it("composes the endpoint from path and modelId and forwards the policy response", async () => {
        const policy = {
            url: "https://signed.example/upload",
            fields: { key: "models/m-1/config.json" },
            maxBytes: 100
        };
        vi.mocked(getPreSignedUrl).mockResolvedValue(policy);
        const file = new File(["{}"], "config.json");

        const result = await createPreSignedUrl(file, "/files/upload", "m-1");

        expect(getPreSignedUrl).toHaveBeenCalledWith(
            "/files/upload/m-1",
            {
                fileName: "config.json",
                contentType: "application/json"
            }
        );
        expect(result).toBe(policy);
    });

    it("resolves the content type from the file name via mime.getType", async () => {
        vi.mocked(getPreSignedUrl).mockResolvedValue(null);
        const file = new File(["{}"], "config.json");

        await createPreSignedUrl(file, "/files/upload", "m-1");

        const [, body] = vi.mocked(getPreSignedUrl).mock.calls[0];
        expect(body.contentType).toBe(mime.getType("config.json"));
    });

    it("falls back to application/octet-stream when mime cannot resolve a type", async () => {
        // Files without a recognised extension (e.g. `Dockerfile`,
        // `LICENSE`) return null from mime.getType. The S3 policy is
        // signed with a Content-Type prefix condition, so we must still
        // send a non-empty value; octet-stream is the conventional
        // "unknown binary" sentinel.
        vi.mocked(getPreSignedUrl).mockResolvedValue(null);
        const file = new File(["data"], "Dockerfile");

        await createPreSignedUrl(file, "/files/upload", "m-1");

        expect(getPreSignedUrl).toHaveBeenCalledWith(
            "/files/upload/m-1",
            {
                fileName: "Dockerfile",
                contentType: "application/octet-stream"
            }
        );
    });

    it("propagates a null response from getPreSignedUrl", async () => {
        // Caller switches on null to surface an upload-error snackbar, so
        // the helper must not coerce it into anything else.
        vi.mocked(getPreSignedUrl).mockResolvedValue(null);
        const file = new File(["payload"], "trainer.py");

        const result = await createPreSignedUrl(file, "/files/upload", "m-1");

        expect(result).toBeNull();
    });
});
