"""
Test script for running EPANET and exporting results to DATABASE.
"""
import os
import sys

# Add the src directory to path for development
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import hydraulic_engine as he
from hydraulic_engine.utils import tools_log

# =============================================================================
# CONFIGURATION - Update these values
# =============================================================================

# EPANET INP file path
INP_FILE = r""  # <-- Change this, e.g., r"C:\path\to\input\results.inp"

# Output paths (optional - will be auto-generated if None)
RPT_FILE = r""  # e.g., r"C:\path\to\output\results.rpt"
BIN_FILE = r""  # e.g., r"C:\path\to\output\results.bin"

# =============================================================================
# SCRIPT
# =============================================================================

def progress_callback(progress: int, message: str):
    """Callback to show simulation progress."""
    bar_length = 30
    filled = int(bar_length * progress / 100)
    bar = '█' * filled + '░' * (bar_length - filled)
    print(f"\r[{bar}] {progress:3d}% - {message}", end='', flush=True)
    if progress == 100:
        print()  # New line at the end


def main():
    print("=" * 60)
    print("HYDRAULIC ENGINE - EPANET Test Script to run and export results to DATABASE")
    print("=" * 60)

    # -------------------------------------------------------------------------
    # Initialize logger (logs will be in %APPDATA%/hydraulic_engine/log/)
    # -------------------------------------------------------------------------
    tools_log.set_logger("hydraulic_engine", min_log_level=10)  # 10=DEBUG
    print(f"\n[0] Logger initialized")
    print(f"    Log file: {he.config.logger.filepath if he.config.logger else 'N/A'}")

    # -------------------------------------------------------------------------
    # Step 1: Run EPANET simulation
    # -------------------------------------------------------------------------
    print(f"\n[1] Running EPANET simulation...")

    runner = he.epanet.EpanetRunner(
        inp_path=INP_FILE,
        rpt_path=RPT_FILE,
        bin_path=BIN_FILE,
        progress_callback=progress_callback,  # Optional: callback to show simulation progress (progress, message)
    )

    # Run simulation
    try:
        result = runner.run()
    except he.SimulationCancelled as e:
        print(f"\n    Simulation cancelled: {e}")
        return 1
    except he.SimulationError as e:
        print(f"\n    Simulation error: {e}")
        if e.result and e.result.errors:
            for error in e.result.errors:
                print(f"      ✗ {error}")
        return 1

    print(f"    - RPT: {result.rpt_path}")
    print(f"    - BIN: {result.bin_path}")

    # Check for errors and warnings
    if result.errors:
        print(f"\n    Errors:")
        for error in result.errors:
            print(f"      ✗ {error}")

    if result.warnings:
        print(f"\n    Warnings:")
        for warning in result.warnings:
            print(f"      ! {warning}")

    # -------------------------------------------------------------------------
    # Step 3: Initialize DATABASE client and export results
    # -------------------------------------------------------------------------

    print(f"\n[3] Initializing DATABASE client...")

    dao = he.create_pg_connection(
        host="localhost", # <-- Change this, e.g., "localhost"
        port=5432, # <-- Change this, e.g., 5432
        dbname="postgres", # <-- Change this, e.g., "postgres"
        user="postgres", # <-- Change this, e.g., "postgres"
        password="", # <-- Change this, e.g., "password"
        schema="", # <-- Change this, e.g., "schema"
    )

    if not dao:
        print(f"    ✗ Failed to initialize DATABASE client")
        return 1

    print(f"\n[4] Exporting results to DATABASE...")

    if runner.export_result(to=he.ExportDataSource.DATABASE, result_id="test_epanet", batch_size=10, client=dao):
        print(f"    ✓ Results exported to DATABASE")
    else:
        print(f"    ✗ Failed to export results to DATABASE")

    runner.cleanup()

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)

    return 0 if result.status.value in ("success", "warning") else 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
