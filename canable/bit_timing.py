"""Capability-aware bit-timing selection for Elmü CANable 2.5 adapters.

Python-can expresses bit timing as ``brp``, ``tseg1``, ``tseg2`` and ``sjw``, where
``tseg1`` excludes the synchronous segment. The firmware's ``kBitTiming`` splits
segment 1 into ``prop`` plus ``seg1``; this module always emits ``prop = 1`` and
``seg1 = tseg1 - 1`` so the effective segment length is preserved.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, List, Tuple

import can

from .protocol import BitTiming, Capability, TimingLimits

__all__ = [
    "validate_timing",
    "select_classic_timing",
    "select_fd_timing",
]

_MIN_CLASSIC_TQ = 8
_MIN_DATA_TQ = 5
_MAX_CLASSIC_SJW = 4
_MAX_DATA_SJW = 8


def _effective_seg1(timing: BitTiming) -> int:
    """Return the effective segment 1 length including the propagation segment."""
    return timing.prop + timing.seg1


def validate_timing(timing: BitTiming, limits: TimingLimits) -> None:
    """Validate firmware timing values against controller capability limits.

    Args:
        timing: Timing values to validate.
        limits: Controller limits decoded from ``kTimeMinMax``.

    Returns:
        ``None`` when the timing is valid.

    Raises:
        ValueError: If any timing value is outside the controller limits.
    """
    seg1 = _effective_seg1(timing)
    if timing.prop < 0 or timing.seg1 < 0:
        raise ValueError("segment values must not be negative")
    if not limits.seg1_min <= seg1 <= limits.seg1_max:
        raise ValueError(f"effective seg1 {seg1} outside {limits.seg1_min}..{limits.seg1_max}")
    if not limits.seg2_min <= timing.seg2 <= limits.seg2_max:
        raise ValueError(
            f"seg2 {timing.seg2} outside {limits.seg2_min}..{limits.seg2_max}"
        )
    if not 1 <= timing.sjw <= limits.sjw_max:
        raise ValueError(f"sjw {timing.sjw} exceeds maximum {limits.sjw_max}")
    if timing.sjw > seg1 or timing.sjw > timing.seg2:
        raise ValueError("sjw must not exceed seg1 or seg2")
    if not limits.brp_min <= timing.brp <= limits.brp_max:
        raise ValueError(f"brp {timing.brp} outside {limits.brp_min}..{limits.brp_max}")
    if limits.brp_inc > 0 and (timing.brp - limits.brp_min) % limits.brp_inc:
        raise ValueError(f"brp {timing.brp} does not satisfy increment {limits.brp_inc}")


def _sample_point(timing: BitTiming) -> float:
    """Return the sample point percentage of a firmware timing."""
    total_tq = 1 + _effective_seg1(timing) + timing.seg2
    return 100.0 * (1 + _effective_seg1(timing)) / total_tq


def _from_timing_values(
    brp: int, tseg1: int, tseg2: int, sjw: int, max_sjw: int
) -> BitTiming:
    """Convert raw timing fields to a firmware ``kBitTiming``."""
    effective_sjw = min(sjw, max_sjw, tseg1, tseg2)
    return BitTiming(
        prop=1, seg1=tseg1 - 1, seg2=tseg2, sjw=effective_sjw, brp=brp
    )


def _python_can_candidates(timing_class: Any, options: Any) -> Iterator[Any]:
    """Yield timing candidates using whichever python-can API is available.

    Args:
        timing_class: ``can.BitTiming`` or ``can.BitTimingFd``.
        options: Keyword arguments for the timing factory.

    Yields:
        Timing candidates; nothing when the installed python-can version cannot
        compute a timing for the request.
    """
    iterate = getattr(timing_class, "iterate_from_sample_point", None)
    if iterate is not None:
        try:
            yield from iterate(**options)
        except (ValueError, ZeroDivisionError, TypeError):
            return
        return
    factory = getattr(timing_class, "from_sample_point", None)
    if factory is None:
        return
    try:
        yield factory(**options)
    except (ValueError, ZeroDivisionError, TypeError):
        return


def _python_can_classic_candidates(
    clock: int, bitrate: int, sample_point: float
) -> Iterator[can.BitTiming]:
    """Yield python-can classic timing candidates for the installed version.

    Args:
        clock: CAN clock frequency in Hz.
        bitrate: Requested bitrate in bit/s.
        sample_point: Requested sample point in percent.

    Yields:
        Timing candidates from ``python-can``; nothing when the installed
        version cannot compute a timing for the request.
    """
    yield from _python_can_candidates(
        can.BitTiming,
        {"f_clock": clock, "bitrate": bitrate, "sample_point": sample_point},
    )


def _python_can_fd_candidates(
    clock: int,
    bitrate: int,
    sample_point: float,
    data_bitrate: int,
    data_sample_point: float,
) -> Iterator[Any]:
    """Yield python-can FD timing candidates for the installed version.

    Args:
        clock: CAN clock frequency in Hz.
        bitrate: Nominal bitrate in bit/s.
        sample_point: Nominal sample point in percent.
        data_bitrate: Data-phase bitrate in bit/s.
        data_sample_point: Data-phase sample point in percent.

    Yields:
        ``BitTimingFd`` candidates; nothing when FD timing is unavailable.
    """
    timing_fd = getattr(can, "BitTimingFd", None)
    if timing_fd is None:
        return
    yield from _python_can_candidates(
        timing_fd,
        {
            "f_clock": clock,
            "nom_bitrate": bitrate,
            "nom_sample_point": sample_point,
            "data_bitrate": data_bitrate,
            "data_sample_point": data_sample_point,
        },
    )


def _valid_candidates(
    candidates: Iterable[BitTiming], limits: TimingLimits
) -> Iterator[BitTiming]:
    """Yield unique candidates that satisfy the controller limits."""
    seen: set[Tuple[int, int, int, int, int]] = set()
    for candidate in candidates:
        key = (
            candidate.prop,
            candidate.seg1,
            candidate.seg2,
            candidate.sjw,
            candidate.brp,
        )
        if key in seen:
            continue
        seen.add(key)
        try:
            validate_timing(candidate, limits)
        except ValueError:
            continue
        yield candidate


def _search_timing(
    clock: int,
    bitrate: int,
    limits: TimingLimits,
    *,
    min_total_tq: int,
    max_sjw: int,
) -> List[BitTiming]:
    """Search all controller-compatible timings for a requested sample point.

    Args:
        clock: CAN clock frequency in Hz.
        bitrate: Requested bitrate in bit/s.
        limits: Controller timing limits.
        min_total_tq: Minimum number of time quanta per bit.
        max_sjw: Maximum synchronization jump width to emit.

    Returns:
        A list of valid timing candidates.
    """
    candidates: List[BitTiming] = []
    step = limits.brp_inc or 1
    for brp in range(limits.brp_min, limits.brp_max + 1, step):
        exact_tq = clock / (brp * bitrate)
        total_tq = round(exact_tq)
        if total_tq < min_total_tq:
            continue
        if abs(exact_tq - total_tq) > max(0.01, total_tq * 1e-4):
            continue
        max_seg2 = min(limits.seg2_max, total_tq - limits.seg1_min - 1)
        for seg2 in range(limits.seg2_min, max_seg2 + 1):
            seg1 = total_tq - 1 - seg2
            if not limits.seg1_min <= seg1 <= limits.seg1_max:
                continue
            sjw = min(max_sjw, limits.sjw_max, seg1, seg2)
            if sjw < 1:
                continue
            candidates.append(BitTiming(prop=1, seg1=seg1 - 1, seg2=seg2, sjw=sjw, brp=brp))
    return candidates


def _rank(timing: BitTiming, sample_point: float) -> Tuple[float, int, int, int, int]:
    """Return a deterministic sort key for timing candidates."""
    return (
        abs(_sample_point(timing) - sample_point),
        timing.brp,
        -timing.sjw,
        _effective_seg1(timing),
        timing.seg2,
    )


def _best_candidate(
    candidates: Iterable[BitTiming],
    limits: TimingLimits,
    sample_point: float,
    error_message: str,
) -> BitTiming:
    """Return the best valid timing or raise with a descriptive message."""
    valid = list(_valid_candidates(candidates, limits))
    if not valid:
        raise ValueError(error_message)
    return min(valid, key=lambda item: _rank(item, sample_point))


def _classic_candidates(
    clock: int, bitrate: int, sample_point: float, limits: TimingLimits
) -> List[BitTiming]:
    """Collect python-can and exhaustive-search candidates for a nominal rate."""
    python_can = (
        _from_timing_values(
            candidate.brp,
            candidate.tseg1,
            candidate.tseg2,
            candidate.sjw,
            _MAX_CLASSIC_SJW,
        )
        for candidate in _python_can_classic_candidates(clock, bitrate, sample_point)
    )
    return [
        *python_can,
        *_search_timing(
            clock,
            bitrate,
            limits,
            min_total_tq=_MIN_CLASSIC_TQ,
            max_sjw=_MAX_CLASSIC_SJW,
        ),
    ]


def select_classic_timing(
    bitrate: int, sample_point: float, capability: Capability
) -> BitTiming:
    """Select classic CAN bit timing for a controller and requested sample point.

    Args:
        bitrate: Nominal bitrate in bit/s.
        sample_point: Requested sample point in percent.
        capability: Capability record containing the clock and timing limits.

    Returns:
        Firmware timing values ready for ``GS_ReqSetBitTiming``.

    Raises:
        ValueError: If the bitrate or sample point is invalid, or no compatible
            timing exists.
    """
    if bitrate <= 0:
        raise ValueError("bitrate must be positive")
    if not 50.0 <= sample_point <= 95.0:
        raise ValueError("sample_point must be between 50 and 95 percent")
    error_message = (
        f"no classic timing for {bitrate} bit/s at {sample_point}% with "
        f"{capability.fclk_can} Hz clock"
    )
    return _best_candidate(
        _classic_candidates(
            capability.fclk_can, bitrate, sample_point, capability.limits
        ),
        capability.limits,
        sample_point,
        error_message,
    )


def select_fd_timing(
    bitrate: int,
    sample_point: float,
    data_bitrate: int,
    data_sample_point: float,
    nominal: Capability,
    data: Capability,
) -> Tuple[BitTiming, BitTiming]:
    """Select nominal and data phase timings for CAN FD.

    Args:
        bitrate: Nominal (arbitration) bitrate in bit/s.
        sample_point: Nominal sample point in percent.
        data_bitrate: Data phase bitrate in bit/s.
        data_sample_point: Data phase sample point in percent.
        nominal: Capability record for the nominal phase.
        data: Capability record for the data phase.

    Returns:
        A ``(nominal_timing, data_timing)`` tuple.

    Raises:
        ValueError: If any parameter is invalid or no compatible timing exists.
    """
    if bitrate <= 0:
        raise ValueError("bitrate must be positive")
    if data_bitrate < bitrate:
        raise ValueError("data_bitrate must be greater than or equal to bitrate")
    if not 50.0 <= sample_point <= 95.0:
        raise ValueError("sample_point must be between 50 and 95 percent")
    if not 50.0 <= data_sample_point <= 95.0:
        raise ValueError("data_sample_point must be between 50 and 95 percent")

    nominal_candidates = _classic_candidates(
        nominal.fclk_can, bitrate, sample_point, nominal.limits
    )
    for candidate in _python_can_fd_candidates(
        nominal.fclk_can,
        bitrate,
        sample_point,
        data_bitrate,
        data_sample_point,
    ):
        nominal_candidates.append(
            _from_timing_values(
                candidate.nom_brp,
                candidate.nom_tseg1,
                candidate.nom_tseg2,
                candidate.nom_sjw,
                _MAX_CLASSIC_SJW,
            )
        )
    nominal_best = _best_candidate(
        nominal_candidates,
        nominal.limits,
        sample_point,
        f"no classic timing for {bitrate} bit/s at {sample_point}% with "
        f"{nominal.fclk_can} Hz clock",
    )

    data_best = _best_candidate(
        _search_timing(
            data.fclk_can,
            data_bitrate,
            data.limits,
            min_total_tq=_MIN_DATA_TQ,
            max_sjw=_MAX_DATA_SJW,
        ),
        data.limits,
        data_sample_point,
        f"no FD data timing for {data_bitrate} bit/s at {data_sample_point}% "
        f"with {data.fclk_can} Hz clock",
    )
    return nominal_best, data_best
