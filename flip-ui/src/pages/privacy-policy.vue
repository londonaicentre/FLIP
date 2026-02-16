<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

<route lang="yaml">
meta:
    layout: SecondaryLayout
</route>

<template>
    <div class="flex">
        <a
            role="button"
            tabindex="0"
            class="flex items-center text-sm"
            data-test="back-to-login-btn"
            @click="routeChange.back()"
        >
            <icon-mdi-chevron-left class="mr-1" />
            Back
        </a>
    </div>
    <div class="flex flex-col gap-5 overflow-hidden">
        <h1
            class="text-xl font-heading md:text-2xl"
        >
            Privacy Policy
        </h1>
        <div class="flex flex-col gap-2 overflow-auto">
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Blanditiis, officia numquam? Iure quia culpa magnam ullam,
                nulla accusantium perferendis possimus sequi iste corrupti voluptatibus aliquid libero pariatur.
                Mollitia, nesciunt molestiae.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Id perferendis facilis vel aliquid perspiciatis aut.
                Cum perspiciatis dolorem adipisci ea libero fugiat suscipit, in distinctio dolores impedit.
                Fuga, voluptatum fugiat!
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Ullam est ea eos illo quae praesentium cumque adipisci totam eaque autem iure quaerat ipsa,
                ad velit, molestias, tempore nisi nesciunt laborum.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Itaque, obcaecati! Qui natus saepe ratione ut iste vitae,
                eaque quas accusamus dolorum repellendus sint.
                Ipsam, neque? Voluptates iusto libero ducimus dolor.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Blanditiis, officia numquam? Iure quia culpa magnam ullam,
                nulla accusantium perferendis possimus sequi iste corrupti voluptatibus aliquid libero pariatur.
                Mollitia, nesciunt molestiae.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Id perferendis facilis vel aliquid perspiciatis aut.
                Cum perspiciatis dolorem adipisci ea libero fugiat suscipit, in distinctio dolores impedit.
                Fuga, voluptatum fugiat!
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Ullam est ea eos illo quae praesentium cumque adipisci totam eaque autem iure quaerat ipsa,
                ad velit, molestias, tempore nisi nesciunt laborum.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Itaque, obcaecati! Qui natus saepe ratione ut iste vitae,
                eaque quas accusamus dolorum repellendus sint.
                Ipsam, neque? Voluptates iusto libero ducimus dolor.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Blanditiis, officia numquam? Iure quia culpa magnam ullam,
                nulla accusantium perferendis possimus sequi iste corrupti voluptatibus aliquid libero pariatur.
                Mollitia, nesciunt molestiae.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Id perferendis facilis vel aliquid perspiciatis aut.
                Cum perspiciatis dolorem adipisci ea libero fugiat suscipit, in distinctio dolores impedit.
                Fuga, voluptatum fugiat!
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Ullam est ea eos illo quae praesentium cumque adipisci totam eaque autem iure quaerat ipsa,
                ad velit, molestias, tempore nisi nesciunt laborum.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Itaque, obcaecati! Qui natus saepe ratione ut iste vitae,
                eaque quas accusamus dolorum repellendus sint.
                Ipsam, neque? Voluptates iusto libero ducimus dolor.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Blanditiis, officia numquam? Iure quia culpa magnam ullam,
                nulla accusantium perferendis possimus sequi iste corrupti voluptatibus aliquid libero pariatur.
                Mollitia, nesciunt molestiae.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Id perferendis facilis vel aliquid perspiciatis aut.
                Cum perspiciatis dolorem adipisci ea libero fugiat suscipit, in distinctio dolores impedit.
                Fuga, voluptatum fugiat!
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Ullam est ea eos illo quae praesentium cumque adipisci totam eaque autem iure quaerat ipsa,
                ad velit, molestias, tempore nisi nesciunt laborum.
            </p>
            <p>
                Lorem ipsum dolor sit amet consectetur adipisicing elit.
                Itaque, obcaecati! Qui natus saepe ratione ut iste vitae,
                eaque quas accusamus dolorum repellendus sint.
                Ipsam, neque? Voluptates iusto libero ducimus dolor.
            </p>
        </div>
    </div>
</template>
<script setup lang="ts">
import { routeChange } from "@/router";
</script>
