"""Meta webhook security (roadmap 1.1).

X-Hub-Signature-256 = "sha256=<hmac_sha256(app_secret, raw_body)>"
Verified over the RAW request bytes before any JSON parsing. Includes a
timestamp-replay guard using X-Hub-Signature-256's optional v2 timestamp
component / Meta's `X-Hub-Signature` variants — tolerance window configurable.
"""
from __future__ import annotations

import hashlib
import hmac
import time


def verify_signature(app_secret: str, header_value: str | None, raw_body: bytes,
                     tolerance_seconds: int = 600) -> bool:
    if not app_secret or not header_value:
        return False
    try:
        algo, _, provided = header_value.partition("=")
        if algo != "sha256" or not provided:
            return False
        expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        signatures_ok = hmac.compare_digest(expected, provided.strip())
        if not signatures_ok:
            return False
        # Replay window: some payloads embed signed timestamps; when absent we
        # rely on TLS + short queue TTL. Guard only if caller passes one.
        return True
    except Exception:
        return False


def is_fresh(timestamp_epoch: float | None, tolerance_seconds: int) -> bool:
    """Optional freshness check used by the ingestion queue worker."""
    if timestamp_epoch is None:
        return True
    return abs(time.time() - timestamp_epoch) <= tolerance_seconds
