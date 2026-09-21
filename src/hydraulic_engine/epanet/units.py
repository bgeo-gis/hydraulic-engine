"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

EPANET INP unit conversion helpers
----------------------------------
Callers pass feature/options/other settings in EPANET INP file units.
WNTR stores the network in SI. Convert with ``to_si`` / ``HydParam`` before
setattr / add_demand, mirroring ``wntr.epanet.io`` read behaviour.

See docs/units.md for the full convert / conditional / never / deferred matrix.
"""
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Optional, Sequence

from wntr.epanet.util import FlowUnits, HydParam, from_si, to_si
from wntr.network.elements import Tank, Valve

from ..utils import tools_log

# Attributes that always convert with a fixed HydParam (when numeric).
_ALWAYS_CONVERT: dict[str, HydParam] = {
    "elevation": HydParam.Elevation,
    "emitter_coefficient": HydParam.EmitterCoeff,
    "base_head": HydParam.HydraulicHead,
    "init_level": HydParam.HydraulicHead,
    "min_level": HydParam.HydraulicHead,
    "max_level": HydParam.HydraulicHead,
    "min_vol": HydParam.Volume,
    "length": HydParam.Length,
    "power": HydParam.Power,
}

# Options that convert (section.attr -> HydParam).
_OPTIONS_CONVERT: dict[tuple[str, str], HydParam] = {
    ("hydraulic", "minimum_pressure"): HydParam.Pressure,
    ("hydraulic", "required_pressure"): HydParam.Pressure,
}

_PRESSURE_VALVE_TYPES = frozenset({"PRV", "PSV", "PBV"})
_FLOW_VALVE_TYPES = frozenset({"FCV"})
_NO_CONVERT_VALVE_TYPES = frozenset({"TCV", "GPV"})


def get_flow_units(wn) -> FlowUnits:
    """Resolve EPANET FlowUnits from the loaded WNTR network."""
    units_name = wn.options.hydraulic.inpfile_units
    try:
        flow_units = getattr(FlowUnits, units_name)
    except AttributeError as exc:
        raise ValueError(f"Invalid inpfile_units: {units_name!r}") from exc
    if flow_units is None:
        raise ValueError(f"Invalid inpfile_units: {units_name!r}")
    return flow_units


def is_darcy_weisbach(wn) -> bool:
    """Return True when headloss formula is Darcy-Weisbach (D-W)."""
    return wn.options.hydraulic.headloss == "D-W"


def convert_to_si(
    flow_units: FlowUnits,
    value: float,
    param: HydParam,
    *,
    darcy_weisbach: bool = False,
) -> float:
    """Convert a scalar from EPANET INP units to SI."""
    return float(to_si(flow_units, value, param, darcy_weisbach=darcy_weisbach))


def convert_from_si(
    flow_units: FlowUnits,
    value: float,
    param: HydParam,
    *,
    darcy_weisbach: bool = False,
) -> float:
    """Convert a scalar from SI to EPANET INP units."""
    return float(from_si(flow_units, value, param, darcy_weisbach=darcy_weisbach))


def _unwrap_enum(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _normalize_curve_type(curve_type: Any) -> Optional[str]:
    if curve_type is None:
        return None
    name = str(_unwrap_enum(curve_type)).upper()
    if name == "PUMP":
        return "HEAD"
    return name


def resolve_valve_type(source_obj: Any, target_obj: Any) -> Optional[str]:
    """Prefer valve_type from settings; fall back to the WNTR valve."""
    settings_type = getattr(source_obj, "valve_type", None)
    if settings_type is not None:
        return str(_unwrap_enum(settings_type)).upper()
    target_type = getattr(target_obj, "valve_type", None)
    if target_type is not None:
        return str(target_type).upper()
    return None


def resolve_curve_type(source_obj: Any, target_obj: Any) -> Optional[str]:
    """Prefer curve_type from settings; fall back to the existing WNTR curve."""
    settings_type = getattr(source_obj, "curve_type", None)
    if settings_type is not None:
        return _normalize_curve_type(settings_type)
    return _normalize_curve_type(getattr(target_obj, "curve_type", None))


def convert_valve_initial_setting(
    flow_units: FlowUnits,
    value: Any,
    valve_type: Optional[str],
) -> Any:
    """
    Convert valve initial_setting based on valve type (WNTR [VALVES] rules).

    PRV/PSV/PBV -> Pressure; FCV -> Flow; TCV/GPV -> no conversion.
    """
    if not isinstance(value, (int, float)):
        tools_log.log_warning(
            f"Skipping unit conversion for non-numeric valve initial_setting "
            f"(type={valve_type!r})"
        )
        return value

    if valve_type in _PRESSURE_VALVE_TYPES:
        return convert_to_si(flow_units, float(value), HydParam.Pressure)
    if valve_type in _FLOW_VALVE_TYPES:
        return convert_to_si(flow_units, float(value), HydParam.Flow)
    if valve_type in _NO_CONVERT_VALVE_TYPES or valve_type is None:
        return float(value)

    tools_log.log_warning(
        f"Unknown valve_type {valve_type!r}; leaving initial_setting unconverted"
    )
    return float(value)


def convert_curve_points(
    flow_units: FlowUnits,
    points: Sequence[Sequence[float]],
    curve_type: Optional[str],
) -> list[tuple[float, float]]:
    """
    Convert curve point axes from INP units to SI based on curve type.

    VOLUME: Length, Volume; HEAD/HEADLOSS: Flow, HydraulicHead;
    EFFICIENCY: Flow, Y unchanged (%). Unknown type: no conversion.
    """
    converted: list[tuple[float, float]] = []
    if curve_type is None:
        tools_log.log_warning(
            "Curve type unknown; leaving curve points unconverted"
        )
        return [(float(p[0]), float(p[1])) for p in points]

    for point in points:
        x, y = float(point[0]), float(point[1])
        if curve_type == "VOLUME":
            x = convert_to_si(flow_units, x, HydParam.Length)
            y = convert_to_si(flow_units, y, HydParam.Volume)
        elif curve_type in ("HEAD", "HEADLOSS"):
            x = convert_to_si(flow_units, x, HydParam.Flow)
            y = convert_to_si(flow_units, y, HydParam.HydraulicHead)
        elif curve_type == "EFFICIENCY":
            x = convert_to_si(flow_units, x, HydParam.Flow)
            # Y is percent efficiency — leave unchanged
        else:
            tools_log.log_warning(
                f"Unknown curve_type {curve_type!r}; leaving points unconverted"
            )
            return [(float(p[0]), float(p[1])) for p in points]
        converted.append((x, y))
    return converted


def convert_diameter(
    flow_units: FlowUnits,
    value: float,
    target_obj: Any,
) -> float:
    """Tank diameter uses TankDiameter; pipe/valve diameter uses PipeDiameter."""
    if isinstance(target_obj, Tank):
        return convert_to_si(flow_units, value, HydParam.TankDiameter)
    return convert_to_si(flow_units, value, HydParam.PipeDiameter)


def convert_feature_value(
    attr_name: str,
    value: Any,
    *,
    flow_units: FlowUnits,
    wn,
    source_obj: Any,
    target_obj: Any,
) -> Any:
    """
    Convert a single feature/other attribute from INP units to SI when needed.

    Returns the value unchanged when no conversion applies.
    """
    if attr_name == "points":
        curve_type = resolve_curve_type(source_obj, target_obj)
        return convert_curve_points(flow_units, value, curve_type)

    if attr_name == "roughness":
        if is_darcy_weisbach(wn):
            return convert_to_si(
                flow_units,
                float(value),
                HydParam.RoughnessCoeff,
                darcy_weisbach=True,
            )
        return float(value)

    if attr_name == "initial_setting":
        if isinstance(target_obj, Valve):
            valve_type = resolve_valve_type(source_obj, target_obj)
            return convert_valve_initial_setting(flow_units, value, valve_type)
        # Pump relative speed / other links: never convert
        return value

    if attr_name == "diameter":
        return convert_diameter(flow_units, float(value), target_obj)

    param = _ALWAYS_CONVERT.get(attr_name)
    if param is not None and isinstance(value, (int, float)):
        return convert_to_si(flow_units, float(value), param)

    return value


def convert_option_value(
    section_name: str,
    attr_name: str,
    value: Any,
    flow_units: FlowUnits,
) -> Any:
    """Convert an options attribute from INP units to SI when mapped."""
    param = _OPTIONS_CONVERT.get((section_name, attr_name))
    if param is not None and isinstance(value, (int, float)):
        return convert_to_si(flow_units, float(value), param)
    return value


def convert_demand_base(flow_units: FlowUnits, base_demand: float) -> float:
    """Convert junction base_demand from INP units to SI."""
    return convert_to_si(flow_units, float(base_demand), HydParam.Demand)


def convert_valve_initial_setting_from_si(
    flow_units: FlowUnits,
    value: Any,
    valve_type: Optional[str],
) -> Any:
    """Convert valve initial_setting from SI to INP units (inverse of to_si)."""
    if not isinstance(value, (int, float)):
        return value
    if valve_type in _PRESSURE_VALVE_TYPES:
        return convert_from_si(flow_units, float(value), HydParam.Pressure)
    if valve_type in _FLOW_VALVE_TYPES:
        return convert_from_si(flow_units, float(value), HydParam.Flow)
    return float(value)


def convert_diameter_from_si(
    flow_units: FlowUnits,
    value: float,
    target_obj: Any,
) -> float:
    """Tank diameter uses TankDiameter; pipe/valve diameter uses PipeDiameter."""
    if isinstance(target_obj, Tank):
        return convert_from_si(flow_units, value, HydParam.TankDiameter)
    return convert_from_si(flow_units, value, HydParam.PipeDiameter)


def convert_feature_from_si(
    attr_name: str,
    value: Any,
    *,
    flow_units: FlowUnits,
    wn,
    target_obj: Any,
) -> Any:
    """
    Convert a single WNTR feature attribute from SI to INP file units.

    Returns the value unchanged when no conversion applies. Enums are left as-is
    for the caller to stringify.
    """
    if value is None:
        return None

    if attr_name == "roughness":
        if is_darcy_weisbach(wn) and isinstance(value, (int, float)):
            return convert_from_si(
                flow_units,
                float(value),
                HydParam.RoughnessCoeff,
                darcy_weisbach=True,
            )
        return float(value) if isinstance(value, (int, float)) else value

    if attr_name == "initial_setting":
        if isinstance(target_obj, Valve):
            valve_type = resolve_valve_type(None, target_obj)
            return convert_valve_initial_setting_from_si(flow_units, value, valve_type)
        return value

    if attr_name == "diameter" and isinstance(value, (int, float)):
        return convert_diameter_from_si(flow_units, float(value), target_obj)

    param = _ALWAYS_CONVERT.get(attr_name)
    if param is not None and isinstance(value, (int, float)):
        return convert_from_si(flow_units, float(value), param)

    return value


def convert_option_from_si(
    section_name: str,
    attr_name: str,
    value: Any,
    flow_units: FlowUnits,
) -> Any:
    """Convert an options attribute from SI to INP units when mapped."""
    param = _OPTIONS_CONVERT.get((section_name, attr_name))
    if param is not None and isinstance(value, (int, float)):
        return convert_from_si(flow_units, float(value), param)
    return value
