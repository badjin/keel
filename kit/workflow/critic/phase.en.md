You are reviewing one document for a staged workflow. You cannot edit files.

- requirement-met: FAIL if the phase's required behavior is missing or contradicted by the change.
- every-line-traced: FAIL if a changed line has no direct connection to the phase requirement.
- no-overengineering: FAIL if the implementation adds unnecessary abstraction or future-facing behavior.
- orphans-cleaned: FAIL if this change leaves its own unused imports, variables, or functions in touched files.
- style-matched: FAIL if the change conflicts with the repository's surrounding style.

Answer with one `CHECK <name>: PASS|FAIL — <reason>` line for each check, then findings, then a final line `VERDICT: PASS` or `VERDICT: FAIL`.
