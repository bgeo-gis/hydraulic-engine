"""
Probe SWMM hydraulic-engine 0.11.0 patches before release.

Uses a Giswater UD INP (blank Manning N on conduits) and applies the new
create-or-replace groups + report_settings, then optionally runs SWMM.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import hydraulic_engine as he
from hydraulic_engine.swmm import (
    SwmmConduit,
    SwmmControl,
    SwmmCurve,
    SwmmCurveKind,
    SwmmFeatureSettings,
    SwmmInflow,
    SwmmInflowKind,
    SwmmInpHandler,
    SwmmOptionsSettings,
    SwmmOtherSettings,
    SwmmPattern,
    SwmmPatternCycle,
    SwmmRaingage,
    SwmmReportSettings,
    SwmmRunner,
    SwmmTimeseries,
)
from hydraulic_engine.utils import tools_log

# =============================================================================
# CONFIGURATION
# =============================================================================

# Prefer the Giswater export WITH blank Manning N (reproduces offset shift).
INP_FILE = r"C:\Users\Usuario\Documents\inps\mom\test.inp"

# Written patched INP (temp if None)
OUT_INP = None  # e.g. r"C:\Users\Usuario\Documents\inps\mom\probe_0_11.inp"

RUN_SIMULATION = True

RG_ID = "RG-01"
JUNCTION_ID = "100"
CONDUIT_ID = "133"
EXPECTED_Z1 = 58.55
EXPECTED_Z2 = 54.53

# =============================================================================
# HELPERS
# =============================================================================

_PASS = 0
_FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def progress_callback(progress: int, message: str) -> None:
    bar_length = 30
    filled = int(bar_length * progress / 100)
    bar = "█" * filled + "░" * (bar_length - filled)
    print(f"\r[{bar}] {progress:3d}% - {message}", end="", flush=True)
    if progress == 100:
        print()


def _conduit_line(text: str, arc_id: str) -> str | None:
    for line in text.splitlines():
        if re.match(rf"^{re.escape(arc_id)}\s", line):
            return line
    return None


# =============================================================================
# MAIN
# =============================================================================


def main() -> int:
    global _PASS, _FAIL
    _PASS = _FAIL = 0

    print("=" * 60)
    print("HYDRAULIC ENGINE - SWMM 0.11.0 probe")
    print("=" * 60)

    tools_log.set_logger("hydraulic_engine", min_log_level=20)
    print(f"\n[0] hydraulic_engine {he.__version__}")
    print(f"    INP: {INP_FILE}")

    if not Path(INP_FILE).is_file():
        print("    ERROR: INP not found")
        return 1

    out_path = OUT_INP or str(Path(tempfile.gettempdir()) / "probe_swmm_0_11.inp")

    # -------------------------------------------------------------------------
    print("\n[1] Load INP and snapshot conduit offsets")
    handler = SwmmInpHandler()
    handler.load_file(INP_FILE)

    raw_before = Path(INP_FILE).read_text(encoding="utf-8", errors="replace")
    line_before = _conduit_line(raw_before, CONDUIT_ID)
    print(f"    source line: {line_before!r}")

    # -------------------------------------------------------------------------
    print("\n[2] Apply feature / other / report settings")

    feature = SwmmFeatureSettings(
        raingages={
            RG_ID: SwmmRaingage(scf=1.1),
        },
        inflows={
            f"{JUNCTION_ID}|FLOW": SwmmInflow(
                node=JUNCTION_ID,
                constituent="FLOW",
                kind=SwmmInflowKind.FLOW,
                time_series="PROBE-FLOW",
                scale_factor=1.0,
                base_value=0.0,
            ),
        },
        conduits={
            CONDUIT_ID: SwmmConduit(roughness=0.014),
        },
    )

    other = SwmmOtherSettings(
        timeseries={
            "PROBE-FLOW": SwmmTimeseries(data=[(0.0, 0.0), (1.0, 0.01), (2.0, 0.0)]),
        },
        patterns={
            "PROBE-PAT": SwmmPattern(cycle=SwmmPatternCycle.HOURLY, factors=[1.0] * 24),
        },
        curves={
            "PROBE-CURVE": SwmmCurve(
                kind=SwmmCurveKind.PUMP3,
                points=[[0.0, 0.0], [1.0, 1.0], [2.0, 0.5]],
            ),
        },
        controls={
            "PROBE_RULE": SwmmControl(
                text=(
                    "RULE PROBE_RULE\n"
                    "IF NODE 100 DEPTH > 10\n"
                    "THEN ORIFICE 100021 SETTING = 0.5\n"
                    "ELSE ORIFICE 100021 SETTING = 1.0\n"
                    "PRIORITY 1\n"
                ),
            ),
        },
    )

    report = SwmmReportSettings(
        input=True,
        continuity=True,
        flowstats=True,
        controls=True,
        nodes=[JUNCTION_ID, "101"],
        links="ALL",
        subcatchments="NONE",
    )

    options = SwmmOptionsSettings(
        max_trials=8,
        threads=2,
        end_time=time(1, 0, 0),
    )

    handler.update_inp_from_settings(
        feature_settings=feature,
        options_settings=options,
        other_settings=other,
        report_settings=report,
    )
    handler.write(out_path)
    print(f"    wrote: {out_path}")

    text = Path(out_path).read_text(encoding="utf-8", errors="replace")

    # -------------------------------------------------------------------------
    print("\n[3] Checks on patched INP")

    check("raingage SCF 1.1", bool(re.search(rf"^{RG_ID}\b.*\b1\.1", text, re.M)))
    check("REPORT INPUT YES", re.search(r"^INPUT\s+YES", text, re.M) is not None)
    check(
        "REPORT NODES 100 101",
        re.search(r"^NODES\s+.*\b100\b", text, re.M) is not None
        and re.search(r"^NODES\s+.*\b101\b", text, re.M) is not None,
    )
    check("REPORT LINKS ALL", re.search(r"^LINKS\s+ALL", text, re.M) is not None)
    check("MAX_TRIALS 8", re.search(r"^MAX_TRIALS\s+8\b", text, re.M) is not None)
    check("THREADS 2", re.search(r"^THREADS\s+2\b", text, re.M) is not None)
    check("END_TIME 01:00:00", re.search(r"^END_TIME\s+01:00:00", text, re.M) is not None)
    check("TIMESERIES PROBE-FLOW", "PROBE-FLOW" in text)
    check("PATTERN PROBE-PAT", "PROBE-PAT" in text)
    check("CURVE PROBE-CURVE", "PROBE-CURVE" in text)
    check("CONTROL PROBE_RULE", "PROBE_RULE" in text)
    check(
        "INFLOW node 100",
        bool(re.search(rf"^\[INFLOWS\][\s\S]*?^{JUNCTION_ID}\b", text, re.M)),
    )

    line_after = _conduit_line(text, CONDUIT_ID)
    print(f"    patched line: {line_after!r}")
    toks = line_after.split() if line_after else []
    # Name Node1 Node2 Length N Z1 Z2 Q0 Qmax
    if len(toks) >= 7:
        n_val, z1, z2 = float(toks[4]), float(toks[5]), float(toks[6])
        check("conduit N == 0.014", abs(n_val - 0.014) < 1e-9, f"n={n_val}")
        check(
            "conduit z1 preserved (~58.55)",
            abs(z1 - EXPECTED_Z1) < 0.02,
            f"z1={z1} (BAD if ~54.53)",
        )
        check(
            "conduit z2 preserved (~54.53)",
            abs(z2 - EXPECTED_Z2) < 0.02,
            f"z2={z2} (BAD if ~0)",
        )
    else:
        check("conduit line parseable", False, f"tokens={toks}")

    # -------------------------------------------------------------------------
    print("\n[4] get_objects JSON")
    handler2 = SwmmInpHandler()
    handler2.load_file(out_path)
    objects = handler2.get_objects()
    try:
        payload = json.dumps(objects, allow_nan=False)
        check("get_objects allow_nan=False", True, f"{len(payload)} bytes")
    except ValueError as exc:
        check("get_objects allow_nan=False", False, str(exc))

    gages = objects.get("raingages") or {}
    scf = None
    gage = gages.get(RG_ID)
    if isinstance(gage, dict):
        scf = gage.get("scf", gage.get("SCF"))
    check(
        "get_objects raingage scf",
        scf is not None and abs(float(scf) - 1.1) < 1e-6,
        f"scf={scf}",
    )

    report_obj = objects.get("report") or {}
    check("get_objects report.INPUT", report_obj.get("INPUT") in (True, "YES", "yes", 1))

    # -------------------------------------------------------------------------
    if RUN_SIMULATION:
        print("\n[5] Run SWMM simulation on patched INP")
        runner = SwmmRunner(inp_path=out_path, progress_callback=progress_callback)
        try:
            result = runner.run()
            status = getattr(result.status, "value", str(result.status))
            check("simulation finished", status in ("success", "warning"), f"status={status}")
            if getattr(result, "errors", None):
                for err in result.errors[:5]:
                    print(f"      error: {err}")
            rpt = ""
            if getattr(result, "rpt_path", None):
                rpt = Path(result.rpt_path).read_text(encoding="utf-8", errors="replace")
            warn_133 = "WARNING 03" in rpt and CONDUIT_ID in rpt
            check(
                "no WARNING 03 for conduit 133 (soft)",
                not warn_133,
                "still present — often blank-N network-wide",
            )
        except he.SimulationError as exc:
            check("simulation finished", False, str(exc))
    else:
        print("\n[5] Simulation skipped (RUN_SIMULATION=False)")

    print("\n" + "=" * 60)
    print(f"Done: {_PASS} passed, {_FAIL} failed")
    print("=" * 60)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(1)
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)