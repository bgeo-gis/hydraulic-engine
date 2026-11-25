"""
Copyright © 2026 by BGEO. All rights reserved.
Extract requested variable time series from EPANET BIN and SWMM OUT.
"""

# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import pandas as pd
import wntr
from swmm_api.output_file.definitions import OBJECTS
from wntr.epanet.util import FlowUnits, HydParam, from_si

from .epanet.inp_handler import EpanetInpHandler
from .exceptions import ValidationError

ObjectType = Literal["node", "link", "system"]

EPANET_NODE_VARIABLES: dict[str, tuple[HydParam | None, str]] = {
    "demand": (HydParam.Demand, "demand"),
    "head": (HydParam.HydraulicHead, "head"),
    "pressure": (HydParam.Pressure, "pressure"),
    "quality": (None, "quality"),
}

EPANET_LINK_VARIABLES: dict[str, tuple[HydParam | None, str]] = {
    "flow": (HydParam.Flow, "flowrate"),
    "velocity": (HydParam.Velocity, "velocity"),
    "headloss": (HydParam.HeadLoss, "headloss"),
    "setting": (None, "setting"),
}

SWMM_KIND: dict[ObjectType, str] = {
    "node": OBJECTS.NODE,
    "link": OBJECTS.LINK,
    "system": OBJECTS.SYSTEM,
}

SWMM_VARIABLES: dict[ObjectType, dict[str, str]] = {
    "node": {
        "depth": "depth",
        "head": "head",
        "volume": "volume",
        "lateral_inflow": "lateral_inflow",
        "total_inflow": "total_inflow",
        "flooding": "flooding",
    },
    "link": {
        "flow": "flow",
        "depth": "depth",
        "velocity": "velocity",
        "volume": "volume",
        "capacity": "capacity",
    },
    "system": {
        "temperature": "air_temperature",
        "rainfall": "rainfall",
        "snow_depth": "snow_depth",
        "infiltration": "infiltration",
        "runoff": "runoff",
        "dry_weather_inflow": "dry_weather_inflow",
        "groundwater_inflow": "groundwater_inflow",
        "rdii_inflow": "RDII_inflow",
        "direct_inflow": "direct_inflow",
        "lateral_inflow": "lateral_inflow",
        "flooding": "flooding",
        "outflow": "outflow",
        "storage": "volume",
        "evaporation": "evaporation",
        "pet": "PET",
    },
}


@dataclass(frozen=True)
class SeriesRequest:
    object_type: ObjectType
    variable: str
    object_id: str | None = None


def _unit_system(inp_handler: EpanetInpHandler) -> FlowUnits:
    unit_system = getattr(
        wntr.epanet.util.FlowUnits,
        inp_handler.file_object.options.hydraulic.inpfile_units,
    )
    if unit_system is None:
        raise ValidationError(f"Invalid unit system: {inp_handler.file_object.options.hydraulic.inpfile_units}")
    return unit_system


def _convert_series(
    values: pd.Series,
    unit_system: FlowUnits,
    param: HydParam | None,
    round_decimals: int,
) -> list[float | None]:
    if param is None:
        rounded = values.round(round_decimals)
    else:
        factor = from_si(unit_system, 1.0, param)
        rounded = (values * factor).round(round_decimals)
    out: list[float | None] = []
    for value in rounded.tolist():
        if value is None or (isinstance(value, float) and pd.isna(value)):
            out.append(None)
        else:
            out.append(float(value))
    return out


def _index_to_seconds(index: Sequence[Any], start: datetime | None) -> tuple[datetime | None, list[int]]:
    if not len(index):
        return start, []
    times_seconds: list[int] = []
    resolved_start = start
    first = index[0]
    if isinstance(first, datetime):
        if resolved_start is None:
            resolved_start = first
        for stamp in index:
            delta = stamp - resolved_start
            times_seconds.append(int(delta.total_seconds()))
        return resolved_start, times_seconds

    for step in index:
        times_seconds.append(int(step))
    return resolved_start, times_seconds


def extract_epanet_timeseries(
    inp_handler: EpanetInpHandler,
    results: wntr.sim.SimulationResults,
    series: Sequence[SeriesRequest | dict[str, Any]],
    *,
    round_decimals: int = 4,
) -> dict[str, Any]:
    """Extract requested series from EPANET results (values in INP units)."""
    unit_system = _unit_system(inp_handler)
    requests = [_coerce_request(item) for item in series]
    node_data = results.node or {}
    link_data = results.link or {}

    ref_index: Sequence[Any] = ()
    if "demand" in node_data:
        ref_index = node_data["demand"].index
    elif link_data.get("flowrate") is not None:
        ref_index = link_data["flowrate"].index

    _, times_seconds = _index_to_seconds(ref_index, None)

    output_series: list[dict[str, Any]] = []
    for req in requests:
        entry: dict[str, Any] = {
            "object_type": req.object_type,
            "variable": req.variable,
        }
        if req.object_id is not None:
            entry["object_id"] = req.object_id

        try:
            if req.object_type == "system":
                raise ValidationError("EPANET has no system time series")

            if req.object_type == "node":
                mapping = EPANET_NODE_VARIABLES.get(req.variable)
                if mapping is None or not req.object_id:
                    raise ValidationError(f"Unknown node variable or missing object_id: {req.variable}")
                param, wntr_key = mapping
                frame = node_data.get(wntr_key)
                if frame is None or req.object_id not in frame.columns:
                    raise KeyError(f"Node '{req.object_id}' or variable '{req.variable}' not in BIN")
                values = _convert_series(frame[req.object_id], unit_system, param, round_decimals)
            else:
                mapping = EPANET_LINK_VARIABLES.get(req.variable)
                if mapping is None or not req.object_id:
                    raise ValidationError(f"Unknown link variable or missing object_id: {req.variable}")
                param, wntr_key = mapping
                frame = link_data.get(wntr_key)
                if frame is None or req.object_id not in frame.columns:
                    raise KeyError(f"Link '{req.object_id}' or variable '{req.variable}' not in BIN")
                values = _convert_series(frame[req.object_id], unit_system, param, round_decimals)

            entry["values"] = values
            entry["unit"] = inp_handler.file_object.options.hydraulic.inpfile_units
        except Exception as exc:
            entry["error"] = str(exc)

        output_series.append(entry)

    return {
        "start": None,
        "times_seconds": times_seconds,
        "series": output_series,
    }


def _swmm_series_values(results: Any, req: SeriesRequest) -> pd.Series:
    kind = SWMM_KIND[req.object_type]
    variable_map = SWMM_VARIABLES.get(req.object_type) or {}
    out_var = variable_map.get(req.variable)
    if out_var is None:
        raise ValidationError(f"Unknown SWMM variable '{req.variable}' for {req.object_type}")

    if req.object_type == "system":
        part = results.get_part(kind=kind, variable=out_var, show_progress=False)
        if part is None:
            raise KeyError(f"System variable '{req.variable}' not in OUT")
        if isinstance(part, pd.DataFrame) and part.shape[1] == 1:
            return part.iloc[:, 0]
        if isinstance(part, pd.Series):
            return part
        raise KeyError(f"Unexpected OUT shape for system '{req.variable}'")

    if not req.object_id:
        raise ValidationError("object_id is required for node and link series")

    part = results.get_part(req.object_type, req.object_id, out_var)
    if part is None:
        raise KeyError(f"{req.object_type} '{req.object_id}' variable '{req.variable}' not in OUT")
    if not isinstance(part, pd.Series):
        raise KeyError(f"Unexpected OUT shape for {req.object_id}/{req.variable}")
    return part


def extract_swmm_timeseries(
    results: Any,
    series: Sequence[SeriesRequest | dict[str, Any]],
    *,
    round_decimals: int = 4,
) -> dict[str, Any]:
    """Extract requested series from a loaded swmm_api SwmmOutput."""
    requests = [_coerce_request(item) for item in series]
    start = getattr(results, "start_date", None)
    index = getattr(results, "index", None)
    if index is None:
        index = []
    start, times_seconds = _index_to_seconds(index, start)

    output_series: list[dict[str, Any]] = []
    for req in requests:
        entry: dict[str, Any] = {
            "object_type": req.object_type,
            "variable": req.variable,
        }
        if req.object_id is not None:
            entry["object_id"] = req.object_id
        try:
            values_series = _swmm_series_values(results, req)
            values = []
            for value in values_series.round(round_decimals).tolist():
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    values.append(None)
                else:
                    values.append(float(value))
            entry["values"] = values
        except Exception as exc:
            entry["error"] = str(exc)
        output_series.append(entry)

    return {
        "start": start.isoformat() if start else None,
        "times_seconds": times_seconds,
        "series": output_series,
    }


def _coerce_request(item: SeriesRequest | dict[str, Any]) -> SeriesRequest:
    if isinstance(item, SeriesRequest):
        return item
    object_type = item.get("object_type")
    if object_type not in ("node", "link", "system"):
        raise ValidationError(f"Invalid object_type: {object_type}")
    variable = item.get("variable")
    if not variable:
        raise ValidationError("variable is required")
    object_id = item.get("object_id")
    return SeriesRequest(object_type=object_type, variable=str(variable), object_id=object_id)
