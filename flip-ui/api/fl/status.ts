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




export const connectedNets = [
    {
        name: "net-1",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    },
    {
        name: "net-2",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: false,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    },
    {
        name: "net-3",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    },
    {
        name: "net-4",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    },
    {
        name: "net-5",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    },
    {
        name: "net-6",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    },
    {
        name: "net-7",
        clients: [
            {
                name: "KCH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            },
            {
                name: "UCLH",
                online: true,
                lastConnected: "Fri Aug 19 10:27:58 2022"
            }
        ]
    }
];

export const detailedNetResponse = {
    name: "net-7",
    clients: [
        {
            "name": "UCLH",
            "online": true,
            "status": "execution exception. Please try again."
        },
        {
            "name": "KCH",
            "online": true,
            "status": "not started"
        }
    ]
};
