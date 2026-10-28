# 0004: Pipeline steps declare themselves

- **Status:** proposed
- **Issues:** #25 (step interface), for the planned steps: animal detection (#54), splitting by animal presence (#55), behaviour models (#56) and exports (#50, #52)

## Context

`crittervid run` (`pipeline.run`) knows its three steps by name, and runs convert, then merge, then validate. Each step's peculiarities are written into the runner:

- **Before the merge:** the runner removes the previous merge report, so a stopped merge's old report can't be mistaken for this run's.
- **After the conversion:** if it was stopped with Ctrl+C, the whole run stops.
- **Validation** is skipped with `--no-validate`.
- **Dry run:** `PipelineConfig.command_args(step)` knows each step's options, for the `[DRY RUN] Would run: …` lines.
- **The summary** names the conversion and merge reports, and repeats the merge report's warnings.
- **The command list:** `cli.COMMANDS` lists the steps a second time.

Each new step today would mean editing the runner, `PipelineConfig.command_args`, the CLI and the summary. The architecture agreed in 2026-10 plans more steps: animal detection as an optional worker, splitting recordings by animal presence, behaviour models, and exports to Camtrap DP and BORIS. The event clip is their unit of work, and their results go into a manifest (#37, #38). Adding one should touch only the new step.

## Decision

### A step is a declaration

A new module, `crittervid/steps.py`, defines `Step`, a frozen dataclass, and `STEPS`, the ordered list of the built-in steps.

| Field | Meaning | convert / merge / validate |
|---|---|---|
| `name` | its command, `crittervid <name>`, and its logger's name | `convert`, `merge`, `validate` |
| `title` | the heading in `=== Step N: <title> ===` | as today |
| `module` | the module with `run(config)` and `main(argv)` | `crittervid.convert`, … |
| `needs` | what it reads, as names of things | `card` / `converted` / `converted`, `merged` |
| `makes` | what it writes | `converted` / `merged` / nothing |
| `options` | `options(config)`: its command-line options that match the config, for dry runs and for running it as its own program | moved from `PipelineConfig.command_args` |
| `enabled` | `enabled(config, choices)`: whether it runs in this run | validate: unless `--no-validate` |
| `prepare` | `prepare(config)`: run before it, if given | merge: remove the previous report |
| `reports` | `reports(config)`: lines for the run's summary, if given | convert and merge: their report files; merge: its warnings |

`needs` and `makes` are short names, not file patterns. They let the runner check the order when the list is built: everything a step needs comes from an earlier step, or from `card`, the input. They also tell later work such as `--steps` or a GUI which steps depend on which. Nothing reads files by these names.

### The runner runs the list

`pipeline.run(config)` goes through `STEPS` in order, and for each step:

- prints its heading, numbered by its position;
- skips it if it isn't `enabled`;
- in a dry run, prints the command it would run, built from `options`;
- otherwise calls `prepare`, then `run_step(step, config)` as today;
- after it, **stops the run if the cancel token is set**, whichever step it was. Today only the conversion is checked.

The summary collects each step's `reports`. The output stays word for word the same for the three built-in steps, and the existing tests check that.

`cli.COMMANDS` is made from `STEPS`, plus `run` and `gui`. Each step still has its own `main(argv)` for its own command and options, as now.

### Adding a step

A new step is a module with `run(config)` (and `main(argv)` for its own command), plus one `Step(...)` in `STEPS` at its place in the order. The runner, the CLI and the summary need no change.

## Not decided here

- **Steps from other packages,** such as an optional `crittervid-ai`, would be found through Python entry points (`crittervid.steps`), and would say where they go (`after='merge'`). That's left to #54, so the first real plug-in shapes it.
- **Settings of new steps:** the built-in steps keep their fields in `PipelineConfig`. How a plug-in adds options and settings without editing `PipelineConfig` is also left to #54.
- **Results:** steps still return an exit code. Their results (detections, intervals) go to the manifest (#37, #38), designed with it.
- **Separate processes for AI steps** (#54) are chosen per step when they come. The interface doesn't assume steps run in this process, because `run(config)` can start a worker.

## Plan: one PR

- `steps.py` with `Step` and `STEPS` for convert, merge and validate. `PipelineConfig.command_args` moves into the steps' `options`.
- `pipeline.run` goes through the list. The generic stop after any step replaces the conversion-only check.
- `cli.COMMANDS` is made from `STEPS`.
- **Tests:**
  - The output of `crittervid run` and its dry run is unchanged; the existing tests check this.
  - The order check rejects a step whose needs come later.
  - A test step added to the list runs at its place, appears in dry runs and the summary, and is stopped by the cancel token.
  - The CLI offers each step as a command.
- `.github/copilot-instructions.md` describes how to add a step.

## Questions for you

1. **Leave plug-in discovery and plug-in settings to #54,** so the first real plug-in shapes them? Recommended. Designing them now, with no plug-in to try them on, risks guessing wrong.
2. **Stop the run after any step when it's cancelled,** not only after the conversion? Recommended. Today a stop during the merge already ends the run through Ctrl+C. With a GUI Stop button (#42), the token is the only signal, so the runner must check it after every step.
