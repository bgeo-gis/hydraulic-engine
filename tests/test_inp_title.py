"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

INP [TITLE] stamp written by EPANET and SWMM handlers.
"""
# -*- coding: utf-8 -*-
from datetime import datetime, timezone

from hydraulic_engine.epanet import EpanetInpHandler
from hydraulic_engine.swmm import SwmmInpHandler
from hydraulic_engine.utils.inp_title import build_inp_title, engine_version


_EPANET = """[TITLE]
Old
[JUNCTIONS]
11 216.4 0
[RESERVOIRS]
9 243.8
[PIPES]
10 9 11 1000 450 110 0 Open
[TIMES]
Duration 1:00
[OPTIONS]
Units LPS
Headloss H-W
[COORDINATES]
11 0 0
9 1 0
"""

_SWMM = """[TITLE]
Old
[OPTIONS]
FLOW_UNITS LPS
FLOW_ROUTING KINWAVE
START_DATE 01/01/2020
START_TIME 00:00:00
END_DATE 01/01/2020
END_TIME 01:00:00
[JUNCTIONS]
J1 10 0 0 0 0
[OUTFALLS]
O1 0 FREE NO
[CONDUITS]
C1 J1 O1 100 0.01 0 0 0
[XSECTIONS]
C1 CIRCULAR 1 0 0 0 1
[COORDINATES]
J1 0 0
O1 1 0
"""


def _title_block(text: str) -> str:
    start = text.index("[TITLE]")
    rest = text[start + len("[TITLE]"):]
    end = rest.find("\n[")
    return rest[:end] if end >= 0 else rest


def test_build_inp_title_aligns_base_and_appends_extras():
    lines = build_inp_title(
        ["Giswater version: 4.17.7", "  ", ";Source result: wb1"],
        generated_at=datetime(2026, 11, 12, 14, 51, 3, tzinfo=timezone.utc),
    )
    assert lines[0] == ";Created by Hydraulic Engine"
    assert lines[1] == f";Hydraulic Engine version: {engine_version()}"
    assert lines[2] == ";Datetime:                 2026-11-12 14:51:03"
    assert lines[1].index(engine_version()) == lines[2].index("2026-11-12")
    assert lines[3] == ";Giswater version: 4.17.7"
    assert lines[4] == ";Source result: wb1"


def test_epanet_write_stamps_title(tmp_path):
    src = tmp_path / "src.inp"
    src.write_text(_EPANET, encoding="utf-8")
    out = tmp_path / "out.inp"
    handler = EpanetInpHandler()
    handler.load_file(str(src))
    handler.write(str(out), title_extra=["Result name: child"])
    block = _title_block(out.read_text(encoding="utf-8"))
    assert ";Created by Hydraulic Engine" in block
    assert f";Hydraulic Engine version: {engine_version()}" in block
    assert ";Datetime:" in block
    assert ";Result name: child" in block
    assert "Old" not in block


def test_swmm_write_stamps_title(tmp_path):
    src = tmp_path / "src.inp"
    src.write_text(_SWMM, encoding="utf-8")
    out = tmp_path / "out.inp"
    handler = SwmmInpHandler()
    handler.load_file(str(src))
    handler.write(str(out), title_extra=[";User: alice"])
    block = _title_block(out.read_text(encoding="utf-8"))
    assert ";Created by Hydraulic Engine" in block
    assert f";Hydraulic Engine version: {engine_version()}" in block
    assert ";Datetime:" in block
    assert ";User: alice" in block
    assert "Old" not in block
