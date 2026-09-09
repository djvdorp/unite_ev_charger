"""Unit tests for baseline capture + restore (safety.py).

Proves the ownership fix: before our first write we capture what is there,
and on exit we put it back and verify. Uses a fake client; no hardware.
"""
from __future__ import annotations

import asyncio

from uec import registers as R
from uec import safety as S
from uec.modbus import WebastoModbusError


class FakeClient:
    """Minimal read_register/write_register stand-in."""

    def __init__(self, regs: dict[int, int], fail_writes: set[str] | None = None):
        self._regs = dict(regs)
        self.fail_writes = fail_writes or set()
        self.writes: list[tuple[str, int]] = []

    async def read_register(self, reg):
        if reg.address not in self._regs:
            raise WebastoModbusError(f"no such register: {reg.name}")
        return self._regs[reg.address]

    async def write_register(self, reg, value):
        if reg.name in self.fail_writes:
            raise WebastoModbusError(f"write failed: {reg.name}")
        self.writes.append((reg.name, value))
        self._regs[reg.address] = value


FULL = {5004: 8, 2000: 12, 2002: 45, 405: 1}


def test_capture_reads_before_any_write():
    client = FakeClient(dict(FULL))
    baseline = asyncio.run(S.capture_baseline(client, include_phase=True))
    assert client.writes == []
    assert baseline == {
        "set_current": 8,
        "failsafe_current": 12,
        "failsafe_timeout": 45,
        "phase_switch": 1,
    }


def test_capture_skips_missing_registers():
    regs = {5004: 8, 2000: 12, 2002: 45}  # no 405 on this firmware
    baseline = asyncio.run(S.capture_baseline(FakeClient(regs), include_phase=True))
    assert baseline["phase_switch"] is None
    assert baseline["failsafe_current"] == 12


def test_capture_without_phase_leaves_phase_empty():
    baseline = asyncio.run(S.capture_baseline(FakeClient(dict(FULL)), include_phase=False))
    assert baseline["phase_switch"] is None
    assert baseline["set_current"] == 8


def test_restore_writes_back_and_verifies():
    client = FakeClient({5004: 6, 2000: 6, 2002: 30, 405: 0})
    baseline = {"set_current": 8, "failsafe_current": 12, "failsafe_timeout": 45, "phase_switch": 1}
    failed = asyncio.run(S.restore_baseline(client, baseline))
    assert failed == []
    assert client._regs[5004] == 8
    assert client._regs[2000] == 12
    assert client._regs[2002] == 45
    assert client._regs[405] == 1


def test_restore_skips_none_and_reports_failures_but_continues():
    client = FakeClient(
        {5004: 6, 2000: 6, 2002: 30, 405: 0}, fail_writes={"failsafe_current_a"}
    )
    baseline = {
        "set_current": 8,
        "failsafe_current": 12,
        "failsafe_timeout": 45,
        "phase_switch": None,  # never captured: skipped silently
    }
    failed = asyncio.run(S.restore_baseline(client, baseline))
    assert failed == ["failsafe_current"]
    # The other registers were still restored despite the failure.
    assert client._regs[5004] == 8
    assert client._regs[2002] == 45
    assert ("phase_switch", 1) not in client.writes


def test_restore_reports_verify_mismatch():
    class LyingClient(FakeClient):
        async def read_register(self, reg):
            if reg.address == 2000:
                return 999  # charger did not actually take the value
            return await super().read_register(reg)

    client = LyingClient(dict(FULL))
    failed = asyncio.run(
        S.restore_baseline(client, {"set_current": None, "failsafe_current": 12,
                                    "failsafe_timeout": None, "phase_switch": None})
    )
    assert failed == ["failsafe_current"]
