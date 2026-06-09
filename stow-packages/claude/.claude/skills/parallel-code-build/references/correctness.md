# Correctness lens

You are reviewing for changes that will behave wrong at runtime: logic errors, unhandled failures, races, and leaks. The build compiles and the suite is green - your job is the layer the tools can't reach: does the *intent* match the diff?

## What to flag

### Logic errors

- Inverted or incomplete conditions (`<` vs `<=`, missing negation, wrong boolean operator) on reachable paths. Severity: high; confidence 90+ when visible in-diff.
- Off-by-one in loops, slices, pagination, and index math. Severity: high.
- Wrong variable used where a sibling exists (`startDate` where `endDate` was meant). Severity: high when observable.
- Fallthrough or default cases that swallow unexpected values silently. Severity: medium.

### Error handling

- Plausible failure points with no handling: network calls, file I/O, subprocess execution, JSON/YAML parsing of external input. Severity: high.
- Errors caught and discarded (empty catch, `except: pass`, ignored error return in Go) where the caller needs to know. Severity: high.
- Error messages that drop the underlying cause, making the failure undiagnosable. Severity: medium.
- Async-specific: unawaited promises whose rejection escapes, missing error propagation in callbacks, one failed item rejecting a whole batch where partial success is wanted. Severity: high.

### Edge cases

- Empty input (empty list, empty string, zero), missing keys, null/None flowing into code that assumes presence. Severity: medium-high depending on reachability.
- Boundary values: first/last element, single-element collections, max sizes.
- Concurrent or repeated invocation of code that assumes single execution (re-entrancy, idempotency of retried operations). Severity: high.

### Races and resource leaks

- Shared mutable state accessed from concurrent contexts without synchronization (goroutines, async tasks, threads). Severity: high.
- Files, processes, connections, watchers opened without close/cleanup on all paths (including error paths). Severity: high on repeated paths, medium on one-shots.
- Temp files or background processes left behind on failure. Severity: medium.

### Contract breaks

- A changed function/CLI/API whose existing callers (grep for them) still pass the old shape. Severity: critical when the caller is in-repo and visible.
- Behavior changes hidden behind unchanged signatures (a function that used to return sorted output, now unsorted). Severity: high.

## What NOT to flag

- Test quality, missing tests, or TDD compliance - the tests-tdd lens owns that.
- Duplication, over-engineering, naming, structure - the simplicity-architecture lens owns that.
- Anything the build, lint, or test suite already catches - the verifier ran them; their output is input context.
- Pre-existing bugs the diff didn't touch or make worse.
