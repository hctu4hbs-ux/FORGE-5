# Executable evaluation projects

The project generators and the deterministic, explicitly non-AI repair provider live in `forge5.evaluation.case_suite` and `FIXTURE_PROVIDER`. Running the command below materializes real source files, a service facade, protected tests and independent baseline/FORGE-5 workspaces for all five cases; it runs the tests before and after each repair.

```bash
python -m pip install -e .
forge5 evaluate --mode fixture --execute --allow-local --run-id example-five --format markdown
```

Inspect `.forge5/evaluations/example-five/report.json` and the `logic`, `concurrency`, `security`, `migration` and `performance` directories under that run. To retry an interrupted run, repeat the exact command with `--resume`. Use a new run ID after changing the engine, evaluation configuration or protected files.

This example uses a declared repair oracle in both arms and measures protocol integration. It is not evidence that an AI model discovered these repairs. See [the full evaluation guide](../../docs/evaluation-v2.md) for provider comparisons and explicit limits.
