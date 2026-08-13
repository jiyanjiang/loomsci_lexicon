#!/usr/bin/env python3
"""DNS resilience for DeepSeek API calls (loomsci_lexicon).

Backported from sci365/scripts/generate_arxiv_terms.py (verified in prod),
extended with a DISK-PERSISTED lookup cache so that transient system-DNS
failures ("Failed to resolve 'api.deepseek.com' / nodename nor servname")
are served from the last-known-good IP addresses instead of hard-failing.

How it works:
  1. On import, monkey-patches socket.getaddrinfo.
  2. Successful lookups are cached in memory AND on disk
     (~/.cache/loomsci_lexicon_dns.json, TTL 7 days).
  3. If system DNS raises gaierror, the cache is used as a fallback;
     if there is no cache, the original error propagates (caller retries).
  4. classify_error() / backoff_for() help callers retry smartly.

Usage:  import dns_patch   (before any requests call)
"""
import json
import os
import socket
import threading
import time

_ORIG_GETADDRINFO = socket.getaddrinfo
_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "loomsci_lexicon_dns.json")
_TTL = 7 * 24 * 3600  # 7 days

_mem = {}  # "host:port" -> (serializable result, ts)
_lock = threading.Lock()


def _load_disk():
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data or {}
    except Exception:
        return {}


def _save_disk():
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        tmp = _CACHE_FILE + ".tmp"
        # keys are already "host:port" strings (JSON-safe)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_mem, f)
        os.replace(tmp, _CACHE_FILE)
    except Exception:
        pass


def _serialize(result):
    # [(family, socktype, proto, canonname, sockaddr), ...] -> JSON-safe
    out = []
    for family, socktype, proto, canon, sockaddr in result:
        out.append([family, socktype, proto, canon, list(sockaddr)])
    return out


def _deserialize(data):
    out = []
    for family, socktype, proto, canon, sockaddr in data:
        out.append((family, socktype, proto, canon, tuple(sockaddr)))
    return tuple(out)


def _getaddrinfo(host, port, *args, **kwargs):
    key = f"{host}:{port}"
    now = time.time()
    with _lock:
        hit = _mem.get(key)
        if hit and now - hit[1] < _TTL:
            return _deserialize(hit[0])
    try:
        result = _ORIG_GETADDRINFO(host, port, *args, **kwargs)
    except socket.gaierror:
        # system DNS failed -> fall back to disk cache (last known good)
        with _lock:
            hit = _mem.get(key)
            if hit and now - hit[1] < _TTL:
                return _deserialize(hit[0])
        # disk cache may be stale in memory (fresh process): reload
        disk = _load_disk()
        raw = disk.get(key)
        if raw and now - raw[1] < _TTL:
            with _lock:
                _mem[key] = raw
            return _deserialize(raw[0])
        raise  # no cache: let the caller retry
    with _lock:
        _mem[key] = (_serialize(result), now)
    _save_disk()
    return result


socket.getaddrinfo = _getaddrinfo

# ---------------------------------------------------------------------------
# error classification + smart backoff
# ---------------------------------------------------------------------------

DNS_KEYWORDS = ("getaddrinfo", "nodename", "name or service not known",
                "failed to resolve", "no address associated with hostname")


def classify_error(e) -> str:
    s = str(e)
    s_l = s.lower()
    if any(k in s_l for k in DNS_KEYWORDS):
        return "dns"
    if "429" in s or "too many requests" in s_l or "rate limit" in s_l:
        return "rate"
    if "5" in s[:3] or "internal server" in s_l or "bad gateway" in s_l \
            or "service unavailable" in s_l:
        return "server"
    if "timeout" in s_l or "timed out" in s_l:
        return "timeout"
    return "other"


def backoff_for(e, attempt) -> float:
    """Seconds to sleep before the next retry.
    dns/timeout: aggressive exponential (transient) up to 60s;
    others: linear 2s increments.
    """
    cls = classify_error(e)
    if cls in ("dns", "timeout", "rate"):
        return min(60.0, 5.0 * (2 ** attempt))
    return min(30.0, 2.0 * (attempt + 1))
