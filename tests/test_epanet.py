"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

EPANET module tests.
"""
# -*- coding: utf-8 -*-
import pytest

from hydraulic_engine import FileLoadError, ModelNotLoadedError, ValidationError
from hydraulic_engine.epanet import (
    EpanetRunner,
    EpanetInpHandler,
    EpanetBinHandler,
    EpanetFileHandler,
    EpanetRunResult,
    EpanetOtherSettings,
    EpanetControl,
    EpanetRule,
)
from hydraulic_engine.utils.enums import RunStatus


_MINIMAL_EPANET_INP = """[TITLE]
Minimal
[JUNCTIONS]
11 216.4 0
[RESERVOIRS]
9 243.8
[TANKS]
2 259.1 12.2 0 18.3 15.2 0
[PIPES]
10 9 11 1000 450 110 0 Open
[PUMPS]
9 11 2 HEAD 1
[CURVES]
1 1500 76.2
1 0 91.4
[TIMES]
Duration 1:00
Hydraulic Timestep 0:05
Pattern Timestep 1:00
[OPTIONS]
Units LPS
Headloss H-W
[COORDINATES]
11 0 0
9 1 0
2 0 1
"""


@pytest.fixture
def minimal_epanet_inp(tmp_path):
    path = tmp_path / "minimal.inp"
    path.write_text(_MINIMAL_EPANET_INP, encoding="utf-8")
    return str(path)


class TestEpanetImports:
    """Test EPANET module imports."""

    def test_import_from_package(self):
        from hydraulic_engine import epanet
        assert EpanetRunner is not None
        assert epanet.EpanetInpHandler is not None
        assert epanet.EpanetBinHandler is not None
        assert epanet.EpanetFileHandler is not None
        assert EpanetFileHandler is not None
        assert EpanetRunResult is not None

    def test_import_exceptions_from_epanet(self):
        from hydraulic_engine.epanet import ModelNotLoadedError, ValidationError
        assert issubclass(ModelNotLoadedError, Exception)

    def test_import_control_models(self):
        from hydraulic_engine.epanet import EpanetControl, EpanetRule
        assert EpanetControl is not None
        assert EpanetRule is not None


class TestEpanetRunner:
    """Test EpanetRunner class."""

    def test_runner_initialization(self):
        runner = EpanetRunner(inp_path="model.inp")
        assert runner is not None

    def test_run_missing_file_raises(self):
        runner = EpanetRunner(inp_path="nonexistent.inp")
        with pytest.raises(FileLoadError):
            runner.run()

    def test_run_with_epanet_cancel_raises(self, monkeypatch):
        from hydraulic_engine import SimulationCancelled
        from hydraulic_engine.utils.enums import RunStatus
        from unittest.mock import MagicMock

        class FakeEN:
            def ENopen(self, **kwargs):
                return None

            def ENgettimeparam(self, _param):
                return 3600

            def ENreport(self):
                return None

            def ENclose(self):
                return None

        monkeypatch.setattr(
            "hydraulic_engine.epanet.runner.toolkit.ENepanet", FakeEN
        )

        runner = EpanetRunner()
        runner.bin = MagicMock()
        result = EpanetRunResult(
            inp_path="model.inp",
            rpt_path="model.rpt",
            bin_path="model.bin",
        )

        def cancel_hydraulic(*_args, **_kwargs):
            raise SimulationCancelled("Stopped at hydraulic step 1")

        monkeypatch.setattr(runner, "_run_hydraulic_simulation", cancel_hydraulic)

        with pytest.raises(SimulationCancelled) as exc_info:
            runner._run_with_epanet(
                result, step_callback=None, calculate_water_quality=False
            )

        assert exc_info.value.result is not None
        assert exc_info.value.result.status == RunStatus.CANCELLED
        assert runner.result is exc_info.value.result

    def test_run_with_epanet_engine_error_raises(self, monkeypatch):
        from hydraulic_engine import SimulationError
        from hydraulic_engine.utils.enums import RunStatus
        from unittest.mock import MagicMock

        class FakeEN:
            def ENopen(self, **kwargs):
                raise RuntimeError("engine failed")

            def ENclose(self):
                return None

        monkeypatch.setattr(
            "hydraulic_engine.epanet.runner.toolkit.ENepanet", FakeEN
        )

        runner = EpanetRunner()
        runner.bin = MagicMock()
        result = EpanetRunResult(
            inp_path="model.inp",
            rpt_path="model.rpt",
            bin_path="model.bin",
        )

        with pytest.raises(SimulationError) as exc_info:
            runner._run_with_epanet(
                result, step_callback=None, calculate_water_quality=False
            )

        assert "engine failed" in str(exc_info.value)
        assert exc_info.value.result.status == RunStatus.ERROR
        assert runner.result is exc_info.value.result


class TestEpanetInpHandler:
    """Test EpanetInpHandler class."""

    def test_handler_initialization(self):
        handler = EpanetInpHandler()
        assert handler is not None
        assert handler.file_path is None

    def test_is_loaded_false(self):
        handler = EpanetInpHandler()
        assert handler.is_loaded() is False

    def test_load_missing_file_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(FileLoadError):
            handler.load_file("nonexistent.inp")

    def test_get_summary_not_loaded(self):
        handler = EpanetInpHandler()
        summary = handler.get_summary()
        assert summary["loaded"] is False
        assert summary["counts"] == {}

    def test_validate_without_load_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.validate_inp()

    def test_validate_missing_file_raises(self):
        handler = EpanetInpHandler()
        handler.file_path = "nonexistent.inp"
        handler.file_object = object()  # pretend loaded
        with pytest.raises(FileLoadError, match="not found"):
            handler.validate_inp()


class TestEpanetControlsAndRules:
    """Test create/replace for CONTROLS and RULES via update_inp_from_settings."""

    def test_create_and_replace_control(self, minimal_epanet_inp):
        handler = EpanetInpHandler()
        handler.load_file(minimal_epanet_inp)

        handler.update_inp_from_settings(
            other_settings=EpanetOtherSettings(
                controls={
                    "c1": EpanetControl(text="LINK 9 OPEN AT TIME 0"),
                }
            )
        )
        assert "c1" in handler.get_controls()
        assert "c1" not in handler.get_rules()

        handler.update_inp_from_settings(
            other_settings=EpanetOtherSettings(
                controls={
                    "c1": EpanetControl(text="LINK 9 CLOSED AT TIME 1"),
                }
            )
        )
        assert list(handler.get_controls()) == ["c1"]
        assert "CLOSED" in str(handler.get_controls()["c1"]).upper()

    def test_create_rule_and_round_trip(self, minimal_epanet_inp, tmp_path):
        handler = EpanetInpHandler()
        handler.load_file(minimal_epanet_inp)

        handler.update_inp_from_settings(
            other_settings=EpanetOtherSettings(
                rules={
                    "R1": EpanetRule(
                        text=(
                            "IF NODE 2 LEVEL ABOVE 5\n"
                            "THEN PUMP 9 STATUS IS CLOSED\n"
                            "PRIORITY 1"
                        )
                    ),
                }
            )
        )
        assert "R1" in handler.get_rules()
        assert "R1" not in handler.get_controls()

        out_path = tmp_path / "with_rule.inp"
        handler.write(str(out_path))

        reloaded = EpanetInpHandler()
        reloaded.load_file(str(out_path))
        assert "R1" in reloaded.get_rules()
        assert reloaded.get_summary()["counts"]["rules"] == 1

    def test_empty_control_text_raises(self, minimal_epanet_inp):
        handler = EpanetInpHandler()
        handler.load_file(minimal_epanet_inp)
        with pytest.raises(ValidationError, match="text is empty"):
            handler.update_inp_from_settings(
                other_settings=EpanetOtherSettings(
                    controls={"c1": EpanetControl(text="")}
                )
            )

    def test_rule_name_mismatch_raises(self, minimal_epanet_inp):
        handler = EpanetInpHandler()
        handler.load_file(minimal_epanet_inp)
        with pytest.raises(ValidationError, match="does not match RULE id"):
            handler.update_inp_from_settings(
                other_settings=EpanetOtherSettings(
                    rules={
                        "R1": EpanetRule(
                            text=(
                                "RULE OTHER\n"
                                "IF NODE 2 LEVEL ABOVE 5\n"
                                "THEN PUMP 9 STATUS IS CLOSED"
                            )
                        )
                    }
                )
            )


class TestEpanetBinHandler:
    """Test EpanetBinHandler class."""

    def test_handler_initialization(self):
        handler = EpanetBinHandler()
        assert handler is not None
        assert handler.is_loaded() is False

    def test_export_without_bin_raises(self):
        from hydraulic_engine.exceptions import ModelNotLoadedError

        handler = EpanetBinHandler()
        inp = EpanetInpHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.export_to_database(result_id="1", inp_handler=inp)
