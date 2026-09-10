"""
Custom exceptions for hydraulic_engine.
"""
# -*- coding: utf-8 -*-
from typing import Any, Optional


class HydraulicEngineError(Exception):
    """Base exception for all hydraulic_engine errors."""


class FileLoadError(HydraulicEngineError):
    """Raised when a file cannot be loaded."""


class FileWriteError(HydraulicEngineError):
    """Raised when a file cannot be written."""


class UnsupportedFileTypeError(HydraulicEngineError):
    """Raised when an unsupported file type is provided."""


class ModelNotLoadedError(HydraulicEngineError):
    """Raised when an operation requires a loaded model/file that is not loaded."""


class ValidationError(HydraulicEngineError):
    """Raised when model or file validation fails."""


class DatabaseError(HydraulicEngineError):
    """Raised when a database operation fails."""


class APIError(HydraulicEngineError):
    """Raised when an API client operation fails."""


class ExportError(HydraulicEngineError):
    """Raised when exporting results to a target system fails."""


class SimulationError(HydraulicEngineError):
    """Raised when a hydraulic simulation fails unexpectedly (engine crash)."""

    def __init__(self, message: str, result: Optional[Any] = None) -> None:
        super().__init__(message)
        self.result = result


class SimulationCancelled(HydraulicEngineError):
    """Raised when a simulation is stopped by the step callback."""

    def __init__(self, message: str, result: Optional[Any] = None) -> None:
        super().__init__(message)
        self.result = result
