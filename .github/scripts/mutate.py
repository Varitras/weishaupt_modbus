"""Check that a test actually fails when the code it guards is broken.

A test that passes is not evidence. A test that passes *and* fails when its
fix is removed is. Every worthless test written in this repository so far
passed happily while the feature was broken, and every one of them was found
this way rather than by reading it:

  * a swap test that built the replacement object itself, so it only proved
    the constructor accepts an argument,
  * an abort test whose gate tripped one step too early, leaving the step
    that mattered untested,
  * a reload test that checked THAT a reload happened, not that it saw the
    new data - which is where the bug was,
  * a teardown test that set the flag the production code is supposed to set.

The pattern behind all four: if the test establishes the condition the
production code is supposed to establish, it tests nothing.

The harness itself is held to the same standard. "The tests noticed" is
pytest exit code 1 with a failed test in its count, and nothing else - a
usage error, an internal error, an interrupted run, a run the timeout killed
or a fixture that raised are not evidence, and reading any of them as success
was this script telling itself what it wanted to hear.

Usage
-----
Describe each mutation in a JSON file - a list of objects with:

    path     file to mutate, relative to the repository root
    old      snippet to replace (must appear exactly once; an absent
             snippet is reported as an error, never skipped silently)
    new      replacement, usually "" or a disabling variant; the result has
             to compile (or parse, for JSON, TOML and YAML) and read no name
             the file leaves undefined - checked for the whole plan before
             the first test runs
    tests    -k expression selecting the test(s) that must fail

Then:

    python .github/scripts/mutate.py mutations.json

Every mutation is reverted afterwards, including on failure. Exit code is
non-zero if any mutation SURVIVED - that is, the suite stayed green while the
code was broken, which means the test does not test it.

Cases run several at a time, each in its own copy of the repository under the
system temp directory (`--jobs`, default: cores - 2, capped at 24). Copies
rather than locking, because the thing being shared is a file this script
deliberately breaks. `--jobs 1` skips the copying and works in the repository
itself, which is what to fall back to if a parallel run ever reports
something surprising.

Results are printed in PLAN order regardless, so two runs of the same plan
produce the same output.
"""

from __future__ import annotations

import argparse
import builtins
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
import tomllib

import yaml

REPO = Path(__file__).resolve().parents[2]
# What a module reads without defining it: the builtins, its own file, and
# where Python 3.14 keeps the annotations of a conditional module statement.
MODULE_NAMES = frozenset(dir(builtins)) | {"__file__", "__conditional_annotations__"}


# pytest's exit codes. Only ONE of them means "the tests noticed": 1. The
# script used to read `returncode != 0` as caught, which let a usage error, an
# internal error or an interrupted run all report success - a broken harness
# congratulating itself, which is the failure this whole file exists against.
PYTEST_ALL_PASSED = 0
PYTEST_TESTS_FAILED = 1
PYTEST_INTERRUPTED = 2
PYTEST_INTERNAL_ERROR = 3
PYTEST_USAGE_ERROR = 4
PYTEST_NO_TESTS = 5
# Exit code 1 alone is not enough either: pytest-timeout ends a hung run with
# it and no count at all, and a fixture that raises gives it with an error
# count only. A failed test shows in the count line that ends the run.
FAILED_COUNT = re.compile(r"^\d+ failed\b", re.MULTILINE)

# A hung test run would otherwise hold a MUTATED source file indefinitely.
TEST_TIMEOUT_SECONDS = 900


def collect_test_locations() -> dict:
    """Which file each test lives in, collected once before anything is broken.

    Every mutation used to run `pytest tests/`, and collecting the whole tree
    costs about four seconds - repeated for each of a hundred-odd mutations,
    while the tests actually selected are usually one or two. Measured:
    collection was the run. Handing pytest only the files that hold the
    selected tests cuts roughly a third off the total.

    Derived rather than written into the plan by hand: a `file` field per
    mutation is one more thing to keep true when a test moves, and this stays
    correct by construction.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-m", "", "--collect-only"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=TEST_TIMEOUT_SECONDS,
    )
    if result.returncode != PYTEST_ALL_PASSED:
        raise SystemExit(
            "Could not collect the test suite, so no mutation can be run "
            f"against the right file. pytest exited {result.returncode}:\n"
            + (result.stderr or result.stdout).strip()[-2000:]
        )

    locations: dict = {}
    for line in result.stdout.splitlines():
        path, sep, rest = line.partition("::")
        if not sep or not path.endswith(".py"):
            continue
        # "tests/x.py::test_name[param]" and "tests/x.py::Class::test_name"
        name = rest.split("[")[0].split("::")[-1].strip()
        locations.setdefault(name, set()).add(path)
    return locations


def files_for(selector: str, locations: dict) -> list:
    """The files holding the tests this selector names.

    `-k` matches substrings, so the same rule applies here - a clause selects
    every test whose name contains it.
    """
    files = set()
    for clause in selector.split(" or "):
        clause = clause.strip()
        if not clause:
            continue
        for name, paths in locations.items():
            if clause in name:
                files |= paths
    return sorted(files)


def run_tests(selector: str, paths=None, root: Path | None = None) -> bool:
    """True if the selected tests FAIL, i.e. the mutation was caught.

    `root` is the tree to run in, which is the repository itself unless a
    parallel run handed this worker a copy of it.
    """
    targets = list(paths) if paths else ["tests/"]
    try:
        result = subprocess.run(
            # -x: the question is whether at least one selected test notices,
            # not how many do.
            [
                sys.executable,
                "-m",
                "pytest",
                *targets,
                "-q",
                "-m",
                "",
                "-x",
                # The duration budget in conftest turns an otherwise GREEN
                # run red, and exit code 1 is the entire evidence here - a
                # test that merely ran slowly would be reported as a caught
                # mutation. Useful in the everyday suite, wrong as an answer
                # to "did anything notice", so it is switched off for this
                # question only.
                "--slow-test-seconds",
                "inf",
                "-k",
                selector,
            ],
            cwd=root or REPO,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(
            f"selector {selector!r}: the test run did not finish within "
            f"{TEST_TIMEOUT_SECONDS}s. The mutated file is restored, but the "
            "result says nothing - investigate before trusting this plan."
        ) from exc

    # A selector that matches nothing exits 5 and would otherwise read as
    # "caught" - the same silent pass this script exists to prevent.
    if "no tests ran" in result.stdout or result.returncode == PYTEST_NO_TESTS:
        raise SystemExit(f"selector {selector!r} matched no tests")

    if result.returncode == PYTEST_TESTS_FAILED:
        if FAILED_COUNT.search(result.stdout) is None:
            raise SystemExit(
                f"selector {selector!r}: pytest exited 1 without a failed test "
                "- a run the timeout killed, or a fixture that raised - which "
                "says nothing about the mutation. Last output:\n"
                + (result.stdout or "(empty)").strip()[-2000:]
            )
        return True
    if result.returncode == PYTEST_ALL_PASSED:
        return False

    raise SystemExit(
        f"selector {selector!r}: pytest exited {result.returncode} "
        f"({_EXIT_REASON.get(result.returncode, 'unknown')}), which says "
        "nothing about the mutation. Last stderr:\n"
        + (result.stderr or "(empty)").strip()[-2000:]
    )


_EXIT_REASON = {
    PYTEST_INTERRUPTED: "interrupted",
    PYTEST_INTERNAL_ERROR: "internal error",
    PYTEST_USAGE_ERROR: "usage error",
}


def apply_mutation(case: dict, root: Path | None = None) -> tuple[Path, bytes]:
    """Break one file on purpose, and hand back what it takes to undo that.

    `root` is the tree to break, which is the repository itself unless a
    parallel run handed this worker a copy of it.

    The original is returned as BYTES and kept in memory. It used to be copied
    into a fresh tempfile.mkdtemp() that nothing ever removed - one directory
    per case, so a full run left as many behind as the plan has entries, each
    holding a copy of a source file.

    Bytes rather than text because restoring has to be exact: the snippets in
    the plan are written with \\n, so the match below needs a newline-normalised
    read, and writing that back would silently convert a CRLF checkout to LF.
    Two different reads, on purpose - one to compare against, one to restore.
    """
    target = (root or REPO) / case["path"]
    original = target.read_bytes()
    source = target.read_text(encoding="utf-8")
    occurrences = source.count(case["old"])
    if occurrences != 1:
        raise SystemExit(
            f"{case['path']}: snippet found {occurrences} times, expected once. "
            "A mutation that cannot be applied proves nothing - fix the snippet."
        )
    target.write_text(
        source.replace(case["old"], case["new"], 1), encoding="utf-8", newline=""
    )
    return target, original


def _label(case: dict) -> str:
    """The case's name in the output; the plan format leaves it optional."""
    return case.get("label", case["path"])


def check_mutants(cases: list) -> None:
    """Refuse the plan if a mutant does not run as written.

    A mutant that does not compile, names something undefined or no longer
    parses in its file's format fails its tests by that error alone, and
    "caught" would prove nothing. A case once wrote `cookie` for
    `self._cookie`; code moved into a deeper block left three snippets
    cutting a line in half.
    """
    broken = []
    for case in cases:
        source = (REPO / case["path"]).read_text(encoding="utf-8")
        mutated = source.replace(case["old"], case["new"], 1)
        try:
            _parse(case["path"], mutated)
        except (SyntaxError, ValueError, yaml.YAMLError) as error:
            broken.append(f"{_label(case)}: {error}")
            continue
        if not case["path"].endswith(".py"):
            continue
        # Only what the mutation brings in: what the original already reads
        # is not the case's doing, and covers what the scan cannot follow.
        introduced = _undefined_names(mutated, case["path"]) - _undefined_names(
            source, case["path"]
        )
        if introduced:
            broken.append(f"{_label(case)}: undefined {', '.join(sorted(introduced))}")
    if broken:
        raise SystemExit(
            "These mutations do not run as written, so their tests would "
            "fail on that alone:\n  " + "\n  ".join(broken)
        )


def _parse(path: str, text: str) -> None:
    """Raise if `text` is not valid in the format of the file at `path`."""
    match Path(path).suffix:
        case ".py":
            compile(text, path, "exec")
        case ".json":
            json.loads(text)
        case ".toml":
            tomllib.loads(text)
        case ".yaml" | ".yml":
            yaml.safe_load(text)


def _undefined_names(text: str, path: str) -> set:
    """The global names a module reads that nothing in it defines.

    The standard library's symbol tables rather than Ruff's F821: the test
    jobs that run the mutations do not install Ruff.
    """
    top = symtable.symtable(text, path, "exec")
    defined = set(MODULE_NAMES)
    read = set()
    tables = [top]
    while tables:
        table = tables.pop()
        tables.extend(table.get_children())
        for symbol in table.get_symbols():
            binds_module_name = table is top or symbol.is_declared_global()
            if binds_module_name and (symbol.is_assigned() or symbol.is_imported()):
                defined.add(symbol.get_name())
            if symbol.is_referenced() and (table is top or symbol.is_global()):
                read.add(symbol.get_name())
    return read - defined


# Every worker is a full pytest process with Home Assistant imported, so what
# a big machine runs out of first is memory, about 0.15 GB a worker. Measured
# on 32 cores with 203 cases (2026-10-03): 8 workers 40 s, 16 24 s, 24 21 s,
# 30 19 s. Past 24 the gain is small, while cores-2 on a 64-core build machine
# would start 62 interpreters at once and each a copy of the tree. --jobs
# overrides this for anyone who has measured otherwise.
MAX_DEFAULT_JOBS = 24


def default_jobs() -> int:
    """Workers to use when nobody says. Two cores are left for everything else.

    Measured on this suite: a single mutation spends about 0.8s running its
    tests and roughly twice that starting pytest up, so the run is dominated
    by per-process startup - which is exactly the shape that parallelises.
    Six workers on eight cores came out at 5.3x.
    """
    return max(1, min((os.cpu_count() or 2) - 2, MAX_DEFAULT_JOBS))


def _tracked_files() -> list[Path]:
    """What git would commit: tracked files plus untracked ones it does not
    ignore. A plain directory copy took .venv, .storage and secrets.yaml along
    into the temp directory whenever a checkout happened to hold them."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    # A file deleted in the working tree is still listed until it is staged.
    listed_paths = (REPO / name.decode() for name in listed.split(b"\0") if name)
    return [path for path in listed_paths if path.is_file()]


def _copy_tracked_tree(tree: Path) -> None:
    for source in _tracked_files():
        target = tree / source.relative_to(REPO)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def build_worktrees(count: int, into: Path, cases: list) -> list:
    """One independent copy of the repository per worker.

    The whole point of a copy: mutating a shared tree is why this ran serially
    before. Each worker breaks only its own file, so nothing has to be
    coordinated beyond handing a tree to one worker at a time.

    They are built under the system temp directory, not inside the repository,
    and that placement is doing real work: on WSL2 the repository lives on
    /mnt/c, whose filesystem calls cross into Windows and cost roughly twice
    what the Linux-native temp directory does. Measured on one mutation, 4.7s
    against 2.4s. For the same reason only the first copy is read from the
    repository and the others are copied from it: from /mnt/c every file is
    a round trip into Windows, 0.9 s a copy and 14 s before the first of
    sixteen workers could start (2026-10-03).
    """
    first = into / "worker0"
    _copy_tracked_tree(first)
    # A tree missing a file the plan mutates would report every one of its
    # cases as an unrelated pytest error. Cheaper to say so here, once,
    # naming the file - the alternative is reading a wall of exit-code 4.
    for case in cases:
        if not (first / case["path"]).exists():
            raise SystemExit(
                f"the worker copy has no {case['path']}, which the plan "
                "mutates: the copies hold only the files git tracks."
            )
    trees = [first]
    for index in range(1, count):
        tree = into / f"worker{index}"
        shutil.copytree(first, tree)
        trees.append(tree)
    return trees


def run_case(case: dict, files: list, root: Path | None) -> bool:
    """One mutation, applied and reverted in `root`."""
    target, original = apply_mutation(case, root=root)
    try:
        return run_tests(case["tests"], files, root=root)
    finally:
        target.write_bytes(original)


def run_in_parallel(cases: list, targets: dict, jobs: int) -> list:
    """Every case, `jobs` at a time, each in a tree of its own.

    Results come back in PLAN order rather than finishing order, so two runs
    of the same plan produce the same output and can be diffed.
    """
    holding = Path(tempfile.mkdtemp(prefix="mutate-"))
    try:
        free_trees: queue.SimpleQueue = queue.SimpleQueue()
        for tree in build_worktrees(jobs, holding, cases):
            free_trees.put(tree)

        done = 0

        def one(case):
            nonlocal done
            tree = free_trees.get()
            try:
                caught = run_case(case, targets[case["tests"]], tree)
            finally:
                free_trees.put(tree)
            done += 1
            # A counter on stderr, so stdout stays the result list - and only
            # on a terminal, because \r into a log file writes one long line.
            if sys.stderr.isatty():
                print(f"\r{done}/{len(cases)}", end="", file=sys.stderr, flush=True)
            return caught

        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(one, cases))
        if sys.stderr.isatty():
            print(file=sys.stderr)
        return results
    finally:
        _remove_worktrees(holding)


def _remove_worktrees(holding: Path) -> None:
    """Delete the worker copies, and say so when they will not go.

    Removed with errors ignored before, which is how a run that could not
    clean up left several megabytes of checkout under the temp directory with
    nothing anywhere pointing at the cause - once per run, quietly. The run
    itself still succeeds: the results are in, and leftover copies are a
    housekeeping problem, not a wrong answer.
    """
    try:
        shutil.rmtree(holding)
    except OSError as exc:
        print(
            f"warning: the worker copies could not be removed ({exc}). "
            f"They are still in {holding} and can go by hand.",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="JSON file describing the mutations")
    parser.add_argument(
        "--jobs",
        type=int,
        default=default_jobs(),
        help=(
            "mutations to run at once, each in its own copy of the repository "
            f"(default: cores - 2, capped at {MAX_DEFAULT_JOBS}). 1 runs in "
            "the repository itself, unchanged from how this always worked."
        ),
    )
    args = parser.parse_args()

    cases = json.loads(args.plan.read_text(encoding="utf-8"))
    if not cases:
        # "all 0 mutations caught" is the same green line as a real run, and
        # a plan that lost its cases - a bad filter, a truncated file - would
        # report the suite as fully guarded while proving nothing at all.
        raise SystemExit(f"{args.plan} describes no mutations")
    check_mutants(cases)
    survived = []

    locations = collect_test_locations()
    # Resolved for every case BEFORE the first mutation is applied: a selector
    # that names nothing is a broken plan, and finding that out halfway
    # through leaves the run half-done for no reason.
    targets = {}
    for case in cases:
        selector = case["tests"]
        files = files_for(selector, locations)
        if not files:
            raise SystemExit(f"selector {selector!r} matched no tests")
        targets[selector] = files

    jobs = max(1, args.jobs)
    if jobs == 1:
        # In the repository itself, one at a time - what this always did, and
        # what to fall back to when a parallel run reports something odd.
        results = [run_case(case, targets[case["tests"]], None) for case in cases]
    else:
        results = run_in_parallel(cases, targets, jobs)

    for case, caught in zip(cases, results, strict=True):
        label = _label(case)
        print(f"{'caught  ' if caught else 'SURVIVED'} {label}")
        if not caught:
            survived.append(label)

    if survived:
        print(
            "\nThese mutations survived - the code was broken and the tests "
            "stayed green:\n  " + "\n  ".join(survived)
        )
        return 1
    print(f"\nall {len(cases)} mutations caught")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
