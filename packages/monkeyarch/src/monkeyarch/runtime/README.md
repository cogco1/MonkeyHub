# MonkeyArch runtime

Runtime: `project_runner.run_project` binds a record to a run, projects the developed-design view, runs the seats through the producers and the compiler, checks relations, writes the stage closure and exit binding, and retains everything through P036. It writes; it never decides what the design is.

The shared project repository remains the only persistent writer. Drawing execution belongs to `monkeydiagram`, coordinated by the application host.
