# Debugging across services in FLIP [VScode guide]

Before running the debug command, ensure that you have the services up and running. You can do this by executing:

```bash
make up
```

This will run all API services in debug mode.
When in debug mode, the services will wait for a debugger to attach before proceeding.
You can attach a debugger using your IDE or by using `pdb` in the terminal.

## Debugging with VSCode

### Debugging tasks

![Run and Debug panel](https://github.com/user-attachments/assets/57ac2c82-7f70-49c4-bc8b-6e5f2d9c220f)

In the VSCode `Run and Debug` panel, you will find a task named:

- `Central Hub API`
- `FL API`
- `Trust API`
- `Imaging API`
- `Data Access API`
- `UI`
- `Central Services` (UI, Central Hub API, FL API)
- `Trust Services` (Trust API, Imaging API, Data Access API)

#### Automatically creating projects for manually testing the system

To automatically create projects for manually testing the system, you can use the `make -C flip-api create_testing_projects`
command. This command will create projects in different stages (e.g. `unstaged`, `staged`, `approved`).
To clean the environment, you can use the `make -C flip-api delete_testing_projects` command.
These are also available as vscode tasks. To run them, you click on the `Terminal > Run Task...` in VSCode top menu and
select `Create testing projects` or `Delete testing projects`, or you can use the command palette (Ctrl+Shift+P) and type
`Tasks: Run Task` to find and run the tasks.
