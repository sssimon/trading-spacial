# API parity baselines

Each domain has a `<domain>.json` file capturing the HTTP responses produced
by `btc_api.py` against a deterministically seeded DB. Tests at
`tests/test_api_<domain>_parity.py` assert the post-refactor response matches
the baseline byte-for-byte.

## Regenerating a baseline

ONLY do this if the response format intentionally changed. Otherwise, a
mismatch is a real bug.

    python -m tests._baseline_capture <domain> > tests/_baselines/<domain>.json
    git add tests/_baselines/<domain>.json
    git commit -m "test(parity): regenerate <domain> baseline (<reason>)"
