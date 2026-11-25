"""Unit tests for hydraulic_engine.timeseries."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import wntr

from hydraulic_engine.timeseries import (
    SeriesRequest,
    extract_epanet_timeseries,
    extract_swmm_timeseries,
)


class _FakeInpHandler:
    def __init__(self):
        self.file_object = wntr.network.WaterNetworkModel()
        self.file_object.options.hydraulic.inpfile_units = "LPS"
        self.file_object.options.time.start_clocktime = 0


class _FakeSwmmOutput:
    def __init__(self):
        self.start_date = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
        self.index = [
            self.start_date,
            self.start_date.replace(minute=5),
        ]
        self._node_depth = {
            "N1": pd.Series([0.1, 0.2], index=self.index),
        }

    def get_part(self, kind, label=None, variable=None, show_progress=False, **kwargs):
        if kind == "node" and label == "N1" and variable == "depth":
            return self._node_depth["N1"]
        if kind == kwargs.get("kind") or (kind is None and "kind" in kwargs):
            pass
        if variable == "depth" and kind == "node":
            return None
        return None


def test_swmm_extract_known_series():
    out = _FakeSwmmOutput()

    def get_part(kind, label=None, variable=None, show_progress=False, **kwargs):
        if kind == "node" and label == "N1" and variable == "depth":
            return out._node_depth["N1"]
        return None

    out.get_part = get_part  # type: ignore[method-assign]

    payload = extract_swmm_timeseries(
        out,
        [SeriesRequest(object_type="node", object_id="N1", variable="depth")],
    )
    assert payload["times_seconds"] == [0, 300]
    assert len(payload["series"]) == 1
    assert payload["series"][0]["values"] == [0.1, 0.2]


def test_swmm_extract_missing_returns_error():
    out = _FakeSwmmOutput()
    out.get_part = lambda *a, **k: None  # type: ignore[method-assign]
    payload = extract_swmm_timeseries(
        out,
        [SeriesRequest(object_type="node", object_id="MISSING", variable="depth")],
    )
    assert "error" in payload["series"][0]


def test_epanet_si_conversion():
    index = [0, 300]
    demand_si = pd.DataFrame({"J1": [0.01, 0.02]}, index=index)
    results = wntr.sim.SimulationResults()
    results.node = {"demand": demand_si}

    payload = extract_epanet_timeseries(
        _FakeInpHandler(),
        results,
        [SeriesRequest(object_type="node", object_id="J1", variable="demand")],
        round_decimals=4,
    )
    values = payload["series"][0]["values"]
    assert values is not None
    assert len(values) == 2
    assert payload["start"] is None
    assert payload["times_seconds"] == [0, 300]


def test_epanet_missing_id_error():
    index = [0]
    demand_si = pd.DataFrame({"J1": [0.01]}, index=index)
    results = wntr.sim.SimulationResults()
    results.node = {"demand": demand_si}

    payload = extract_epanet_timeseries(
        _FakeInpHandler(),
        results,
        [SeriesRequest(object_type="node", object_id="NOPE", variable="demand")],
    )
    assert "error" in payload["series"][0]
