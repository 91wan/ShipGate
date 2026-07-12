# Threat Model

## Protected Outcome

Prevent a public push, tag or release from receiving a trustworthy-looking pass
when required publication content was not inspected or when configured high-risk
indicators, invalid release structure, or unstable assets were found.

## In Scope

- Credentials, private-key headers and private host paths in working files,
  Git index content, reachable history and supplied assets' metadata.
- Tracked `.github`, build and distribution files when they belong to the
  declared publication surface.
- Large files, UTF-8/UTF-16 text and ASCII indicators inside binary content.
- Broken, external and unsupported symlinks; special or unreadable files.
- Shallow history, unresolved Git refs and unverified gitlinks/submodules.
- Report leakage through absolute paths, full matches or unstable output.
- Asset replacement or mutation while size and hash are being read.

## Trust Boundaries

The project filesystem, Git executable/object database, report destination and
release assets are untrusted inputs. ShipGate never invokes a shell and does not
perform network or publication writes.

Git metadata is accepted only after commands complete successfully within a
timeout. File content is opened without following the final symlink where the
platform supports it, and metadata is compared before and after streaming.

## Fail-Closed Rules

- Read or inventory failure becomes `error`.
- Public Git operations fail for shallow history or unverified submodules.
- External symlinks are not followed; broken and unsupported links block.
- Missing release assets fail unless `--source-only` is explicit.
- No directory name is broadly ignored once it belongs to the Git publication surface.
- Reports contain relative labels and fingerprints, not matched secret values.

## Out of Scope

- Proving no unknown secret exists.
- Semantic translation-quality review.
- Code signing, notarization, malware analysis or binary archive extraction.
- GitHub authentication, remote authorization or release-state mutation.
- Network-side verification of uploaded assets.
- Fully validating submodule content or Git LFS payloads in the current version.

These out-of-scope checks must not be described as passed. A host workflow may
add separate evidence after ShipGate succeeds.
