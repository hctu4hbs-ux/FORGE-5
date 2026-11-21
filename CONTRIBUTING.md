# Contributing

Bring a concrete engineering problem, a reproduction and observed evidence.
Keep runtime changes provider-neutral and preserve the zero-dependency core.

1. Open an issue for a substantial change, describing the user-visible behavior
   and acceptance criteria. Small focused fixes can go directly to a pull request.
2. Install `.[dev]` in a virtual environment. Run the checks in the README and the
   DurableFlow test suite for integration changes.
3. Add a regression for a plausible defect. Avoid tests that only mirror private
   implementation details or inflate counts. Do not weaken release policy to make
   a failing test pass.
4. Document compatibility changes, trust assumptions and measured limits. Keep
   the portable skill concise; put supporting procedures in its references.
5. Include the commands actually run, results, and any missing capability in the
   pull request. Do not claim independent review when it was a sequential self-review.

Use a clear, respectful tone. Report security issues according to SECURITY.md.
Contributions are accepted under the project's MIT license.
