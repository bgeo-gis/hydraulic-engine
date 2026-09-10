"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

SWMM export mapping and streaming helper tests.
"""
# -*- coding: utf-8 -*-
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from hydraulic_engine.exceptions import ModelNotLoadedError
from hydraulic_engine.swmm import out_handler as out_module
from hydraulic_engine.swmm import rpt_handler as rpt_module
from hydraulic_engine.swmm.export_db import (
    FLOW_ROUTING_FIELDS,
    RUNOFF_QUANTITY_FIELDS,
    SUMMARY_TOPICS,
    SummaryField,
    SummaryTopic,
    ReportElementSelection,
    build_continuity_row,
    build_summary_records,
    continuity_volume,
    copy_records,
    filter_labels_by_selection,
    integer_values,
    lookup_key,
    normalize_label,
    numeric_values,
    parse_report_kind,
    resolve_column,
    split_occurrence,
    to_float,
    truncate_text,
)
from hydraulic_engine.swmm.out_handler import (
    _TIMESERIES_TOPICS,
    _export_timeseries,
    _format_timestamps,
    _iter_timeseries_records,
    _variable_matrix,
)
from hydraulic_engine.swmm.rpt_handler import _diagnostic_lines, _export_summaries, _section
from hydraulic_engine.swmm.runner import SwmmRunner


# =============================================================================
# Test doubles
# =============================================================================

class FakeCursor:
    """Captures COPY statements the way psycopg exposes them."""

    def __init__(self, log):
        self.log = log

    @contextmanager
    def copy(self, sql):
        entry = {'sql': sql, 'rows': []}
        self.log.append(entry)
        yield SimpleNamespace(write_row=entry['rows'].append)


class FakeDao:
    """Minimal HePgDao stand-in that records everything it is asked to run."""

    def __init__(self, columns_by_table=None):
        self.columns_by_table = columns_by_table or {}
        self.copies = []
        self.statements = []
        self.cursor = FakeCursor(self.copies)
        self.commits = 0
        self.rollbacks = 0

    def is_connected(self):
        return True

    def get_rows(self, sql, params=None):
        candidates = params[0] if params else []
        return [
            (table, column)
            for table in candidates
            if table in self.columns_by_table
            for column in self.columns_by_table[table]
        ]

    def execute(self, sql, params=None, commit=False):
        self.statements.append((' '.join(sql.split()), params))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeOutput:
    """SwmmOutput stand-in backed by in-memory frames."""

    def __init__(self, index, data):
        self.index = index
        self._data = data
        self.labels = {kind: list(frames['labels']) for kind, frames in data.items()}
        self.variables = {kind: list(frames['variables']) for kind, frames in data.items()}

    def get_part(self, kind=None, variable=None, show_progress=True, **kwargs):
        frames = self._data[kind]['frames']
        if variable not in frames:
            return None
        return frames[variable]


# =============================================================================
# Column resolution
# =============================================================================

class TestNormalizeLabel:
    def test_strips_everything_but_digits_and_letters(self):
        assert normalize_label('Average_Depth_Meters') == 'averagedepthmeters'
        assert normalize_label('Maximum_|Flow|_LPS') == 'maximumflowlps'
        assert normalize_label('Hours Full_Both_Ends') == 'hoursfullbothends'

    def test_matches_across_report_spacing_variants(self):
        assert normalize_label('HoursFull_Both_Ends') == normalize_label('Hours Full_Both Ends')


class TestResolveColumn:
    def test_matches_exact_label(self):
        frame = pd.DataFrame({'Type': ['JUNCTION']}, index=['J1'])
        assert resolve_column(frame, ('Type',)) == 'Type'

    def test_matches_unit_suffixed_label_by_prefix(self):
        frame = pd.DataFrame({'Average_Depth_Meters': [1.0]}, index=['J1'])
        assert resolve_column(frame, ('Average_Depth',)) == 'Average_Depth_Meters'

    def test_honours_candidate_priority(self):
        frame = pd.DataFrame({'Maximum_Flow_LPS': [1.0]}, index=['C1'])
        assert resolve_column(frame, ('Maximum_|Flow|', 'Maximum_Flow')) == 'Maximum_Flow_LPS'

    def test_returns_none_for_unknown_and_empty(self):
        frame = pd.DataFrame({'Type': ['JUNCTION']}, index=['J1'])
        assert resolve_column(frame, ('Missing',)) is None
        assert resolve_column(None, ('Type',)) is None
        assert resolve_column(pd.DataFrame(), ('Type',)) is None

    def test_distinguishes_runoff_depth_from_volume_by_unit(self):
        frame = pd.DataFrame(
            {'Total_Runoff_mm': [12.9], 'Total_Runoff_10^6 ltr': [0.52]}, index=['S1']
        )
        assert resolve_column(frame, ('Total_Runoff_mm',)) == 'Total_Runoff_mm'
        assert resolve_column(frame, ('Total_Runoff_10',)) == 'Total_Runoff_10^6 ltr'


class TestLookupKey:
    def test_finds_key_ignoring_punctuation(self):
        mapping = {'Continuity Error (%)': -0.32}
        assert lookup_key(mapping, ('Continuity Error (%)',)) == -0.32
        # The unit marker is punctuation, so the bare label resolves too.
        assert lookup_key(mapping, ('Continuity Error',)) == -0.32
        assert lookup_key(mapping, ('Flooding Loss',)) is None

    def test_returns_none_for_missing_mapping(self):
        assert lookup_key(None, ('Anything',)) is None
        assert lookup_key({}, ('Anything',)) is None


# =============================================================================
# Value conversion
# =============================================================================

class TestToFloat:
    def test_parses_plain_and_comparator_values(self):
        assert to_float('12.5') == 12.5
        assert to_float('>50.00') == 50.0
        assert to_float(3) == 3.0

    def test_returns_none_for_missing_or_unparseable(self):
        assert to_float(None) is None
        assert to_float('') is None
        assert to_float('n/a') is None
        assert to_float(float('nan')) is None


class TestNumericValues:
    def test_coerces_capped_velocity_to_the_reportable_maximum(self):
        series = pd.Series(['1.25', '>50.00', ''], index=['C1', 'C2', 'C3'])
        assert list(numeric_values(series, 2)) == [1.25, 50.0, None]

    def test_coerces_capped_velocity_with_string_dtype(self):
        series = pd.Series(['1.25', '>50.00', ''], index=['C1', 'C2', 'C3'], dtype='string')
        assert list(numeric_values(series, 2)) == [1.25, 50.0, None]

    def test_rounds_and_maps_missing_to_none(self):
        series = pd.Series([1.234, np.nan, 5.678])
        assert list(numeric_values(series, 2)) == [1.23, None, 5.68]

    def test_keeps_full_precision_without_rounding(self):
        series = pd.Series([1.23456])
        assert list(numeric_values(series)) == [1.23456]


class TestIntegerValues:
    def test_narrows_counts_so_copy_does_not_send_a_decimal_point(self):
        values = integer_values(pd.Series([1.0, 4.0, np.nan]))
        assert list(values) == [1, 4, None]
        assert all(isinstance(value, int) for value in values if value is not None)


class TestTruncateText:
    def test_trims_to_column_width(self):
        assert truncate_text('  JUNCTION  ', 4) == 'JUNC'
        assert truncate_text('JUNCTION') == 'JUNCTION'

    def test_returns_none_for_blank_and_missing(self):
        assert truncate_text(None) is None
        assert truncate_text('   ') is None
        assert truncate_text(np.nan) is None


class TestSplitOccurrence:
    def test_splits_timedelta_into_days_and_hours(self):
        assert split_occurrence(pd.Timedelta(days=1, hours=2, minutes=3)) == ('1', '02:03')

    def test_splits_raw_report_text(self):
        assert split_occurrence('0  01:01') == ('0', '01:01')
        assert split_occurrence('01:01') == (None, '01:01')

    def test_returns_none_pair_for_missing(self):
        assert split_occurrence(None) == (None, None)
        assert split_occurrence(pd.NaT) == (None, None)


# =============================================================================
# Summary records
# =============================================================================

class TestBuildSummaryRecords:
    def test_shapes_rows_with_id_and_mapped_columns(self):
        frame = pd.DataFrame(
            {
                'Type': ['JUNCTION', 'OUTFALL'],
                'Average_Depth_Meters': [0.081, 0.052],
                'Maximum_Depth_Meters': [0.294, 0.267],
                'Maximum_HGL_Meters': [10.294, 9.267],
                'Time of Max_Occurrence_days hr:min': [
                    pd.Timedelta(hours=1, minutes=1),
                    pd.Timedelta(hours=1, minutes=3),
                ],
            },
            index=['J1', 'J2'],
        )
        topic = next(t for t in SUMMARY_TOPICS if t.table == 'rpt_nodedepth_sum')
        columns, records = build_summary_records(frame, 'r1', topic, 2)
        rows = list(records)

        assert columns == [
            'result_id', 'node_id', 'swnod_type', 'aver_depth',
            'max_depth', 'max_hgl', 'time_days', 'time_hour',
        ]
        assert rows[0] == ('r1', 'J1', 'JUNCTION', 0.08, 0.29, 10.29, '0', '01:01')
        assert rows[1] == ('r1', 'J2', 'OUTFALL', 0.05, 0.27, 9.27, '0', '01:03')

    def test_drops_columns_the_target_table_does_not_have(self):
        frame = pd.DataFrame({'Average_Depth_Meters': [0.5]}, index=['J1'])
        topic = next(t for t in SUMMARY_TOPICS if t.table == 'rpt_nodedepth_sum')
        columns, records = build_summary_records(
            frame, 'r1', topic, 2, allowed_columns={'node_id', 'aver_depth'}
        )

        assert columns == ['result_id', 'node_id', 'aver_depth']
        assert list(records) == [('r1', 'J1', 0.5)]

    def test_unmapped_source_yields_none_placeholder(self):
        frame = pd.DataFrame({'Type': ['JUNCTION']}, index=['J1'])
        topic = next(t for t in SUMMARY_TOPICS if t.table == 'rpt_nodedepth_sum')
        _columns, records = build_summary_records(frame, 'r1', topic, 2)
        assert list(records) == [('r1', 'J1', 'JUNCTION', None, None, None, None, None)]

    def test_sum_kind_adds_available_sources(self):
        frame = pd.DataFrame(
            {'Evap_Pcnt_Loss': [1.5], 'Exfil_Pcnt_Loss': [2.0]}, index=['ST1']
        )
        topic = SummaryTopic(
            attr='storage_volume_summary',
            table='rpt_storagevol_sum',
            id_column='node_id',
            id_length=50,
            fields=(SummaryField('ei_loss', ('Evap_Pcnt_Loss', 'Exfil_Pcnt_Loss'), 'sum'),),
        )
        _columns, records = build_summary_records(frame, 'r1', topic, 2)
        assert list(records) == [('r1', 'ST1', 3.5)]

    def test_truncates_element_ids_to_the_column_width(self):
        frame = pd.DataFrame({'Total_Precip_mm': [10.0]}, index=['S' * 20])
        topic = next(t for t in SUMMARY_TOPICS if t.table == 'rpt_subcatchrunoff_sum')
        _columns, records = build_summary_records(frame, 'r1', topic, 2)
        assert list(records)[0][1] == 'S' * topic.id_length


class TestSummaryTopicDefinitions:
    def test_pump_start_up_count_is_exported_as_an_integer(self):
        topic = next(t for t in SUMMARY_TOPICS if t.table == 'rpt_pumping_sum')
        field = next(f for f in topic.fields if f.column == 'num_startup')
        assert field.kind == 'integer'

    def test_every_topic_has_unique_target_columns(self):
        for topic in SUMMARY_TOPICS:
            columns = [field.column for field in topic.fields]
            assert len(columns) == len(set(columns)), topic.table


# =============================================================================
# Continuity
# =============================================================================

class TestContinuityVolume:
    def test_prefers_the_volume_column_over_the_depth_column(self):
        entry = {'Volume_hectare-m': 0.061, 'Depth_mm': 15.25}
        assert continuity_volume(entry) == 0.061

    def test_falls_back_to_the_first_numeric_value(self):
        assert continuity_volume({'Depth_mm': 15.25}) == 15.25
        assert continuity_volume(0.5) == 0.5
        assert continuity_volume(None) is None


class TestBuildContinuityRow:
    def test_maps_runoff_lines_and_error(self):
        continuity = {
            'Total Precipitation': {'Volume_hectare-m': 0.061, 'Depth_mm': 15.25},
            'Infiltration Loss': {'Volume_hectare-m': 0.009, 'Depth_mm': 2.322},
            'Continuity Error (%)': -0.325,
        }
        row = build_continuity_row(continuity, RUNOFF_QUANTITY_FIELDS, 2)

        assert row['total_prec'] == 0.06
        assert row['infil_loss'] == 0.01
        assert row['cont_error'] == -0.33
        # Snow lines are absent from the report when nothing melts.
        assert row['snow_re'] is None

    def test_maps_the_swmm_52_flooding_line_to_the_legacy_column(self):
        continuity = {
            'Flooding Loss': {'Volume_hectare-m': 3.5},
            'Continuity Error (%)': 1.2,
        }
        row = build_continuity_row(continuity, FLOW_ROUTING_FIELDS, 2)
        assert row['int_out'] == 3.5

    def test_adds_the_split_52_losses_back_into_one_column(self):
        continuity = {
            'Evaporation Loss': {'Volume_hectare-m': 0.4},
            'Exfiltration Loss': {'Volume_hectare-m': 0.25},
        }
        row = build_continuity_row(continuity, FLOW_ROUTING_FIELDS, 2)
        assert row['stor_loss'] == 0.65

    def test_uses_the_single_51_losses_line_when_present(self):
        continuity = {'Storage Losses': {'Volume_hectare-m': 0.7}}
        row = build_continuity_row(continuity, FLOW_ROUTING_FIELDS, 2)
        assert row['stor_loss'] == 0.7

    def test_returns_none_for_a_missing_section(self):
        assert build_continuity_row(None, FLOW_ROUTING_FIELDS, 2) is None
        assert build_continuity_row({}, FLOW_ROUTING_FIELDS, 2) is None


# =============================================================================
# Diagnostics
# =============================================================================

class TestDiagnosticLines:
    def test_renders_element_listings_with_the_value_in_brackets(self):
        lines = _diagnostic_lines({'J2': '1.79%', 'ST1': '1.51%'}, True)
        assert lines == ['J2 (1.79%)', 'ST1 (1.51%)']

    def test_renders_summary_lines_as_key_value(self):
        lines = _diagnostic_lines({'% of Steps Not Converging': 0.09}, False)
        assert lines == ['% of Steps Not Converging: 0.09']

    def test_prints_durations_in_seconds_rather_than_timedelta_repr(self):
        lines = _diagnostic_lines({'Minimum Time Step': pd.Timedelta(seconds=29.5)}, False)
        assert lines == ['Minimum Time Step: 29.50 sec']

    def test_handles_missing_and_list_sections(self):
        assert _diagnostic_lines(None, True) == []
        assert _diagnostic_lines(['  a  ', ''], True) == ['a']


class TestSectionAccess:
    def test_missing_section_is_none(self):
        assert _section(SimpleNamespace(), 'node_depth_summary') is None

    def test_unparseable_section_is_treated_as_missing(self):
        class Report:
            @property
            def node_depth_summary(self):
                raise ValueError('malformed block')

        assert _section(Report(), 'node_depth_summary') is None


class TestExportSummariesSkipsGaps:
    def test_skips_missing_sections_and_absent_tables(self):
        report = SimpleNamespace(
            node_depth_summary=pd.DataFrame(
                {'Average_Depth_Meters': [0.5]}, index=['J1']
            ),
            link_flow_summary=pd.DataFrame(),
        )
        dao = FakeDao()
        described = {'rpt_nodedepth_sum': {'node_id', 'aver_depth'}}

        total = _export_summaries(dao, report, 'r1', 2, described)

        assert total == 1
        assert len(dao.copies) == 1
        assert 'rpt_nodedepth_sum' in dao.copies[0]['sql']


# =============================================================================
# COPY streaming
# =============================================================================

class TestCopyRecords:
    def test_streams_rows_and_names_the_columns(self):
        dao = FakeDao()
        count = copy_records(dao, 'rpt_node', ['result_id', 'node_id'], [('r1', 'J1'), ('r1', 'J2')])

        assert count == 2
        assert dao.copies[0]['sql'] == 'COPY rpt_node (result_id, node_id) FROM STDIN'
        assert dao.copies[0]['rows'] == [('r1', 'J1'), ('r1', 'J2')]

    def test_writes_nothing_without_columns(self):
        dao = FakeDao()
        assert copy_records(dao, 'rpt_node', [], [('r1',)]) == 0
        assert dao.copies == []


# =============================================================================
# OUT time series
# =============================================================================

class TestFormatTimestamps:
    def test_builds_locale_independent_report_style_stamps(self):
        index = pd.to_datetime(['2024-01-01 00:15:00', '2024-03-09 13:05:30'])
        dates, times = _format_timestamps(index)

        assert dates == ['JAN-01-2024', 'MAR-09-2024']
        assert times == ['00:15:00', '13:05:30']


class TestVariableMatrix:
    def test_orders_columns_by_the_requested_labels(self):
        index = pd.to_datetime(['2024-01-01 00:15:00', '2024-01-01 00:30:00'])
        frame = pd.DataFrame([[1.0, 2.0], [3.0, 4.0]], index=index, columns=['J2', 'J1'])
        results = FakeOutput(
            index,
            {'node': {'labels': ['J1', 'J2'], 'variables': ['depth'], 'frames': {'depth': frame}}},
        )

        matrix = _variable_matrix(results, 'node', ['J1', 'J2'], 'depth')
        assert matrix.tolist() == [[2.0, 1.0], [4.0, 3.0]]

    def test_accepts_a_series_for_a_single_element_model(self):
        index = pd.to_datetime(['2024-01-01 00:15:00'])
        series = pd.Series([7.0], index=index)
        results = FakeOutput(
            index,
            {'node': {'labels': ['J1'], 'variables': ['depth'], 'frames': {'depth': series}}},
        )

        matrix = _variable_matrix(results, 'node', ['J1'], 'depth')
        assert matrix.tolist() == [[7.0]]

    def test_collapses_a_multiindex_down_to_the_labels(self):
        index = pd.to_datetime(['2024-01-01 00:15:00'])
        columns = pd.MultiIndex.from_tuples([('node', 'J1', 'depth'), ('node', 'J2', 'depth')])
        frame = pd.DataFrame([[1.0, 2.0]], index=index, columns=columns)
        results = FakeOutput(
            index,
            {'node': {'labels': ['J1', 'J2'], 'variables': ['depth'], 'frames': {'depth': frame}}},
        )

        matrix = _variable_matrix(results, 'node', ['J1', 'J2'], 'depth')
        assert matrix.tolist() == [[1.0, 2.0]]


class TestIterTimeseriesRecords:
    def test_emits_one_row_per_element_and_step(self):
        matrices = [np.array([[1.0, 2.0], [3.0, 4.0]]), np.array([[5.0, 6.0], [7.0, 8.0]])]
        rows = list(
            _iter_timeseries_records(
                'r1', ['J1', 'J2'], ['JAN-01-2024', 'JAN-01-2024'],
                ['00:15:00', '00:30:00'], matrices,
            )
        )

        assert len(rows) == 4
        assert rows[0] == ('r1', 'J1', 'JAN-01-2024', '00:15:00', 1.0, 5.0)
        assert rows[1] == ('r1', 'J2', 'JAN-01-2024', '00:15:00', 2.0, 6.0)
        assert rows[-1] == ('r1', 'J2', 'JAN-01-2024', '00:30:00', 4.0, 8.0)

    def test_maps_unreported_steps_to_null(self):
        matrices = [np.array([[np.nan, 2.0]])]
        rows = list(
            _iter_timeseries_records('r1', ['J1', 'J2'], ['JAN-01-2024'], ['00:15:00'], matrices)
        )

        assert rows[0][4] is None
        assert rows[1][4] == 2.0


class TestExportTimeseries:
    def _node_output(self):
        index = pd.to_datetime(['2024-01-01 00:15:00', '2024-01-01 00:30:00'])
        frames = {
            'depth': pd.DataFrame([[0.1, 0.2], [0.3, 0.4]], index=index, columns=['J1', 'J2']),
            'head': pd.DataFrame([[10.1, 9.2], [10.3, 9.4]], index=index, columns=['J1', 'J2']),
            'flooding': pd.DataFrame([[0.0, 0.0], [1.5, 0.0]], index=index, columns=['J1', 'J2']),
            'total_inflow': pd.DataFrame([[5.0, 6.0], [7.0, 8.0]], index=index, columns=['J1', 'J2']),
        }
        return FakeOutput(
            index,
            {
                'node': {
                    'labels': ['J1', 'J2'],
                    'variables': list(frames),
                    'frames': frames,
                }
            },
        )

    def test_writes_node_series_in_giswater_column_order(self):
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_node')
        dao = FakeDao()
        allowed = {'result_id', 'node_id', 'resultdate', 'resulttime',
                   'flooding', 'depth', 'head', 'inflow'}

        count = _export_timeseries(dao, self._node_output(), 'r1', topic, 2, allowed, 'ALL')

        assert count == 4
        assert dao.copies[0]['sql'] == (
            'COPY rpt_node (result_id, node_id, resultdate, resulttime, '
            'flooding, depth, head, inflow) FROM STDIN'
        )
        assert dao.copies[0]['rows'][0] == ('r1', 'J1', 'JAN-01-2024', '00:15:00', 0.0, 0.1, 10.1, 5.0)

    def test_omits_columns_missing_from_the_target_table(self):
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_node')
        dao = FakeDao()
        allowed = {'node_id', 'resultdate', 'resulttime', 'depth'}

        _export_timeseries(dao, self._node_output(), 'r1', topic, 2, allowed, 'ALL')

        assert dao.copies[0]['sql'] == (
            'COPY rpt_node (result_id, node_id, resultdate, resulttime, depth) FROM STDIN'
        )

    def test_scales_link_capacity_into_a_percentage(self):
        index = pd.to_datetime(['2024-01-01 00:15:00'])
        frames = {'capacity': pd.DataFrame([[0.62]], index=index, columns=['C1'])}
        results = FakeOutput(
            index,
            {'link': {'labels': ['C1'], 'variables': ['capacity'], 'frames': frames}},
        )
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_arc')
        dao = FakeDao()

        _export_timeseries(dao, results, 'r1', topic, 2, {'arc_id', 'resultdate', 'resulttime', 'fullpercent'}, 'ALL')

        assert dao.copies[0]['rows'][0][-1] == 62.0

    def test_adds_evaporation_and_infiltration_into_subcatchment_losses(self):
        index = pd.to_datetime(['2024-01-01 00:15:00'])
        frames = {
            'evaporation': pd.DataFrame([[0.5]], index=index, columns=['S1']),
            'infiltration': pd.DataFrame([[1.47]], index=index, columns=['S1']),
        }
        results = FakeOutput(
            index,
            {
                'subcatchment': {
                    'labels': ['S1'],
                    'variables': list(frames),
                    'frames': frames,
                }
            },
        )
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_subcatchment')
        dao = FakeDao()

        _export_timeseries(dao, results, 'r1', topic, 2, {'subc_id', 'resultdate', 'resulttime', 'losses'}, 'ALL')

        assert dao.copies[0]['rows'][0][-1] == 1.97

    def test_writes_nothing_when_the_kind_has_no_elements(self):
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_arc')
        results = FakeOutput(pd.to_datetime(['2024-01-01']), {})
        results.labels = {}
        results.variables = {}

        assert _export_timeseries(FakeDao(), results, 'r1', topic, 2, {'arc_id'}, 'ALL') == 0

    def test_id_list_keeps_only_requested_labels(self):
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_node')
        dao = FakeDao()
        allowed = {'node_id', 'resultdate', 'resulttime', 'depth'}

        count = _export_timeseries(
            dao, self._node_output(), 'r1', topic, 2, allowed, frozenset({'J2'})
        )

        assert count == 2  # one element x two steps
        assert all(row[1] == 'J2' for row in dao.copies[0]['rows'])

    def test_none_selection_writes_nothing(self):
        topic = next(t for t in _TIMESERIES_TOPICS if t.table == 'rpt_node')
        dao = FakeDao()

        assert _export_timeseries(
            dao, self._node_output(), 'r1', topic, 2, {'node_id', 'depth'}, None
        ) == 0
        assert dao.copies == []


# =============================================================================
# REPORT selection
# =============================================================================

class TestParseReportKind:
    def test_all_none_and_id_list(self):
        assert parse_report_kind('ALL') == 'ALL'
        assert parse_report_kind('all') == 'ALL'
        assert parse_report_kind(None) is None
        assert parse_report_kind('NONE') is None
        assert parse_report_kind(['J1', 'J2']) == frozenset({'J1', 'J2'})
        assert parse_report_kind('J1 J2') == frozenset({'J1', 'J2'})

    def test_empty_list_is_none(self):
        assert parse_report_kind([]) is None


class TestFilterLabelsBySelection:
    def test_all_keeps_order(self):
        assert filter_labels_by_selection(['J2', 'J1'], 'ALL') == ['J2', 'J1']

    def test_set_intersects_preserving_order(self):
        assert filter_labels_by_selection(['J1', 'J2', 'J3'], frozenset({'J3', 'J1'})) == [
            'J1', 'J3'
        ]

    def test_none_yields_empty(self):
        assert filter_labels_by_selection(['J1'], None) == []


class TestReportElementSelection:
    def test_has_any_and_for_kind(self):
        empty = ReportElementSelection()
        assert empty.has_any() is False
        assert empty.for_kind('node') is None

        selection = ReportElementSelection(nodes='ALL', links=frozenset({'C1'}))
        assert selection.has_any() is True
        assert selection.for_kind('node') == 'ALL'
        assert selection.for_kind('link') == frozenset({'C1'})
        assert selection.for_kind('subcatchment') is None

    def test_reads_report_section_from_inp(self, tmp_path):
        from hydraulic_engine.swmm.inp_handler import SwmmInpHandler

        inp_path = tmp_path / 'report.inp'
        inp_path.write_text(
            "[TITLE]\nT\n"
            "[OPTIONS]\nFLOW_UNITS LPS\n"
            "[REPORT]\nNODES J1 J2\nLINKS ALL\nSUBCATCHMENTS NONE\n",
            encoding='utf-8',
        )
        handler = SwmmInpHandler()
        handler.load_file(str(inp_path))

        selection = handler.get_report_element_selection()
        assert selection.nodes == frozenset({'J1', 'J2'})
        assert selection.links == 'ALL'
        assert selection.subcatchments is None
        assert selection.has_any() is True


# =============================================================================
# Runner orchestration
# =============================================================================

class TestRunnerReportSelection:
    def _runner(self, calls, report_selection=None):
        if report_selection is None:
            report_selection = ReportElementSelection(nodes='ALL', links='ALL')

        runner = SwmmRunner()
        runner.rpt = SimpleNamespace(
            is_loaded=lambda: True,
            export_to_database=lambda **kwargs: calls.append(('rpt', kwargs)) or True,
        )
        runner.out = SimpleNamespace(
            is_loaded=lambda: True,
            export_to_database=lambda **kwargs: calls.append(('out', kwargs)) or True,
        )
        runner.inp = SimpleNamespace(
            is_loaded=lambda: True,
            get_report_element_selection=lambda: report_selection,
        )
        return runner

    def test_report_none_skips_the_time_series_export(self):
        calls = []
        dao = FakeDao()
        selection = ReportElementSelection()

        assert self._runner(calls, selection)._export_to_database('r1', dao=dao) is True
        assert [name for name, _ in calls] == ['rpt']

    def test_report_active_runs_both_handlers_in_one_transaction(self):
        calls = []
        dao = FakeDao()
        selection = ReportElementSelection(nodes='ALL')

        assert self._runner(calls, selection)._export_to_database('r1', dao=dao) is True
        assert [name for name, _ in calls] == ['rpt', 'out']
        assert all(kwargs['commit'] is False for _name, kwargs in calls)
        assert calls[1][1]['report_selection'] is selection
        assert dao.commits == 1

    def test_report_none_does_not_require_a_loaded_out_file(self):
        calls = []
        runner = self._runner(calls, ReportElementSelection())
        runner.out = None

        assert runner._export_to_database('r1', dao=FakeDao()) is True

    def test_report_active_requires_a_loaded_out_file(self):
        runner = self._runner([], ReportElementSelection(links=frozenset({'C1'})))
        runner.out = None

        with pytest.raises(ModelNotLoadedError):
            runner._export_to_database('r1', dao=FakeDao())

    def test_missing_inp_is_rejected(self):
        runner = self._runner([])
        runner.inp = None

        with pytest.raises(ModelNotLoadedError):
            runner._export_to_database('r1', dao=FakeDao())

    def test_missing_report_is_rejected(self):
        runner = SwmmRunner()
        with pytest.raises(ModelNotLoadedError):
            runner._export_to_database('r1', dao=FakeDao())

    def test_failure_rolls_back_the_transaction(self):
        def boom(**kwargs):
            raise ValueError('bad section')

        runner = SwmmRunner()
        runner.rpt = SimpleNamespace(is_loaded=lambda: True, export_to_database=boom)
        runner.inp = SimpleNamespace(
            is_loaded=lambda: True,
            get_report_element_selection=lambda: ReportElementSelection(),
        )
        dao = FakeDao()

        with pytest.raises(Exception):
            runner._export_to_database('r1', dao=dao)

        assert dao.rollbacks == 1
        assert dao.commits == 0


# =============================================================================
# Handler entry points
# =============================================================================

class TestHandlerGuards:
    def test_rpt_export_requires_a_loaded_report(self):
        with pytest.raises(ModelNotLoadedError):
            rpt_module.SwmmRptHandler().export_to_database(result_id='r1')

    def test_out_export_requires_a_loaded_binary(self):
        with pytest.raises(ModelNotLoadedError):
            out_module.SwmmOutHandler().export_to_database(result_id='r1')
