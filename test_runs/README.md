# Test run evidence

The central test archive currently contains:

- `2026-11-22_round-01/`: complete reproducible test round with raw
  stdout/stderr, timing, exit codes, environment, and failure attribution.
- `2026-11-22_round-02/`: post-fix round with 84-question route coverage,
  strict freshness checks and corrected audit failure semantics.
- `2026-11-22_comparison-01/`: same-model comparison of force-aware QA,
  plain local RAG and direct model knowledge over all 84 questions.
- `legacy-archive/`: earlier QA datasets and answers, blind-review package,
  temporal review queue, self-check/spot-check evidence, maintenance logs, and
  the PageIndex real-client sample.

Each `YYYY-MM-DD_round-NN/` directory is one project test round produced by:

```powershell
python tools\run_test_round.py
```

Use the bundled project runtime when the shell default Python does not include
the project dependencies:

```powershell
& 'C:\Users\Lenovo\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' `
  tools\run_test_round.py
```

The directory contains `summary.json`, `summary.md`, `environment.json`, and
the raw stdout/stderr of every test. Add `--upstream` to include the PageIndex
submodule tests. Generated evidence is not part of the application runtime.

Refresh the historical snapshot without changing canonical source files:

```powershell
python tools\archive_legacy_tests.py
```
