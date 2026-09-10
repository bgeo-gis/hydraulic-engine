"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

SWMM module tests.
"""
# -*- coding: utf-8 -*-
import pytest

from hydraulic_engine import FileLoadError, ModelNotLoadedError, ValidationError
from hydraulic_engine.swmm import (
    SwmmRunner,
    SwmmRunResult,
    SwmmInpHandler,
    SwmmRptHandler,
    SwmmOutHandler,
    SwmmFileHandler,
    SwmmOtherSettings,
    SwmmControl,
    SwmmFeatureSettings,
    SwmmOptionsSettings,
    SwmmJunction,
    SwmmFlowUnits,
)
from hydraulic_engine.utils.enums import RunStatus


_MINIMAL_SWMM_INP = """[TITLE]
Minimal SWMM
[OPTIONS]
FLOW_UNITS LPS
INFILTRATION HORTON
FLOW_ROUTING KINWAVE
START_DATE 01/01/2020
START_TIME 00:00:00
END_DATE 01/01/2020
END_TIME 01:00:00
REPORT_START_DATE 01/01/2020
REPORT_START_TIME 00:00:00
[JUNCTIONS]
J1 10 0 0 0 0
[OUTFALLS]
O1 0 FREE NO
[PUMPS]
P1 J1 O1 ALWAYS_OPEN *
[XSECTIONS]
P1 CIRCULAR 1 0 0 0 1
[COORDINATES]
J1 0 0
O1 1 0
"""


@pytest.fixture
def minimal_swmm_inp(tmp_path):
    path = tmp_path / "minimal_swmm.inp"
    path.write_text(_MINIMAL_SWMM_INP, encoding="utf-8")
    return str(path)


class TestSwmmImports:
    """Test SWMM module imports."""

    def test_import_from_package(self):
        from hydraulic_engine import swmm
        assert SwmmRunner is not None
        assert swmm.SwmmInpHandler is not None
        assert swmm.SwmmRptHandler is not None

    def test_import_result_classes(self):
        assert SwmmRunResult is not None
        assert SwmmFileHandler is not None


class TestSwmmRunner:
    """Test SwmmRunner class."""

    def test_runner_initialization(self):
        runner = SwmmRunner(inp_path="model.inp")
        assert runner is not None

    def test_runner_cleanup_removes_temp_files(self):
        runner = SwmmRunner()
        runner.rpt = SwmmRptHandler()
        runner.out = SwmmOutHandler()
        rpt_path = runner.rpt.get_file_path(None, ".rpt")
        out_path = runner.out.get_file_path(None, ".out")
        assert rpt_path.exists()
        assert out_path.exists()
        runner.cleanup()
        assert not rpt_path.exists()
        assert not out_path.exists()

    def test_run_missing_file_raises(self):
        runner = SwmmRunner(inp_path="nonexistent.inp")
        with pytest.raises(FileLoadError):
            runner.run()

    def test_run_with_pyswmm_cancel_raises(self, monkeypatch):
        from hydraulic_engine import SimulationCancelled
        from hydraulic_engine.utils.enums import RunStatus
        from unittest.mock import MagicMock

        class FakeSim:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                yield None

            percent_complete = 0.1
            current_time = MagicMock()
            current_time.strftime = lambda _fmt: "2020-01-01 00:00:00"
            flow_routing_error = 0.0
            runoff_error = 0.0

        monkeypatch.setattr(
            "hydraulic_engine.swmm.runner.Simulation",
            lambda **_kwargs: FakeSim(),
        )

        runner = SwmmRunner()
        runner.rpt = MagicMock()
        runner.out = MagicMock()
        result = SwmmRunResult(
            inp_path="model.inp",
            rpt_path="model.rpt",
            out_path="model.out",
        )

        with pytest.raises(SimulationCancelled) as exc_info:
            runner._run_with_pyswmm(
                result, step_callback=lambda _sim, _step: False
            )

        assert exc_info.value.result is not None
        assert exc_info.value.result.status == RunStatus.CANCELLED
        assert runner.result is exc_info.value.result

    def test_run_with_pyswmm_engine_error_raises(self, monkeypatch):
        from hydraulic_engine import SimulationError
        from hydraulic_engine.utils.enums import RunStatus
        from unittest.mock import MagicMock

        class FakeSim:
            def __enter__(self):
                raise RuntimeError("swmm engine failed")

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(
            "hydraulic_engine.swmm.runner.Simulation",
            lambda **_kwargs: FakeSim(),
        )

        runner = SwmmRunner()
        runner.rpt = MagicMock()
        runner.out = MagicMock()
        result = SwmmRunResult(
            inp_path="model.inp",
            rpt_path="model.rpt",
            out_path="model.out",
        )

        with pytest.raises(SimulationError) as exc_info:
            runner._run_with_pyswmm(result, step_callback=None)

        assert "swmm engine failed" in str(exc_info.value)
        assert exc_info.value.result.status == RunStatus.ERROR
        assert runner.result is exc_info.value.result

    def test_progress_callback(self):
        progress_calls = []

        def callback(progress, message):
            progress_calls.append((progress, message))

        runner = SwmmRunner(progress_callback=callback)
        runner._report_progress(50, "Test message")

        assert len(progress_calls) == 1
        assert progress_calls[0] == (50, "Test message")


class TestSwmmInpHandler:
    """Test SwmmInpHandler class."""

    def test_handler_initialization(self):
        handler = SwmmInpHandler()
        assert handler is not None
        assert handler.file_path is None

    def test_is_loaded_false(self):
        handler = SwmmInpHandler()
        assert handler.is_loaded() is False

    def test_load_missing_file_raises(self):
        handler = SwmmInpHandler()
        with pytest.raises(FileLoadError):
            handler.load_file("nonexistent.inp")

    def test_get_junctions_without_load_raises(self):
        handler = SwmmInpHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.get_junctions()

    def test_get_summary_not_loaded_empty_counts(self):
        handler = SwmmInpHandler()
        summary = handler.get_summary()
        assert summary["loaded"] is False
        assert summary["counts"] == {}

    def test_validate_without_load_raises(self):
        handler = SwmmInpHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.validate_inp()

    def test_validate_missing_file_raises(self):
        handler = SwmmInpHandler()
        handler.file_path = "nonexistent.inp"
        handler.file_object = object()  # pretend loaded
        with pytest.raises(FileLoadError, match="not found"):
            handler.validate_inp()


class TestSwmmControls:
    """Test create/replace for CONTROLS via update_inp_from_settings."""

    def test_create_replace_and_round_trip(self, minimal_swmm_inp, tmp_path):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)

        handler.update_inp_from_settings(
            other_settings=SwmmOtherSettings(
                controls={
                    "R1": SwmmControl(
                        text=(
                            "RULE R1\n"
                            "IF NODE J1 DEPTH > 1\n"
                            "THEN PUMP P1 STATUS = ON\n"
                            "ELSE PUMP P1 STATUS = OFF\n"
                            "PRIORITY 1"
                        )
                    )
                }
            )
        )
        assert "R1" in handler.get_controls()
        assert handler.get_controls()["R1"].priority == 1
        assert handler.get_summary()["counts"]["controls"] == 1

        handler.update_inp_from_settings(
            other_settings=SwmmOtherSettings(
                controls={
                    "R1": SwmmControl(
                        text=(
                            "RULE R1\n"
                            "IF NODE J1 DEPTH > 2\n"
                            "THEN PUMP P1 STATUS = OFF\n"
                            "PRIORITY 2"
                        )
                    )
                }
            )
        )
        assert handler.get_controls()["R1"].priority == 2

        out_path = tmp_path / "with_control.inp"
        handler.write(str(out_path))
        reloaded = SwmmInpHandler()
        reloaded.load_file(str(out_path))
        assert "R1" in reloaded.get_controls()
        assert reloaded.get_controls()["R1"].priority == 2

    def test_empty_control_text_raises(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        with pytest.raises(ValidationError, match="text is empty"):
            handler.update_inp_from_settings(
                other_settings=SwmmOtherSettings(
                    controls={"R1": SwmmControl(text="")}
                )
            )

    def test_control_name_mismatch_raises(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        with pytest.raises(ValidationError, match="does not match"):
            handler.update_inp_from_settings(
                other_settings=SwmmOtherSettings(
                    controls={
                        "R2": SwmmControl(
                            text=(
                                "RULE R1\n"
                                "IF NODE J1 DEPTH > 1\n"
                                "THEN PUMP P1 STATUS = ON\n"
                                "PRIORITY 1"
                            )
                        )
                    }
                )
            )


class TestSwmmSettingsPassthrough:
    """SWMM settings stay in INP / FLOW_UNITS units (no SI conversion)."""

    def test_junction_elevation_and_flow_units_unchanged(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(
                junctions={"J1": SwmmJunction(elevation=12.5)}
            ),
            options_settings=SwmmOptionsSettings(flow_units=SwmmFlowUnits.CFS),
        )

        assert handler.file_object.JUNCTIONS["J1"].elevation == 12.5
        assert handler.file_object.OPTIONS["FLOW_UNITS"] == "CFS"


class TestSwmmRptHandler:
    """Test SwmmRptHandler class."""

    def test_handler_initialization(self):
        handler = SwmmRptHandler()
        assert handler is not None

    def test_is_loaded_false(self):
        handler = SwmmRptHandler()
        assert handler.is_loaded() is False

    def test_load_missing_file_raises(self):
        handler = SwmmRptHandler()
        with pytest.raises(FileLoadError):
            handler.load_file("nonexistent.rpt")

    def test_get_errors_without_load_raises(self):
        handler = SwmmRptHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.get_errors()

    def test_export_database_without_load_raises(self):
        handler = SwmmRptHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.export_to_database(result_id="1")

    def test_export_frost_not_implemented(self):
        handler = SwmmRptHandler()
        with pytest.raises(NotImplementedError):
            handler.export_to_frost()


class TestSwmmOutHandler:
    """Test SwmmOutHandler class."""

    def test_export_database_without_load_raises(self):
        handler = SwmmOutHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.export_to_database(result_id="1")
