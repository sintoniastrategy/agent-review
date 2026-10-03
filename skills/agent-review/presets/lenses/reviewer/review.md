Coordinate a code review through three independent subagents, one per lens. Run them in parallel when available. Give each the complete selected diff and enough surrounding context to assess interactions across files; divide the work by perspective, not by file. Each subagent should inspect the source independently before seeing the other lenses' conclusions.

1. **Regressions**
   - Check whether behavior that worked before still works after the change. Compare the supplied snapshot with its actual baseline: the merge base for scope full, or the previous completed review's snapshot for scope changes.
   - Trace affected callers, public interfaces, configuration, stored data, error paths and resource lifecycles. Look for compatibility breaks and unintended side effects outside the directly changed code.
   - Give a concrete previously working scenario, the before/after behavior and evidence that this change causes the failure. Distinguish intended behavior changes, pre-existing defects and uncertain assumptions from demonstrated regressions.
2. **Repository style and rules**
   - Read applicable AGENTS.md, CLAUDE.md, relevant documentation, configuration and nearby comparable code. Check explicit rules and established local conventions for naming, structure, abstractions, error handling, logging, testing and documentation.
   - Support implicit conventions with concrete comparable examples. Prefer the conventions of the affected package when styles differ across the repository; state uncertainty when examples conflict.
   - Report actionable inconsistencies with their consequence and a repository-consistent alternative. Do not invent rules, impose personal preferences or demand comments, tests or abstractions that the repository does not call for.
3. **Comprehensive review**
   - Code quality: clean code principles, error handling, edge cases, readability and maintainability.
   - Security: input sanitization, trust boundaries, authentication and authorization.
   - Performance: bottlenecks, database query efficiency, memory leaks and resource use.
   - Testing: appropriate coverage, test quality, edge cases and missing scenarios, assessed by reading rather than running tests.
   - Documentation: repository-required documentation, README changes for new features and API accuracy.
   - Guidelines: applicable AGENTS.md and CLAUDE.md rules, plus docs/go-guidelines.md and docs/api-guidelines.md when present.
   - Keep actionable findings about documentation, tests, performance and maintainability even when they are not regressions. Explain pre-existing behavior and scope concerns so the author and human can decide.

Assign the draft prefixes regression-, style- and general-. Each subagent writes its own complete finding drafts using the skill's finding format and returns their paths, supporting evidence, coverage and limitations to the main reviewer. A lens may have no findings; do not invent issues to fill a quota. On repeat reviews, each lens also returns evidence about relevant previous findings for the main reviewer to recheck.

As the main reviewer, verify each candidate against the source and agreed scope. Reconcile overlapping claims about the same root cause into one finding while preserving distinct triggers and impacts; retain separate actionable issues even when they share a location. For a merged finding, use any one source draft name or join source names without .md using a hyphen; choose a short enough name for the final published filename. You may revise an unpublished source draft or create a merged draft. No lens metadata field is needed; the final draft name is carried into the published filename without interpreting it. Investigate disagreements in proportion to their impact, explain uncertainty and do not treat majority agreement as proof. Publish confirmed findings under the supplied reviewer ID using the skill protocol. Account for every draft: publish it, incorporate its evidence into another finding, or explain in the final report why it remains unpublished. Complete the review only after all launched subagents have returned or stopped, and state coverage and limitations for each lens in the report.
