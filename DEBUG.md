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

# Debugging across services in FLIP [VScode guide]

Before running the debug command, ensure that the services are up. Start the full stack normally with:

```bash
make up
```

then bring up the API services in debug mode with either:

```bash
make debug-all                      # all API services in debug mode
make debug SERVICE=<service-name>   # one service in debug mode
```

When in debug mode, the services will wait for a debugger to attach before proceeding.
You can attach a debugger using your IDE or by using `pdb` in the terminal.
To leave debug mode use `make debug-off SERVICE=<service-name>` or `make debug-off-all`.

## Debugging with VSCode

### Debugging tasks

![Run and Debug panel](https://github.com/user-attachments/assets/57ac2c82-7f70-49c4-bc8b-6e5f2d9c220f)

In the VSCode `Run and Debug` panel, you will find a launch configuration named:

- `CH API`
- `Trust API`
- `Imaging API`
- `Data Access API`
- `UI`

There is no launch configuration for the FL API, and no compound configurations to launch several
services together — attach to each one individually. To debug the FL API, run the `debug-fl-api`
VSCode task (`Terminal > Run Task... > debug-fl-api`), which is equivalent to
`make debug SERVICE=fl-api-net-1`, then attach your debugger to the port it opens.

#### Automatically creating projects for manually testing the system

To automatically create projects for manually testing the system, you can use the `make -C flip-api create_testing_projects`
command. This command will create projects in different stages (e.g. `unstaged`, `staged`, `approved`).
To clean the environment, you can use the `make -C flip-api delete_testing_projects` command.
These are also available as vscode tasks. To run them, you click on the `Terminal > Run Task...` in VSCode top menu and
select `Create testing projects` or `Delete testing projects`, or you can use the command palette (Ctrl+Shift+P) and type
`Tasks: Run Task` to find and run the tasks.
