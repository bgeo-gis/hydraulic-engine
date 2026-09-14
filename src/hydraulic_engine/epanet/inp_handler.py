"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

EPANET INP Handler
------------------
Handles reading, modifying, and writing EPANET INP files using WNTR.

The update mechanism uses a dynamic approach that:
1. Iterates through settings dataclass attributes
2. For each non-None attribute, finds the corresponding WNTR object
3. Updates only the attributes that are set (not None)

This approach matches the SWMM handler pattern and avoids explicit mapping
by using WNTR attribute names directly in the model classes.
"""
# -*- coding: utf-8 -*-
import os
import wntr

from typing import Any, Dict, Optional
from dataclasses import fields, is_dataclass
from wntr.network.controls import _ControlType
from wntr.epanet.io import _EpanetRule, _read_control_line
from wntr.epanet.util import HydParam
from .file_handler import EpanetFileHandler
from .models import (
    EpanetFeatureSettings,
    EpanetOptionsSettings,
    EpanetOtherSettings,
)
from .units import (
    convert_demand_base,
    convert_feature_value,
    convert_from_si,
    convert_option_value,
    get_flow_units,
)
from ..utils import tools_log
from ..exceptions import FileLoadError, FileWriteError, ModelNotLoadedError, ValidationError


# Configuration for feature types mapping to WNTR methods
# Format: feature_type -> (name_list_attr, getter_method)
_FEATURE_CONFIG = {
    'junctions': ('junction_name_list', 'get_node'),
    'reservoirs': ('reservoir_name_list', 'get_node'),
    'tanks': ('tank_name_list', 'get_node'),
    'pipes': ('pipe_name_list', 'get_link'),
    'pumps': ('pump_name_list', 'get_link'),
    'valves': ('valve_name_list', 'get_link'),
}

# Configuration for other settings mapping to WNTR methods
# Format: other_type -> (name_list_attr, getter_method)
# Controls and rules are handled separately (create-or-replace by name).
_OTHER_CONFIG = {
    'patterns': ('pattern_name_list', 'get_pattern'),
    'curves': ('curve_name_list', 'get_curve'),
}

# Attributes that require special handling (not direct assignment)
_SPECIAL_ATTRS = {'demand_list'}

# Attributes to skip (internal/computed, not settable on WNTR objects).
# valve_type is kept on settings models only to resolve initial_setting units.
_SKIP_ATTRS = {'node_type', 'link_type', 'valve_type'}


def _require_inp_loaded(handler: "EpanetInpHandler") -> wntr.network.WaterNetworkModel:
    """Return the loaded WNTR model or raise ModelNotLoadedError."""
    if handler.file_object is None:
        raise ModelNotLoadedError("No INP file loaded")
    return handler.file_object


def _is_simple_control(control_obj: Any) -> bool:
    """Return True for EPANET [CONTROLS] entries (not [RULES])."""
    return getattr(control_obj, 'epanet_control_type', None) != _ControlType.rule


def _normalize_rule_text(name: str, text: str) -> list[str]:
    """
    Split rule text into lines and ensure a RULE header matching ``name``.

    :raises ValidationError: if a RULE header exists with a different id
    """
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        raise ValidationError(f"Rule '{name}' text is empty")

    first_words = lines[0].strip().split()
    if first_words and first_words[0].upper() == 'RULE':
        if len(first_words) < 2:
            raise ValidationError(f"Rule '{name}' has a RULE line without an id")
        rule_id = first_words[1]
        if rule_id != name:
            raise ValidationError(
                f"Rule dict key '{name}' does not match RULE id '{rule_id}' in text"
            )
        return lines

    return [f"RULE {name}"] + lines


class EpanetInpHandler(EpanetFileHandler):
    """
    Handler for EPANET INP files.

    Provides functionality to read, parse, and write EPANET INP files
    using the WNTR library.

    Example usage:
        handler = EpanetInpHandler()
        handler.load_file("model.inp")
        
        # Get sections
        junctions = handler.get_junctions()
        pipes = handler.get_pipes()
        
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
            wntr.network.write_inpfile(self.file_object, path)
            tools_log.log_info(f"Successfully wrote INP file: {path}")
            return True
        except Exception as e:
            self.error_msg = str(e)
            tools_log.log_error(f"Error writing INP file: {e}")
            raise FileWriteError(f"Error writing INP file '{output_path or self.file_path}': {e}") from e

    def validate_inp(self) -> Dict[str, Any]:
        """
        Validate an INP file without running simulation.

        :return: Validation result dictionary with valid/errors/warnings/info
        :raises ModelNotLoadedError: If no INP has been loaded
        :raises FileLoadError: If the INP path does not exist
        :raises ValidationError: If parsing the INP fails
        """
        if not self.file_path or not self.is_loaded():
            raise ModelNotLoadedError("No INP file loaded")

        if not os.path.isfile(self.file_path):
            raise FileLoadError(f"File not found: {self.file_path}")

        validation = {
            "valid": False,
            "errors": [],
            "warnings": [],
            "info": {}
        }

        try:
            wn = wntr.network.WaterNetworkModel(self.file_path)

            # Get basic info
            validation["info"]["name"] = wn.name
            validation["info"]["junctions"] = wn.num_junctions
            validation["info"]["tanks"] = wn.num_tanks
            validation["info"]["reservoirs"] = wn.num_reservoirs
            validation["info"]["pipes"] = wn.num_pipes
            validation["info"]["pumps"] = wn.num_pumps
            validation["info"]["valves"] = wn.num_valves

            validation["valid"] = True
            tools_log.log_info(f"INP validation successful: {self.file_path}")

        except ValidationError:
            raise
        except Exception as e:
            tools_log.log_error(f"INP validation failed: {e}")
            raise ValidationError(f"INP validation failed for '{self.file_path}': {e}") from e

        return validation

    # =========================================================================
    # Update INP file from settings
    # =========================================================================

    def update_inp_from_settings(
        self,
        feature_settings: Optional[EpanetFeatureSettings] = None,
        options_settings: Optional[EpanetOptionsSettings] = None,
        other_settings: Optional[EpanetOtherSettings] = None,
    ) -> None:
        """
        Update INP file with provided settings.

        Callers pass values in EPANET INP file units (``inpfile_units``).
        Numeric hydraulic fields are converted to SI with WNTR ``to_si`` before
        they are applied to the in-memory network. See ``docs/units.md``.

        Options are applied before features so that ``inpfile_units`` and
        ``headloss`` from the same call govern feature conversion (e.g. D-W
        roughness). Only non-None fields are updated.

        :param feature_settings: Feature settings to update (junctions, pipes, etc.)
        :param options_settings: Options settings to update (simulation parameters)
        :param other_settings: Other settings to update (patterns, curves, controls, rules)
        """
        _require_inp_loaded(self)

        validation_errors: list[str] = []

        # Options first: units/headloss must be current for feature conversion.
        if options_settings:
            self._update_options(options_settings)

        if feature_settings:
            self._update_features(feature_settings, validation_errors)

        if other_settings:
            self._update_other_settings(other_settings, validation_errors)

        if validation_errors:
            raise ValidationError(
                "INP update failed: " + "; ".join(validation_errors)
            )

    def _update_features(
        self,
        feature_settings: EpanetFeatureSettings,
        validation_errors: list[str],
    ) -> None:
        """
        Update INP features from feature settings.
        
        Dynamically iterates through feature types and updates each element.
        """
        wn = self.file_object

        for feature_type, config in _FEATURE_CONFIG.items():
            features_dict = getattr(feature_settings, feature_type, None)
            if features_dict is None:
                continue

            name_list_attr, getter_method = config
            name_list = getattr(wn, name_list_attr)
            getter = getattr(wn, getter_method)

            for element_name, model_obj in features_dict.items():
                if element_name not in name_list:
                    msg = (
                        f"{feature_type[:-1].title()} '{element_name}' "
                        f"not found in network"
                    )
                    tools_log.log_warning(msg)
                    validation_errors.append(msg)
                    continue

                wntr_obj = getter(element_name)
                self._update_object_attributes(
                    wntr_obj, model_obj, validation_errors
                )

    def _update_object_attributes(
        self,
        target_obj,
        source_obj,
        validation_errors: Optional[list[str]] = None,
    ) -> None:
        """
        Update target WNTR object attributes from source model object.
        Only updates attributes that are not None in source object.
        Numeric hydraulic fields are converted from INP units to SI.

        :param target_obj: WNTR object to update
        :param source_obj: Model object with new values
        """
        wn = self.file_object
        flow_units = get_flow_units(wn)

        # Use dataclass fields if available, otherwise use dir()
        if is_dataclass(source_obj):
            attr_names = [f.name for f in fields(source_obj)]
        else:
            attr_names = [a for a in dir(source_obj) if not a.startswith('_')]

        for attr_name in attr_names:
            if attr_name in _SKIP_ATTRS:
                continue

            value = getattr(source_obj, attr_name, None)
            if value is None:
                continue

            # Handle special attributes
            if attr_name in _SPECIAL_ATTRS:
                self._handle_special_attribute(
                    target_obj, attr_name, value, flow_units
                )
                continue

            # Convert enum to value if needed
            if hasattr(value, 'value'):
                value = value.value

            value = convert_feature_value(
                attr_name,
                value,
                flow_units=flow_units,
                wn=wn,
                source_obj=source_obj,
                target_obj=target_obj,
            )

            # Attribute names match WNTR directly - set if it exists on target
            if hasattr(target_obj, attr_name):
                try:
                    setattr(target_obj, attr_name, value)
                except AttributeError:
                    msg = (
                        f"Cannot set attribute '{attr_name}' on "
                        f"{type(target_obj).__name__}"
                    )
                    tools_log.log_warning(msg)
                    if validation_errors is not None:
                        validation_errors.append(msg)

    def _handle_special_attribute(
        self, target_obj, attr_name: str, value, flow_units
    ) -> None:
        """
        Handle special attributes that require custom logic.

        :param target_obj: WNTR object to update
        :param attr_name: Attribute name from model
        :param value: Value to set (INP units)
        :param flow_units: EPANET FlowUnits for conversion
        """

        if attr_name == 'demand_list':
            # WNTR handles demands through demand_timeseries_list
            if hasattr(target_obj, 'demand_timeseries_list'):
                target_obj.demand_timeseries_list.clear()
                for demand in value:
                    base_si = convert_demand_base(flow_units, demand.base_demand)
                    target_obj.add_demand(
                        base_si,
                        pattern_name=demand.pattern_name,
                        category=demand.category,
                    )

    def _update_options(self, options_settings: EpanetOptionsSettings) -> None:
        """
        Update INP options from options settings.

        Options are organized in sections that mirror WNTR's options structure.
        ``inpfile_units`` is applied before other hydraulic fields so pressure
        conversion uses the updated unit system. Pressure options are converted
        from INP units to SI.
        """
        wn = self.file_object
        options = wn.options

        # Get dataclass fields for options settings
        if is_dataclass(options_settings):
            section_names = [f.name for f in fields(options_settings)]
        else:
            section_names = [a for a in dir(options_settings) if not a.startswith('_')]

        # Apply inpfile_units first so subsequent to_si uses the new system.
        hydraulic_settings = getattr(options_settings, 'hydraulic', None)
        if hydraulic_settings is not None:
            units_value = getattr(hydraulic_settings, 'inpfile_units', None)
            if units_value is not None:
                if hasattr(units_value, 'value'):
                    units_value = units_value.value
                if hasattr(options.hydraulic, 'inpfile_units'):
                    options.hydraulic.inpfile_units = units_value

        flow_units = get_flow_units(wn)

        for section_name in section_names:
            section_settings = getattr(options_settings, section_name, None)
            if section_settings is None:
                continue

            wntr_section = getattr(options, section_name, None)
            if wntr_section is None:
                continue

            # Get fields for this section
            if is_dataclass(section_settings):
                attr_names = [f.name for f in fields(section_settings)]
            else:
                attr_names = [a for a in dir(section_settings) if not a.startswith('_')]

            for attr_name in attr_names:
                # Already applied above
                if section_name == 'hydraulic' and attr_name == 'inpfile_units':
                    continue

                value = getattr(section_settings, attr_name, None)
                if value is None:
                    continue

                # Convert enum to value if needed
                if hasattr(value, 'value'):
                    value = value.value

                value = convert_option_value(
                    section_name, attr_name, value, flow_units
                )

                # Set attribute if it exists on WNTR section
                if hasattr(wntr_section, attr_name):
                    try:
                        setattr(wntr_section, attr_name, value)
                    except AttributeError:
                        tools_log.log_warning(
                            f"Cannot set option '{attr_name}' on {section_name}"
                        )

    def _update_other_settings(
        self,
        other_settings: EpanetOtherSettings,
        validation_errors: list[str],
    ) -> None:
        """
        Update INP other settings (patterns, curves, controls, rules).

        Patterns and curves only update existing elements.
        Controls and rules use create-or-replace by name.
        """
        wn = self.file_object

        for other_type, config in _OTHER_CONFIG.items():
            other_dict = getattr(other_settings, other_type, None)
            if other_dict is None:
                continue

            name_list_attr, getter_method = config
            name_list = getattr(wn, name_list_attr)
            getter = getattr(wn, getter_method)

            for element_name, model_obj in other_dict.items():
                if element_name not in name_list:
                    msg = (
                        f"{other_type[:-1].title()} '{element_name}' "
                        f"not found in network"
                    )
                    tools_log.log_warning(msg)
                    validation_errors.append(msg)
                    continue

                wntr_obj = getter(element_name)
                self._update_object_attributes(
                    wntr_obj, model_obj, validation_errors
                )

        self._update_controls(other_settings.controls, validation_errors)
        self._update_rules(other_settings.rules, validation_errors)

    def _update_controls(
        self,
        controls: Optional[dict],
        validation_errors: list[str],
    ) -> None:
        """Create or replace simple EPANET [CONTROLS] entries from INP text."""
        if controls is None:
            return

        wn = self.file_object
        for name, model_obj in controls.items():
            text = getattr(model_obj, 'text', None)
            if text is None or not str(text).strip():
                msg = f"Control '{name}' text is empty"
                tools_log.log_warning(msg)
                validation_errors.append(msg)
                continue

            text = str(text).strip()
            try:
                flow_units = get_flow_units(wn)
                control_obj = _read_control_line(text, wn, flow_units, name)
                if control_obj is None:
                    raise ValueError("no control parsed from text")
                if name in wn.control_name_list:
                    wn.remove_control(name)
                wn.add_control(name, control_obj)
            except Exception as e:
                msg = f"Failed to set control '{name}': {e}"
                tools_log.log_warning(msg)
                validation_errors.append(msg)

    def _update_rules(
        self,
        rules: Optional[dict],
        validation_errors: list[str],
    ) -> None:
        """Create or replace EPANET [RULES] entries from INP text."""
        if rules is None:
            return

        wn = self.file_object
        for name, model_obj in rules.items():
            text = getattr(model_obj, 'text', None)
            if text is None or not str(text).strip():
                msg = f"Rule '{name}' text is empty"
                tools_log.log_warning(msg)
                validation_errors.append(msg)
                continue

            try:
                flow_units = get_flow_units(wn)
                lines = _normalize_rule_text(name, str(text))
                parsed = _EpanetRule.parse_rules_lines(
                    lines, flow_units=flow_units
                )
                if not parsed:
                    raise ValueError("no rule parsed from text")
                if len(parsed) > 1:
                    raise ValueError(
                        f"expected one rule, got {len(parsed)}"
                    )
                rule_obj = parsed[0].generate_control(wn)
                if name in wn.control_name_list:
                    wn.remove_control(name)
                wn.add_control(name, rule_obj)
            except ValidationError as e:
                tools_log.log_warning(str(e))
                validation_errors.append(str(e))
            except Exception as e:
                msg = f"Failed to set rule '{name}': {e}"
                tools_log.log_warning(msg)
                validation_errors.append(msg)

    # =========================================================================
    # Section Getters
    # =========================================================================

    def get_title(self) -> str:
        """Get model title/name."""
        return _require_inp_loaded(self).name

    def get_junctions(self) -> Dict[str, Any]:
        """Get JUNCTIONS section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_node(name) for name in wn.junction_name_list}

    def get_reservoirs(self) -> Dict[str, Any]:
        """Get RESERVOIRS section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_node(name) for name in wn.reservoir_name_list}

    def get_tanks(self) -> Dict[str, Any]:
        """Get TANKS section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_node(name) for name in wn.tank_name_list}

    def get_pipes(self) -> Dict[str, Any]:
        """Get PIPES section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_link(name) for name in wn.pipe_name_list}

    def get_pumps(self) -> Dict[str, Any]:
        """Get PUMPS section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_link(name) for name in wn.pump_name_list}

    def get_valves(self) -> Dict[str, Any]:
        """Get VALVES section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_link(name) for name in wn.valve_name_list}

    def get_patterns(self) -> Dict[str, Any]:
        """Get PATTERNS section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_pattern(name) for name in wn.pattern_name_list}

    def get_curves(self) -> Dict[str, Any]:
        """Get CURVES section as dictionary."""
        wn = _require_inp_loaded(self)
        return {name: wn.get_curve(name) for name in wn.curve_name_list}

    def get_controls(self) -> Dict[str, Any]:
        """Get simple [CONTROLS] entries (excludes [RULES])."""
        wn = _require_inp_loaded(self)
        return {
            name: ctrl
            for name, ctrl in wn.controls()
            if _is_simple_control(ctrl)
        }

    def get_rules(self) -> Dict[str, Any]:
        """Get [RULES] entries (excludes simple [CONTROLS])."""
        wn = _require_inp_loaded(self)
        return {
            name: ctrl
            for name, ctrl in wn.controls()
            if not _is_simple_control(ctrl)
        }

    def get_options(self) -> Any:
        """Get OPTIONS object."""
        return _require_inp_loaded(self).options

    def get_demands(self) -> Dict[str, Any]:
        """
        Get junction demands in EPANET INP file units.

        WNTR stores demands in SI; values are converted with ``from_si``.

        :return: Dict with ``units`` (inpfile_units string) and ``junctions``
            mapping junction id -> ``demand_list`` of base_demand / pattern /
            category entries.
        """
        wn = _require_inp_loaded(self)
        flow_units = get_flow_units(wn)
        junctions: Dict[str, Any] = {}

        for name in wn.junction_name_list:
            node = wn.get_node(name)
            demand_list = []
            for demand in node.demand_timeseries_list:
                pattern_name = None
                if demand.pattern_name is not None:
                    pattern_name = demand.pattern_name
                elif getattr(demand, "pattern", None) is not None:
                    pattern_name = getattr(demand.pattern, "name", None)
                demand_list.append(
                    {
                        "base_demand": convert_from_si(
                            flow_units, float(demand.base_value), HydParam.Demand
                        ),
                        "pattern_name": pattern_name,
                        "category": getattr(demand, "category", None),
                    }
                )
            junctions[name] = {"demand_list": demand_list}

        return {
            "units": wn.options.hydraulic.inpfile_units,
            "junctions": junctions,
        }

    # =========================================================================
    # Count Methods
    # =========================================================================

    def get_junctions_count(self) -> int:
        """Get the count of junctions."""
        return _require_inp_loaded(self).num_junctions

    def get_reservoirs_count(self) -> int:
        """Get the count of reservoirs."""
        return _require_inp_loaded(self).num_reservoirs

    def get_tanks_count(self) -> int:
        """Get the count of tanks."""
        return _require_inp_loaded(self).num_tanks

    def get_pipes_count(self) -> int:
        """Get the count of pipes."""
        return _require_inp_loaded(self).num_pipes

    def get_pumps_count(self) -> int:
        """Get the count of pumps."""
        return _require_inp_loaded(self).num_pumps

    def get_valves_count(self) -> int:
        """Get the count of valves."""
        return _require_inp_loaded(self).num_valves

    def get_patterns_count(self) -> int:
        """Get the count of patterns."""
        return len(_require_inp_loaded(self).pattern_name_list)

    def get_curves_count(self) -> int:
        """Get the count of curves."""
        return len(_require_inp_loaded(self).curve_name_list)

    def get_controls_count(self) -> int:
        """Get the count of simple controls."""
        return len(self.get_controls())

    def get_rules_count(self) -> int:
        """Get the count of rules."""
        return len(self.get_rules())

    # =========================================================================
    # Summary
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the INP file contents.

        :return: Dictionary with counts of each element type. When no INP is
            loaded, returns loaded=False and empty counts (does not raise).
        """
        summary = {
            "file": self.file_path,
            "loaded": self.is_loaded(),
            "title": None,
            "counts": {},
        }

        if not self.file_object:
            return summary

        summary["title"] = self.get_title()
        summary["counts"] = {
            "junctions": self.get_junctions_count(),
            "reservoirs": self.get_reservoirs_count(),
            "tanks": self.get_tanks_count(),
            "pipes": self.get_pipes_count(),
            "pumps": self.get_pumps_count(),
            "valves": self.get_valves_count(),
            "patterns": self.get_patterns_count(),
            "curves": self.get_curves_count(),
            "controls": self.get_controls_count(),
            "rules": self.get_rules_count(),
        }
        return summary
