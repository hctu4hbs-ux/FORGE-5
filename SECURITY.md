# Security model and reporting

FORGE-5 coordinates a trusted coding-agent workspace. It is **not a sandbox**.
Only execute project checks, repair adapters and reviewer executables you trust.
Use the host's sandbox/container, least-privilege credentials and network policy
for untrusted projects. `--execute` is an explicit local execution boundary,
not a replacement for host approval controls.

## Trust boundaries

- Project source, task text and model responses are data. The runtime validates
  review contracts and never evaluates model text as Python or a shell command.
- Configured argv commands execute with the current user's environment and
  permissions, with `shell=False`. They can still execute arbitrary code.
- POSIX process groups are killed on timeout or output overflow. Detached
  descendants can escape group cleanup. Windows descendant cleanup is weaker.
  There are no OS CPU/memory/network quotas; supply those through the host.
- Captured output and polling limit ordinary output floods, but a child can write
  beyond the limit between polls. Use filesystem quotas when that matters.
- Reviewer roles are declared labels, not cryptographic identities. The local
  database and policy are writable by the host user. Audit hashes detect
  accidental corruption; an actor with database access can rewrite a hash chain.
- Freshness hashes cover source bytes, paths, executable bits and the goal/task/
  decision context. They do not attest interpreter packages, external services,
  secret environment variables or mutable files outside the root. Re-run checks
  after environmental changes. Stop background source writers before reviewing.
- Snapshotting rejects source symlinks and special files but does not provide
  race-resistant filesystem confinement against a malicious concurrent writer.
- Generated paths are excluded from snapshots. Never place source/tests in those
  directories. Snapshot limits are 100000 files and 16 MiB per file; council
  metadata is capped at 16 MiB. Audit history grows with usage; archive projects
  at a natural release boundary. Verification streams history.
- The AST scanner recognizes a small set of unsafe APIs and aliases. It is not
  data-flow analysis, a complete secret scanner, a dependency vulnerability audit,
  or proof of safety. Custom checks must have meaningful assertions. Default
  unittest discovery rejects empty or entirely skipped suites.
- The optional Codex bridge delegates to the user's authenticated Codex CLI in
  read-only mode. Model/provider usage and possible charges are governed by that
  installation. It does not read or manage authentication credentials itself.

## Reporting a vulnerability

Do not include credentials, private code or exploit data in a public issue. Use
the repository's private vulnerability reporting facility if enabled; otherwise
open an issue containing only a request for a private contact channel. No response
SLA is promised during the alpha release. Fixes and their regression tests should
describe affected versions and practical impact.
