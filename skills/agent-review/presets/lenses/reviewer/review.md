Coordinate a code review through three independent subagents, one per lens. Run them in parallel when available; divide the work by perspective, not by file. Each subagent should inspect the source independently before seeing the other lenses' conclusions.

1. **Regressions**
   - Check whether behavior that worked before still works after the change. Compare the supplied snapshot with its actual baseline: the merge base for scope full, or the previous completed review's snapshot for scope changes.
   - Trace affected callers, public interfaces, configuration, stored data, error paths and resource lifecycles. Look for compatibility breaks and unintended side effects outside the directly changed code.
   - Give a concrete previously working scenario, the before/after behavior and evidence that this change causes the failure. Distinguish intended behavior changes, pre-existing defects and uncertain assumptions from demonstrated regressions.
2. **Repository style and rules**
   - Check explicit repository rules and established local conventions for naming, structure, abstractions, error handling, logging, testing and documentation, using relevant documentation, configuration and nearby comparable code.
   - Support implicit conventions with concrete comparable examples. Prefer the conventions of the affected package when styles differ across the repository; state uncertainty when examples conflict.
   - Report actionable inconsistencies with their consequence and a repository-consistent alternative.
3. **Comprehensive review**
   - Code quality: clean code principles, error handling, edge cases, readability and maintainability.
   - Security: input sanitization, trust boundaries, authentication and authorization.
   - Performance: bottlenecks, database query efficiency, memory leaks and resource use.
   - Testing: appropriate coverage, test quality, edge cases and missing scenarios.
   - Documentation: repository-required documentation, README changes for new features and API accuracy.
   - Guidelines: repository rules, plus docs/go-guidelines.md and docs/api-guidelines.md when present.
   - Keep actionable findings about documentation, tests, performance and maintainability even when they are not regressions. Explain pre-existing behavior and scope concerns so the author and human can decide.

Use the draft prefixes regression-, style- and general- for the corresponding perspectives. When comparing the lenses' conclusions, preserve distinct regression scenarios, repository conventions and comprehensive concerns even when they concern the same code.
