import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Set dummy env vars before importing main
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"


import time


def test_main_imports():
    from src import main
    assert main.app is not None
    assert main.telegram_app is not None
    print("✓ Main module imports successfully")


def test_rate_limiter():
    from src.main import SimpleRateLimiter

    limiter = SimpleRateLimiter(max_requests=2, window_seconds=1)
    assert limiter.is_allowed("ip1") is True
    assert limiter.is_allowed("ip1") is True
    assert limiter.is_allowed("ip1") is False
    assert limiter.is_allowed("ip2") is True

    time.sleep(1.1)
    assert limiter.is_allowed("ip1") is True
    print("✓ Webhook rate limiter tests passed")


if __name__ == "__main__":
    test_main_imports()
    test_rate_limiter()
    print("\nMain tests passed!")
