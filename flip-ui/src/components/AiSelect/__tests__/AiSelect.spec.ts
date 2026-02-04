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



import { mountComponent } from "@test/helper";
import { createPinia } from "pinia";

import AiSelect from "@/components/AiSelect/AiSelect.vue";

import { AiSelectComponent } from "./selectors";

describe("Ai Select", () => {

    const props = [{
        text: "Some string",
        id: 1
    }];

    const mountedComponent = mountComponent(AiSelect, {
        props: { items: props },
        global: { plugins: [createPinia()] }
    });

    it("renders the correct markups", () => {
        expect(mountedComponent.element).toMatchSnapshot();
    });

    it("select button exists and displays default text", () => {
        const selectButton = mountedComponent.find(
            AiSelectComponent.selectButton
        );
        const selectButtonText = mountedComponent.find(
            AiSelectComponent.selectButtonText
        );

        expect(selectButton.isVisible).toBeTruthy();
        expect(selectButtonText.text()).toMatch("Select item");
    });
});
