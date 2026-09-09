"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.
"""
# -*- coding: utf-8 -*-
import os
from typing import Any, Dict, List, Optional

from swmm_api.input_file.section_labels import REPORT
from swmm_api.input_file.sections import Control

from .export_db import ReportElementSelection, parse_report_kind
from .file_handler import SwmmFileHandler
from .models import SwmmFeatureSettings, SwmmOptionsSettings, SwmmOtherSettings
from ..utils import tools_log
from ..exceptions import FileWriteError, ModelNotLoadedError, ValidationError


def _require_inp_loaded(handler: "SwmmInpHandler") -> Any:
    if handler.file_object is None:
        raise ModelNotLoadedError("No INP file loaded")
    return handler.file_object


def _get_section_dict(handler: "SwmmInpHandler", section_name: str) -> Dict[str, Any]:
    inp = _require_inp_loaded(handler)
    if not hasattr(inp, section_name):
        return {}
    section = getattr(inp, section_name)
    return dict(section) if section else {}


class SwmmInpHandler(SwmmFileHandler):
    """
    Handler for SWMM INP files.
    
    Provides functionality to read, parse, and write SWMM INP files
    using the swmm-api library.
    
    Example usage:
        handler = SwmmInpHandler()
        handler.read("model.inp")
        
        # Get sections
        junctions = handler.get_junctions()
        conduits = handler.get_conduits()
        
        # Modify and save
        handler.write("modified_model.inp")
    """

    def write(self, output_path: Optional[str] = None) -> bool:
        """
        Write INP file to disk.
        
        :param output_path: Output path (uses original path if not provided)
        :return: True if successful
        """
        if self.file_object is None:
            self.error_msg = "No INP file loaded"
            raise FileWriteError(self.error_msg)

        try:
            path = output_path or self.file_path
            self.file_object.write_file(path)
            tools_log.log_info(f"Successfully wrote INP file: {path}")
            return True

        except Exception as e:
            self.error_msg = str(e)
            tools_log.log_error(f"Error writing INP file: {e}")
            raise FileWriteError(f"Error writing INP file '{output_path or self.file_path}': {e}") from e

    def validate_inp(self) -> Dict[str, Any]:
        """
        Validate an INP file without running full simulation.
        
        Uses swmm-api for parsing validation.
        
        :return: Validation result dictionary
        """
        validation = {
            "valid": False,
            "errors": [],
            "warnings": [],
            "info": {}
        }

        if not os.path.isfile(self.file_path):
            validation["errors"].append(f"File not found: {self.file_path}")
            return validation

        try:
            inp = self.file_object

            # Get basic info
            validation["info"]["title"] = getattr(inp.TITLE, 'title', 'N/A') if hasattr(inp, 'TITLE') else 'N/A'
            validation["info"]["junctions"] = len(inp.JUNCTIONS) if hasattr(inp, 'JUNCTIONS') and inp.JUNCTIONS else 0
            validation["info"]["conduits"] = len(inp.CONDUITS) if hasattr(inp, 'CONDUITS') and inp.CONDUITS else 0
            validation["info"]["outfalls"] = len(inp.OUTFALLS) if hasattr(inp, 'OUTFALLS') and inp.OUTFALLS else 0
            validation["info"]["subcatchments"] = len(inp.SUBCATCHMENTS) if hasattr(inp, 'SUBCATCHMENTS') and inp.SUBCATCHMENTS else 0
            validation["info"]["storage"] = len(inp.STORAGE) if hasattr(inp, 'STORAGE') and inp.STORAGE else 0
            validation["info"]["pumps"] = len(inp.PUMPS) if hasattr(inp, 'PUMPS') and inp.PUMPS else 0
            validation["info"]["orifices"] = len(inp.ORIFICES) if hasattr(inp, 'ORIFICES') and inp.ORIFICES else 0
            validation["info"]["weirs"] = len(inp.WEIRS) if hasattr(inp, 'WEIRS') and inp.WEIRS else 0

            validation["valid"] = True
            tools_log.log_info(f"INP validation successful: {self.file_path}")

        except Exception as e:
            tools_log.log_error(f"INP validation failed: {e}")
            raise ValidationError(f"INP validation failed for '{self.file_path}': {e}") from e

        return validation

    # =========================================================================
    # Update INP file from settings
    # =========================================================================

    def update_inp_from_settings(
        self,
        feature_settings: Optional[SwmmFeatureSettings] = None,
        options_settings: Optional[SwmmOptionsSettings] = None,
        other_settings: Optional[SwmmOtherSettings] = None,
    ) -> None:
        """
        Update INP file with provided settings.
        Only updates fields that are not None.
        
        :param feature_settings: Feature settings to update
        :param options_settings: Options settings to update
        :param other_settings: Other settings to update
        """

        _require_inp_loaded(self)
        validation_errors: list[str] = []

        if feature_settings:
            self._update_features(feature_settings, validation_errors)

        if options_settings:
            self._update_options(options_settings, validation_errors)

        if other_settings:
            self._update_other_settings(other_settings, validation_errors)

        if validation_errors:
            raise ValidationError(
                "INP update failed: " + "; ".join(validation_errors)
            )

    def _update_features(
        self,
        feature_settings: SwmmFeatureSettings,
        validation_errors: list[str],
    ) -> None:
        """Update INP features from feature settings."""
        # Iterate through all feature setting attributes
        for section_name in dir(feature_settings):
            if section_name.startswith('_'):
                continue

            features_dict = getattr(feature_settings, section_name, None)
            if features_dict is None:
                continue

            section_name = section_name.upper()

            if not hasattr(self.file_object, section_name):
                msg = f"Section {section_name} not in INP"
                tools_log.log_warning(msg)
                validation_errors.append(msg)
                continue

            inp_section = getattr(self.file_object, section_name)
            if inp_section is None:
                continue

            for feature_name, feature_obj in features_dict.items():
                if feature_name in inp_section:
                    self._update_object_attributes(inp_section[feature_name], feature_obj)

                    # Handle cross-section updates for link objects
                    # Cross-sections are stored in XSECTIONS but accessed via link.cross_section
                    if hasattr(feature_obj, 'cross_section') and feature_obj.cross_section is not None:
                        self._update_cross_section(feature_name, feature_obj.cross_section)

    def _update_options(
        self,
        options_settings: SwmmOptionsSettings,
        validation_errors: list[str],
    ) -> None:
        """Update INP options from options settings."""
        if not hasattr(self.file_object, 'OPTIONS'):
            validation_errors.append("Section OPTIONS not in INP")
            return

        options = self.file_object.OPTIONS

        # Update all option attributes that are not None
        for attr_name in dir(options_settings):
            if attr_name.startswith('_'):
                continue

            value = getattr(options_settings, attr_name, None)
            if value is None:
                continue

            # Convert enum to value if needed
            if hasattr(value, 'value'):
                value = value.value

            # Convert attribute name to uppercase (e.g., 'flow_units' -> 'FLOW_UNITS')
            option_name = attr_name.upper()

            if option_name in options:
                options[option_name] = value

    def _update_other_settings(
        self,
        other_settings: SwmmOtherSettings,
        validation_errors: list[str],
    ) -> None:
        """Update INP other settings (curves, timeseries, patterns, controls)."""
        # Iterate through all other setting attributes
        for attr_name in dir(other_settings):
            if attr_name.startswith('_'):
                continue

            setting_dict = getattr(other_settings, attr_name, None)
            if setting_dict is None:
                continue

            if attr_name == 'controls':
                self._update_controls(setting_dict, validation_errors)
                continue

            # Convert attribute name to uppercase section name (e.g., 'curves' -> 'CURVES')
            section_name = attr_name.upper()

            if not hasattr(self.file_object, section_name):
                msg = f"Section {section_name} not in INP"
                tools_log.log_warning(msg)
                validation_errors.append(msg)
                continue

            inp_section = getattr(self.file_object, section_name)
            if inp_section is None:
                continue

            for item_name, item_obj in setting_dict.items():
                if item_name in inp_section:
                    self._update_object_attributes(inp_section[item_name], item_obj)

    def _update_controls(
        self,
        controls: dict,
        validation_errors: list[str],
    ) -> None:
        """Create or replace SWMM [CONTROLS] entries from INP text."""
        inp = self.file_object

        if not hasattr(inp, 'CONTROLS') or getattr(inp, 'CONTROLS', None) is None:
            inp['CONTROLS'] = Control.create_section()

        controls_section = inp.CONTROLS

        for name, model_obj in controls.items():
            text = getattr(model_obj, 'text', None)
            if text is None or not str(text).strip():
                msg = f"Control '{name}' text is empty"
                tools_log.log_warning(msg)
                validation_errors.append(msg)
                continue

            try:
                parsed = Control.create_section(str(text).strip())
                if not parsed:
                    raise ValueError("no control parsed from text")
                if len(parsed) != 1:
                    raise ValueError(
                        f"expected one CONTROLS entry, got {len(parsed)}"
                    )
                obj = next(iter(parsed.values()))
                obj_name = getattr(obj, 'name', None)
                if obj_name != name:
                    raise ValidationError(
                        f"Control dict key '{name}' does not match "
                        f"name '{obj_name}' in text"
                    )
                if name in controls_section:
                    del controls_section[name]
                controls_section.add_obj(obj)
            except ValidationError as e:
                tools_log.log_warning(str(e))
                validation_errors.append(str(e))
            except Exception as e:
                msg = f"Failed to set control '{name}': {e}"
                tools_log.log_warning(msg)
                validation_errors.append(msg)

    def _update_object_attributes(self, target_obj, source_obj) -> None:
        """
        Update target object attributes from source object.
        Only updates attributes that are not None in source object.
        
        :param target_obj: Object to update (from swmm-api)
        :param source_obj: Object with new values (from models)
        """
        for attr_name in dir(source_obj):
            if attr_name.startswith('_'):
                continue

            value = getattr(source_obj, attr_name, None)
            if value is not None:
                # Convert enum to value if needed
                if hasattr(value, 'value'):
                    value = value.value

                # Since field names match swmm-api exactly, use them directly
                if hasattr(target_obj, attr_name):
                    setattr(target_obj, attr_name, value)

    def _update_cross_section(self, link_name: str, cross_section_obj) -> None:
        """
        Update cross-section in XSECTIONS section for a given link.
        
        :param link_name: Name of the link (conduit, pump, etc.)
        :param cross_section_obj: SwmmCrossSection object with new values
        """
        # Check if XSECTIONS section exists
        if not hasattr(self.file_object, 'XSECTIONS'):
            return

        xsections = self.file_object.XSECTIONS
        if xsections is None:
            return

        # Check if cross-section exists for this link
        if link_name in xsections:
            self._update_object_attributes(xsections[link_name], cross_section_obj)

    # =========================================================================
    # Section Getters
    # =========================================================================

    def get_title(self) -> Optional[str]:
        """Get model title."""
        inp = _require_inp_loaded(self)
        return getattr(inp.TITLE, 'title', None) if hasattr(inp, 'TITLE') else None

    def get_options(self) -> Dict[str, Any]:
        """Get OPTIONS section as dictionary."""
        inp = _require_inp_loaded(self)
        if not hasattr(inp, 'OPTIONS'):
            return {}
        return {k: v for k, v in vars(inp.OPTIONS).items() if not k.startswith('_')}

    def get_junctions(self) -> Dict[str, Any]:
        """Get JUNCTIONS section."""
        return _get_section_dict(self, 'JUNCTIONS')

    def get_outfalls(self) -> Dict[str, Any]:
        """Get OUTFALLS section."""
        return _get_section_dict(self, 'OUTFALLS')

    def get_storage(self) -> Dict[str, Any]:
        """Get STORAGE section."""
        return _get_section_dict(self, 'STORAGE')

    def get_dividers(self) -> Dict[str, Any]:
        """Get DIVIDERS section."""
        return _get_section_dict(self, 'DIVIDERS')

    def get_conduits(self) -> Dict[str, Any]:
        """Get CONDUITS section."""
        return _get_section_dict(self, 'CONDUITS')

    def get_pumps(self) -> Dict[str, Any]:
        """Get PUMPS section."""
        return _get_section_dict(self, 'PUMPS')

    def get_orifices(self) -> Dict[str, Any]:
        """Get ORIFICES section."""
        return _get_section_dict(self, 'ORIFICES')

    def get_weirs(self) -> Dict[str, Any]:
        """Get WEIRS section."""
        return _get_section_dict(self, 'WEIRS')

    def get_outlets(self) -> Dict[str, Any]:
        """Get OUTLETS section."""
        return _get_section_dict(self, 'OUTLETS')

    def get_subcatchments(self) -> Dict[str, Any]:
        """Get SUBCATCHMENTS section."""
        return _get_section_dict(self, 'SUBCATCHMENTS')

    def get_subareas(self) -> Dict[str, Any]:
        """Get SUBAREAS section."""
        return _get_section_dict(self, 'SUBAREAS')

    def get_infiltration(self) -> Dict[str, Any]:
        """Get INFILTRATION section."""
        return _get_section_dict(self, 'INFILTRATION')

    def get_coordinates(self) -> Dict[str, tuple]:
        """Get COORDINATES section."""
        return _get_section_dict(self, 'COORDINATES')

    def get_vertices(self) -> Dict[str, List[tuple]]:
        """Get VERTICES section (link vertices)."""
        return _get_section_dict(self, 'VERTICES')

    def get_polygons(self) -> Dict[str, List[tuple]]:
        """Get POLYGONS section (subcatchment polygons)."""
        return _get_section_dict(self, 'POLYGONS')

    def get_xsections(self) -> Dict[str, Any]:
        """Get XSECTIONS section."""
        return _get_section_dict(self, 'XSECTIONS')

    def get_transects(self) -> Dict[str, Any]:
        """Get TRANSECTS section."""
        return _get_section_dict(self, 'TRANSECTS')

    def get_curves(self) -> Dict[str, Any]:
        """Get CURVES section."""
        return _get_section_dict(self, 'CURVES')

    def get_timeseries(self) -> Dict[str, Any]:
        """Get TIMESERIES section."""
        return _get_section_dict(self, 'TIMESERIES')

    def get_patterns(self) -> Dict[str, Any]:
        """Get PATTERNS section."""
        return _get_section_dict(self, 'PATTERNS')

    def get_controls(self) -> Dict[str, Any]:
        """Get CONTROLS section."""
        return _get_section_dict(self, 'CONTROLS')

    def get_raingages(self) -> Dict[str, Any]:
        """Get RAINGAGES section."""
        return _get_section_dict(self, 'RAINGAGES')

    def get_inflows(self) -> Dict[str, Any]:
        """Get INFLOWS section."""
        return _get_section_dict(self, 'INFLOWS')

    def get_dwf(self) -> Dict[str, Any]:
        """Get DWF (Dry Weather Flow) section."""
        return _get_section_dict(self, 'DWF')

    def get_report_element_selection(self) -> ReportElementSelection:
        """
        Read which nodes, links and subcatchments the INP asks to report.

        Driven by ``[REPORT] NODES / LINKS / SUBCATCHMENTS``. Values resolve to
        ``ALL``, an ID set, or None (NONE / absent) so the OUT exporter can skip
        or filter each time-series table independently.

        :return: Normalized report element selection
        """
        inp = _require_inp_loaded(self)
        report = getattr(inp, 'REPORT', None)
        if report is None and REPORT in inp:
            report = inp[REPORT]
        if report is None or not hasattr(report, 'get'):
            return ReportElementSelection()

        return ReportElementSelection(
            nodes=parse_report_kind(report.get('NODES')),
            links=parse_report_kind(report.get('LINKS')),
            subcatchments=parse_report_kind(report.get('SUBCATCHMENTS')),
        )

    # =========================================================================
    # Summary and Statistics
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the INP file contents.
        
        :return: Dictionary with counts of each element type
        """
        summary = {
            "file": self.file_path,
            "loaded": self.is_loaded(),
            "title": None,
            "counts": {}
        }

        if not self.file_object:
            return summary

        summary["title"] = self.get_title()

        sections = [
            ("junctions", "JUNCTIONS"),
            ("outfalls", "OUTFALLS"),
            ("storage", "STORAGE"),
            ("dividers", "DIVIDERS"),
            ("conduits", "CONDUITS"),
            ("pumps", "PUMPS"),
            ("orifices", "ORIFICES"),
            ("weirs", "WEIRS"),
            ("outlets", "OUTLETS"),
            ("subcatchments", "SUBCATCHMENTS"),
            ("raingages", "RAINGAGES"),
            ("curves", "CURVES"),
            ("timeseries", "TIMESERIES"),
            ("patterns", "PATTERNS"),
            ("controls", "CONTROLS"),
        ]

        for name, attr in sections:
            if hasattr(self.file_object, attr):
                section = getattr(self.file_object, attr)
                summary["counts"][name] = len(section) if section else 0
            else:
                summary["counts"][name] = 0

        return summary

    # =========================================================================
    # Raw INP Access
    # =========================================================================

    def get_raw_inp(self) -> Any:
        """
        Get the raw swmm_api SwmmInput object.
        
        :return: SwmmInput object
        """
        return _require_inp_loaded(self)

    def get_section(self, section_name: str) -> Optional[Any]:
        """
        Get any section by name.
        
        :param section_name: Section name (e.g., 'JUNCTIONS', 'CONDUITS')
        :return: Section data or None if the section is not present
        """
        return getattr(_require_inp_loaded(self), section_name.upper(), None)
