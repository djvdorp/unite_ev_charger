"""Failsafe and heartbeat handling.

The wallbox falls back to ``failsafe_current`` if it does not receive an alive
write (register 6000) within ``failsafe_timeout`` seconds. We program a safe
failsafe at startup and write the heartbeat every poll cycle. This is what makes
it acceptable to steer the charging current from Home Assistant: if HA, the
network, or this integration dies, the charger stops (or limits) on its own.
"""
from __future__ import annotations

import logging

from . import registers as R
from .const import (
    DEFAULT_FAILSAFE_CURRENT_A,
    DEFAULT_FAILSAFE_TIMEOUT_S,
    HEARTBEAT_ALIVE_VALUE,
)
from .modbus import WebastoModbus, WebastoModbusError

_LOGGER = logging.getLogger(__name__)


async def program_failsafe(
    client: WebastoModbus,
    *,
    failsafe_current_a: int = DEFAULT_FAILSAFE_CURRENT_A,
    failsafe_timeout_s: int = DEFAULT_FAILSAFE_TIMEOUT_S,
) -> None:
    """Configure the charger's failsafe current and timeout.

    Best-effort: some firmware revisions reject these writes. We log and carry
    on rather than failing setup, but the heartbeat is still written each cycle.
    """
    try:
        await client.write_register(R.FAILSAFE_TIMEOUT_S, failsafe_timeout_s)
        await client.write_register(R.FAILSAFE_CURRENT_A, failsafe_current_a)
        _LOGGER.debug(
            "Programmed failsafe: %s A after %s s timeout",
            failsafe_current_a,
            failsafe_timeout_s,
        )
    except WebastoModbusError as err:
        _LOGGER.warning("Could not program failsafe registers (continuing): %s", err)


async def write_heartbeat(client: WebastoModbus) -> None:
    """Write the alive register. Raises so callers can surface comms loss."""
    await client.write_register(R.ALIVE, HEARTBEAT_ALIVE_VALUE)


# --- Baseline capture + restore -------------------------------------------
# Register 2000 (failsafe current) is persistent user-visible configuration:
# it survives a Modbus disconnect and even a power cycle, and a stale value
# actively drives behaviour (the charger overwrites 5004 with it once Alive
# lapses). So before our first write we capture what is there, and on exit we
# put it back. The baseline answers "before us", never "factory default".

# Keys used in the stored baseline dict.
BASELINE_SET_CURRENT = "set_current"
BASELINE_FAILSAFE_CURRENT = "failsafe_current"
BASELINE_FAILSAFE_TIMEOUT = "failsafe_timeout"
BASELINE_PHASE_SWITCH = "phase_switch"


async def capture_baseline(
    client: WebastoModbus, *, include_phase: bool
) -> dict[str, int | None]:
    """Read the registers we are about to manage, before writing any of them.

    Best-effort per register: a missing register becomes None and is simply
    skipped at restore time. Performs no writes.
    """
    baseline: dict[str, int | None] = {}
    for key, reg in (
        (BASELINE_SET_CURRENT, R.SET_CURRENT_A),
        (BASELINE_FAILSAFE_CURRENT, R.FAILSAFE_CURRENT_A),
        (BASELINE_FAILSAFE_TIMEOUT, R.FAILSAFE_TIMEOUT_S),
    ):
        try:
            baseline[key] = int(await client.read_register(reg))
        except WebastoModbusError:
            baseline[key] = None
    if include_phase:
        try:
            baseline[BASELINE_PHASE_SWITCH] = int(await client.read_register(R.PHASE_SWITCH))
        except WebastoModbusError:
            baseline[BASELINE_PHASE_SWITCH] = None
    else:
        baseline[BASELINE_PHASE_SWITCH] = None
    return baseline


async def restore_baseline(
    client: WebastoModbus, baseline: dict[str, int | None]
) -> list[str]:
    """Write captured values back and verify by read-back.

    Returns the keys that could not be restored (write or verify failed), so
    the caller can log them for manual recovery. Never raises.
    """
    failed: list[str] = []
    for key, reg in (
        (BASELINE_SET_CURRENT, R.SET_CURRENT_A),
        (BASELINE_FAILSAFE_CURRENT, R.FAILSAFE_CURRENT_A),
        (BASELINE_FAILSAFE_TIMEOUT, R.FAILSAFE_TIMEOUT_S),
        (BASELINE_PHASE_SWITCH, R.PHASE_SWITCH),
    ):
        value = baseline.get(key)
        if value is None:
            continue
        try:
            await client.write_register(reg, int(value))
            if int(await client.read_register(reg)) != int(value):
                failed.append(key)
        except WebastoModbusError:
            failed.append(key)
    return failed
