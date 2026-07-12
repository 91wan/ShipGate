# Code Review Contract

Review ShipGate changes as security-boundary changes, even when the patch looks
like ordinary parsing or reporting work.

## Required Questions

1. Does every gate consume the shared inventory rather than walking files again?
2. Can unreadable, unrecognized, shallow, special or skipped content become pass?
3. Is the selected operation bound to the source that will actually be public?
4. Can a path, finding, remote value or error disclose host-private information?
5. Does any subprocess use a shell, unbounded input, inherited prompts or no timeout?
6. Can a symlink or path traversal cross the project or installer boundary?
7. Are asset size and hash derived from one stable open handle?
8. Are English and Chinese docs, skill instructions and adapters synchronized?
9. Does each behavior change have a positive and negative regression test?
10. Do line and branch coverage remain at or above the documented thresholds?

## Prohibited Patterns

- `shell=True` or commands assembled from untrusted strings.
- Automatic push, tag, release, upload, remote or authentication writes.
- Broad directory skips for publication content.
- Catch-and-pass exception handling.
- Full credential matches or absolute host paths in reports.
- Unbounded recursive deletion in installation code.
- README keyword counts presented as content-quality proof.
- Project type decisions based only on a container suffix or comment.

## Final Review

Search the diff for private paths, credential-shaped fixtures, generated caches,
vendored dependencies, `shell=True`, deletion primitives and documentation drift.
Run the adversarial tests, then inspect both a passing and failing report. A
green overall status without understandable source and inventory evidence is not
sufficient review evidence.
