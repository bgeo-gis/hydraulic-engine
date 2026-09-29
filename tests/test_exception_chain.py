"""
Tests for exception-chain formatting and root-cause surfacing in EPANET and SWMM.
"""
# -*- coding: utf-8 -*-
import pytest

from hydraulic_engine import FileLoadError, ValidationError, FileWriteError
from hydraulic_engine.epanet import EpanetInpHandler
from hydraulic_engine.epanet import file_handler as epanet_file_handler
from hydraulic_engine.swmm import SwmmInpHandler
from hydraulic_engine.swmm import file_handler as swmm_file_handler
from hydraulic_engine.utils.tools_exceptions import (
    collect_engine_failure,
    extract_rpt_errors,
    format_exception_chain,
)

ROOT_MSG = "(Error 205) undefined time pattern, 'TEST'"
WRAPPER_MSG = "(Error 200) one or more errors in input file 'model.inp'"


def _chained_exception() -> Exception:
    """Build an exception chain shaped like WNTR's: root cause wrapped by a generic error."""
    try:
        try:
            raise KeyError(ROOT_MSG)
        except KeyError as root:
            raise Exception(WRAPPER_MSG) from root
    except Exception as outer:
        return outer


class TestFormatExceptionChain:
    def test_single_exception(self):
        assert format_exception_chain(ValueError("boom")) == "boom"

    def test_nested_causes_innermost_first(self):
        exc = Exception("outer")
        exc.__cause__ = Exception("middle")
        exc.__cause__.__cause__ = Exception("inner")
        assert format_exception_chain(exc) == "inner | middle | outer"

    def test_uses_context_when_no_cause(self):
        exc = Exception("outer")
        exc.__context__ = Exception("inner")
        assert format_exception_chain(exc) == "inner | outer"

    def test_cause_preferred_over_context(self):
        exc = Exception("outer")
        exc.__cause__ = Exception("cause")
        exc.__context__ = Exception("context")
        assert format_exception_chain(exc) == "cause | outer"

    def test_cycle_is_safe(self):
        a = Exception("a")
        b = Exception("b")
        a.__cause__ = b
        b.__cause__ = a
        assert format_exception_chain(a) == "b | a"

    def test_duplicate_messages_are_collapsed(self):
        exc = Exception("same")
        exc.__cause__ = Exception("same")
        assert format_exception_chain(exc) == "same"

    def test_empty_message_falls_back_to_class_name(self):
        assert format_exception_chain(RuntimeError()) == "RuntimeError"

    def test_empty_outer_message_is_skipped(self):
        exc = Exception("")
        exc.__cause__ = Exception("inner")
        assert format_exception_chain(exc) == "inner"

    def test_depth_is_capped(self):
        exc = Exception("e0")
        current = exc
        for i in range(1, 50):
            current.__cause__ = Exception(f"e{i}")
            current = current.__cause__
        assert len(format_exception_chain(exc).split(" | ")) == 10

    def test_wntr_like_chain(self):
        # KeyError repr quotes are stripped from the message
        assert format_exception_chain(_chained_exception()) == f"{ROOT_MSG} | {WRAPPER_MSG}"


class TestRptErrors:
    def test_extract_rpt_errors(self, tmp_path):
        rpt = tmp_path / "run.rpt"
        rpt.write_text(
            "Page 1\n"
            "  Error 205: undefined time pattern TEST.\n"
            "  Input Error 213: invalid option value.\n"
            "  ERROR 217: something in SWMM\n"
            "  Continuity Error (%) ......  0.01\n"
            "  Error 205: undefined time pattern TEST.\n",
            encoding="utf-8",
        )
        assert extract_rpt_errors(str(rpt)) == [
            "Error 205: undefined time pattern TEST.",
            "Input Error 213: invalid option value.",
            "ERROR 217: something in SWMM",
        ]

    def test_extract_rpt_errors_missing_file(self, tmp_path):
        assert extract_rpt_errors(str(tmp_path / "missing.rpt")) == []
        assert extract_rpt_errors(None) == []

    def test_collect_engine_failure_appends_rpt_error(self, tmp_path):
        rpt = tmp_path / "run.rpt"
        rpt.write_text("Error 205: undefined time pattern.\n", encoding="utf-8")
        errors = []
        message = collect_engine_failure(Exception("engine failed"), errors, str(rpt))
        assert message == "engine failed | Error 205: undefined time pattern."
        assert errors == ["engine failed", "Error 205: undefined time pattern."]

    def test_collect_engine_failure_keeps_chain_code(self, tmp_path):
        rpt = tmp_path / "run.rpt"
        rpt.write_text("Error 205: undefined time pattern.\n", encoding="utf-8")
        errors = []
        message = collect_engine_failure(_chained_exception(), errors, str(rpt))
        assert message == f"{ROOT_MSG} | {WRAPPER_MSG}"

    def test_collect_engine_failure_ignores_stale_rpt(self, tmp_path):
        rpt = tmp_path / "run.rpt"
        rpt.write_text("Error 205: undefined time pattern.\n", encoding="utf-8")
        errors = []
        message = collect_engine_failure(
            Exception("engine failed"), errors, str(rpt), since=rpt.stat().st_mtime + 100
        )
        assert message == "engine failed"
        assert errors == ["engine failed"]


def _touch_inp(tmp_path, name):
    path = tmp_path / name
    path.write_text("[TITLE]\n", encoding="utf-8")
    return str(path)


class TestFileLoadErrorSurfacesRootCause:
    def test_epanet_load_error_includes_root_cause(self, tmp_path, monkeypatch):
        def fake_model(_path):
            raise _chained_exception()

        monkeypatch.setattr(epanet_file_handler.wntr.network, "WaterNetworkModel", fake_model)
        handler = EpanetInpHandler()
        with pytest.raises(FileLoadError) as excinfo:
            handler.load_file(_touch_inp(tmp_path, "bad.inp"))

        assert "Error 205" in str(excinfo.value)
        assert "undefined time pattern" in str(excinfo.value)
        assert "Error 200" in str(excinfo.value)
        assert excinfo.value.__cause__ is not None
        assert "undefined time pattern" in handler.error_msg

    def test_swmm_load_error_includes_root_cause(self, tmp_path, monkeypatch):
        def fake_read(_path):
            raise _chained_exception()

        monkeypatch.setattr(swmm_file_handler, "read_inp_file", fake_read)
        handler = SwmmInpHandler()
        with pytest.raises(FileLoadError) as excinfo:
            handler.load_file(_touch_inp(tmp_path, "bad.inp"))

        assert "undefined time pattern" in str(excinfo.value)
        assert "Error 200" in str(excinfo.value)
        assert excinfo.value.__cause__ is not None

    def test_epanet_validation_error_includes_root_cause(self, tmp_path, monkeypatch):
        handler = EpanetInpHandler()
        handler.file_path = _touch_inp(tmp_path, "ok.inp")
        handler.file_object = object()

        def fake_model(_path):
            raise _chained_exception()

        from hydraulic_engine.epanet import inp_handler as epanet_inp_handler
        monkeypatch.setattr(epanet_inp_handler.wntr.network, "WaterNetworkModel", fake_model)
        with pytest.raises(ValidationError, match="undefined time pattern"):
            handler.validate_inp()

    def test_swmm_write_error_includes_root_cause(self, tmp_path):
        class FailingInp:
            def write_file(self, _path):
                raise _chained_exception()

        handler = SwmmInpHandler()
        handler.file_path = str(tmp_path / "out.inp")
        handler.file_object = FailingInp()
        with pytest.raises(FileWriteError, match="undefined time pattern"):
            handler.write()
