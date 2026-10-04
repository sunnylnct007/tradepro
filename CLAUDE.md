
## Merging — the gate, and why it exists

`main` requires three passing checks (`python suite`, `backend suite`,
`frontend build + typecheck`) from `.github/workflows/ci-tests.yml`.

**Never run tests and merge in the same shell command.** Two broken merges
(#250, #277) happened exactly that way: the suite reported a failure, the
output scrolled past, and the merge went through in the same invocation.
Run the suite, READ the result, then merge as a separate step.

**Do not use `gh pr merge --admin`.** It bypasses the checks above. If a
merge is genuinely blocked by something unrelated, say so and ask — an
override is the owner's decision, not a convenience.
