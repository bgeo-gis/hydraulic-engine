"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

Shared building blocks for exporting SWMM results into the Giswater UD result tables.

The RPT handler writes the summary tables and the OUT handler writes the time series,
but both need the same column resolution, COPY streaming and transaction helpers, and
the runner needs the clean/finalize steps to run once around them.
"""
# -*- coding: utf-8 -*-
import re

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, Iterator, List, Literal, Optional, Sequence, Set, Tuple, Union

import numpy as np
import pandas as pd

from ..exceptions import DatabaseError
from ..utils.tools_db import HePgDao


# region Report element selection

# One [REPORT] kind: None skips the kind, 'ALL' keeps every OUT label, otherwise
# only the listed IDs are exported.
ReportKindSelection = Optional[Union[Literal['ALL'], FrozenSet[str]]]


@dataclass(frozen=True)
class ReportElementSelection:
    """
    Which OUT elements to write into the Giswater time-series tables.

    Built from the INP ``[REPORT]`` section. Defaults (absent / NONE) leave each
    kind as None so that kind is skipped.

    :param nodes: Selection for rpt_node
    :param links: Selection for rpt_arc
    :param subcatchments: Selection for rpt_subcatchment
    """
    nodes: ReportKindSelection = None
    links: ReportKindSelection = None
    subcatchments: ReportKindSelection = None

    def has_any(self) -> bool:
        """True when at least one kind should export time series."""
        return any(
            selection is not None
            for selection in (self.nodes, self.links, self.subcatchments)
        )

    def for_kind(self, kind: str) -> ReportKindSelection:
        """
        Map an OUT object kind to its REPORT selection.

        :param kind: ``node``, ``link`` or ``subcatchment``
        :return: Selection for that kind
        """
        if kind == 'node':
            return self.nodes
        if kind == 'link':
            return self.links
        if kind == 'subcatchment':
            return self.subcatchments
        return None


def parse_report_kind(value: Any) -> ReportKindSelection:
    """
    Normalize one ``[REPORT]`` NODES/LINKS/SUBCATCHMENTS value from swmm_api.

    swmm_api stores ``ALL`` as the string ``'ALL'``, ``NONE``/absent as ``None``,
    and an ID list as a list of strings.

    :param value: Raw section value
    :return: Normalized selection
    """
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text or text.upper() == 'NONE':
            return None
        if text.upper() == 'ALL':
            return 'ALL'
        return frozenset(text.split())

    if isinstance(value, (list, tuple, set, frozenset)):
        ids = {str(item).strip() for item in value if str(item).strip()}
        if not ids:
            return None
        upper = {item.upper() for item in ids}
        if upper == {'ALL'}:
            return 'ALL'
        if upper == {'NONE'}:
            return None
        return frozenset(ids)

    return None


def filter_labels_by_selection(
    labels: Sequence[str],
    selection: ReportKindSelection,
) -> List[str]:
    """
    Keep the OUT labels that the REPORT selection allows.

    :param labels: Element labels present in the OUT file
    :param selection: REPORT selection for this kind
    :return: Filtered labels in the original order, empty when the kind is skipped
    """
    if selection is None:
        return []
    if selection == 'ALL':
        return list(labels)
    allowed = selection
    return [label for label in labels if label in allowed]


# endregion


# region Table inventory

# Summary tables fed from the RPT report sections.
SUMMARY_TABLES: Tuple[str, ...] = (
    'rpt_nodedepth_sum',
    'rpt_nodeinflow_sum',
    'rpt_nodesurcharge_sum',
    'rpt_nodeflooding_sum',
    'rpt_storagevol_sum',
    'rpt_outfallflow_sum',
    'rpt_arcflow_sum',
    'rpt_flowclass_sum',
    'rpt_condsurcharge_sum',
    'rpt_pumping_sum',
    'rpt_subcatchrunoff_sum',
    'rpt_lidperformance_sum',
    'rpt_runoff_quant',
    'rpt_flowrouting_cont',
    'rpt_high_conterrors',
    'rpt_timestep_critelem',
    'rpt_high_flowinest_ind',
    'rpt_routing_timestep',
    'rpt_warning_summary',
    'rpt_control_actions_taken',
)

# Time-series tables fed from the OUT binary.
TIMESERIES_TABLES: Tuple[str, ...] = (
    'rpt_node',
    'rpt_arc',
    'rpt_subcatchment',
)

# endregion

# region Column resolution

_NON_ALPHANUMERIC = re.compile(r'[^0-9a-z]+')
_LEADING_COMPARATOR = r'^\s*[<>=]+\s*'


def normalize_label(label: Any) -> str:
    """
    Reduce a report column label to a comparable key.

    SWMM appends the active unit to most summary headers (``Average_Depth_Meters``,
    ``Maximum_|Flow|_LPS``) and swmm_api keeps the raw spacing of the report
    (``HoursFull_Both_Ends`` next to ``Hours Full_Upstream``). Dropping everything
    that is not a digit or a lowercase letter makes lookups survive both.

    :param label: Raw column label
    :return: Normalized key
    """
    return _NON_ALPHANUMERIC.sub('', str(label).lower())


def resolve_column(frame: Optional[pd.DataFrame], candidates: Sequence[str]) -> Optional[str]:
    """
    Find the first candidate present in a summary DataFrame.

    Each candidate is matched against the normalized column labels, first as an
    exact key and then as a prefix so that unit suffixes can be omitted.

    :param frame: Summary DataFrame, may be None
    :param candidates: Candidate labels in priority order
    :return: Matching column label or None
    """
    if frame is None or frame.empty:
        return None

    columns = list(frame.columns)
    exact = {normalize_label(column): column for column in columns}

    for candidate in candidates:
        key = normalize_label(candidate)
        if not key:
            continue
        if key in exact:
            return exact[key]
        for column in columns:
            if normalize_label(column).startswith(key):
                return column
    return None


def lookup_key(mapping: Optional[Dict[str, Any]], candidates: Sequence[str]) -> Optional[Any]:
    """
    Find the first candidate key in a continuity/diagnostic dictionary.

    :param mapping: Dictionary parsed by swmm_api, may be None
    :param candidates: Candidate keys in priority order
    :return: Matching value or None
    """
    if not mapping:
        return None

    normalized = {normalize_label(key): value for key, value in mapping.items()}
    for candidate in candidates:
        key = normalize_label(candidate)
        if key in normalized:
            return normalized[key]
    return None

# endregion

# region Value conversion


def to_float(value: Any) -> Optional[float]:
    """
    Convert a report value to float, tolerating unit suffixes and ``>`` markers.

    :param value: Raw value
    :return: Float value or None when not numeric
    """
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)):
        return None if pd.isna(value) else float(value)

    text = re.sub(_LEADING_COMPARATOR, '', str(value)).strip()
    match = re.match(r'[-+]?[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?', text)
    if not match:
        return None
    return float(match.group(0))


def truncate_text(value: Any, max_length: Optional[int] = None) -> Optional[str]:
    """
    Normalize a value to a trimmed string that fits the target column.

    :param value: Raw value
    :param max_length: Maximum column width, None for no limit
    :return: Trimmed string or None
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    if not text:
        return None
    if max_length is not None:
        text = text[:max_length]
    return text


def numeric_values(series: pd.Series, round_decimals: Optional[int] = None) -> np.ndarray:
    """
    Convert a summary column to an object array of floats and None.

    Values are coerced rather than parsed strictly: SWMM prints ``>50.00`` when a
    velocity exceeds the reportable maximum, and blank cells are common for
    element types a section does not apply to.

    :param series: Summary column
    :param round_decimals: Decimal places to round to, None to keep full precision
    :return: Object array with float values and None for missing data
    """
    values = series
    if pd.api.types.is_object_dtype(values) or pd.api.types.is_string_dtype(values):
        values = values.astype(str).str.replace(_LEADING_COMPARATOR, '', regex=True)

    numeric = pd.to_numeric(values, errors='coerce').to_numpy(dtype=np.float64)
    if round_decimals is not None:
        numeric = np.round(numeric, round_decimals)

    result = numeric.astype(object)
    result[np.isnan(numeric)] = None
    return result


def integer_values(series: pd.Series) -> np.ndarray:
    """
    Convert a summary column to an object array of ints and None.

    COPY sends values as text, so a float would reach an integer column as
    ``1.0`` and be rejected; counts such as the pump start-up total therefore
    have to be narrowed before streaming.

    :param series: Summary column
    :return: Object array with int values and None for missing data
    """
    numeric = pd.to_numeric(series, errors='coerce').to_numpy(dtype=np.float64)
    return np.array(
        [None if np.isnan(value) else int(round(value)) for value in numeric],
        dtype=object,
    )


def text_column_values(series: pd.Series, max_length: Optional[int] = None) -> np.ndarray:
    """
    Convert a summary column to an object array of trimmed strings and None.

    :param series: Summary column
    :param max_length: Maximum column width
    :return: Object array with string values and None for missing data
    """
    return np.array(
        [truncate_text(value, max_length) for value in series.to_numpy()],
        dtype=object,
    )


def split_occurrence(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Split a "time of maximum occurrence" value into Giswater's day/hour columns.

    swmm_api parses the report's ``days hr:min`` field into a Timedelta, but older
    versions and unparseable rows keep the raw text, so both are accepted.

    :param value: Timedelta or raw ``days hr:min`` text
    :return: Tuple of (time_days, time_hour), each None when unavailable
    """
    if value is None:
        return None, None

    if isinstance(value, str):
        parts = value.split()
        if not parts:
            return None, None
        if len(parts) >= 2:
            return parts[0], parts[1]
        return None, parts[0]

    try:
        if pd.isna(value):
            return None, None
    except (TypeError, ValueError):
        pass

    try:
        delta = pd.Timedelta(value)
    except (TypeError, ValueError):
        return None, None
    if pd.isna(delta):
        return None, None

    total_seconds = int(delta.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    return str(days), f"{hours:02d}:{minutes:02d}"

# endregion

# region Summary topic definitions


@dataclass(frozen=True)
class SummaryField:
    """
    One Giswater column fed from a swmm_api summary DataFrame.

    :param column: Target column in the Giswater table
    :param sources: Candidate report labels, in priority order
    :param kind: How to derive the value (numeric, integer, text, sum, time_days, time_hour)
    :param max_length: Maximum width for text columns
    """
    column: str
    sources: Tuple[str, ...] = ()
    kind: str = 'numeric'
    max_length: Optional[int] = None


@dataclass(frozen=True)
class SummaryTopic:
    """
    Mapping between a swmm_api report section and a Giswater summary table.

    :param attr: Attribute name on the swmm_api SwmmReport object
    :param table: Target Giswater table
    :param id_column: Column receiving the DataFrame index (element id)
    :param id_length: Maximum width of the id column
    :param fields: Column mappings
    """
    attr: str
    table: str
    id_column: str
    id_length: int
    fields: Tuple[SummaryField, ...]


_OCCURRENCE = ('Time of Max_Occurrence', 'Time of Max')

# Giswater column names follow the UD schema, not the report headers, so the maps
# below are the single place where the two vocabularies meet.
SUMMARY_TOPICS: Tuple[SummaryTopic, ...] = (
    SummaryTopic(
        attr='node_depth_summary',
        table='rpt_nodedepth_sum',
        id_column='node_id',
        id_length=50,
        fields=(
            SummaryField('swnod_type', ('Type',), 'text', 18),
            SummaryField('aver_depth', ('Average_Depth',)),
            SummaryField('max_depth', ('Maximum_Depth',)),
            SummaryField('max_hgl', ('Maximum_HGL',)),
            SummaryField('time_days', _OCCURRENCE, 'time_days', 10),
            SummaryField('time_hour', _OCCURRENCE, 'time_hour', 10),
        ),
    ),
    SummaryTopic(
        attr='node_inflow_summary',
        table='rpt_nodeinflow_sum',
        id_column='node_id',
        id_length=50,
        fields=(
            SummaryField('swnod_type', ('Type',), 'text', 18),
            SummaryField('max_latinf', ('Maximum_Lateral_Inflow',)),
            SummaryField('max_totinf', ('Maximum_Total_Inflow',)),
            SummaryField('time_days', _OCCURRENCE, 'time_days', 10),
            SummaryField('time_hour', _OCCURRENCE, 'time_hour', 10),
            SummaryField('latinf_vol', ('Lateral_Inflow_Volume',)),
            SummaryField('totinf_vol', ('Total_Inflow_Volume',)),
            SummaryField('flow_balance_error', ('Flow_Balance_Error',)),
        ),
    ),
    SummaryTopic(
        attr='node_surcharge_summary',
        table='rpt_nodesurcharge_sum',
        id_column='node_id',
        id_length=50,
        fields=(
            SummaryField('swnod_type', ('Type',), 'text', 18),
            SummaryField('hour_surch', ('Hours_Surcharged',)),
            SummaryField('max_height', ('Max. Height', 'Max_Height')),
            SummaryField('min_depth', ('Min. Depth', 'Min_Depth')),
        ),
    ),
    SummaryTopic(
        attr='node_flooding_summary',
        table='rpt_nodeflooding_sum',
        id_column='node_id',
        id_length=50,
        fields=(
            SummaryField('hour_flood', ('Hours_Flooded',)),
            SummaryField('max_rate', ('Maximum_Rate',)),
            SummaryField('time_days', _OCCURRENCE, 'time_days', 10),
            SummaryField('time_hour', _OCCURRENCE, 'time_hour', 10),
            SummaryField('tot_flood', ('Total_Flood_Volume',)),
            SummaryField('max_ponded', ('Maximum_Ponded',)),
        ),
    ),
    SummaryTopic(
        attr='storage_volume_summary',
        table='rpt_storagevol_sum',
        id_column='node_id',
        id_length=50,
        fields=(
            SummaryField('aver_vol', ('Average_Volume',)),
            SummaryField('avg_full', ('Avg_Pcnt_Full',)),
            # Giswater keeps a single evaporation/exfiltration loss column.
            SummaryField('ei_loss', ('Evap_Pcnt_Loss', 'Exfil_Pcnt_Loss'), 'sum'),
            SummaryField('max_vol', ('Maximum_Volume',)),
            SummaryField('max_full', ('Max_Pcnt_Full',)),
            SummaryField('time_days', _OCCURRENCE, 'time_days', 10),
            SummaryField('time_hour', _OCCURRENCE, 'time_hour', 10),
            SummaryField('max_out', ('Maximum_Outflow',)),
        ),
    ),
    SummaryTopic(
        attr='outfall_loading_summary',
        table='rpt_outfallflow_sum',
        id_column='node_id',
        id_length=50,
        fields=(
            SummaryField('flow_freq', ('Flow_Freq',)),
            SummaryField('avg_flow', ('Avg_Flow',)),
            SummaryField('max_flow', ('Max_Flow',)),
            SummaryField('total_vol', ('Total_Volume',)),
        ),
    ),
    SummaryTopic(
        attr='link_flow_summary',
        table='rpt_arcflow_sum',
        id_column='arc_id',
        id_length=50,
        fields=(
            SummaryField('arc_type', ('Type',), 'text', 18),
            SummaryField('max_flow', ('Maximum_|Flow|', 'Maximum_Flow')),
            SummaryField('time_days', _OCCURRENCE, 'time_days', 10),
            SummaryField('time_hour', _OCCURRENCE, 'time_hour', 10),
            SummaryField('max_veloc', ('Maximum_|Veloc|', 'Maximum_Veloc')),
            SummaryField('mfull_flow', ('Max/_Full_Flow',)),
            SummaryField('mfull_depth', ('Max/_Full_Depth',)),
        ),
    ),
    SummaryTopic(
        attr='flow_classification_summary',
        table='rpt_flowclass_sum',
        id_column='arc_id',
        id_length=50,
        fields=(
            SummaryField('length', ('Adjusted_/Actual_Length', 'Adjusted')),
            SummaryField('dry', ('Dry',)),
            SummaryField('up_dry', ('Up_Dry',)),
            SummaryField('down_dry', ('Down_Dry',)),
            SummaryField('sub_crit', ('Sub_Crit',)),
            # Giswater's legacy column names are positional; sub_crit_1 holds the
            # supercritical fraction, froud_numb the normal-flow-limited fraction
            # and flow_chang the inlet-controlled fraction.
            SummaryField('sub_crit_1', ('Sup_Crit',)),
            SummaryField('up_crit', ('Up_Crit',)),
            SummaryField('down_crit', ('Down_Crit',)),
            SummaryField('froud_numb', ('Norm_Ltd',)),
            SummaryField('flow_chang', ('Inlet_Ctrl',)),
        ),
    ),
    SummaryTopic(
        attr='conduit_surcharge_summary',
        table='rpt_condsurcharge_sum',
        id_column='arc_id',
        id_length=50,
        fields=(
            SummaryField('both_ends', ('HoursFull_Both_Ends',)),
            SummaryField('upstream', ('HoursFull_Upstream',)),
            SummaryField('dnstream', ('HoursFull_Dnstream',)),
            SummaryField('hour_nflow', ('Hours_Above Full_Normal Flow',)),
            SummaryField('hour_limit', ('Hours_Capacity_Limited',)),
        ),
    ),
    SummaryTopic(
        attr='pumping_summary',
        table='rpt_pumping_sum',
        id_column='arc_id',
        id_length=50,
        fields=(
            SummaryField('percent', ('Percent_Utilized',)),
            SummaryField('num_startup', ('Number of_Start-Ups',), 'integer'),
            SummaryField('min_flow', ('Min_Flow',)),
            SummaryField('avg_flow', ('Avg_Flow',)),
            SummaryField('max_flow', ('Max_Flow',)),
            SummaryField('vol_ltr', ('Total_Volume',)),
            SummaryField('powus_kwh', ('Power_Usage',)),
            SummaryField('timoff_min', ('% Time Off_Pump Curve_Low',)),
            SummaryField('timoff_max', ('% Time Off_Pump Curve_High',)),
        ),
    ),
    SummaryTopic(
        attr='subcatchment_runoff_summary',
        table='rpt_subcatchrunoff_sum',
        id_column='subc_id',
        id_length=16,
        fields=(
            SummaryField('tot_precip', ('Total_Precip',)),
            SummaryField('tot_runon', ('Total_Runon',)),
            SummaryField('tot_evap', ('Total_Evap',)),
            SummaryField('tot_infil', ('Total_Infil',)),
            # tot_runoff is the runoff depth, tot_runofl the runoff volume, so the
            # unit suffix is the only thing telling the two report columns apart.
            SummaryField('tot_runoff', ('Total_Runoff_mm', 'Total_Runoff_in')),
            SummaryField('tot_runofl', ('Total_Runoff_10', 'Total_Runoff_Mgal')),
            SummaryField('peak_runof', ('Peak_Runoff',)),
            SummaryField('runoff_coe', ('Runoff_Coeff',)),
        ),
    ),
    SummaryTopic(
        attr='lid_performance_summary',
        table='rpt_lidperformance_sum',
        id_column='subc_id',
        id_length=16,
        fields=(
            SummaryField('lidco_id', ('LID_Control',), 'text', 16),
            SummaryField('tot_inflow', ('Total_Inflow',)),
            SummaryField('evap_loss', ('Evap_Loss',)),
            SummaryField('infil_loss', ('Infil_Loss',)),
            SummaryField('surf_outf', ('Surface_Outflow',)),
            SummaryField('drain_outf', ('Drain_Outflow',)),
            SummaryField('init_stor', ('Initial_Storage',)),
            SummaryField('final_stor', ('Final_Storage',)),
            SummaryField('per_error', ('Continuity_Error',)),
        ),
    ),
)

# endregion

# region Continuity definitions

# Continuity sections are parsed by swmm_api into
# {line label: {volume column: value}} plus a scalar continuity error.


@dataclass(frozen=True)
class ContinuityField:
    """
    One Giswater column fed from a continuity section.

    :param column: Target column in the Giswater table
    :param sources: Candidate report line labels, in priority order
    :param combine: True to add up every matching line instead of taking the first
    """
    column: str
    sources: Tuple[str, ...]
    combine: bool = False


RUNOFF_QUANTITY_FIELDS: Tuple[ContinuityField, ...] = (
    ContinuityField('initsw_co', ('Initial Snow Cover',)),
    ContinuityField('initlid_sto', ('Initial LID Storage',)),
    ContinuityField('total_prec', ('Total Precipitation',)),
    ContinuityField('evap_loss', ('Evaporation Loss',)),
    ContinuityField('infil_loss', ('Infiltration Loss',)),
    ContinuityField('surf_runof', ('Surface Runoff',)),
    ContinuityField('snow_re', ('Snow Removal',)),
    ContinuityField('finalsw_co', ('Final Snow Cover',)),
    ContinuityField('finals_sto', ('Final Storage',)),
)

FLOW_ROUTING_FIELDS: Tuple[ContinuityField, ...] = (
    ContinuityField('dryw_inf', ('Dry Weather Inflow',)),
    ContinuityField('wetw_inf', ('Wet Weather Inflow',)),
    ContinuityField('ground_inf', ('Groundwater Inflow',)),
    ContinuityField('rdii_inf', ('RDII Inflow',)),
    ContinuityField('ext_inf', ('External Inflow',)),
    ContinuityField('ext_out', ('External Outflow',)),
    # SWMM 5.1 reported one "Internal Outflow" line, 5.2 renamed it to
    # "Flooding Loss"; Giswater kept the 5.1 column name.
    ContinuityField('int_out', ('Internal Outflow', 'Flooding Loss')),
    # 5.1 lumped both losses into "Storage Losses", so the split 5.2 lines are
    # added back together to keep the column comparable across versions.
    ContinuityField(
        'stor_loss', ('Storage Losses', 'Evaporation Loss', 'Exfiltration Loss'), True
    ),
    ContinuityField('initst_vol', ('Initial Stored Volume',)),
    ContinuityField('finst_vol', ('Final Stored Volume',)),
)

CONTINUITY_ERROR_KEYS: Tuple[str, ...] = ('Continuity Error (%)', 'Continuity Error')


def continuity_volume(entry: Any) -> Optional[float]:
    """
    Read the volume value of a continuity line.

    Each line carries the same quantity in two unit systems; the first volume
    column is used so the stored value matches the report's primary volume unit.

    :param entry: Continuity line value, a dict of unit columns or a scalar
    :return: Volume as float or None
    """
    if entry is None:
        return None
    if not isinstance(entry, dict):
        return to_float(entry)

    for key, value in entry.items():
        if normalize_label(key).startswith('volume'):
            return to_float(value)
    for value in entry.values():
        number = to_float(value)
        if number is not None:
            return number
    return None


def _continuity_value(continuity: Dict[str, Any], field: ContinuityField) -> Optional[float]:
    """
    Read the value of one mapped continuity column.

    :param continuity: Continuity dictionary parsed by swmm_api
    :param field: Column mapping
    :return: Value or None when no source line is present
    """
    if not field.combine:
        return continuity_volume(lookup_key(continuity, field.sources))

    total: Optional[float] = None
    for candidate in field.sources:
        value = continuity_volume(lookup_key(continuity, (candidate,)))
        if value is not None:
            total = value if total is None else total + value
    return total


def build_continuity_row(
    continuity: Optional[Dict[str, Any]],
    fields: Sequence[ContinuityField],
    round_decimals: int,
) -> Optional[Dict[str, Optional[float]]]:
    """
    Map a continuity section to Giswater columns.

    :param continuity: Continuity dictionary parsed by swmm_api
    :param fields: Column to report-label mapping
    :param round_decimals: Decimal places to round to
    :return: Column/value mapping, or None when the section is absent
    """
    if not continuity:
        return None

    row: Dict[str, Optional[float]] = {}
    for field in fields:
        value = _continuity_value(continuity, field)
        row[field.column] = None if value is None else round(value, round_decimals)

    error = to_float(lookup_key(continuity, CONTINUITY_ERROR_KEYS))
    row['cont_error'] = None if error is None else round(error, round_decimals)
    return row

# endregion

# region Database helpers


def describe_tables(dao: HePgDao, tables: Sequence[str]) -> Dict[str, Set[str]]:
    """
    Look up which of the given tables exist and which columns they expose.

    The UD result schema changed across Giswater releases, so the exporter adapts
    to the target database instead of assuming a fixed set of columns.

    :param dao: Database access object
    :param tables: Candidate table names
    :return: Mapping of existing table name to its column names
    """
    sql = """
        SELECT cls.relname, att.attname
        FROM unnest(%s::text[]) AS candidate
        JOIN pg_class cls ON cls.oid = to_regclass(candidate)
        JOIN pg_attribute att ON att.attrelid = cls.oid
        WHERE att.attnum > 0 AND NOT att.attisdropped
    """
    rows = dao.get_rows(sql, (list(tables),))

    described: Dict[str, Set[str]] = {}
    for table, column in rows or []:
        described.setdefault(table, set()).add(column)
    return described


def clean_result(
    dao: HePgDao,
    result_id: str,
    tables: Sequence[str],
    described: Optional[Dict[str, Set[str]]] = None,
) -> None:
    """
    Remove previous rows for a result id from the given tables.

    :param dao: Database access object
    :param result_id: Result identifier
    :param tables: Tables to clean, missing ones are skipped
    :param described: Pre-fetched table inventory, looked up when not provided
    :raises DatabaseError: If a delete statement fails
    """
    available = describe_tables(dao, tables) if described is None else described
    for table in tables:
        if table not in available:
            continue
        try:
            dao.execute(f"DELETE FROM {table} WHERE result_id = %s", (result_id,), commit=False)
        except DatabaseError as e:
            raise DatabaseError(f"Failed to clean {table}") from e


def copy_records(
    dao: HePgDao,
    table: str,
    columns: Sequence[str],
    records: Iterable[Tuple[Any, ...]],
) -> int:
    """
    Stream rows into a table using PostgreSQL COPY.

    :param dao: Database access object
    :param table: Target table
    :param columns: Target columns, matching the record layout
    :param records: Row iterator
    :return: Number of rows written
    """
    if not dao.cursor or not columns:
        return 0

    copy_sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    count = 0
    with dao.cursor.copy(copy_sql) as copy:
        for record in records:
            copy.write_row(record)
            count += 1
    return count


def insert_row(dao: HePgDao, table: str, row: Dict[str, Any]) -> None:
    """
    Insert a single row built from a column/value mapping.

    :param dao: Database access object
    :param table: Target table
    :param row: Column/value mapping
    """
    if not row:
        return

    columns = list(row.keys())
    placeholders = ', '.join(['%s'] * len(columns))
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    dao.execute(sql, tuple(row[column] for column in columns), commit=False)


def finalize_result(dao: HePgDao, result_id: str) -> None:
    """
    Mark the result as imported and make it the active one for the current user.

    :param dao: Database access object
    :param result_id: Result identifier
    """
    dao.execute(
        """
        UPDATE rpt_cat_result
        SET exec_date = now(), cur_user = current_user, status = 2,
            expl_id = (SELECT array_agg(expl_id) FROM selector_expl
                       WHERE cur_user = current_user AND expl_id > 0),
            sector_id = (SELECT array_agg(sector_id) FROM selector_sector
                         WHERE cur_user = current_user AND sector_id > 0)
        WHERE result_id = %s
        """,
        (result_id,),
        commit=False,
    )

    dao.execute(
        "DELETE FROM selector_rpt_main WHERE cur_user = current_user",
        commit=False,
    )
    dao.execute(
        "INSERT INTO selector_rpt_main (result_id, cur_user) VALUES (%s, current_user)",
        (result_id,),
        commit=False,
    )

# endregion

# region Summary record building


def _field_values(
    frame: pd.DataFrame,
    field: SummaryField,
    round_decimals: int,
) -> np.ndarray:
    """
    Resolve one mapped column into an aligned object array.

    :param frame: Summary DataFrame
    :param field: Column mapping
    :param round_decimals: Decimal places to round numeric values to
    :return: Object array aligned with the DataFrame rows
    """
    empty = np.full(len(frame), None, dtype=object)

    if field.kind == 'sum':
        total: Optional[np.ndarray] = None
        for candidate in field.sources:
            column = resolve_column(frame, (candidate,))
            if column is None:
                continue
            values = pd.to_numeric(frame[column], errors='coerce').to_numpy(dtype=np.float64)
            total = values if total is None else np.nansum([total, values], axis=0)
        if total is None:
            return empty
        rounded = np.round(total, round_decimals)
        result = rounded.astype(object)
        result[np.isnan(rounded)] = None
        return result

    column = resolve_column(frame, field.sources)
    if column is None:
        return empty

    if field.kind == 'text':
        return text_column_values(frame[column], field.max_length)

    if field.kind == 'integer':
        return integer_values(frame[column])

    if field.kind in ('time_days', 'time_hour'):
        index = 0 if field.kind == 'time_days' else 1
        return np.array(
            [split_occurrence(value)[index] for value in frame[column].to_numpy()],
            dtype=object,
        )

    return numeric_values(frame[column], round_decimals)


def build_summary_records(
    frame: pd.DataFrame,
    result_id: str,
    topic: SummaryTopic,
    round_decimals: int,
    allowed_columns: Optional[Set[str]] = None,
) -> Tuple[List[str], Iterator[Tuple[Any, ...]]]:
    """
    Turn a summary DataFrame into COPY-ready columns and rows.

    :param frame: Summary DataFrame from swmm_api
    :param result_id: Result identifier
    :param topic: Mapping definition for this section
    :param round_decimals: Decimal places to round numeric values to
    :param allowed_columns: Columns present in the target table, None to accept all
    :return: Tuple of (column names, row iterator)
    """
    fields = [
        field for field in topic.fields
        if allowed_columns is None or field.column in allowed_columns
    ]
    columns = ['result_id', topic.id_column] + [field.column for field in fields]
    ids = [truncate_text(value, topic.id_length) for value in frame.index.to_numpy()]
    arrays = [_field_values(frame, field, round_decimals) for field in fields]

    def records() -> Iterator[Tuple[Any, ...]]:
        for row_index, element_id in enumerate(ids):
            yield (result_id, element_id) + tuple(array[row_index] for array in arrays)

    return columns, records()

# endregion
