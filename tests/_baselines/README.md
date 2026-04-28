# Baselines

This directory holds frozen snapshots of `scan()` output used by `tests/test_scanner_snapshot.py` to detect regressions during the per-purpose refactor of `btc_scanner.py` (issue #225).

## When to regenerate scanner baselines

**Almost never.** The baseline is the ground truth that proves the refactor preserves `scan()` behavior byte-for-byte.

If you intentionally change `scan()` output (new field, fixed bug, new indicator):

1. Discuss the change with a reviewer first.
2. Run `pytest tests/_fixtures/capture_baseline.py::test_capture_btcusdt -s` to regenerate.
3. Diff the new vs old baseline and commit BOTH the baseline and the code change in the same PR.
4. PR description must explain the intentional drift.

If a refactor PR causes the snapshot to drift, **STOP** and investigate. It means the refactor introduced a behavior change. Don't regenerate to make the test pass.

## Files

- `scan_btcusdt.json` — full `scan("BTCUSDT")` return value with frozen clock + klines + network mocks.

---

## API parity baselines

Each domain has a `<domain>.json` file capturing the HTTP responses produced
by `btc_api.py` against a deterministically seeded DB. Tests at
`tests/test_api_<domain>_parity.py` assert the post-refactor response matches
the baseline byte-for-byte.

## Regenerating an API parity baseline

ONLY do this if the response format intentionally changed. Otherwise, a
mismatch is a real bug.

    python -m tests._baseline_capture <domain> > tests/_baselines/<domain>.json
    git add tests/_baselines/<domain>.json
    git commit -m "test(parity): regenerate <domain> baseline (<reason>)"
