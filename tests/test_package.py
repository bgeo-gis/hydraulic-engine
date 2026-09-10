"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

Basic package tests.
"""
# -*- coding: utf-8 -*-
import pytest


class TestPackageImport:
    """Test package import functionality."""

    def test_import_package(self):
        import hydraulic_engine
        assert hydraulic_engine is not None

    def test_package_version(self):
        from hydraulic_engine import __version__
        assert __version__ is not None
        assert isinstance(__version__, str)

    def test_import_config(self):
        from hydraulic_engine import config
        assert config is not None

    def test_import_utils(self):
        from hydraulic_engine.utils import tools_log
        assert tools_log is not None

    def test_import_all_exceptions(self):
        from hydraulic_engine import (
            HydraulicEngineError,
            ModelNotLoadedError,
            ValidationError,
            DatabaseError,
            APIError,
            ExportError,
            SimulationError,
            SimulationCancelled,
        )
        assert HydraulicEngineError is not None
        assert SimulationCancelled is not None

    def test_import_symmetric_public_api(self):
        from hydraulic_engine.epanet import (
            EpanetRunner,
            EpanetRunResult,
            EpanetFileHandler,
            SimulationCancelled as EpanetSimulationCancelled,
        )
        from hydraulic_engine.swmm import (
            SwmmRunner,
            SwmmRunResult,
            SwmmFileHandler,
            SimulationCancelled as SwmmSimulationCancelled,
        )

        assert EpanetRunner is not None
        assert EpanetRunResult is not None
        assert EpanetFileHandler is not None
        assert SwmmRunner is not None
        assert SwmmRunResult is not None
        assert SwmmFileHandler is not None
        assert EpanetSimulationCancelled is SwmmSimulationCancelled


class TestConfig:
    """Test config module."""

    def test_init_global(self):
        from hydraulic_engine import config
        config.init_global("/test/path", "test_package", "/test/user")
        assert config.package_dir == "/test/path"
        assert config.package_name == "test_package"
        assert config.user_folder_dir == "/test/user"

    def test_reset_session(self):
        from hydraulic_engine import config
        config.reset_session()
        assert config.session_vars["db_connection"] is None
