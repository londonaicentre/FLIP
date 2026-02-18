<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

<template>
    <div class="flex h-full w-[384px] rounded-lg relative p-2 select-none">
        <img src="/images/AI-centre.png" class="absolute top-3 left-4 h-[76px] w-[116px]">
        <div class="login-branding-container w-full h-full relative">
            <img src="@/assets/login/branding-top-right.png" class="absolute top-2 right-2 h-[97.61px] w-[156px]">
            <img src="@/assets/login/branding-bottom-left.png" class="absolute bottom-2 left-2 h-[74.88px] w-[99px]">
        </div>
    </div>
</template>

<style>
.login-branding-container {
    /* inline svg for browser compatibility */
    filter: url("data:image/svg+xml,\
        <svg xmlns='http://www.w3.org/2000/svg'>\
            <filter id='rounding'>\
                <feGaussianBlur\
                    in='SourceGraphic' \
                    stdDeviation='4'\
                    result='blur'\
                />\
                <feColorMatrix in='blur'\
                    mode='matrix'\
                    values='1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 20 -9'\
                    result='matrix'\
                />\
                <feComposite \
                    in='SourceGraphic'\
                    in2='matrix'\
                    operator='atop'\
                />\
            </filter>\
        </svg>#rounding\
    ");
}

.login-branding-container::before {
    @apply bg-[#61366E] absolute inset-0 content-[""];
    clip-path: polygon(0px 93px, 135.16px 93px, 135.16px 0px, 100% 0%, 100% 100%, 0 100%);
}
</style>
