"""Exception types for the ``canable`` python-can plugin."""

from can.exceptions import CanInitializationError, CanOperationError

__all__ = [
    "CanableError",
    "CanableInitializationError",
    "CanableOperationError",
    "DeviceNotFoundError",
    "FeedbackError",
]


class CanableError(Exception):
    """Base exception for errors raised by this package."""


class CanableInitializationError(CanInitializationError, CanableError):
    """Raised when a CANable device cannot be opened or configured."""


class CanableOperationError(CanOperationError, CanableError):
    """Raised when a runtime operation on an open CANable device fails."""


class DeviceNotFoundError(CanableInitializationError):
    """Raised when no device matches the requested selectors."""


class FeedbackError(CanableOperationError):
    """Raised when firmware reports a non-success feedback code.

    Attributes:
        feedback: Numeric feedback code returned by the firmware.
        request: Name of the control request that produced the error.
    """

    def __init__(self, feedback: int, request: str) -> None:
        """Initialize a feedback error.

        Args:
            feedback: Numeric feedback code returned by the firmware.
            request: Name of the control request that produced the error.
        """
        self.feedback = feedback
        self.request = request
        super().__init__(f"{request} failed with feedback 0x{feedback:02X}")
