"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

Utils module - Contains utility functions for hydraulic calculations.
"""
# -*- coding: utf-8 -*-
from .enums import ExportDataSource, RunStatus
from .tools_db import HeDbDao, HePgDao, HeSqliteDao, HeGpkgDao, DbType
from .tools_db import create_pg_connection, create_gpkg_connection, create_sqlite_connection
from .tools_db import get_connection, close_connection
from .tools_api import HeApiClient, HeFrostClient, ApiType
from .tools_api import create_frost_connection, get_api_client, close_api_client
from .tools_log import set_logger, log_debug, log_info, log_warning, log_error, HeLogger
from .tools_os import get_datadir
from .tools_exceptions import format_exception_chain, extract_rpt_errors

__all__ = [
    "ExportDataSource",
    "RunStatus",
    "HeDbDao",
    "HePgDao",
    "HeSqliteDao",
    "HeGpkgDao",
    "DbType",
    "create_pg_connection",
    "create_gpkg_connection",
    "create_sqlite_connection",
    "get_connection",
    "close_connection",
    "HeApiClient",
    "HeFrostClient",
    "ApiType",
    "create_frost_connection",
    "get_api_client",
    "close_api_client",
    "set_logger",
    "log_debug",
    "log_info",
    "log_warning",
    "log_error",
    "HeLogger",
    "get_datadir",
    "format_exception_chain",
    "extract_rpt_errors",
]
