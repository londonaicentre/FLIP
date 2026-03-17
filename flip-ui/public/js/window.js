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


const AWS_BASE_URL = "http://localhost:8080/api";
const AWS_USER_POOL_ID = "eu-west-2_4QdpwX1GW";
const AWS_CLIENT_ID = "5hcskgab29jpmti0esd655lfsv";
const BLACKLISTED_MODEL_FILES = "";
const RELEASE_VERSION = "";
const MAX_REIMPORT_COUNT = parseInt("10", 10);

if (global === undefined) {
    var global = window;
    global.AWS_BASE_URL = AWS_BASE_URL;
    global.AWS_USER_POOL_ID = AWS_USER_POOL_ID;
    global.AWS_CLIENT_ID = AWS_CLIENT_ID;
    global.BLACKLISTED_MODEL_FILES = BLACKLISTED_MODEL_FILES;
    global.RELEASE_VERSION = RELEASE_VERSION;
    global.MAX_REIMPORT_COUNT = MAX_REIMPORT_COUNT;
}
