"""Token-bucket per channel. Default capacity=20, refill_per_sec=20/60 = 0.333."""
import time


def test_fresh_bucket_allows_up_to_capacity():
    from notifier.ratelimit import TokenBucket
    b = TokenBucket(capacity=20, refill_per_sec=1.0)
    # Fresh bucket starts full; can consume 20 without waiting
    for _ in range(20):
        assert b.acquire() is True
    # 21st must fail (bucket empty, no time passed)
    assert b.acquire() is False


def test_refill_over_time():
    from notifier.ratelimit import TokenBucket
    b = TokenBucket(capacity=10, refill_per_sec=10.0)
    # Drain
    for _ in range(10):
        assert b.acquire() is True
    assert b.acquire() is False
    time.sleep(0.5)  # refill 5 tokens
    # Now ~5 should be available
    acquired = sum(1 for _ in range(10) if b.acquire())
    assert 3 <= acquired <= 7, f"expected ~5 refilled tokens, got {acquired}"


def test_bucket_never_exceeds_capacity():
    from notifier.ratelimit import TokenBucket
    b = TokenBucket(capacity=5, refill_per_sec=100.0)
    time.sleep(0.5)  # should refill way past capacity
    # Can only drain up to capacity even though many tokens were "refilled"
    for _ in range(5):
        assert b.acquire() is True
    assert b.acquire() is False
