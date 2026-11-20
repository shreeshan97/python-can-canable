"""Unit tests for capability-aware bit-timing selection."""

from __future__ import annotations

import pytest

from canable.bit_timing import select_classic_timing, select_fd_timing, validate_timing
from canable.protocol import BitTiming, Capability, DeviceFlags, TimingLimits


def make_capability(
    clock: int = 160_000_000,
    feature: int = int(DeviceFlags.PROTOCOL_ELMUE),
    **limits: int,
) -> Capability:
    """Build a capability record with optional timing-limit overrides."""
    values = {
        "seg1_min": 1,
        "seg1_max": 128,
        "seg2_min": 1,
        "seg2_max": 32,
        "sjw_max": 4,
        "brp_min": 1,
        "brp_max": 512,
        "brp_inc": 1,
    }
    values.update(limits)
    return Capability(feature, clock, TimingLimits(**values))


def sample_point(timing: BitTiming) -> float:
    """Compute the sample point percentage of a firmware timing."""
    total = 1 + timing.prop + timing.seg1 + timing.seg2
    return 100.0 * (1 + timing.prop + timing.seg1) / total


def test_validate_timing_checks_capability_limits() -> None:
    """Timing validation enforces segment, SJW, and prescaler limits."""
    limits = TimingLimits(1, 16, 1, 8, 4, 1, 64, 1)
    validate_timing(BitTiming(1, 14, 2, 2, 16), limits)
    with pytest.raises(ValueError):
        validate_timing(BitTiming(1, 20, 2, 2, 16), limits)
    with pytest.raises(ValueError):
        validate_timing(BitTiming(1, 14, 9, 2, 16), limits)
    with pytest.raises(ValueError):
        validate_timing(BitTiming(1, 14, 2, 5, 16), limits)
    with pytest.raises(ValueError):
        validate_timing(BitTiming(1, 14, 2, 2, 128), limits)


def test_classic_timing_160mhz_500k() -> None:
    """160 MHz and 500 kbit/s yields an exact 87.5 percent sample point."""
    timing = select_classic_timing(500_000, 87.5, make_capability())
    assert timing.prop == 1
    assert sample_point(timing) == pytest.approx(87.5)
    assert timing.sjw <= min(timing.prop + timing.seg1, timing.seg2, 4)
    validate_timing(timing, make_capability().limits)


def test_classic_timing_48mhz_reference_clock() -> None:
    """The 48 MHz reference clock also produces a valid 500 kbit/s timing."""
    capability = make_capability(clock=48_000_000)
    timing = select_classic_timing(500_000, 87.5, capability)
    assert sample_point(timing) == pytest.approx(87.5, abs=0.5)
    validate_timing(timing, capability.limits)


def test_classic_timing_respects_narrow_limits() -> None:
    """The fallback search uses capability limits rather than python-can limits."""
    capability = make_capability(seg1_max=16, seg2_max=8, brp_max=64)
    timing = select_classic_timing(500_000, 87.5, capability)
    validate_timing(timing, capability.limits)
    assert sample_point(timing) == pytest.approx(87.5, abs=0.5)


def test_classic_timing_is_deterministic() -> None:
    """Repeated selection returns the same candidate."""
    capability = make_capability()
    first = select_classic_timing(250_000, 87.5, capability)
    second = select_classic_timing(250_000, 87.5, capability)
    assert first == second


def test_classic_timing_rejects_invalid_requests() -> None:
    """Invalid bitrate and sample-point requests are rejected."""
    capability = make_capability()
    with pytest.raises(ValueError):
        select_classic_timing(0, 87.5, capability)
    with pytest.raises(ValueError):
        select_classic_timing(500_000, 40.0, capability)
    with pytest.raises(ValueError):
        select_classic_timing(1, 87.5, make_capability(brp_max=1, seg1_max=1, seg2_max=1))


def test_fd_timing_nominal_and_data_phases() -> None:
    """FD selection returns valid nominal and data-phase timings."""
    nominal = make_capability(
        feature=int(DeviceFlags.PROTOCOL_ELMUE | DeviceFlags.CAN_FD | DeviceFlags.BIT_TIMING_FD)
    )
    data = make_capability(
        feature=int(DeviceFlags.PROTOCOL_ELMUE | DeviceFlags.CAN_FD | DeviceFlags.BIT_TIMING_FD)
    )
    nominal_timing, data_timing = select_fd_timing(
        500_000, 87.5, 2_000_000, 87.5, nominal, data
    )
    assert sample_point(nominal_timing) == pytest.approx(87.5, abs=1.0)
    assert sample_point(data_timing) == pytest.approx(87.5, abs=4.0)
    validate_timing(nominal_timing, nominal.limits)
    validate_timing(data_timing, data.limits)


def test_fd_timing_rejects_invalid_data_bitrate() -> None:
    """Data bitrate lower than nominal is rejected."""
    capability = make_capability()
    with pytest.raises(ValueError):
        select_fd_timing(500_000, 87.5, 250_000, 87.5, capability, capability)
    with pytest.raises(ValueError):
        select_fd_timing(500_000, 87.5, 1_000_000, 40.0, capability, capability)
