"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

Stamp a caller-agnostic [TITLE] block on every INP write.
"""

# -*- coding: utf-8 -*-
from collections.abc import Sequence
from datetime import datetime
from importlib import metadata

_CREATED_BY = ";Created by Hydraulic Engine"


def engine_version() -> str:
    """Installed hydraulic-engine version, or ``unknown`` when not installed."""
    try:
        return metadata.version("hydraulic-engine")
    except metadata.PackageNotFoundError:
        return "unknown"


def build_inp_title(
    title_extra: Sequence[str] | None = None,
    *,
    generated_at: datetime | None = None,
) -> list[str]:
    """Base TITLE lines plus optional opaque caller lines.

    Datetime is the process local timezone at write time (with UTC offset).
    Extra lines are appended as-is after a leading ``;`` is added when missing.
    """
    generated_dt = (generated_at or datetime.now()).astimezone()
    rows = [
        ("Hydraulic Engine version", engine_version()),
        ("Datetime", generated_dt.strftime("%Y-%m-%d %H:%M:%S %z")),
    ]
    width = max(len(f"{key}:") for key, _ in rows) + 1
    lines = [_CREATED_BY] + [f";{f'{key}:':<{width}}{value}" for key, value in rows]
    for extra in title_extra or ():
        text = str(extra).strip()
        if not text:
            continue
        if not text.startswith(";"):
            text = f";{text}"
        lines.append(text)
    return lines
