"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.
"""
# -*- coding: utf-8 -*-
import wntr

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from datetime import datetime, timezone
from pyproj import Transformer
from datetime import timedelta
import numpy as np
import pandas as pd
from wntr.epanet.util import from_si, HydParam

from .file_handler import EpanetResultHandler, EpanetFileHandler
from .inp_handler import EpanetInpHandler
from ..utils import tools_log
from ..utils.tools_api import get_api_client, HeFrostClient
from ..utils import tools_sensorthings
from ..exceptions import (
    ModelNotLoadedError,
    DatabaseError,
    ExportError,
    SimulationError,
    APIError,
)
from ..utils.tools_db import HePgDao, get_connection


class EpanetBinHandler(EpanetFileHandler, EpanetResultHandler):
    """
    Handler for EPANET BIN (binary) files.

    Provides functionality to read and parse EPANET simulation output.

    Note: This module uses private helper functions (_prepare_*)
    for data preparation tasks. These are not part of the public API.

    Example usage:
        handler = EpanetBinHandler()
        handler.load_file("results.bin")
        handler.export_to_frost(inp_handler=inp_handler, result_id="test1")
        handler.export_to_database(result_id="test1")
    """

    def export_to_database(
            self,
            result_id: str,
            inp_handler: EpanetInpHandler,
            round_decimals: int = 4,
            dao: Optional[HePgDao] = None,
            giswater_version: int = 4,
            only_extrema: bool = False
        ) -> bool:
        """
        Export simulation results to Giswater database.

        Fills the following tables:
        - rpt_node: Time series node results (demand, head, pressure, quality)
        - rpt_arc: Time series arc results (flow, velocity, headloss, etc.)
        - rpt_node_stats: Aggregated node statistics (max/min/avg)
        - rpt_arc_stats: Aggregated arc statistics (max/min/avg)
        - selector_rpt_main: Sets the current result for visualization
        - rpt_cat_result: Updates execution metadata

        Prerequisites:
        - The rpt_inp_node and rpt_inp_arc tables must be populated by the plugin
          (via gw_fct_pg2epa_main) before calling this method.

        :param result_id: The result identifier (must match rpt_cat_result.result_id)
        :param inp_handler: INP handler to get coordinates
        :param round_decimals: Number of decimal places to round the results (default: 2)
        :param dao: Database access object (optional, uses global connection if not provided)
        :param giswater_version: Version of Giswater (default: 4)
        :param only_extrema: If True, skip time series inserts and only export aggregated stats
        :return: True if export successful, False otherwise
        """
        if not self.is_loaded():
            raise ModelNotLoadedError("No binary file loaded")

        if dao is None:
            dao = get_connection()

        if dao is None or not dao.is_connected():
            raise DatabaseError("No database connection available")

        results: wntr.sim.SimulationResults = self.file_object

        try:
            tools_log.log_info(f"Starting export to database for result_id: {result_id}")

            tools_log.log_info("Cleaning previous results...")
            _clean_previous_results(dao, result_id, giswater_version)

            tools_log.log_info("Preparing node and arc data...")
            prepared_nodes = _prepare_node_data(results, inp_handler, round_decimals)
            prepared_arcs = _prepare_arc_data(results, inp_handler, round_decimals)

            if not only_extrema:
                tools_log.log_info("Inserting node results...")
                node_count = _copy_node_results(dao, result_id, prepared_nodes, giswater_version)
                tools_log.log_info(f"Inserted {node_count} node result records")

                tools_log.log_info("Inserting arc results...")
                arc_count, reversed_arcs = _copy_arc_results(dao, result_id, prepared_arcs)
                tools_log.log_info(f"Inserted {arc_count} arc result records")

                if reversed_arcs:
                    tools_log.log_info("Reversing arc geometries for negative flow...")
                    _reverse_arc_geometries(dao, result_id, reversed_arcs)
            else:
                tools_log.log_info("Skipping time series inserts (only_extrema=True)")

            if giswater_version > 3:
                tools_log.log_info("Calculating node statistics...")
                _export_node_stats(dao, result_id, prepared_nodes, round_decimals, giswater_version)

                tools_log.log_info("Calculating arc statistics...")
                _export_arc_stats(dao, result_id, prepared_arcs, round_decimals)

            tools_log.log_info("Updating result catalog and selectors...")
            _finalize_import(dao, result_id, giswater_version)

            dao.commit()
            tools_log.log_info(f"Export to database completed successfully for result_id: {result_id}")
            return True

        except (DatabaseError, ExportError, SimulationError, ModelNotLoadedError):
            try:
                dao.rollback()
            except DatabaseError:
                pass
            raise
        except Exception as e:
            tools_log.log_error(f"Error exporting to database: {e}")
            try:
                dao.rollback()
            except DatabaseError:
                pass
            raise ExportError(f"Error exporting to database: {e}") from e

    def export_to_frost(
            self,
            inp_handler: EpanetFileHandler,
            result_id: str,
            batch_size: int = 50,
            max_workers: int = 4,
            crs_from: int = 25831,
            crs_to: int = 4326,
            start_time: Optional[datetime] = None,
            client: Optional[HeFrostClient] = None
        ) -> bool:
        """
        Export simulation results to FROST-Server (SensorThings API).

        Creates Things for nodes and links, Datastreams for each output variable, and Observations for the time series data.

        :param inp_handler: INP handler to get coordinates
        :param result_id: ID of the result
        :param batch_size: Number of operations per batch request (default: 200)
        :param crs_from: Source CRS code (default: 25831 - ETRS89 / UTM zone 31N)
        :param crs_to: Target CRS code (default: 4326 - WGS84)
        :param network_type: Type of network (default: "EPANET")
        :param start_time: Simulation start time (default: None, uses current time + start_clocktime)
        :param max_workers: Number of concurrent batch requests (default: 4)
        """
        if client is None:
            client = get_api_client()

        if not client or not isinstance(client, HeFrostClient):
            raise APIError("No FROST client available")

        if not self.is_loaded():
            raise ModelNotLoadedError("No binary file loaded")

        # Delete all existing entities. Note: This is only used for testing purposes.
        # if delete_all:
        #     tools_log.log_info("Deleting all existing entities...")
        #     tools_sensorthings.delete_all_entities(
        #         batch_size=batch_size,
        #         max_workers=max_workers,
        #         client=client
        #     )
        #     tools_log.log_info("Cleanup completed.")

        # Check if INP file is loaded
        if not inp_handler.is_loaded():
            raise ModelNotLoadedError("No INP file loaded")

        # Determine simulation start time
        if start_time is None:
            start_time = datetime.now(timezone.utc) + timedelta(seconds=inp_handler.file_object.options.time.start_clocktime)
        tools_log.log_info(f"Simulation start time: {start_time.isoformat()}")

        # Pre-fetch existing entities (optimized: 2 API calls instead of N)
        tools_log.log_info("Fetching existing entities from server...")
        things_cache = tools_sensorthings.get_all_things_with_locations(client)
        obs_props_cache = tools_sensorthings.get_all_observed_properties(client)
        tools_log.log_info(f"Found {len(things_cache)} existing Things and {len(obs_props_cache)} ObservedProperties")

        # Get or create ObservedProperties (only creates missing ones)
        property_ids = tools_sensorthings.get_or_create_observed_properties(
            obs_props_cache=obs_props_cache,
            engine='epanet',
            client=client
        )

        # Create new Sensor for this simulation run
        sensor_ids = tools_sensorthings.create_simulation_sensor(
            result_id=result_id,
            network_type='EPANET',
            inp_file=inp_handler.file_path,
            client=client
        )

        # Set up coordinate transformer
        transformer = Transformer.from_crs(crs_from, crs_to, always_xy=True)

        # Track feature IDs from the INP file
        inp_feature_ids = set()

        # Prepare node and link data
        tools_log.log_info("Preparing nodes...")
        nodes_data = _prepare_nodes_data(inp_handler.file_object)
        tools_log.log_info(f"Found {len(nodes_data)} nodes")

        node_things = _prepare_nodes_things_data(
            nodes_data, inp_handler.file_object, self.file_object, sensor_ids, property_ids,
            transformer, start_time, inp_feature_ids
        )

        tools_log.log_info("Preparing links...")
        links_data = _prepare_links_data(inp_handler.file_object)
        tools_log.log_info(f"Found {len(links_data)} links")

        link_things = _prepare_links_things_data(
            links_data, self.file_object, sensor_ids, property_ids,
            inp_handler.file_object, transformer, start_time, inp_feature_ids
        )

        # Combine all Things and process in batches
        all_things = node_things + link_things
        tools_log.log_info(
            f"Processing {len(all_things)} Things using batch requests "
            f"(batch_size={batch_size}, max_workers={max_workers})..."
        )
        tools_sensorthings.process_things_batch(
            all_things,
            things_cache,
            batch_size=batch_size,
            max_workers=max_workers,
            client=client
        )

        # Mark Things not in INP as obsolete
        tools_log.log_info("Checking for obsolete Things...")
        tools_sensorthings.mark_obsolete_things(
            things_cache,
            inp_feature_ids,
            batch_size=batch_size,
            max_workers=max_workers,
            client=client
        )

        tools_log.log_info("Processing completed!")
        return True


# region Export to Database Helper Functions

# region Export to Database Helper Functions

_STATUS_MAP = {0: 'CLOSED', 1: 'OPEN', 2: 'ACTIVE'}


@dataclass
class _PreparedNodeData:
    """Converted node time-series data ready for streaming export and stats."""
    node_ids: List[str]
    time_strings: List[str]
    top_elev: Dict[str, Optional[float]]
    demand: Optional[pd.DataFrame]
    head: Optional[pd.DataFrame]
    press: Optional[pd.DataFrame]
    quality: Optional[pd.DataFrame]


@dataclass
class _PreparedArcData:
    """Converted arc time-series data ready for streaming export and stats."""
    arc_ids: List[str]
    time_strings: List[str]
    length: Dict[str, Optional[float]]
    diameter: Dict[str, Optional[float]]
    flow: Optional[pd.DataFrame]
    velocity: Optional[pd.DataFrame]
    headloss: Optional[pd.DataFrame]
    setting: Optional[pd.DataFrame]
    reaction: Optional[pd.DataFrame]
    ffactor: Optional[pd.DataFrame]
    status: Optional[pd.DataFrame]


def _seconds_to_time_str(seconds: int) -> str:
    """
    Convert seconds to HH:MM:SS time string format.

    :param seconds: Time in seconds
    :return: Time string in HH:MM:SS format
    """
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}:{m:02d}:{s:02d}"


def _get_unit_system(inp_handler: EpanetInpHandler) -> wntr.epanet.util.FlowUnits:
    """Resolve the EPANET flow unit system from the INP handler."""
    unit_system = getattr(
        wntr.epanet.util.FlowUnits,
        inp_handler.file_object.options.hydraulic.inpfile_units
    )
    if unit_system is None:
        tools_log.log_error(
            f"Invalid unit system: {inp_handler.file_object.options.hydraulic.inpfile_units}"
        )
        raise SimulationError(
            f"Invalid unit system: {inp_handler.file_object.options.hydraulic.inpfile_units}"
        )
    return unit_system


def _convert_from_si(
    value: float,
    unit_system: wntr.epanet.util.FlowUnits,
    param: HydParam,
    round_decimals: int = 4,
) -> float:
    """Convert a scalar value from SI to EPANET units."""
    return round(float(from_si(unit_system, value, param)), round_decimals)


def _convert_dataframe_from_si(
    df: pd.DataFrame,
    unit_system: wntr.epanet.util.FlowUnits,
    param: HydParam,
    round_decimals: int = 4,
) -> pd.DataFrame:
    """
    Convert an entire result DataFrame from SI to EPANET units.

    WNTR unit conversions are linear multipliers, so a single factor is applied
    to the full array instead of converting cell by cell.
    """
    factor = from_si(unit_system, 1.0, param)
    return (df * factor).round(round_decimals)


def _round_dataframe(df: pd.DataFrame, round_decimals: int = 4) -> pd.DataFrame:
    """Round all numeric values in a result DataFrame."""
    return df.round(round_decimals)


def _df_value(df: Optional[pd.DataFrame], t_idx: int, col_idx: int) -> Optional[Any]:
    """Read a single cell from a DataFrame, returning None for missing values."""
    if df is None:
        return None
    value = df.iat[t_idx, col_idx]
    if pd.isna(value):
        return None
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


def _status_to_str(value: Optional[int]) -> Optional[str]:
    """Map EPANET status codes to their string representation."""
    if value is None:
        return None
    return _STATUS_MAP.get(value, str(value))


def _dataframe_metric_stats(
    df: pd.DataFrame,
    round_decimals: int,
    use_abs: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Compute max, min, avg, t_max and t_min for each column in a time-series DataFrame.

    :param df: Time-series DataFrame with time index and entity columns
    :param round_decimals: Decimal places for numeric stats
    :param use_abs: If True, use absolute values before computing statistics
    :return: Per-column statistics keyed by column name
    """
    working_df = df.abs() if use_abs else df
    max_series = working_df.max(axis=0)
    min_series = working_df.min(axis=0)
    avg_series = working_df.mean(axis=0).round(round_decimals)
    tmax_series = working_df.idxmax(axis=0)
    tmin_series = working_df.idxmin(axis=0)

    stats: Dict[str, Dict[str, Any]] = {}
    for column in working_df.columns:
        if pd.isna(max_series[column]) and pd.isna(min_series[column]):
            stats[column] = {
                'max': None,
                'min': None,
                'avg': None,
                'tmax': None,
                'tmin': None,
            }
            continue

        stats[column] = {
            'max': round(float(max_series[column]), round_decimals) if pd.notna(max_series[column]) else None,
            'min': round(float(min_series[column]), round_decimals) if pd.notna(min_series[column]) else None,
            'avg': round(float(avg_series[column]), round_decimals) if pd.notna(avg_series[column]) else None,
            'tmax': _seconds_to_time_str(int(tmax_series[column])) if pd.notna(tmax_series[column]) else None,
            'tmin': _seconds_to_time_str(int(tmin_series[column])) if pd.notna(tmin_series[column]) else None,
        }

    return stats


def _prepare_node_data(
    results: wntr.sim.SimulationResults,
    inp_handler: EpanetInpHandler,
    round_decimals: int = 4,
) -> _PreparedNodeData:
    """Prepare converted node DataFrames and static metadata for export."""
    unit_system = _get_unit_system(inp_handler)
    node_data = results.node
    if node_data is None:
        raise SimulationError("No node data found in results")
    if 'demand' not in node_data:
        raise SimulationError("No demand data found in results")

    demand_df = node_data['demand']
    node_ids = demand_df.columns.tolist()
    time_steps = demand_df.index.tolist()
    if not time_steps:
        raise SimulationError("No time steps found in results")

    time_strings = [_seconds_to_time_str(int(time_sec)) for time_sec in time_steps]
    top_elev: Dict[str, Optional[float]] = {}
    for node_id in node_ids:
        elevation = getattr(inp_handler.file_object.nodes[node_id], 'elevation', None)
        top_elev[node_id] = (
            _convert_from_si(elevation, unit_system, HydParam.Elevation, round_decimals)
            if elevation is not None else None
        )

    return _PreparedNodeData(
        node_ids=node_ids,
        time_strings=time_strings,
        top_elev=top_elev,
        demand=_convert_dataframe_from_si(demand_df, unit_system, HydParam.Demand, round_decimals),
        head=_convert_dataframe_from_si(node_data['head'], unit_system, HydParam.HydraulicHead, round_decimals)
        if 'head' in node_data else None,
        press=_convert_dataframe_from_si(node_data['pressure'], unit_system, HydParam.Pressure, round_decimals)
        if 'pressure' in node_data else None,
        quality=_round_dataframe(node_data['quality'], round_decimals)
        if 'quality' in node_data else None,
    )


def _prepare_arc_data(
    results: wntr.sim.SimulationResults,
    inp_handler: EpanetInpHandler,
    round_decimals: int = 4,
) -> _PreparedArcData:
    """Prepare converted arc DataFrames and static metadata for export."""
    unit_system = _get_unit_system(inp_handler)
    link_data = results.link
    if link_data is None:
        raise SimulationError("No link data found in results")
    if 'flowrate' not in link_data:
        tools_log.log_warning("No flowrate data found in results")
        raise SimulationError("No flowrate data found in results")

    flow_df = link_data['flowrate']
    arc_ids = flow_df.columns.tolist()
    time_steps = flow_df.index.tolist()
    if not time_steps:
        raise SimulationError("No time steps found in results")

    time_strings = [_seconds_to_time_str(int(time_sec)) for time_sec in time_steps]
    length: Dict[str, Optional[float]] = {}
    diameter: Dict[str, Optional[float]] = {}
    for arc_id in arc_ids:
        link = inp_handler.file_object.links[arc_id]
        link_length = getattr(link, 'length', None)
        link_diameter = getattr(link, 'diameter', None)
        length[arc_id] = (
            _convert_from_si(link_length, unit_system, HydParam.Length, round_decimals)
            if link_length is not None else None
        )
        diameter[arc_id] = (
            _convert_from_si(link_diameter, unit_system, HydParam.PipeDiameter, round_decimals)
            if link_diameter is not None else None
        )

    return _PreparedArcData(
        arc_ids=arc_ids,
        time_strings=time_strings,
        length=length,
        diameter=diameter,
        flow=_convert_dataframe_from_si(flow_df, unit_system, HydParam.Flow, round_decimals),
        velocity=_convert_dataframe_from_si(link_data['velocity'], unit_system, HydParam.Velocity, round_decimals)
        if 'velocity' in link_data else None,
        headloss=_convert_dataframe_from_si(link_data['headloss'], unit_system, HydParam.HeadLoss, round_decimals)
        if 'headloss' in link_data else None,
        setting=_round_dataframe(link_data['setting'], round_decimals)
        if 'setting' in link_data else None,
        reaction=_round_dataframe(link_data['reaction_rate'], round_decimals)
        if 'reaction_rate' in link_data else None,
        ffactor=_round_dataframe(link_data['friction_factor'], round_decimals)
        if 'friction_factor' in link_data else None,
        status=link_data['status'].astype('Int64') if 'status' in link_data else None,
    )


def _iter_node_records(
    result_id: str,
    prepared: _PreparedNodeData,
) -> Iterator[Tuple[Any, ...]]:
    """Yield node result rows for PostgreSQL COPY streaming."""
    for t_idx, time_str in enumerate(prepared.time_strings):
        for n_idx, node_id in enumerate(prepared.node_ids):
            yield (
                result_id,
                node_id,
                time_str,
                prepared.top_elev.get(node_id),
                _df_value(prepared.demand, t_idx, n_idx),
                _df_value(prepared.head, t_idx, n_idx),
                _df_value(prepared.press, t_idx, n_idx),
                _df_value(prepared.quality, t_idx, n_idx),
            )


def _copy_node_results(
    dao: HePgDao,
    result_id: str,
    prepared: _PreparedNodeData,
    giswater_version: int = 4,
) -> int:
    """Insert node time-series results using PostgreSQL COPY."""
    if not dao.cursor:
        return 0

    elevation_column = 'elevation' if giswater_version == 3 else 'top_elev'
    copy_sql = f"""
        COPY rpt_node (result_id, node_id, time, {elevation_column}, demand, head, press, quality)
        FROM STDIN
    """
    count = 0
    with dao.cursor.copy(copy_sql) as copy:
        for record in _iter_node_records(result_id, prepared):
            copy.write_row(record)
            count += 1
    return count


def _copy_arc_results(
    dao: HePgDao,
    result_id: str,
    prepared: _PreparedArcData,
) -> Tuple[int, Set[str]]:
    """Insert arc time-series results using PostgreSQL COPY."""
    if not dao.cursor:
        return 0, set()

    copy_sql = """
        COPY rpt_arc (
            result_id, arc_id, time, length, diameter, flow, vel, headloss,
            setting, reaction, ffactor, status
        ) FROM STDIN
    """
    count = 0
    reversed_arcs: Set[str] = set()
    with dao.cursor.copy(copy_sql) as copy:
        for t_idx, time_str in enumerate(prepared.time_strings):
            for a_idx, arc_id in enumerate(prepared.arc_ids):
                flow = _df_value(prepared.flow, t_idx, a_idx)
                if flow is not None:
                    if flow < 0:
                        reversed_arcs.add(arc_id)
                    flow = abs(flow)

                copy.write_row((
                    result_id,
                    arc_id,
                    time_str,
                    prepared.length.get(arc_id),
                    prepared.diameter.get(arc_id),
                    flow,
                    _df_value(prepared.velocity, t_idx, a_idx),
                    _df_value(prepared.headloss, t_idx, a_idx),
                    _df_value(prepared.setting, t_idx, a_idx),
                    _df_value(prepared.reaction, t_idx, a_idx),
                    _df_value(prepared.ffactor, t_idx, a_idx),
                    _status_to_str(_df_value(prepared.status, t_idx, a_idx)),
                ))
                count += 1
    return count, reversed_arcs


def _reverse_arc_geometries(dao: HePgDao, result_id: str, reversed_arcs: Set[str]) -> None:
    """Reverse arc geometries for arcs with negative flow."""
    if not reversed_arcs:
        return

    sql_reverse = """
        UPDATE rpt_inp_arc
        SET the_geom = ST_Reverse(the_geom)
        WHERE result_id = %s
          AND arc_id = ANY(%s)
    """
    dao.execute(sql_reverse, (result_id, list(reversed_arcs)), commit=False)


def _export_node_stats(
    dao: HePgDao,
    result_id: str,
    prepared: _PreparedNodeData,
    round_decimals: int = 4,
    giswater_version: int = 4,
) -> None:
    """Calculate node statistics and load them through a staging table."""
    if not dao.cursor:
        return

    metric_frames = {
        'demand': prepared.demand,
        'head': prepared.head,
        'press': prepared.press,
        'quality': prepared.quality,
    }
    metric_stats = {
        metric: _dataframe_metric_stats(df, round_decimals)
        for metric, df in metric_frames.items()
        if df is not None
    }

    elevation_column = 'elevation' if giswater_version == 3 else 'top_elev'
    dao.execute(
        f"""
        CREATE TEMP TABLE tmp_node_stats (
            node_id text,
            result_id text,
            {elevation_column} double precision,
            demand_max double precision,
            demand_min double precision,
            demand_avg double precision,
            t_demand_max text,
            t_demand_min text,
            head_max double precision,
            head_min double precision,
            head_avg double precision,
            t_head_max text,
            t_head_min text,
            press_max double precision,
            press_min double precision,
            press_avg double precision,
            t_press_max text,
            t_press_min text,
            quality_max double precision,
            quality_min double precision,
            quality_avg double precision,
            t_quality_max text,
            t_quality_min text
        ) ON COMMIT DROP
        """,
        commit=False,
    )

    copy_sql = f"""
        COPY tmp_node_stats (
            node_id, result_id, {elevation_column},
            demand_max, demand_min, demand_avg, t_demand_max, t_demand_min,
            head_max, head_min, head_avg, t_head_max, t_head_min,
            press_max, press_min, press_avg, t_press_max, t_press_min,
            quality_max, quality_min, quality_avg, t_quality_max, t_quality_min
        ) FROM STDIN
    """
    with dao.cursor.copy(copy_sql) as copy:
        for node_id in prepared.node_ids:
            head_stats = metric_stats.get('head', {}).get(node_id, {})
            demand_stats = metric_stats.get('demand', {}).get(node_id, {})
            press_stats = metric_stats.get('press', {}).get(node_id, {})
            quality_stats = metric_stats.get('quality', {}).get(node_id, {})

            copy.write_row((
                node_id,
                result_id,
                prepared.top_elev.get(node_id),
                demand_stats.get('max'),
                demand_stats.get('min'),
                demand_stats.get('avg'),
                demand_stats.get('tmax'),
                demand_stats.get('tmin'),
                head_stats.get('max'),
                head_stats.get('min'),
                head_stats.get('avg'),
                head_stats.get('tmax'),
                head_stats.get('tmin'),
                press_stats.get('max'),
                press_stats.get('min'),
                press_stats.get('avg'),
                press_stats.get('tmax'),
                press_stats.get('tmin'),
                quality_stats.get('max'),
                quality_stats.get('min'),
                quality_stats.get('avg'),
                quality_stats.get('tmax'),
                quality_stats.get('tmin'),
            ))

    insert_sql = f"""
        INSERT INTO rpt_node_stats (
            node_id, result_id, node_type, sector_id, nodecat_id, {elevation_column},
            demand_max, demand_min, demand_avg, t_demand_max, t_demand_min,
            head_max, head_min, head_avg, t_head_max, t_head_min,
            press_max, press_min, press_avg, t_press_max, t_press_min,
            quality_max, quality_min, quality_avg, t_quality_max, t_quality_min,
            the_geom
        )
        SELECT
            node.node_id, tmp.result_id, node.node_type, node.sector_id, node.nodecat_id, tmp.{elevation_column},
            tmp.demand_max, tmp.demand_min, tmp.demand_avg, tmp.t_demand_max, tmp.t_demand_min,
            tmp.head_max, tmp.head_min, tmp.head_avg, tmp.t_head_max, tmp.t_head_min,
            tmp.press_max, tmp.press_min, tmp.press_avg, tmp.t_press_max, tmp.t_press_min,
            tmp.quality_max, tmp.quality_min, tmp.quality_avg, tmp.t_quality_max, tmp.t_quality_min,
            node.the_geom
        FROM tmp_node_stats tmp
        JOIN rpt_inp_node node
          ON node.result_id = tmp.result_id
         AND node.node_id::text = tmp.node_id
        WHERE tmp.result_id = %s
    """
    dao.execute(insert_sql, (result_id,), commit=False)
    if dao.cursor.rowcount == 0:
        raise ExportError("No rows inserted into rpt_node_stats")


def _export_arc_stats(
    dao: HePgDao,
    result_id: str,
    prepared: _PreparedArcData,
    round_decimals: int = 4,
) -> None:
    """Calculate arc statistics and load them through a staging table."""
    if not dao.cursor:
        return

    metric_frames = {
        'flow': prepared.flow.abs() if prepared.flow is not None else None,
        'vel': prepared.velocity,
        'headloss': prepared.headloss,
        'setting': prepared.setting,
        'reaction': prepared.reaction,
        'ffactor': prepared.ffactor,
    }
    metric_stats = {
        metric: _dataframe_metric_stats(df, round_decimals)
        for metric, df in metric_frames.items()
        if df is not None
    }

    dao.execute(
        """
        CREATE TEMP TABLE tmp_arc_stats (
            arc_id text,
            result_id text,
            flow_max double precision,
            flow_min double precision,
            flow_avg double precision,
            t_flow_max text,
            t_flow_min text,
            vel_max double precision,
            vel_min double precision,
            vel_avg double precision,
            t_vel_max text,
            t_vel_min text,
            headloss_max double precision,
            headloss_min double precision,
            t_headloss_max text,
            t_headloss_min text,
            setting_max double precision,
            setting_min double precision,
            t_setting_max text,
            t_setting_min text,
            reaction_max double precision,
            reaction_min double precision,
            t_reaction_max text,
            t_reaction_min text,
            ffactor_max double precision,
            ffactor_min double precision,
            t_ffactor_max text,
            t_ffactor_min text,
            length double precision,
            tot_headloss_max double precision,
            tot_headloss_min double precision
        ) ON COMMIT DROP
        """,
        commit=False,
    )

    copy_sql = """
        COPY tmp_arc_stats (
            arc_id, result_id,
            flow_max, flow_min, flow_avg, t_flow_max, t_flow_min,
            vel_max, vel_min, vel_avg, t_vel_max, t_vel_min,
            headloss_max, headloss_min, t_headloss_max, t_headloss_min,
            setting_max, setting_min, t_setting_max, t_setting_min,
            reaction_max, reaction_min, t_reaction_max, t_reaction_min,
            ffactor_max, ffactor_min, t_ffactor_max, t_ffactor_min,
            length, tot_headloss_max, tot_headloss_min
        ) FROM STDIN
    """
    with dao.cursor.copy(copy_sql) as copy:
        for arc_id in prepared.arc_ids:
            flow_stats = metric_stats.get('flow', {}).get(arc_id, {})
            vel_stats = metric_stats.get('vel', {}).get(arc_id, {})
            headloss_stats = metric_stats.get('headloss', {}).get(arc_id, {})
            setting_stats = metric_stats.get('setting', {}).get(arc_id, {})
            reaction_stats = metric_stats.get('reaction', {}).get(arc_id, {})
            ffactor_stats = metric_stats.get('ffactor', {}).get(arc_id, {})
            arc_length = prepared.length.get(arc_id)
            headloss_max = headloss_stats.get('max')
            headloss_min = headloss_stats.get('min')
            tot_headloss_max = (
                None if headloss_max is None or arc_length is None
                else round(headloss_max * arc_length / 1000, round_decimals)
            )
            tot_headloss_min = (
                None if headloss_min is None or arc_length is None
                else round(headloss_min * arc_length / 1000, round_decimals)
            )

            copy.write_row((
                arc_id,
                result_id,
                flow_stats.get('max'),
                flow_stats.get('min'),
                flow_stats.get('avg'),
                flow_stats.get('tmax'),
                flow_stats.get('tmin'),
                vel_stats.get('max'),
                vel_stats.get('min'),
                vel_stats.get('avg'),
                vel_stats.get('tmax'),
                vel_stats.get('tmin'),
                headloss_stats.get('max'),
                headloss_stats.get('min'),
                headloss_stats.get('tmax'),
                headloss_stats.get('tmin'),
                setting_stats.get('max'),
                setting_stats.get('min'),
                setting_stats.get('tmax'),
                setting_stats.get('tmin'),
                reaction_stats.get('max'),
                reaction_stats.get('min'),
                reaction_stats.get('tmax'),
                reaction_stats.get('tmin'),
                ffactor_stats.get('max'),
                ffactor_stats.get('min'),
                ffactor_stats.get('tmax'),
                ffactor_stats.get('tmin'),
                arc_length,
                tot_headloss_max,
                tot_headloss_min,
            ))

    insert_sql = """
        INSERT INTO rpt_arc_stats (
            arc_id, result_id, arc_type, sector_id, arccat_id,
            flow_max, flow_min, flow_avg, t_flow_max, t_flow_min,
            vel_max, vel_min, vel_avg, t_vel_max, t_vel_min,
            headloss_max, headloss_min, t_headloss_max, t_headloss_min,
            setting_max, setting_min, t_setting_max, t_setting_min,
            reaction_max, reaction_min, t_reaction_max, t_reaction_min,
            ffactor_max, ffactor_min, t_ffactor_max, t_ffactor_min,
            length, tot_headloss_max, tot_headloss_min,
            the_geom
        )
        SELECT
            arc.arc_id, tmp.result_id, arc.arc_type, arc.sector_id, arc.arccat_id,
            tmp.flow_max, tmp.flow_min, tmp.flow_avg, tmp.t_flow_max, tmp.t_flow_min,
            tmp.vel_max, tmp.vel_min, tmp.vel_avg, tmp.t_vel_max, tmp.t_vel_min,
            tmp.headloss_max, tmp.headloss_min, tmp.t_headloss_max, tmp.t_headloss_min,
            tmp.setting_max, tmp.setting_min, tmp.t_setting_max, tmp.t_setting_min,
            tmp.reaction_max, tmp.reaction_min, tmp.t_reaction_max, tmp.t_reaction_min,
            tmp.ffactor_max, tmp.ffactor_min, tmp.t_ffactor_max, tmp.t_ffactor_min,
            tmp.length, tmp.tot_headloss_max, tmp.tot_headloss_min,
            arc.the_geom
        FROM tmp_arc_stats tmp
        JOIN rpt_inp_arc arc
          ON arc.result_id = tmp.result_id
         AND arc.arc_id::text = tmp.arc_id
        WHERE tmp.result_id = %s
    """
    dao.execute(insert_sql, (result_id,), commit=False)
    if dao.cursor.rowcount == 0:
        raise ExportError("No rows inserted into rpt_arc_stats")


def _clean_previous_results(dao: HePgDao, result_id: str, giswater_version: int = 4) -> None:
    """
    Clean previous results for the given result_id from all rpt tables.

    :param dao: Database access object
    :param result_id: Result identifier
    :raises DatabaseError: If a delete statement fails
    """
    tables_to_clean = [
        'rpt_node',
        'rpt_arc',
        'rpt_energy_usage',
        'rpt_hydraulic_status'
    ]
    if giswater_version > 3:
        tables_to_clean.append('rpt_node_stats')
        tables_to_clean.append('rpt_arc_stats')

    for table in tables_to_clean:
        sql = f"DELETE FROM {table} WHERE result_id = %s"
        try:
            dao.execute(sql, (result_id,), commit=False)
        except DatabaseError as e:
            raise DatabaseError(f"Failed to clean {table}") from e


def _finalize_import(dao: HePgDao, result_id: str, giswater_version: int = 4) -> None:
    """
    Finalize the import process:
    - Update rpt_cat_result with execution metadata
    - Set selector_rpt_main for current user
    - Clean up null time values

    :param dao: Database access object
    :param result_id: Result identifier
    """
    # Update rpt_cat_result
    if giswater_version == 3:
        sql_update_result = """
            UPDATE rpt_cat_result
            SET exec_date = now(), cur_user = current_user, status = 2,
            expl_id = (SELECT array_agg(expl_id) FROM selector_expl WHERE cur_user = current_user AND expl_id > 0)[1]
            WHERE result_id = %s
        """
    else:
        sql_update_result = """
            UPDATE rpt_cat_result
            SET exec_date = now(), cur_user = current_user, status = 2,
            expl_id = (SELECT array_agg(expl_id) FROM selector_expl WHERE cur_user = current_user AND expl_id > 0),
            sector_id = (SELECT array_agg(sector_id) FROM selector_sector WHERE cur_user = current_user AND sector_id > 0)
            WHERE result_id = %s
        """

    dao.execute(sql_update_result, (result_id,), commit=False)

    # Set result selector for current user
    sql_delete_selector = "DELETE FROM selector_rpt_main WHERE cur_user = current_user"
    dao.execute(sql_delete_selector, commit=False)

    sql_insert_selector = "INSERT INTO selector_rpt_main (result_id, cur_user) VALUES (%s, current_user)"
    dao.execute(sql_insert_selector, (result_id,), commit=False)

    # Clean null time values
    sql_clean_node_time = "UPDATE rpt_node SET time = '0:00' WHERE time = 'null' AND result_id = %s"
    dao.execute(sql_clean_node_time, (result_id,), commit=False)

    sql_clean_arc_time = "UPDATE rpt_arc SET time = '0:00' WHERE time = 'null' AND result_id = %s"
    dao.execute(sql_clean_arc_time, (result_id,), commit=False)


# endregion

# region Export to FROST-Server Helper Functions

def _prepare_nodes_data(wn: wntr.network.WaterNetworkModel) -> List[Dict]:
    """Extract node data from WNTR water network model."""
    nodes_data = []
    for node_name, node in wn.nodes():
        node_type = tools_sensorthings.get_epanet_node_type(node)

        # Get coordinates
        coords = node.coordinates
        if coords is None or len(coords) < 2:
            tools_log.log_warning(f"Node {node_name} has no coordinates, skipping")
            continue

        nodes_data.append({
            'id': node_name,
            'type': node_type,
            'coordinates': (coords[0], coords[1])
        })

    return nodes_data


def _prepare_nodes_things_data(
    nodes_data: List[Dict], wn: wntr.network.WaterNetworkModel, results: wntr.sim.SimulationResults, sensor_ids: Dict[str, str],
    property_ids: Dict[str, str], transformer: Transformer, start_time: datetime,
    inp_feature_ids: set
) -> List[Dict]:
    """Prepare Thing data for nodes (no HTTP calls)."""
    things_data = []
    export_errors: List[str] = []
    for node_data in nodes_data:
        node_id = node_data['id']
        node_type = node_data['type']
        coordinates = node_data['coordinates']

        # Track this feature ID
        inp_feature_ids.add(node_id)

        # Transform coordinates
        lon, lat = transformer.transform(coordinates[0], coordinates[1])
        location = {"type": "Point", "coordinates": [lon, lat]}

        # Create Datastreams with Observations for each property
        datastreams = []
        for prop in tools_sensorthings.EPANET_NODE_PROPERTIES:
            prop_config = tools_sensorthings.EPANET_OBSERVED_PROPERTIES[prop]
            try:
                if prop in results.node:
                    values = results.node[prop][node_id]
                else:
                    continue

                observations = []
                current_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

                for timestamp, value in values.items():
                    observations.append({
                        "phenomenonTime": (start_time + timedelta(seconds=timestamp)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "result": float(value),
                        "resultTime": current_time
                    })

                datastream = {
                    "name": f"{prop_config['name']} at {node_id}",
                    "description": f"The {prop_config['name'].lower()} at EPANET {node_type} {node_id}",
                    "unitOfMeasurement": prop_config['unit'],
                    "observationType": "http://www.opengis.net/def/observationType/OGC-OM/2.0/OM_Measurement",
                    "Sensor": {"@iot.id": sensor_ids['simulated']},
                    "ObservedProperty": {"@iot.id": property_ids[prop]},
                    "Observations": observations
                }
                datastreams.append(datastream)

            except Exception as e:
                msg = f"Could not process {prop} for {node_id}: {e}"
                tools_log.log_warning(msg)
                export_errors.append(msg)

        thing_data = {
            "name": node_id,
            "description": f"EPANET {node_type} {node_id}",
            "Locations": [{
                "name": f"{node_id} Location",
                "description": f"Location of EPANET {node_type} {node_id}",
                "encodingType": "application/geo+json",
                "location": location
            }],
            "Datastreams": datastreams,
            "properties": {
                "node_type": node_type
            }
        }
        things_data.append(thing_data)

    if export_errors:
        raise ExportError(
            "FROST export failed while preparing node Things: " + "; ".join(export_errors)
        )

    return things_data


def _prepare_links_data(wn: wntr.network.WaterNetworkModel) -> List[Dict]:
    """Extract link data from WNTR water network model."""
    links_data = []

    for link_name, link in wn.links():
        link_type = tools_sensorthings.get_epanet_link_type(link)

        links_data.append({
            'id': link_name,
            'type': link_type
        })

    return links_data


def _prepare_links_things_data(
    links_data: List[Dict], results: wntr.sim.SimulationResults, sensor_ids: Dict[str, str],
    property_ids: Dict[str, str], wn: wntr.network.WaterNetworkModel, transformer: Transformer, start_time: datetime,
    inp_feature_ids: set
) -> List[Dict]:
    """Prepare Thing data for links (no HTTP calls)."""
    things_data = []
    export_errors: List[str] = []
    for link_data in links_data:
        link_id = link_data['id']
        link_type = link_data['type']

        # Track this feature ID
        inp_feature_ids.add(link_id)

        link = wn.get_link(link_id)
        vertices = _get_geometry_from_link(link)

        transformed_vertices = []
        for x, y in vertices:
            lon, lat = transformer.transform(x, y)
            transformed_vertices.append([lon, lat])

        datastreams = []
        for prop in tools_sensorthings.EPANET_LINK_PROPERTIES:
            prop_config = tools_sensorthings.EPANET_OBSERVED_PROPERTIES[prop]
            try:
                if prop in results.link:
                    values = results.link[prop][link_id]
                else:
                    continue

                observations = []
                current_time = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

                for timestamp, value in values.items():
                    observations.append({
                        "phenomenonTime": (start_time + timedelta(seconds=timestamp)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                        "result": float(value),
                        "resultTime": current_time
                    })

                datastream = {
                    "name": f"{prop_config['name']} at {link_id}",
                    "description": f"The {prop_config['name'].lower()} at EPANET {link_type} {link_id}",
                    "unitOfMeasurement": prop_config['unit'],
                    "observationType": "http://www.opengis.net/def/observationType/OGC-OM/2.0/OM_Measurement",
                    "Sensor": {"@iot.id": sensor_ids['simulated']},
                    "ObservedProperty": {"@iot.id": property_ids[prop]},
                    "Observations": observations
                }
                datastreams.append(datastream)

            except Exception as e:
                msg = f"Could not process {prop} for {link_id}: {e}"
                tools_log.log_warning(msg)
                export_errors.append(msg)

        thing_data = {
            "name": link_id,
            "description": f"EPANET {link_type} {link_id}",
            "Locations": [{
                "name": f"{link_id} Location",
                "description": f"Location of EPANET {link_type} {link_id}",
                "encodingType": "application/geo+json",
                "location": {
                    "type": "LineString",
                    "coordinates": transformed_vertices
                }
            }],
            "Datastreams": datastreams,
            "properties": {
                "link_type": link_type
            }
        }
        things_data.append(thing_data)

    if export_errors:
        raise ExportError(
            "FROST export failed while preparing link Things: " + "; ".join(export_errors)
        )

    return things_data


def _get_geometry_from_link(link: wntr.network.Link) -> list[tuple[float, float]]:
    """Get geometry coordinates for a link including vertices."""
    start_node = link.start_node
    end_node = link.end_node

    # Check if nodes have coordinates
    if start_node.coordinates is None or len(start_node.coordinates) < 2:
        tools_log.log_warning(
            f"Link {link.name}: start node {start_node.name} has no coordinates"
        )
        return []
    if end_node.coordinates is None or len(end_node.coordinates) < 2:
        tools_log.log_warning(
            f"Link {link.name}: end node {end_node.name} has no coordinates"
        )
        return []

    start_coords = (start_node.coordinates[0], start_node.coordinates[1])
    end_coords = (end_node.coordinates[0], end_node.coordinates[1])

    # Get vertices if they exist (link has vertices property)
    vertices = []
    if hasattr(link, 'vertices') and link.vertices is not None:
        # WNTR stores vertices as a list of (x, y) tuples or lists
        for v in link.vertices:
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                vertices.append((float(v[0]), float(v[1])))

    # Combine start node, vertices, and end node
    coordinates = []
    coordinates.append(start_coords)
    coordinates.extend(vertices)
    coordinates.append(end_coords)

    return coordinates

# endregion
