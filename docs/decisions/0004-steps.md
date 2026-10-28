# 0004: Pipeline steps declare themselves

- **Status:** proposed
- **Issues:** #25 (step interface), for the planned steps: animal detection (#54), splitting by animal presence (#55), behaviour models (#56) and exports (#50, #52)

## Context

`crittervid run` (`pipeline.run`) knows its three steps by name, and runs convert, then merge, then validate. Each step's peculiarities are written into the runner:

- **Before the merge:** the runner removes the previous merge report, but not in a dry run, so a stopped merge's old report can't be mistaken for this run's.
- **After the conversion:** if it was stopped (the cancel token is set), the whole run stops. It prints `=== Stopped by user (Ctrl+C) ===` and no summary.
- **Validation** is skipped with `--no-validate`. Its heading then reads `=== Step 3: Validation skipped (use --validate to enable) ===`.
- **Dry run:** each enabled step prints `[DRY RUN] Would run: crittervid <name> <options>`, from `PipelineConfig.command_args(step)`, and counts as succeeded.
- **The summary** prints:
  - `Conversion summary: <path>` and `Merging summary: <path>`;
  - the merge report's warnings, prefixed `Merge `, except in a dry run;
  - `Validation: see console output above` or `Validation: skipped`;
  - `All steps completed successfully.` (exit code 0), or `Some steps failed. Check logs above and summary reports.` (an error, exit code 1).
- **The command list:** `cli.COMMANDS` lists the steps a second time.

Each new step today would mean editing the runner, `PipelineConfig.command_args`, the CLI and the summary. The architecture agreed in 2026-10 plans more steps: animal detection as an optional worker, splitting recordings by animal presence, behaviour models, and exports to Camtrap DP and BORIS. The event clip is their unit of work, and their results go into a manifest (#37, #38). Adding one should touch only the new step.

## Decision

### A step is a declaration

A new module, `crittervid/steps.py`, defines `Step`, a frozen dataclass, and `STEPS`, the ordered list of the built-in steps.

| Field | Meaning | For convert / merge / validate |
|---|---|---|
| `name` | its command, `crittervid <name>`, and its logger's name | `convert`, `merge`, `validate` |
| `module` | the name of the module with `run(config)` and `main(argv)`, imported when the step runs | `'crittervid.convert'`, … |
| `title` | the heading, `=== Step N: <title> ===` | as today |
| `skipped_title` | the heading when it doesn't run | validate: `Validation skipped (use --validate to enable)` |
| `enabled` | `enabled(config, run_options)`: whether it runs in this run | validate: unless `--no-validate` |
| `options` | `options(config)`: its command-line options that do what the config says, for the dry-run line | moved from `PipelineConfig.command_args` |
| `prepare` | `prepare(config)`: run before it, never in a dry run; optional | merge: remove the previous report |
| `summary` | `summary(config, ran, dry_run)`: its lines for the run's summary; optional | convert: the report's path; merge: the report's path and its warnings; validate: "see console output above" or "skipped" |

`module` is a string, so `steps.py` imports nothing heavy, and the CLI can list the steps without loading them. The runner looks up `run` when it calls the step, so tests that replace a step's `run` still work.

### The runner runs the list

`pipeline.run(config, validate_output, dry_run)` goes through `STEPS`, numbering them by position. For each step:

1. **If it isn't `enabled`,** it prints `=== Step N: <skipped_title> ===` and counts as succeeded.
2. **Otherwise** it prints `=== Step N: <title> ===`, then:
   - **in a dry run,** prints `[DRY RUN] Would run: crittervid <name> <options>` and counts as succeeded;
   - **in a real run,** calls `prepare`, then `run_step(step, config)` as today. `run_step` takes the `Step` and uses its `name` in error messages.
3. **If the cancel token is set and steps remain,** the run stops as it does after the conversion today: no further steps, `=== Stopped by user (Ctrl+C) ===`, and no summary. Today only the conversion is checked. Checking after each step means a GUI Stop button (#42), which can only set the token, stops a run between any two steps. After the last step there's nothing left to stop, so the summary is printed.

Then `=== Summary ===`, each step's `summary` lines in order, and the final line and exit code as today. The output of the three built-in steps stays word for word the same, in runs and in dry runs, and the existing tests check that.

`cli.COMMANDS` is made from `STEPS`, plus `run` and `gui`. Each step still has its own `main(argv)`, for its own command and options.

### Adding a step

A new step is:

- a module with `run(config)`, and `main(argv)` for its own command;
- one `Step(...)` in `STEPS`, at its place in the order.

The runner, the CLI and the summary need no change.

## Not decided here

- **Steps from other packages,** such as an optional `crittervid-ai`: they would be found through Python entry points (`crittervid.steps`), and would say where they go (`after='merge'`). That's left to #54, so the first real plug-in shapes it.
- **Settings of new steps:** the built-in steps keep their fields in `PipelineConfig`. How a plug-in adds options and settings without editing `PipelineConfig` is also left to #54.
- **What each step reads and writes:** declaring this would let a `--steps` option, or the GUI, run a subset in a valid order. Nothing would use it yet, and a strict check would wrongly reject running `validate` alone. It's left until something uses it.
- **Results:** steps still return an exit code. Their results (detections, intervals) go to the manifest (#37, #38), designed with it.
- **Separate processes for AI steps** (#54) are chosen per step when they come. `run(config)` can start a worker, so the interface doesn't assume steps run in this process.

## Plan: one PR

- `steps.py` with `Step` and `STEPS` for convert, merge and validate. `PipelineConfig.command_args` moves into the steps' `options`.
- `pipeline.run` goes through the list. The stop after any step but the last replaces the conversion-only check.
- `cli.COMMANDS` is made from `STEPS`.
- **Tests:**
  - The output and exit codes of `crittervid run`, its dry run, and a run with `--no-validate` are unchanged; the existing tests check this, plus a new one for `--no-validate`.
  - A test step added to the list runs at its place, appears in dry runs and the summary, and isn't started when the step before it was cancelled.
  - `prepare` doesn't run in a dry run.
  - The CLI offers each step as a command.
- `.github/copilot-instructions.md` describes how to add a step.

## Questions for you

1. **Leave plug-in discovery and plug-in settings to #54,** so the first real plug-in shapes them? Recommended. Designing them now, with no plug-in to try them on, risks guessing wrong.
2. **Stop the run between any two steps when it's cancelled,** as it does after the conversion today? Recommended, because a GUI Stop button (#42) can only signal through the token.
