"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.
"""
# -*- coding: utf-8 -*-
import os
import time
from wntr.epanet import toolkit
from wntr.epanet.util import EN

from dataclasses import dataclass, field
from typing import Any, List, Optional, Callable
from datetime import datetime

from ..utils.enums import RunStatus, ExportDataSource
from ..utils import tools_log
from .bin_handler import EpanetBinHandler
from .inp_handler import EpanetInpHandler
from .models import EpanetFeatureSettings, EpanetOptionsSettings, EpanetOtherSettings
from ..utils.tools_api import HeFrostClient
from ..exceptions import (
    HydraulicEngineError,
    ValidationError,
    UnsupportedFileTypeError,
    SimulationCancelled,
    SimulationError,
)


@dataclass
class EpanetRunResult:
    """Result of a EPANET simulation run"""
    status: RunStatus = RunStatus.NOT_RUN
    inp_path: Optional[str] = None
    rpt_path: Optional[str] = None
    bin_path: Optional[str] = None
    return_code: Optional[int] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duration_seconds: Optional[float] = None
    # Simulation statistics
    routing_steps: Optional[int] = None
    flow_routing_error: Optional[float] = None
    runoff_error: Optional[float] = None


class EpanetRunner:
    """
    Class for running EPANET simulations.

    Uses WNTR (Water Network Tool for Resilience) library.

    Example usage:
        # Run simulation
        runner = EpanetRunner(
            inp_path="model.inp",
            rpt_path="results.rpt",
            bin_path="results.bin",
            progress_callback=on_progress
        )
        result = runner.run()

        # Check RPT/outcome status (engine crashes raise SimulationError;
        # step_callback False raises SimulationCancelled)
        if result.status == RunStatus.SUCCESS:
            print(f"Simulation completed successfully in {result.duration_seconds:.2f}s")
            print(f"RPT file: {result.rpt_path}")
            print(f"BIN file: {result.bin_path}")
        else:
            print(f"Simulation failed: {result.errors}")

        # Export results to database
        runner.export_result(ExportDataSource.DATABASE)
    """

    def __init__(
        self,
        inp_path: Optional[str] = None,
        rpt_path: Optional[str] = None,
        bin_path: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ):
        """
        Initialize EPANET runner.
        """
        self.inp_path = inp_path
        self.rpt_path = rpt_path
        self.bin_path = bin_path
        self.result: Optional[EpanetRunResult] = None
        self.inp: Optional[EpanetInpHandler] = None
        self.bin: Optional[EpanetBinHandler] = None
        self._progress_callback = progress_callback

    def _report_progress(self, progress: int, message: str) -> None:
        """Report progress if callback is set."""
        if self._progress_callback:
            self._progress_callback(progress, message)

    def _format_time(self, seconds: float) -> str:
        """Format seconds into a human-readable string (e.g., '2m 30s', '1h 15m')."""
        if seconds < 0:
            return "0s"

        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s" if secs > 0 else f"{minutes}m"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"

    def run(
        self,
        feature_settings: Optional[EpanetFeatureSettings] = None,
        options_settings: Optional[EpanetOptionsSettings] = None,
        other_settings: Optional[EpanetOtherSettings] = None,
        step_callback: Optional[Callable[[Any, int], bool]] = None,
        calculate_water_quality: bool = True
    ) -> EpanetRunResult:
        """
        Run EPANET simulation.

        Hybrid error contract:
        - Preconditions (load/validate INP) raise FileLoadError / ValidationError.
        - step_callback returning False raises SimulationCancelled (with .result).
        - Unexpected engine failures raise SimulationError (with .result).
        - Finished runs return EpanetRunResult with SUCCESS / WARNING / ERROR from
          RPT parsing and output-file checks (no raise).

        :param feature_settings: Feature settings for the simulation (junctions, pipes, etc.)
        :param options_settings: Options settings for the simulation (time, hydraulics, etc.)
        :param other_settings: Other settings for the simulation (patterns, curves, etc.)
        :param step_callback: After each step. Return True to continue, False to abort.
            None return is treated as continue.
        :param calculate_water_quality: Whether to run water quality simulation
        :return: EpanetRunResult with simulation results
        """
        result = EpanetRunResult()
        self.result = result

        self.inp = EpanetInpHandler()
        self.inp.load_file(self.inp_path)

        validation = self.inp.validate_inp()
        if not validation["valid"]:
            errors = validation.get("errors", [])
            raise ValidationError(
                f"INP file validation failed for '{self.inp_path}': "
                + "; ".join(errors)
            )

        self.bin = EpanetBinHandler()

        # Generate temporary bin file path
        self.bin.file_path = str(self.bin.get_file_path(output_path=self.bin_path, extension=".bin"))

        result.inp_path = self.inp.file_path
        result.rpt_path = self.rpt_path
        result.bin_path = self.bin.file_path

        # Modify the INP file with the feature settings, options settings and other settings
        if feature_settings or options_settings or other_settings:
            self.inp.update_inp_from_settings(
                feature_settings=feature_settings,
                options_settings=options_settings,
                other_settings=other_settings,
            )
            temp_inp_path = self.inp.get_file_path(None, ".inp")
            self.inp.write(output_path=temp_inp_path)
            result.inp_path = str(temp_inp_path)

        self._report_progress(5, "Starting EPANET simulation...")
        tools_log.log_info(f"Running EPANET simulation: {self.inp_path}")

        return self._run_with_epanet(result, step_callback, calculate_water_quality)

    def _run_with_epanet(
        self,
        result: EpanetRunResult,
        step_callback: Optional[Callable[[Any, int], bool]] = None,
        calculate_water_quality: bool = True
    ) -> EpanetRunResult:
        """
        Run the EPANET simulation step-by-step using the EPANET toolkit.

        :param result: Object to populate with results (like a status or log).
        :param step_callback: After each step. Return True to continue, False to abort.
            None return is treated as continue.
        :param calculate_water_quality: Whether to run water quality simulation
        :return: Updated result with status and duration.
        :raises SimulationCancelled: When step_callback returns False.
        :raises SimulationError: When the EPANET engine fails unexpectedly.
        """
        start_time = time.time()
        enData = None

        try:
            self._report_progress(10, "Initializing EPANET engine...")

            # Create EPANET project handle
            enData = toolkit.ENepanet()

            # Open the INP file
            enData.ENopen(inpfile=result.inp_path, rptfile=result.rpt_path, binfile=result.bin_path)

            # Get simulation duration for progress calculation
            duration = enData.ENgettimeparam(EN.DURATION)

            self._report_progress(15, "EPANET engine initialized, starting hydraulic simulation...")

            # ========== Hydraulic Simulation (step-by-step) ==========
            step_count = self._run_hydraulic_simulation(enData, duration, step_callback)

            # ========== Water Quality Simulation ==========
            if calculate_water_quality:
                self._report_progress(85, "Running water quality simulation...")
                self._run_water_quality_simulation(enData, step_callback)

            # Close simulation
            enData.ENreport()
            enData.ENclose()
            enData = None

            result.routing_steps = step_count
            result.duration_seconds = time.time() - start_time

            # Check if output files were created
            self._report_progress(90, "Simulation completed, checking results...")

            if result.rpt_path and os.path.isfile(result.rpt_path):
                self._parse_rpt_status(result)
            else:
                result.status = RunStatus.ERROR
                result.errors.append('RPT file was not created')

            if os.path.isfile(result.bin_path):
                self.bin.load_file(result.bin_path)
            else:
                result.status = RunStatus.ERROR
                result.errors.append('BIN file was not created')

            # Set result status based on simulation
            if result.status == RunStatus.NOT_RUN:
                if result.errors:
                    result.status = RunStatus.ERROR
                elif result.warnings:
                    result.status = RunStatus.WARNING
                else:
                    result.status = RunStatus.SUCCESS

            # Final report
            self._report_progress(100, f"Simulation finished: {result.status.value}")
            tools_log.log_info(f"EPANET simulation completed: {result.status.value} "
                f"({result.duration_seconds:.2f}s, {result.routing_steps} steps)")
            self.result = result
            return result
        except SimulationCancelled as e:
            result.status = RunStatus.CANCELLED
            result.warnings.append(str(e))
            result.duration_seconds = time.time() - start_time
            self.result = result
            self._report_progress(100, "Simulation cancelled")
            tools_log.log_info(f"EPANET simulation cancelled: {e}")
            raise SimulationCancelled(str(e), result=result) from e
        except HydraulicEngineError:
            self.result = result
            raise
        except Exception as e:
            result.status = RunStatus.ERROR
            result.errors.append(str(e))
            result.duration_seconds = time.time() - start_time
            self.result = result
            tools_log.log_error(f"EPANET simulation error: {e}")
            raise SimulationError(f"EPANET simulation error: {e}", result=result) from e
        finally:
            # Ensure EPANET is properly closed even on error
            if enData is not None:
                try:
                    enData.ENclose()
                except Exception as e:
                    tools_log.log_warning(f"Error closing EPANET engine: {e}")

    def _run_hydraulic_simulation(
        self,
        enData: toolkit.ENepanet,
        duration: int,
        step_callback: Optional[Callable[[Any, int], bool]] = None,
    ) -> int:
        """
        Run the hydraulic simulation step-by-step using the EPANET toolkit.

        :param enData: EPANET project handle
        :param duration: Simulation duration in seconds
        :param step_callback: Callback function to track simulation progress
        :return: Step count
        """
        enData.ENopenH()
        enData.ENinitH(EN.SAVE)

        last_progress = 15
        last_report_time = time.time()
        real_start_time = time.time()
        step_count = 0

        try:
            while True:
                current_time = enData.ENrunH()
                step_count += 1

                # Calculate progress percentage (0.0 to 1.0)
                if duration > 0:
                    percent = min(current_time / duration, 1.0)
                else:
                    percent = 1.0

                # Map progress to 15-85% range
                sim_progress = min(85, int(15 + percent * 70))

                # Only report every 0.5 seconds to avoid flooding
                current_real_time = time.time()
                if sim_progress != last_progress and (current_real_time - last_report_time) >= 0.5:
                    # Calculate ETA based on elapsed time
                    elapsed_real = current_real_time - real_start_time
                    if percent > 0:
                        estimated_total_time = elapsed_real / percent
                        remaining_real = estimated_total_time - elapsed_real
                    else:
                        remaining_real = 0

                    remaining_str = self._format_time(remaining_real)
                    # Convert simulation time (seconds) to readable format
                    sim_time_str = self._format_simulation_time(current_time)

                    progress_msg = f"ETA: {remaining_str} | Simulation time: {sim_time_str}"
                    self._report_progress(sim_progress, progress_msg)
                    last_progress = sim_progress
                    last_report_time = current_real_time

                # Call user callback if provided
                if step_callback is not None:
                    should_continue = step_callback(enData, step_count)
                    if should_continue is False:  # only False; None does not stop
                        tools_log.log_info(
                            f"Simulation stopped by callback at hydraulic step {step_count}"
                        )
                        raise SimulationCancelled(
                            f"Stopped at hydraulic step {step_count}"
                        )

                # Advance to next hydraulic time step
                time_left = enData.ENnextH()
                if time_left <= 0:
                    break

            # Save hydraulic results
            enData.ENcloseH()
            enData.ENsaveH()

            return step_count
        except SimulationCancelled:
            try:
                enData.ENcloseH()
            except Exception:
                pass
            raise


    def _run_water_quality_simulation(self, enData: toolkit.ENepanet, step_callback: Optional[Callable[[Any, int], bool]] = None) -> None:
        """
        Run the water quality simulation step-by-step using the EPANET toolkit.

        :param enData: EPANET project handle
        :param step_callback: After each step. Return True to continue, False to abort.
            None return is treated as continue.
        """
        enData.ENopenQ()
        enData.ENinitQ(EN.SAVE)
        step_count = 0

        try:
            while True:
                enData.ENrunQ()
                step_count += 1

                if step_callback is not None:
                    should_continue = step_callback(enData, step_count)
                    if should_continue is False:
                        tools_log.log_info(
                            f"Simulation stopped by callback at quality step {step_count}"
                        )
                        raise SimulationCancelled(
                            f"Stopped at quality step {step_count}"
                        )

                if enData.ENnextQ() <= 0:
                    break

            enData.ENcloseQ()
        except SimulationCancelled:
            try:
                enData.ENcloseQ()
            except Exception:
                pass
            raise

    def _parse_rpt_status(self, result: EpanetRunResult) -> None:
        """
        Parse RPT file for errors and warnings.

        :param result: EpanetRunResult to update
        """
        try:
            with open(result.rpt_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            lines = content.split('\n')

            for line in lines:
                line_stripped = line.strip()
                line_lower = line_stripped.lower()

                # Check for run errors
                if 'run was unsuccessful' in line_lower:
                    result.errors.append("EPANET run was unsuccessful")
                    result.status = RunStatus.ERROR

                if 'error' in line_lower and 'error:' in line_lower:
                    result.errors.append(line_stripped)
                    result.status = RunStatus.ERROR

                # Check for warnings
                if line_lower.startswith('warning'):
                    result.warnings.append(line_stripped)

        except Exception as e:
            msg = f"Could not parse RPT file for status: {e}"
            tools_log.log_warning(msg)
            result.warnings.append(msg)

    def _format_simulation_time(self, seconds: int) -> str:
        """Format simulation time (in seconds) into days, hours, minutes format."""
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60

        if days > 0:
            return f"Day {days}, {hours:02d}:{minutes:02d}"
        else:
            return f"{hours:02d}:{minutes:02d}"

    def export_result(
            self,
            to: ExportDataSource,
            result_id: str,
            batch_size: int = 10,
            max_workers: int = 4,
            crs_from: int = 25831,
            crs_to: int = 4326,
            start_time: Optional[datetime] = None,
            round_decimals: int = 4,
            client: Optional[HeFrostClient] = None,
            giswater_version: int = 4,
            only_extrema: bool = False
        ) -> bool:
        """
        Export the result file to a specific datasource
        """

        if to == ExportDataSource.DATABASE:
            return self.bin.export_to_database(
                result_id=result_id,
                inp_handler=self.inp,
                round_decimals=round_decimals,
                dao=client,
                giswater_version=giswater_version,
                only_extrema=only_extrema
            )
        elif to == ExportDataSource.FROST:
            return self.bin.export_to_frost(
                inp_handler=self.inp,
                result_id=result_id,
                batch_size=batch_size,
                max_workers=max_workers,
                crs_from=crs_from,
                crs_to=crs_to,
                start_time=start_time,
                client=client
            )
        else:
            raise UnsupportedFileTypeError(f"Unsupported export target: {to}")
