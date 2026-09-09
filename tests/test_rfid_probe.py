"""Unit tests for the RFID capability probe (RfidProbe).

The probe decides whether the coordinator may read the optional session-RFID
registers (1516-1530, only on newer firmware). New firmware must never notice
it; old firmware gets at most one failed read per connection, and a wallbox
that dies on the probe is left alone after two strikes.
"""
from __future__ import annotations

from uec import control as C


def test_fresh_probe_wants_one_probe():
    probe = C.RfidProbe()
    assert probe.want_probe is True


def test_success_latches_supported_and_clears_strikes():
    probe = C.RfidProbe()
    probe.note_transport_error()
    probe.note_ok()
    assert probe.supported is True
    assert probe.transport_strikes == 0
    assert probe.want_probe is True


def test_clean_refusal_disables_until_reconnect():
    probe = C.RfidProbe()
    probe.note_unsupported()
    assert probe.want_probe is False
    probe.reset_on_reconnect()
    assert probe.want_probe is True


def test_first_transport_error_only_quiets_until_reconnect():
    probe = C.RfidProbe()
    assert probe.note_transport_error() is False
    assert probe.want_probe is False
    assert probe.session_disabled is False
    probe.reset_on_reconnect()
    assert probe.want_probe is True


def test_second_transport_error_disables_for_the_session():
    probe = C.RfidProbe()
    assert probe.note_transport_error() is False
    probe.reset_on_reconnect()
    assert probe.note_transport_error() is True
    assert probe.session_disabled is True
    assert probe.want_probe is False
    # A reconnect must not re-enable a session-disabled probe.
    probe.reset_on_reconnect()
    assert probe.want_probe is False


def test_old_polite_firmware_costs_one_read_per_connection():
    # Simulate: probe refused every connection, reconnects in between.
    probe = C.RfidProbe()
    for _ in range(5):
        assert probe.want_probe is True
        probe.note_unsupported()
        assert probe.want_probe is False
        probe.reset_on_reconnect()
    assert probe.transport_strikes == 0
    assert probe.session_disabled is False
