"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.
"""
# -*- coding: utf-8 -*-

from typing import Optional, Union
from enum import Enum
from datetime import datetime, time, date
from dataclasses import dataclass

from ..exceptions import ValidationError


def _validate_positive(value: Optional[float], field_name: str) -> None:
    if value is not None and value <= 0:
        raise ValidationError(f"{field_name} must be positive, got {value}")


# region Base Classes

@dataclass
class SwmmBaseObject:
    """
    Base class for all SWMM objects.
    """
    name: Optional[str] = None

# region Objects

# region Feature Settings

# region Enums

class SwmmCrossSectionShape(Enum):
    """
    Shape of SWMM cross section.
    """
    IRREGULAR = 'IRREGULAR'  # TransectCoordinates (Natural Channel)
    CUSTOM = 'CUSTOM'  # Full Height, ShapeCurveCoordinates
    STREET = 'STREET'
    CIRCULAR = 'CIRCULAR'  # Full Height = Diameter
    FORCE_MAIN = 'FORCE_MAIN'  # Full Height = Diameter, Roughness
    FILLED_CIRCULAR = 'FILLED_CIRCULAR'  # Full Height = Diameter, Filled Depth
    RECT_CLOSED = 'RECT_CLOSED'  # Rectangular: Full Height, Top Width
    RECT_OPEN = 'RECT_OPEN'  # Rectangular: Full Height, Top Width
    TRAPEZOIDAL = 'TRAPEZOIDAL'  # Full Height, Base Width, Side Slopes
    TRIANGULAR = 'TRIANGULAR'  # Full Height, Top Width
    HORIZ_ELLIPSE = 'HORIZ_ELLIPSE'  # Full Height, Max. Width
    VERT_ELLIPSE = 'VERT_ELLIPSE'  # Full Height, Max. Width
    ARCH = 'ARCH'  # Size Code or Full Height, Max. Width
    PARABOLIC = 'PARABOLIC'  # Full Height, Top Width
    POWER = 'POWER'  # Full Height, Top Width, Exponent
    RECT_TRIANGULAR = 'RECT_TRIANGULAR'  # Full Height, Top Width, Triangle Height
    RECT_ROUND = 'RECT_ROUND'  # Full Height, Top Width, Bottom Radius
    MODBASKETHANDLE = 'MODBASKETHANDLE'  # Full Height, Bottom Width, Top Radius
    EGG = 'EGG'  # Full Height
    HORSESHOE = 'HORSESHOE'  # Full Height Gothic Full Height
    GOTHIC = 'GOTHIC'  # Full Height
    CATENARY = 'CATENARY'  # Full Height
    SEMIELLIPTICAL = 'SEMIELLIPTICAL'  # Full Height
    BASKETHANDLE = 'BASKETHANDLE'  # Full Height
    SEMICIRCULAR = 'SEMICIRCULAR'  # Full Height


class SwmmOutfallKind(Enum):
    """
    Kind of SWMM outfall.
    """
    FREE = "FREE"
    NORMAL = "NORMAL"
    FIXED = "FIXED"
    TIDAL = "TIDAL"
    TIMESERIES = "TIMESERIES"

class SwmmDividerKind(Enum):
    """
    Kind of SWMM divider.
    """
    OVERFLOW = "OVERFLOW"
    CUTOFF = "CUTOFF"
    TABULAR = "TABULAR"
    WEIR = "WEIR"

class SwmmStorageKind(Enum):
    """
    Kind of SWMM storage.
    """
    TABULAR = "TABULAR"
    FUNCTIONAL = "FUNCTIONAL"
    CYLINDRICAL = "CYLINDRICAL"
    CONICAL = "CONICAL"
    PARABOLID = "PARABOLID"
    PYRAMIDAL = "PYRAMIDAL"

class SwmmPumpStatus(Enum):
    """
    Status of SWMM pump.
    """
    ON = "ON"
    OFF = "OFF"

class SwmmOrificeOrientation(Enum):
    """
    Orientation of SWMM orifice.
    """
    SIDE = "SIDE"
    BOTTOM = "BOTTOM"

class SwmmWeirForm(Enum):
    """
    Form of SWMM weir.
    """
    TRANSVERSE = "TRANSVERSE"
    SIDEFLOW = "SIDEFLOW"
    V_NOTCH = "V-NOTCH"
    TRAPEZOIDAL = "TRAPEZOIDAL"
    ROADWAY = "ROADWAY"

class SwmmWeirRoadSurface(Enum):
    """
    Surface of SWMM weir road.
    """
    PAVED = "PAVED"
    GRAVEL = "GRAVEL"

class SwmmOutletCurveType(Enum):
    """
    Type of SWMM outlet curve.
    """
    TABULAR_DEPTH = "TABULAR_DEPTH"
    TABULAR_HEAD = "TABULAR_HEAD"
    FUNCTIONAL_DEPTH = "FUNCTIONAL_DEPTH"
    FUNCTIONAL_HEAD = "FUNCTIONAL_HEAD"

# endregion

# region Objects

@dataclass
class SwmmCrossSection(SwmmBaseObject):
    """
    SWMM cross section.
    """
    link: Optional[str] = None
    shape: Optional[SwmmCrossSectionShape] = None
    height: Optional[float] = None
    parameter_2: Optional[float] = None
    parameter_3: Optional[float] = None
    parameter_4: Optional[float] = None
    n_barrels: Optional[int] = None
    culvert: Optional[int] = None
    transect: Optional[str] = None
    curve_name: Optional[str] = None

@dataclass
class SwmmNode(SwmmBaseObject):
    """
    SWMM node.
    """
    elevation: Optional[float] = None

@dataclass
class SwmmLink(SwmmBaseObject):
    """
    SWMM link.
    """
    from_node: Optional[str] = None
    to_node: Optional[str] = None
    cross_section: Optional[SwmmCrossSection] = None

@dataclass
class SwmmJunction(SwmmNode):
    """
    SWMM junction.
    """
    depth_max: Optional[float] = None
    depth_init: Optional[float] = None
    depth_surcharge: Optional[float] = None
    area_ponded: Optional[float] = None

@dataclass
class SwmmOutfall(SwmmNode):
    """
    SWMM outfall.
    """
    kind: Optional[SwmmOutfallKind] = None
    data: Optional[Union[float, str]] = None
    has_flap_gate: Optional[bool] = None
    route_to: Optional[str] = None

@dataclass
class SwmmDivider(SwmmNode):
    """
    SWMM divider.
    """
    link: Optional[str] = None
    kind: Optional[SwmmDividerKind] = None
    data: Optional[float] = None
    depth_max: Optional[float] = None
    depth_init: Optional[float] = None
    depth_surcharge: Optional[float] = None
    area_ponded: Optional[float] = None

@dataclass
class SwmmStorage(SwmmNode):
    """
    SWMM storage.
    """
    depth_max: Optional[float] = None
    depth_init: Optional[float] = None
    kind: Optional[SwmmStorageKind] = None
    data: Optional[Union[str, list[float]]] = None
    depth_surcharge: Optional[float] = None
    frac_evaporation: Optional[float] = None
    suction_head: Optional[float] = None
    hydraulic_conductivity: Optional[float] = None
    moisture_deficit_init: Optional[float] = None

@dataclass
class SwmmConduit(SwmmLink):
    """
    SWMM conduit.
    """
    length: Optional[float] = None
    roughness: Optional[float] = None
    offset_upstream: Optional[float] = None
    offset_downstream: Optional[float] = None
    flow_initial: Optional[float] = None
    flow_max: Optional[float] = None

    def __post_init__(self) -> None:
        _validate_positive(self.length, "length")

@dataclass
class SwmmPump(SwmmLink):
    """
    SWMM pump.
    """
    curve_name: Optional[str] = None
    status: Optional[SwmmPumpStatus] = None
    depth_on: Optional[float] = None
    depth_off: Optional[float] = None

@dataclass
class SwmmOrifice(SwmmLink):
    """
    SWMM orifice.
    """
    orientation: Optional[SwmmOrificeOrientation] = None
    offset: Optional[float] = None
    discharge_coefficient: Optional[float] = None
    has_flap_gate: Optional[bool] = None
    hours_to_open: Optional[int] = None

@dataclass
class SwmmWeir(SwmmLink):
    """
    SWMM weir.
    """
    form: Optional[SwmmWeirForm] = None
    height_crest: Optional[float] = None
    discharge_coefficient: Optional[float] = None
    has_flap_gate: Optional[bool] = None
    n_end_contractions: Optional[float] = None
    discharge_coefficient_end: Optional[float] = None
    can_surcharge: Optional[bool] = None
    road_width: Optional[float] = None
    road_surface: Optional[SwmmWeirRoadSurface] = None
    coefficient_curve: Optional[str] = None

@dataclass
class SwmmOutlet(SwmmLink):
    """
    SWMM outlet.
    """
    offset: Optional[float] = None
    curve_type: Optional[SwmmOutletCurveType] = None
    curve_description: Optional[Union[str, tuple[float, float]]] = None
    has_flap_gate: Optional[bool] = None

# endregion


@dataclass
class SwmmFeatureSettings:
    """
    Options for a SWMM feature settings.
    """
    # Points
    junctions: Optional[dict[str, SwmmJunction]] = None
    outfalls: Optional[dict[str, SwmmOutfall]] = None
    dividers: Optional[dict[str, SwmmDivider]] = None
    storage: Optional[dict[str, SwmmStorage]] = None

    # Lines
    conduits: Optional[dict[str, SwmmConduit]] = None
    pumps: Optional[dict[str, SwmmPump]] = None
    orifices: Optional[dict[str, SwmmOrifice]] = None
    weirs: Optional[dict[str, SwmmWeir]] = None
    outlets: Optional[dict[str, SwmmOutlet]] = None

# endregion

# region Options Settings

# region Enums

class SwmmFlowUnits(Enum):
    """
    Flow units for SWMM simulation.
    """
    CFS = "CFS"  # cubic feet per second (default)
    GPM = "GPM"  # gallons per minute
    MGD = "MGD"  # million gallons per day
    CMS = "CMS"  # cubic meters per second
    LPS = "LPS"  # liters per second
    MLD = "MLD"  # million liters per day

class SwmmInfiltration(Enum):
    """
    Infiltration model for SWMM simulation.
    """
    HORTON = "HORTON"  # default
    MODIFIED_HORTON = "MODIFIED_HORTON"
    GREEN_AMPT = "GREEN_AMPT"
    MODIFIED_GREEN_AMPT = "MODIFIED_GREEN_AMPT"
    CURVE_NUMBER = "CURVE_NUMBER"

class SwmmFlowRouting(Enum):
    """
    Flow routing method for SWMM simulation.
    """
    STEADY = "STEADY"
    KINWAVE = "KINWAVE"  # default
    DYNWAVE = "DYNWAVE"

class SwmmLinkOffsets(Enum):
    """
    Link offsets convention for SWMM simulation.
    """
    DEPTH = "DEPTH"  # default
    ELEVATION = "ELEVATION"

class SwmmForceMainEquation(Enum):
    """
    Force main equation for SWMM simulation.
    """
    H_W = "H-W"  # Hazen-Williams (default)
    D_W = "D-W"  # Darcy-Weisbach

class SwmmInertialDamping(Enum):
    """
    Inertial damping for SWMM simulation.
    """
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    FULL = "FULL"

class SwmmNormalFlowLimited(Enum):
    """
    Normal flow limited condition for SWMM simulation.
    """
    SLOPE = "SLOPE"
    FROUDE = "FROUDE"
    BOTH = "BOTH"  # default

# endregion

@dataclass
class SwmmOptionsSettings:
    """
    Options for a SWMM options settings.
    """
    # Flow and routing options
    flow_units: Optional[SwmmFlowUnits] = None  # default: CFS
    infiltration: Optional[SwmmInfiltration] = None  # default: HORTON
    flow_routing: Optional[SwmmFlowRouting] = None  # default: KINWAVE
    link_offsets: Optional[SwmmLinkOffsets] = None  # default: DEPTH
    force_main_equation: Optional[SwmmForceMainEquation] = None  # default: H-W

    # Ignore options (YES/NO, default: NO)
    ignore_rainfall: Optional[bool] = None
    ignore_snowmelt: Optional[bool] = None
    ignore_groundwater: Optional[bool] = None
    ignore_rdii: Optional[bool] = None
    ignore_routing: Optional[bool] = None
    ignore_quality: Optional[bool] = None

    # Other boolean options
    allow_ponding: Optional[bool] = None  # default: NO
    skip_steady_state: Optional[bool] = None  # default: NO

    # Tolerance values
    sys_flow_tol: Optional[float] = None  # default: 5
    lat_flow_tol: Optional[float] = None  # default: 5

    # Date/Time options
    start_date: Optional[date] = None  # default: 1/1/2002
    start_time: Optional[time] = None  # default: 0:00:00
    end_date: Optional[date] = None  # default: START_DATE
    end_time: Optional[time] = None  # default: 24:00:00
    report_start_date: Optional[date] = None  # default: START_DATE
    report_start_time: Optional[time] = None  # default: START_TIME
    sweep_start: Optional[str] = None  # default: 1/1
    sweep_end: Optional[str] = None  # default: 12/31

    # Time step options
    dry_days: Optional[float] = None  # default: 0
    report_step: Optional[time] = None  # default: 0:15:00
    wet_step: Optional[time] = None  # default: 0:05:00
    dry_step: Optional[time] = None  # default: 1:00:00
    routing_step: Optional[float] = None  # default: 600 seconds
    lengthening_step: Optional[float] = None  # default: 0 seconds
    variable_step: Optional[float] = None  # default: 0
    minimum_step: Optional[float] = None  # default: 0.5 seconds

    # Dynamic wave options
    inertial_damping: Optional[SwmmInertialDamping] = None
    normal_flow_limited: Optional[SwmmNormalFlowLimited] = None  # default: BOTH

    # Numerical options
    min_surfarea: Optional[float] = None  # default: 12.566 ft2
    min_slope: Optional[float] = None  # default: 0
    max_trials: Optional[int] = None  # default: 8
    head_tolerance: Optional[float] = None  # default: 0.0015
    threads: Optional[int] = None  # default: 1

    # File options
    tempdir: Optional[str] = None

# endregion

# region Other Settings

# region Enums

class SwmmPatternCycle(Enum):
    """
    Cycle of SWMM pattern.
    """
    MONTHLY = "MONTHLY"
    DAILY = "DAILY"
    HOURLY = "HOURLY"
    WEEKEND = "WEEKEND"

class SwmmCurveKind(Enum):
    """
    Kind of SWMM curve.
    """
    STORAGE = "STORAGE"
    SHAPE = "SHAPE"
    DIVERSION = "DIVERSION"
    TIDAL = "TIDAL"
    PUMP1 = "PUMP1"
    PUMP2 = "PUMP2"
    PUMP3 = "PUMP3"
    PUMP4 = "PUMP4"
    RATING = "RATING"
    CONTROL = "CONTROL"
    WEIR = "WEIR"

# endregion

# region Objects

@dataclass
class SwmmCurve(SwmmBaseObject):
    """
    SWMM curve.
    """
    kind: Optional[SwmmCurveKind] = None
    points: Optional[list[list[float, float]]] = None

@dataclass
class SwmmTimeseries(SwmmBaseObject):
    """
    SWMM timeseries.
    """
    data: Optional[list[tuple[Union[datetime, float], float]]] = None

@dataclass
class SwmmPattern(SwmmBaseObject):
    """
    SWMM pattern.
    """
    cycle: Optional[SwmmPatternCycle] = None
    factors: Optional[list[float]] = None


@dataclass
class SwmmControl(SwmmBaseObject):
    """
    One SWMM [CONTROLS] entry as free-form INP text.

    Typically a RULE block (IF / THEN / ELSE / PRIORITY). VARIABLE and
    EXPRESSION lines are also accepted. Dict keys in
    ``SwmmOtherSettings.controls`` are the entry names (create if missing,
    replace if present). The name inside the text must match the dict key.
    """
    text: Optional[str] = None

# endregion

@dataclass
class SwmmOtherSettings:
    """
    Other settings for a SWMM inp file.

    Controls use create-or-replace by name (dict key).
    """
    curves: Optional[dict[str, SwmmCurve]] = None
    timeseries: Optional[dict[str, SwmmTimeseries]] = None
    patterns: Optional[dict[str, SwmmPattern]] = None
    controls: Optional[dict[str, SwmmControl]] = None

# endregion