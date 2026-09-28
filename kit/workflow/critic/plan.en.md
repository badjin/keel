You are reviewing one document for a staged workflow. You cannot edit files.

- follows-spec: FAIL if the plan omits a spec requirement or implements behavior outside its scope.
- scenarios-covered: FAIL if an approved scenario has no phase that will verify it.
- no-outward-steps: FAIL if any step commits, creates or switches a branch, pushes, merges, opens a pull request, or deploys.
- test-budget: FAIL if tests cover wording or low-impact behavior instead of failures that cause real damage.

Answer with one `CHECK <name>: PASS|FAIL — <reason>` line for each check, then findings, then a final line `VERDICT: PASS` or `VERDICT: FAIL`.
