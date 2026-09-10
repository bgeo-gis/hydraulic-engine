"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

SWMM module - Storm Water Management Model functionality.
"""
# -*- coding: utf-8 -*-
from ..exceptions import (
    HydraulicEngineError,
    FileLoadError,
    FileWriteError,
    UnsupportedFileTypeError,
    ModelNotLoadedError,
    ValidationError,
    DatabaseError,
    APIError,
    ExportError,
    SimulationError,
    SimulationCancelled,
)
from .runner import SwmmRunner, SwmmRunResult
from .inp_handler import SwmmInpHandler
from .rpt_handler import SwmmRptHandler
from .out_handler import SwmmOutHandler
from .file_handler import SwmmFileHandler
from .models import SwmmFeatureSettings, SwmmOptionsSettings, SwmmOtherSettings, \
                    SwmmCrossSection, SwmmJunction, SwmmOutfall, SwmmDivider, SwmmStorage, SwmmConduit, SwmmPump, \
                    SwmmOrifice, SwmmWeir, SwmmOutlet, SwmmCurve, SwmmTimeseries, SwmmPattern, SwmmControl, \
                    SwmmCrossSectionShape, SwmmOutfallKind, SwmmDividerKind, SwmmStorageKind, \
                    SwmmPumpStatus, SwmmOrificeOrientation, SwmmWeirForm, SwmmWeirRoadSurface, SwmmOutletCurveType, \
                    SwmmFlowUnits, SwmmInfiltration, SwmmFlowRouting, SwmmLinkOffsets, SwmmForceMainEquation, \
                    SwmmInertialDamping, SwmmNormalFlowLimited, SwmmPatternCycle, SwmmCurveKind

__all__ = [
    # Exceptions
    "HydraulicEngineError",
    "FileLoadError",
    "FileWriteError",
    "UnsupportedFileTypeError",
    "ModelNotLoadedError",
    "ValidationError",
    "DatabaseError",
    "APIError",
    "ExportError",
    "SimulationError",
    "SimulationCancelled",
    # Handlers and Runner
    "SwmmRunner",
    "SwmmRunResult",
    "SwmmInpHandler",
    "SwmmRptHandler",
    "SwmmOutHandler",
    "SwmmFileHandler",
    "SwmmFeatureSettings",
    "SwmmOptionsSettings",
    "SwmmOtherSettings",
    "SwmmCrossSection",
    "SwmmJunction",
    "SwmmOutfall",
    "SwmmDivider",
    "SwmmStorage",
    "SwmmConduit",
    "SwmmPump",
    "SwmmOrifice",
    "SwmmWeir",
    "SwmmOutlet",
    "SwmmCurve",
    "SwmmTimeseries",
    "SwmmPattern",
    "SwmmControl",
    "SwmmCrossSectionShape",
    "SwmmCurveKind",
    "SwmmDividerKind",
    "SwmmFlowRouting",
    "SwmmFlowUnits",
    "SwmmForceMainEquation",
    "SwmmInertialDamping",
    "SwmmInfiltration",
    "SwmmLinkOffsets",
    "SwmmNormalFlowLimited",
    "SwmmOrificeOrientation",
    "SwmmOutfallKind",
    "SwmmPatternCycle",
    "SwmmPumpStatus",
    "SwmmStorageKind",
    "SwmmWeirForm",
    "SwmmWeirRoadSurface",
    "SwmmOutletCurveType",
]
