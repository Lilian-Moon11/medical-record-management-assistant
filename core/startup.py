# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Startup cryptographic self-test wrapper.
#
# This module provides a thin abstraction around the lower-level
# run_crypto_self_test() implementation to keep startup orchestration
# decoupled from crypto internals.
#
# Responsibilities include:
# - Executing the vault integrity and key-consistency self-test
# - Returning the structured SelfTestResult to the caller
# - Leaving fail-closed behavior (closing connections, returning to login,
#   showing UI errors) to higher-level application logic
#
# Design goal:
# - Keep the app startup flow clean and testable by isolating crypto validation
#   behind a small, predictable interface.
# -----------------------------------------------------------------------------

from __future__ import annotations
from crypto.selftest import run_crypto_self_test


def run_self_test(*, db_path: str, conn, db_key_raw: bytes, password: str):
    """
    Runs crypto self-test and returns the result object.
    Caller decides how to fail-closed (close conn, return to login, etc).
    """
    return run_crypto_self_test(
        db_path=db_path,
        conn=conn,
        db_key_raw=db_key_raw,
        password=password,
    )