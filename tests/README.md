# The gates and the guards

What protects this integration, what each guard holds, and what to do when
one of them turns red. Written for whoever changes this code next - which is
usually somebody with no memory of why any of it is here.

## Run everything

```sh
.github/scripts/check.sh
```

Ruff, formatting, mypy, the test suite and the mutation run, in that order,
stopping at the first failure. CI runs the same set; a guard in
`tests/test_guards.py` fails if the two ever drift apart. Anything
machine-local arrives through the environment:

```sh
PYTHON=/path/to/venv/bin/python .github/scripts/check.sh
```

The everyday run is shorter. The end-to-end tests boot a real Home Assistant
instance each and are deselected by default:

```sh
pytest tests/ -q            # fast: everything but e2e
pytest tests/ -q -m e2e     # only the slow ones
pytest tests/ -q -m ""      # all - what CI and check.sh run
```

## Why a mutation run

A passing test proves nothing on its own. `.github/mutations/plan.json`
describes deliberate breakages ("delete this guard clause", "swap these two
values"), and `.github/scripts/mutate.py` checks that a test actually fails
for each one.

A mutation that **survives** means the code was broken and the suite stayed
green: either the test is worthless, or a guard has gone blind and no longer
looks where the code moved.

When you add a behaviour worth keeping, add a mutation for it. When you move
code, the `path` and `old` fields move with it - `test_mutation_harness.py`
fails as soon as a snippet no longer matches.

## The guards

Structural tests that fail on a shape rather than on a value. Each one exists
because the thing it prevents happened, here or in a sibling project.

| Guard | Holds |
|---|---|
| `test_budgets.py` | No module or function grows past its frozen budget |
| `test_ci_matrix.py` | The CI workflow tests the Home Assistant release it claims to (a final one, resolved at run time - never a beta) |
| `test_comment_narration.py` | No comment merely restates the code it sits on (heuristic; a genuine why-comment passes) |
| `test_durations.py` | No single test quietly starts taking minutes (budget in `durations.py`, enforced from `conftest.py`), and a run that stops making progress is cut off rather than only measured |
| `test_guards.py` | No guard binds itself to one source file; every guard is listed; `check.sh` matches CI |
| `test_imports.py` | Every module imports outside the author's own tree - the `from config.custom_components...` line that shipped on main cannot ship again |
| `test_item_register.py` | Every register definition is complete (translated name, result list, address range, unique key) and both copies of the table agree |
| `test_mutation_harness.py` | The mutation run fails loudly rather than printing "all caught" without having checked |
| `test_mypy_scope.py` | Every module is in the mypy scope or carries a written reason why not yet |
| `test_platform_entities.py` | Every platform builds its entities through the one shared helper |
| `test_requirements.py` | `manifest.json` and `requirements.txt` name the same dependencies |

## When a budget turns red

`tests/test_budgets.py` freezes size and complexity so nothing grows back.
Two different rules, on purpose:

**Lines are a ceiling.** They move on almost every change, so the test only
asks that a module not grow past its entry. Modules under `LINE_LIMIT` (900)
need no entry at all.

**Complexity is a ratchet - an exact match.** It changes rarely, and when a
function does get simpler that progress is written down rather than left as
headroom for the next person to spend.

| Message | What it means | What to do |
|---|---|---|
| module over budget | a file grew past its entry (or past 900 lines without one) | Split it. If the growth is genuinely warranted, raise the entry **in the same commit** - the point is that the decision is visible in the diff. |
| budget far above the real size | a module shrank; the ceiling is now meaningless | Lower the entry to today's count. |
| function over budget | new complexity, undeclared | Read the function. Cognitive complexity counts **nesting**, so an early return or a guard clause usually helps more than extracting a helper. If it is warranted, add the entry. |
| budget out of step | a function got simpler, was renamed, or is gone | Set the entry to the current value, or drop it. |

The measure is Cognitive Complexity (Campbell / SonarSource), implemented in
`tests/complexity.py`. It counts nesting rather than paths: a flat ten-case
dispatch is cheap, three loops inside each other are not. `COMPLEXITY_LIMIT`
is 15, SonarSource's default.

## The register table

`hpconst.py` is the integration's data, and it exists twice: the API
subpackage carries its own copy with batch numbers for block reads. The client
polls from its copy and the entities read from the other, keyed by address.
`test_item_register.py` holds the two equal and lists, by name, what was
already wrong when the guard was adopted - so the **next** missing translation
or diverging list fails, not the ones that were there.

## Adding a guard

Two rules, both learned the hard way:

1. **Scan the package, never a single file.** `PACKAGE.rglob("*.py")`, not
   `Path(some_module.__file__).read_text()`. A scan pinned to one file goes
   blind the moment code moves to a new module - and a blind guard is worse
   than none, because the suite stays green. The guard against it is in
   `test_guards.py`.
2. **Prove the guard can fail.** A test that passes proves nothing. Add a
   case that feeds the detector the exact shape it exists to catch - see
   `test_the_scan_catches_the_line_that_shipped` in `test_imports.py`, which
   uses the verbatim line from the incident.

Then add the file to `GUARD_FILES` in `test_guards.py` with one line saying
what it holds, and a row to [the guard table](#the-guards) above.

## Layout

Tests move with their subject: `test_modbus_api.py` covers the batch-reading
client, `test_coordinator.py` the two coordinators, `test_entities.py` the
entity layer, `test_config_flow.py` and `test_e2e.py` the parts that only
exist once Home Assistant is driving the integration (both marked `e2e`).
`test_modbusobject.py` covers the legacy single-register client that is still
in the package.
