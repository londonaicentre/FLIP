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

# flip-api

## [Optional] Configure the connection on pgAdmin

> If the pgAdmin docker is running on a remote machine, you'll need first to tunnel the port from your local machine, e.g.
`ssh -D 1234 -L 5050:localhost:5050 desk`

Go to pgAdmin (<http://localhost:5050>) and log in (note the port and credentials are defined in [compose.yml](../deploy/compose.yml), under the service `pgadmin`).

Click on Register Server in the pgAdmin interface and configure the connection:

* Under 'General' tab
  * Name: centralhub (or any name you prefer)
* Under 'Connection' tab
  * Host name/address: `DB_HOST` defined in [compose.yml](../deploy/compose.yml) (e.g. `flip-db`)
  * Port: `5432`
  * Username: `POSTGRES_USER` defined in [compose.yml](../deploy/compose.yml)
  * Password: `POSTGRES_PASSWORD` defined in [compose.yml](../deploy/compose.yml)
  * Toggle 'Save password'

To view the data you've uploaded, on pgAdmin: Right click on the table (e.g. `fl_logs`) > `Scripts` > `SELECT Script` and then execute the script.
