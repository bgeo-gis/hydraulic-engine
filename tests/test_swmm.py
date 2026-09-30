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
    SwmmInflow,
    SwmmRaingage,
    SwmmRaingageFormat,
    SwmmRaingageSource,
    SwmmTreatment,
    SwmmLidUsage,
    SwmmPattern,
    SwmmPatternCycle,
    SwmmCurve,
    SwmmCurveKind,
    SwmmTimeseries,
    SwmmReportSettings,
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

    def test_get_objects_plain_dicts_in_inp_units(self, minimal_swmm_inp):
        import json

        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        objects = handler.get_objects()
        # Strict JSON: unset optional fields (NaN in swmm-api) must be serialized as None.
        json.dumps(objects, allow_nan=False)

        assert objects["units"] == "LPS"
        assert "J1" in objects["junctions"]
        assert objects["junctions"]["J1"]["elevation"] == pytest.approx(10.0)
        assert "P1" in objects["pumps"]
        assert isinstance(objects["options"], dict)

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


class TestSwmmHydrologyAndQuality:
    """Create-or-replace for INFLOWS, RAINGAGES, TREATMENT and LID_USAGE."""

    def test_inflow_created_updated_and_round_trip(self, minimal_swmm_inp, tmp_path):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(
                inflows={"a": SwmmInflow(node="J1", base_value=0.5, scale_factor=2.0)}
            )
        )
        inflow = handler.file_object.INFLOWS[("J1", "FLOW")]
        assert inflow.base_value == pytest.approx(0.5)
        assert inflow.scale_factor == pytest.approx(2.0)

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(
                inflows={"a": SwmmInflow(node="J1", constituent="FLOW", base_value=0.9)}
            )
        )
        assert len(handler.file_object.INFLOWS) == 1
        inflow = handler.file_object.INFLOWS[("J1", "FLOW")]
        assert inflow.base_value == pytest.approx(0.9)
        assert inflow.scale_factor == pytest.approx(2.0)

        out_path = tmp_path / "inflows.inp"
        handler.write(str(out_path))
        reloaded = SwmmInpHandler()
        reloaded.load_file(str(out_path))
        assert reloaded.get_inflows()[("J1", "FLOW")].base_value == pytest.approx(0.9)
        objects = reloaded.get_objects()
        assert "J1|FLOW" in objects["inflows"]
        assert {"treatment", "lid_usage", "report"} <= set(objects)

    def test_inflow_without_node_raises(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        with pytest.raises(ValidationError, match="missing node"):
            handler.update_inp_from_settings(
                feature_settings=SwmmFeatureSettings(inflows={"a": SwmmInflow(base_value=1.0)})
            )

    def test_raingage_created_from_dict_key_and_updated(self, minimal_swmm_inp, tmp_path):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(
                raingages={
                    "RG1": SwmmRaingage(
                        form=SwmmRaingageFormat.INTENSITY,
                        interval="0:05",
                        scf=1.0,
                        source=SwmmRaingageSource.TIMESERIES,
                        timeseries="TS1",
                    )
                }
            )
        )
        gage = handler.file_object.RAINGAGES["RG1"]
        assert gage.form == "INTENSITY"
        assert gage.SCF == pytest.approx(1.0)
        assert gage.timeseries == "TS1"

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(raingages={"RG1": SwmmRaingage(scf=1.5)})
        )
        assert handler.file_object.RAINGAGES["RG1"].SCF == pytest.approx(1.5)

        out_path = tmp_path / "gage.inp"
        handler.write(str(out_path))
        reloaded = SwmmInpHandler()
        reloaded.load_file(str(out_path))
        assert "RG1" in reloaded.get_raingages()

    def test_missing_raingage_without_required_fields_raises(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        with pytest.raises(ValidationError, match="cannot be created without"):
            handler.update_inp_from_settings(
                feature_settings=SwmmFeatureSettings(raingages={"RG9": SwmmRaingage(scf=2.0)})
            )

    def test_treatment_and_lid_usage_created(self, minimal_swmm_inp, tmp_path):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(
                treatment={"t": SwmmTreatment(node="J1", pollutant="TSS", result="R", function="0.5")},
                lid_usage={
                    "l": SwmmLidUsage(
                        subcatchment="S1", lid="BC1", n_replicate=2, area=10.0, width=1.0,
                        saturation_init=0.0, impervious_portion=0.0,
                    )
                },
            )
        )
        assert handler.file_object.TREATMENT[("J1", "TSS")].function == "0.5"
        assert handler.file_object.LID_USAGE[("S1", "BC1")].n_replicate == 2

        out_path = tmp_path / "quality.inp"
        handler.write(str(out_path))
        text = out_path.read_text(encoding="utf-8")
        assert "[TREATMENT]" in text
        assert "[LID_USAGE]" in text


class TestSwmmOtherCreateOrReplace:
    """Curves, patterns and timeseries are created when missing and updated when present."""

    def test_pattern_curve_timeseries_created_then_updated(self, minimal_swmm_inp, tmp_path):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)

        handler.update_inp_from_settings(
            other_settings=SwmmOtherSettings(
                patterns={"PT1": SwmmPattern(cycle=SwmmPatternCycle.HOURLY, factors=[1.0] * 24)},
                curves={"PC1": SwmmCurve(kind=SwmmCurveKind.PUMP1, points=[[0.0, 0.0], [1.0, 2.0]])},
                timeseries={"TS1": SwmmTimeseries(data=[(0.0, 1.0), (1.0, 2.0)])},
            )
        )
        assert handler.file_object.PATTERNS["PT1"].cycle == "HOURLY"
        assert handler.file_object.CURVES["PC1"].kind == "PUMP1"
        assert list(handler.file_object.TIMESERIES["TS1"].data) == [(0.0, 1.0), (1.0, 2.0)]

        handler.update_inp_from_settings(
            other_settings=SwmmOtherSettings(
                patterns={"PT1": SwmmPattern(factors=[2.0] * 24)},
                timeseries={"TS1": SwmmTimeseries(data=[(0.0, 5.0)])},
            )
        )
        assert handler.file_object.PATTERNS["PT1"].cycle == "HOURLY"
        assert handler.file_object.PATTERNS["PT1"].factors[0] == pytest.approx(2.0)
        assert list(handler.file_object.TIMESERIES["TS1"].data) == [(0.0, 5.0)]

        out_path = tmp_path / "other.inp"
        handler.write(str(out_path))
        reloaded = SwmmInpHandler()
        reloaded.load_file(str(out_path))
        assert "PT1" in reloaded.get_patterns()
        assert "PC1" in reloaded.get_curves()
        assert "TS1" in reloaded.get_timeseries()

    def test_missing_pattern_without_factors_raises(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        with pytest.raises(ValidationError, match="cannot be created without"):
            handler.update_inp_from_settings(
                other_settings=SwmmOtherSettings(patterns={"PT9": SwmmPattern(cycle=SwmmPatternCycle.DAILY)})
            )


class TestSwmmReportSettings:
    """[REPORT] writes drive get_report_element_selection."""

    def test_report_selection_round_trip(self, minimal_swmm_inp, tmp_path):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        assert not handler.get_report_element_selection().has_any()

        handler.update_inp_from_settings(
            report_settings=SwmmReportSettings(
                nodes=["J1", "J2"], links="ALL", subcatchments="NONE", continuity=False, controls=True,
            )
        )
        selection = handler.get_report_element_selection()
        assert selection.nodes == frozenset({"J1", "J2"})
        assert selection.links == "ALL"
        assert selection.subcatchments is None

        out_path = tmp_path / "report.inp"
        handler.write(str(out_path))
        reloaded = SwmmInpHandler()
        reloaded.load_file(str(out_path))
        selection = reloaded.get_report_element_selection()
        assert selection.nodes == frozenset({"J1", "J2"})
        assert selection.links == "ALL"
        assert reloaded.file_object.REPORT["CONTINUITY"] is False
        assert reloaded.file_object.REPORT["CONTROLS"] is True

    def test_report_none_removes_selection(self, minimal_swmm_inp):
        handler = SwmmInpHandler()
        handler.load_file(minimal_swmm_inp)
        handler.update_inp_from_settings(report_settings=SwmmReportSettings(subcatchments="ALL"))
        assert handler.get_report_element_selection().subcatchments == "ALL"
        handler.update_inp_from_settings(report_settings=SwmmReportSettings(subcatchments="NONE"))
        assert handler.get_report_element_selection().subcatchments is None


_SWMM_INP_WITH_EXISTING_SECTIONS = _MINIMAL_SWMM_INP.replace(
    "[JUNCTIONS]",
    "[REPORT]\nINPUT NO\nNODES ALL\n"
    "[RAINGAGES]\nRG1 INTENSITY 0:05 1.0 TIMESERIES TS1\n"
    "[TIMESERIES]\nTS1 0:00 1.0\n"
    "[JUNCTIONS]",
)


class TestSwmmLazySections:
    """swmm-api parses sections lazily; updates right after load must see the parsed section."""

    @pytest.fixture
    def inp_with_sections(self, tmp_path):
        path = tmp_path / "sections.inp"
        path.write_text(_SWMM_INP_WITH_EXISTING_SECTIONS, encoding="utf-8")
        return str(path)

    def test_existing_raingage_is_updated_not_recreated(self, inp_with_sections):
        handler = SwmmInpHandler()
        handler.load_file(inp_with_sections)

        handler.update_inp_from_settings(feature_settings=SwmmFeatureSettings(raingages={"RG1": SwmmRaingage(scf=1.5)}))

        gage = handler.file_object.RAINGAGES["RG1"]
        assert gage.SCF == pytest.approx(1.5)
        assert gage.timeseries == "TS1"

    def test_existing_report_section_is_updated_in_place(self, inp_with_sections):
        handler = SwmmInpHandler()
        handler.load_file(inp_with_sections)

        handler.update_inp_from_settings(report_settings=SwmmReportSettings(nodes=["J1"], input=True))

        report = handler.file_object.REPORT
        assert report["INPUT"] is True
        assert handler.get_report_element_selection().nodes == frozenset({"J1"})

    def test_existing_timeseries_section_is_updated_in_place(self, inp_with_sections):
        handler = SwmmInpHandler()
        handler.load_file(inp_with_sections)

        handler.update_inp_from_settings(
            other_settings=SwmmOtherSettings(timeseries={"TS1": SwmmTimeseries(data=[(0.0, 9.0)])})
        )

        assert list(handler.file_object.TIMESERIES["TS1"].data) == [(0.0, 9.0)]


class TestSwmmOutletCurveType:
    """Outlet curve types are written with the INP keyword (``FUNCTIONAL/DEPTH``)."""

    def test_enum_accepts_keyword_and_legacy_member_name(self):
        from hydraulic_engine.swmm import SwmmOutletCurveType

        assert SwmmOutletCurveType("FUNCTIONAL/DEPTH") is SwmmOutletCurveType.FUNCTIONAL_DEPTH
        assert SwmmOutletCurveType("TABULAR_HEAD") is SwmmOutletCurveType.TABULAR_HEAD
        with pytest.raises(ValueError):
            SwmmOutletCurveType("SOMETHING")

    def test_outlet_curve_type_is_written_as_inp_keyword(self, tmp_path):
        from hydraulic_engine.swmm import SwmmOutlet, SwmmOutletCurveType

        inp = _MINIMAL_SWMM_INP.replace(
            "[XSECTIONS]", "[OUTLETS]\nW1 J1 O1 0 TABULAR/DEPTH OC1 NO\n[XSECTIONS]"
        )
        path = tmp_path / "outlet.inp"
        path.write_text(inp, encoding="utf-8")
        handler = SwmmInpHandler()
        handler.load_file(str(path))

        handler.update_inp_from_settings(
            feature_settings=SwmmFeatureSettings(
                outlets={
                    "W1": SwmmOutlet(
                        curve_type=SwmmOutletCurveType.FUNCTIONAL_DEPTH, curve_description=(2.0, 0.6)
                    )
                }
            )
        )
        out_path = tmp_path / "outlet_out.inp"
        handler.write(str(out_path))

        text = out_path.read_text(encoding="utf-8")
        assert "FUNCTIONAL/DEPTH" in text
        assert "FUNCTIONAL_DEPTH" not in text


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
