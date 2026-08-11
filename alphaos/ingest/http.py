"""One throttled HTTP client for every outbound call.

Two hard-won constraints from AsOf's ingest layer, both verified against live
SEC endpoints:

* The User-Agent must look like an email address. SEC returns 403 for a UA
  containing a URL.
* Calls must be serialized with a real gap. SEC's published ceiling is 10/s;
  350ms of separation has run for months without a throttle.
"""
from __future__ import annotations

import os
import time

import requests

USER_AGENT = os.environ.get("ALPHAOS_USER_AGENT", "AlphaOS/0.1 haeganm@gmail.com")
MIN_GAP_S = 0.35
TIMEOUT_S = 30
RETRIES = 3

_last_call = 0.0
_session = None


def _sess():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        })
    return _session


def _throttle():
    global _last_call
    wait = MIN_GAP_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def get(url: str, *, stream: bool = False, timeout: int = TIMEOUT_S, headers: dict | None = None):
    """Throttled GET with backoff. 4xx other than 429 fails immediately --
    retrying a 403 just burns goodwill with the SEC."""
    last = None
    for attempt in range(1, RETRIES + 1):
        _throttle()
        try:
            r = _sess().get(url, stream=stream, timeout=timeout, headers=headers)
        except requests.RequestException as exc:
            last = exc
            time.sleep(0.6 * attempt)
            continue
        if r.status_code == 200:
            return r
        if 400 <= r.status_code < 500 and r.status_code != 429:
            r.raise_for_status()
        last = requests.HTTPError(f"{r.status_code} for {url}")
        time.sleep(0.6 * attempt)
    raise last


def get_json(url: str):
    return get(url).json()


def download(url: str, dest: str, *, chunk: int = 1 << 20, log_every_mb: int = 100) -> str:
    """Stream a large file to disk. Writes to .part then renames, so an
    interrupted download never masquerades as a complete cache entry."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    r = get(url, stream=True, timeout=120)
    total = int(r.headers.get("Content-Length", 0))
    done = 0
    next_log = log_every_mb << 20
    with open(part, "wb") as fh:
        for block in r.iter_content(chunk_size=chunk):
            if not block:
                continue
            fh.write(block)
            done += len(block)
            if done >= next_log:
                pct = f" ({100 * done / total:.0f}%)" if total else ""
                print(f"  {done >> 20} MB{pct}", flush=True)
                next_log += log_every_mb << 20
    os.replace(part, dest)
    return dest
