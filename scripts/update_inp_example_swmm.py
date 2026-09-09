"""
Test script for updating an INP file for SWMM.

The settings dataclasses in hydraulic_engine.swmm.models mirror the swmm_api
sections, so they double as the list of what can be changed in the INP: only the
attributes set to a value are written, everything else is left untouched.
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

# SWMM INP file path
INP_FILE = r""  # <-- Change this, e.g., r"C:\path\to\input\model.inp"

# Updated INP file path
INP_FILE_UPDATED = r""  # <-- Change this, e.g., r"C:\path\to\input\model_updated.inp"

# =============================================================================
# SCRIPT
# =============================================================================

def main():
    print("=" * 60)
    print("HYDRAULIC ENGINE - Update INP Test Script for SWMM")
    print("=" * 60)

    # -------------------------------------------------------------------------
    # Initialize logger (logs will be in %APPDATA%/hydraulic_engine/log/)
    # -------------------------------------------------------------------------
    tools_log.set_logger("hydraulic_engine", min_log_level=10)  # 10=DEBUG
    print(f"\n[0] Logger initialized")
    print(f"    Log file: {he.config.logger.filepath if he.config.logger else 'N/A'}")

    # -------------------------------------------------------------------------
    # Step 1: Update INP file
    # -------------------------------------------------------------------------
    print(f"\n[1] Updating INP file...")

    inp_handler = he.swmm.SwmmInpHandler()
    inp_handler.load_file(INP_FILE)

    # Features: keys are the element names in the INP, so change the ids below
    # to elements that exist in your model
    feature_settings = he.swmm.SwmmFeatureSettings()
    feature_settings.junctions = {
        "J1": he.swmm.SwmmJunction(elevation=10.0, depth_max=3.0, area_ponded=25.0),
    }
    feature_settings.conduits = {
        "C1": he.swmm.SwmmConduit(
            roughness=0.014,
            cross_section=he.swmm.SwmmCrossSection(
                shape=he.swmm.SwmmCrossSectionShape.CIRCULAR,
                height=0.6,
            ),
        ),
    }
    feature_settings.storage = {
        "ST1": he.swmm.SwmmStorage(depth_max=4.0, depth_init=0.5),
    }

    # Options: the [OPTIONS] section of the INP
    options_settings = he.swmm.SwmmOptionsSettings(
        flow_units=he.swmm.SwmmFlowUnits.LPS,
        infiltration=he.swmm.SwmmInfiltration.HORTON,
        flow_routing=he.swmm.SwmmFlowRouting.DYNWAVE,
        allow_ponding=True,
        max_trials=12,
        head_tolerance=0.0015,
    )

    # Controls: dict key must match the RULE/VARIABLE/EXPRESSION name in the text.
    # Create if missing, replace if present.
    #
    # other_settings = he.swmm.SwmmOtherSettings(
    #     controls={
    #         "R1": he.swmm.SwmmControl(
    #             text=(
    #                 "RULE R1\n"
    #                 "IF NODE J1 DEPTH > 1\n"
    #                 "THEN PUMP P1 STATUS = ON\n"
    #                 "ELSE PUMP P1 STATUS = OFF\n"
    #                 "PRIORITY 1"
    #             )
    #         ),
    #     },
    # )

    inp_handler.update_inp_from_settings(
        feature_settings=feature_settings,
        options_settings=options_settings,
        # other_settings=other_settings,
    )
    inp_handler.write(INP_FILE_UPDATED)

    print(f"    Updated INP written to {INP_FILE_UPDATED}")

    print("\n" + "=" * 60)
    print("Done!")
    print("=" * 60)

    return 0 if os.path.exists(INP_FILE_UPDATED) else 1


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
