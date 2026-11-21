# FORGE-5 paired evaluation

Mode: fixture

Deterministic fixture integration evaluation: identical repair oracle in both arms. No claim of LLM superiority or reviewer independence.

| Case | Arm | Success | Rounds | Seconds | Tests | Regressions |
| --- | --- | --- | --- | --- | --- | --- |
| logic | baseline | True | 1 | 0.449 | 3 | 0 |
| logic | forge5 | True | 1 | 1.454 | 3 | 0 |
| concurrency | baseline | True | 1 | 0.567 | 2 | 0 |
| concurrency | forge5 | True | 1 | 1.375 | 2 | 0 |
| security | baseline | True | 1 | 0.427 | 3 | 0 |
| security | forge5 | True | 1 | 1.338 | 3 | 0 |
| migration | baseline | True | 1 | 0.467 | 2 | 0 |
| migration | forge5 | True | 1 | 1.501 | 2 | 0 |
| performance | baseline | True | 1 | 0.467 | 2 | 0 |
| performance | forge5 | True | 1 | 1.649 | 2 | 0 |

Cost is null when the provider supplies no independently observable usage data.
Local execution is not a sandbox. Fixture timings measure orchestration overhead, not model quality.
