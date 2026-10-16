"""
aws.py — Lazy, cached boto3 clients for the hybrid data layer.

All boto3 clients are built on first use and cached for the life of the
process; nothing here performs network I/O or raises at import time, so the
module is safe to import even when AWS credentials are absent or expired.
Credentials come from boto3's default chain (AWS_* env vars loaded via
app.config.load_dotenv()); no credential handling is added here.
"""

from __future__ import annotations

from functools import lru_cache

import boto3

from app.config import settings


def _region() -> str:
    """Region for all clients, defaulting to us-east-1 when unset."""
    return settings.aws_region or "us-east-1"


@lru_cache(maxsize=1)
def s3_client():
    """Return a cached S3 client."""
    return boto3.client("s3", region_name=_region())


@lru_cache(maxsize=1)
def glue_client():
    """Return a cached Glue client."""
    return boto3.client("glue", region_name=_region())


@lru_cache(maxsize=1)
def athena_client():
    """Return a cached Athena client."""
    return boto3.client("athena", region_name=_region())


@lru_cache(maxsize=1)
def aws_available() -> bool:
    """
    Probe (at most once per process) whether usable AWS credentials exist.

    Calls STS GetCallerIdentity inside a try/except and returns True on
    success, False on any failure (missing/expired creds, no network, etc.).
    The result is cached so the probe runs only once.
    """
    try:
        boto3.client("sts", region_name=_region()).get_caller_identity()
        return True
    except Exception:
        return False
