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

import { AxiosError } from "axios";

import { extractErrorDetail } from "@/utils/api-errors";

const axiosErrorWithBody = (body: unknown): AxiosError => {
    const err = new AxiosError("Request failed");
    err.response = {
        status: 400,
        statusText: "Bad Request",
        data: body,
        headers: {},
        config: { headers: {} } as never
    };

    return err;
};

describe("extractErrorDetail", () => {
    const FALLBACK = "Something went wrong";

    it("returns the FastAPI detail when present on an axios error", () => {
        const err = axiosErrorWithBody({ detail: "User with email alice@example.com already exists." });

        expect(extractErrorDetail(err, FALLBACK))
            .toBe("User with email alice@example.com already exists.");
    });

    it("returns the fallback when the response body has no detail", () => {
        const err = axiosErrorWithBody({ message: "nope" });

        expect(extractErrorDetail(err, FALLBACK)).toBe(FALLBACK);
    });

    it("returns the fallback when detail is not a string", () => {
        const err = axiosErrorWithBody({ detail: ["a", "b"] });

        expect(extractErrorDetail(err, FALLBACK)).toBe(FALLBACK);
    });

    it("returns the fallback when detail is the empty string", () => {
        const err = axiosErrorWithBody({ detail: "" });

        expect(extractErrorDetail(err, FALLBACK)).toBe(FALLBACK);
    });

    it("returns the fallback when there is no response (e.g. network error)", () => {
        const err = new AxiosError("Network Error");

        expect(extractErrorDetail(err, FALLBACK)).toBe(FALLBACK);
    });

    it("returns the fallback for non-axios errors", () => {
        expect(extractErrorDetail(new Error("boom"), FALLBACK)).toBe(FALLBACK);
        expect(extractErrorDetail("string error", FALLBACK)).toBe(FALLBACK);
        expect(extractErrorDetail(undefined, FALLBACK)).toBe(FALLBACK);
    });
});
