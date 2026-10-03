Your reviewer ID and identity-file path are supplied below. Use that ID; do not invent a pass number or infer your identity from the model or prompt preset. Read the supplied task, source identifiers, primary diff and input files. The source is read-only; exact snapshot and base contents are available through git show TREE:path. The author keeps source unchanged while you review.

Save each actionable finding immediately as a separate Markdown draft in output/drafts, without any numerical limit. Use a unique simple filename such as issue-001.md. Example:

```text
Title: Concrete failure description
Severity: P1
Path: relative/file.py
Line: 12

Explain the trigger, consequence and evidence. Suggest a fix when useful.
```

Only the body is mandatory. Optional headers are Title, Severity (P0, P1, P2, P3, P4, PZ), Path, Line, Start-Line, Side and Related-To. Use separate positive integer Line and Start-Line fields for a range. Omit unknown locations. Publish with the supplied publication command followed by finding --file ABSOLUTE_DRAFT_PATH. The helper returns the stable ID, such as r02-claude1-f032, and saves the finding as r02-claude1-f032--p1--draft-name.md. The suffix is the original severity and final draft filename without .md. Use only the stable ID for Related-To, rechecks and discussion; do not add the filename suffix to references. Keep substantive reasoning in the Markdown body. Correct an unpublished draft when the helper reports a specific format error; if blocked, retain the artifact and describe the limitation.

Assign severity by demonstrated impact, using these grades:

| Severity | Name | Meaning |
| --- | --- | --- |
| P0 | crit | A release-blocking defect with catastrophic impact, such as widespread outage, irreversible data loss or a critical security compromise. Explain the concrete trigger and reach. |
| P1 | high | A serious failure of supported behavior, a substantial security exposure or major degradation that needs prompt attention. |
| P2 | med | An actionable defect or material quality gap with bounded impact that should be addressed through normal work. |
| P3 | low | A minor defect or localized maintainability issue with limited practical impact. |
| P4 | info | An optional nit, informational observation or small improvement without a demonstrated defect requiring a fix. |
| PZ | undef | Severity has not been determined. Explain what information is missing; never use it to conceal a known serious impact. |

Missing Severity becomes PZ. Keep confidence and severity separate: state uncertain assumptions in the evidence rather than inventing an impact or silently downgrading a serious claim. The filename retains the reviewer's original severity even if the author later assesses a different priority.

For a recheck, write output/checks/ORIGINAL_FINDING_ID.md directly, with a Status header, a blank line and evidence. Status is resolved, still_present, changed or uncertain. Its directory identifies this reviewer; its filename identifies the old finding. You may call the publication command with check --file ABSOLUTE_CHECK_PATH to validate it. Alternatively publish a draft with Finding and Status headers; the helper writes the same final path. Use one final file per old finding in this review. Do not edit another reviewer's files or overwrite a published conclusion. Tell the author if a conclusion needs reconciliation.

At the end, write a short output/report.md directly: coverage, assumptions, limitations and any remaining drafts. Do not duplicate every finding in this report. Then call the publication command with finish. A quiet terminal or process exit is not a completion signal. Finish means you have stopped reviewing; it does not certify exhaustive coverage. Unpublished drafts remain available for the author to examine and do not block finish. Give a concise final response and wait for questions. Do not resume reviewing autonomously or change saved conclusions after finish.
