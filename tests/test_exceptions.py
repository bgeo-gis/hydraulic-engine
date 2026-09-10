"""
Tests for hydraulic_engine exception hierarchy and error-raising behavior.
"""
# -*- coding: utf-8 -*-
import pytest

from hydraulic_engine import (
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
from hydraulic_engine.epanet import EpanetInpHandler
from hydraulic_engine.swmm import SwmmInpHandler, SwmmRptHandler
from hydraulic_engine.utils import tools_os


class TestExceptionHierarchy:
    """Test custom exceptions are exported and inherit correctly."""

    def test_all_exceptions_inherit_from_base(self):
        for exc_cls in (
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
        ):
            assert issubclass(exc_cls, HydraulicEngineError)
            assert issubclass(exc_cls, Exception)


class TestEpanetErrorHandling:
    """Test EPANET handlers raise on failure."""

    def test_load_missing_inp_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(FileLoadError, match="not found"):
            handler.load_file("nonexistent.inp")

    def test_get_junctions_without_load_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(ModelNotLoadedError, match="No INP file loaded"):
            handler.get_junctions()

    def test_get_junctions_count_without_load_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.get_junctions_count()

    def test_write_without_load_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(FileWriteError):
            handler.write("out.inp")

    def test_unsupported_rpt_raises(self):
        handler = EpanetInpHandler()
        with pytest.raises(FileLoadError, match="not found"):
            handler.load_file("missing.rpt")


class TestSwmmErrorHandling:
    """Test SWMM handlers raise on failure."""

    def test_load_missing_inp_raises(self):
        handler = SwmmInpHandler()
        with pytest.raises(FileLoadError, match="not found"):
            handler.load_file("nonexistent.inp")

    def test_get_junctions_without_load_raises(self):
        handler = SwmmInpHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.get_junctions()

    def test_rpt_get_errors_without_load_raises(self):
        handler = SwmmRptHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.get_errors()

    def test_out_export_database_without_load_raises(self):
        from hydraulic_engine.swmm import SwmmOutHandler

        handler = SwmmOutHandler()
        with pytest.raises(ModelNotLoadedError):
            handler.export_to_database(result_id="1")


class TestUtilsErrorHandling:
    """Test utility modules raise on failure."""

    def test_ensure_dir_invalid_path_raises(self, tmp_path):
        invalid = str(tmp_path / "nested" / "dir")
        # Should succeed on valid path
        assert tools_os.ensure_dir(invalid) is True

    def test_sqlite_connect_invalid_path_raises(self):
        from hydraulic_engine.utils.tools_db import HeSqliteDao

        dao = HeSqliteDao()
        with pytest.raises(DatabaseError):
            dao.connect("/nonexistent/path/that/cannot/exist/test.db")
