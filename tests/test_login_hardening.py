# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

"""Tests for login hardening: rate-limiting and allow_create vault guard."""
from __future__ import annotations

import time
import unittest
from unittest.mock import patch, MagicMock

# ── 1.  vault_exists / allow_create guard ────────────────────────────────────

class TestAllowCreateGuard(unittest.TestCase):
    """open_or_create_vault must refuse creation when allow_create=False."""

    @patch("database.core.load_keybag", return_value=None)
    def test_rejects_creation_when_allow_create_false(self, _mock_kb):
        from database.core import open_or_create_vault
        with self.assertRaises(ValueError) as ctx:
            open_or_create_vault("any-password", allow_create=False)
        self.assertIn("No vault exists", str(ctx.exception))

    @patch("database.core.load_keybag", return_value=None)
    @patch("database.core.create_new_keybag", return_value=(b"\x00" * 32, "recovery_b64"))
    @patch("database.core.init_db_with_db_key")
    def test_allows_creation_when_allow_create_true(self, mock_init, mock_create, _mock_kb):
        from database.core import open_or_create_vault
        mock_init.return_value = MagicMock()
        conn, dmk, path, rk = open_or_create_vault("pw", allow_create=True)
        mock_create.assert_called_once()
        self.assertEqual(rk, "recovery_b64")

    @patch("database.core.load_keybag", return_value={"some": "keybag"})
    @patch("database.core.unlock_db_key_with_password", return_value=b"\x00" * 32)
    @patch("database.core.init_db_with_db_key")
    def test_unlocks_existing_vault_regardless_of_flag(self, mock_init, mock_unlock, _mock_kb):
        from database.core import open_or_create_vault
        mock_init.return_value = MagicMock()
        # allow_create=False should still unlock an existing vault just fine
        conn, dmk, path, rk = open_or_create_vault("pw", allow_create=False)
        mock_unlock.assert_called_once()
        self.assertIsNone(rk)


class TestVaultExists(unittest.TestCase):
    """vault_exists() should reflect keybag presence."""

    @patch("database.core.load_keybag", return_value=None)
    def test_no_vault(self, _mock):
        from database.core import vault_exists
        self.assertFalse(vault_exists())

    @patch("database.core.load_keybag", return_value={"v": 1})
    def test_vault_present(self, _mock):
        from database.core import vault_exists
        self.assertTrue(vault_exists())


# ── 2.  Rate-limiting logic (unit-tested without Flet) ───────────────────────

class TestRateLimiting(unittest.TestCase):
    """Test the rate-limiting state machine extracted from login.py."""

    def setUp(self):
        """Replicate the login state dict and helper functions."""
        self.state = {
            "failed_attempts": 0,
            "lockout_until": 0.0,
        }
        self.MAX = 5
        self.LOCKOUT = 30

    def _is_locked(self) -> bool:
        return self.state["lockout_until"] - time.time() > 0

    def _record_failure(self):
        self.state["failed_attempts"] += 1
        if self.state["failed_attempts"] >= self.MAX:
            self.state["lockout_until"] = time.time() + self.LOCKOUT
            self.state["failed_attempts"] = 0

    def _record_success(self):
        self.state["failed_attempts"] = 0
        self.state["lockout_until"] = 0.0

    def test_not_locked_initially(self):
        self.assertFalse(self._is_locked())

    def test_four_failures_no_lockout(self):
        for _ in range(4):
            self._record_failure()
        self.assertFalse(self._is_locked())
        self.assertEqual(self.state["failed_attempts"], 4)

    def test_five_failures_triggers_lockout(self):
        for _ in range(5):
            self._record_failure()
        self.assertTrue(self._is_locked())
        # Counter resets after lockout engaged
        self.assertEqual(self.state["failed_attempts"], 0)

    def test_lockout_expires(self):
        for _ in range(5):
            self._record_failure()
        self.assertTrue(self._is_locked())
        # Fast-forward past lockout
        self.state["lockout_until"] = time.time() - 1
        self.assertFalse(self._is_locked())

    def test_success_resets_counter(self):
        for _ in range(4):
            self._record_failure()
        self._record_success()
        self.assertEqual(self.state["failed_attempts"], 0)
        self.assertFalse(self._is_locked())

    def test_success_clears_active_lockout(self):
        for _ in range(5):
            self._record_failure()
        self.assertTrue(self._is_locked())
        self._record_success()
        self.assertFalse(self._is_locked())

    def test_counter_resets_after_lockout_window(self):
        """After lockout expires, the user gets a fresh set of 5 attempts."""
        for _ in range(5):
            self._record_failure()
        # Lockout engages and counter resets to 0
        self.assertEqual(self.state["failed_attempts"], 0)
        # Expire the lockout
        self.state["lockout_until"] = time.time() - 1
        # User gets 4 more failures before next lockout
        for _ in range(4):
            self._record_failure()
        self.assertFalse(self._is_locked())
        # 5th failure triggers lockout again
        self._record_failure()
        self.assertTrue(self._is_locked())


if __name__ == "__main__":
    unittest.main()
