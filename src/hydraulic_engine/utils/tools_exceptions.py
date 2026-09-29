"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

Helpers to surface the root cause of chained exceptions and engine (RPT) errors.
"""
# -*- coding: utf-8 -*-
import os
import re
from typing import List, Optional

# Matches engine error lines such as "Error 205: ..." or "Input Error 213: ..." (EPANET),
# and "ERROR 217: ..." (SWMM).
RPT_ERROR_CODE_RE = re.compile(r"\berror\s+\d{3}\b", re.IGNORECASE)

_MAX_CHAIN_DEPTH = 10


def _exception_message(exc: BaseException) -> str:
    """Return ``str(exc)``, without the extra repr quotes ``KeyError`` adds to string messages."""
    if isinstance(exc, KeyError) and len(exc.args) == 1 and isinstance(exc.args[0], str):
        return exc.args[0].strip()
    return str(exc).strip()


def format_exception_chain(exc: BaseException, separator: str = " | ") -> str:
    """
    Build a user-facing message from an exception and its causes.

    Walks ``__cause__`` (preferred) or ``__context__`` (cycle-safe, depth-limited),
    collects unique non-empty messages and returns them innermost first, so the
    most specific message comes first, e.g.
    ``(Error 205) undefined time pattern, 'TEST' | (Error 200) one or more errors in input file``.

    :param exc: Exception to describe
    :param separator: Separator placed between messages
    :return: Joined messages, or the exception class name when all messages are empty
    """
    messages: List[str] = []
    seen_ids = set()
    current: Optional[BaseException] = exc

    while current is not None and id(current) not in seen_ids and len(seen_ids) < _MAX_CHAIN_DEPTH:
        seen_ids.add(id(current))
        message = _exception_message(current)
        if message and message not in messages:
            messages.append(message)
        current = current.__cause__ if current.__cause__ is not None else current.__context__

    if not messages:
        return type(exc).__name__

    messages.reverse()
    return separator.join(messages)


def collect_engine_failure(
    exc: BaseException,
    errors: List[str],
    rpt_path: Optional[str] = None,
    since: Optional[float] = None,
) -> str:
    """
    Describe an unexpected engine failure and record it in ``errors``.

    Appends the exception chain message and any numbered error lines found in the RPT
    written by the engine (without duplicates). If the chain itself carries no
    ``Error NNN`` code, the first RPT error is appended to the returned message.

    :param exc: Exception raised by the engine
    :param errors: Error list to update (e.g. ``result.errors``)
    :param rpt_path: RPT path, if any
    :param since: Epoch seconds; RPT files last modified earlier are ignored as stale
    :return: User-facing message for the failure
    """
    detail = format_exception_chain(exc)
    if detail not in errors:
        errors.append(detail)

    rpt_errors: List[str] = []
    if rpt_path:
        try:
            fresh = since is None or os.path.getmtime(rpt_path) >= since
        except OSError:
            fresh = False
        if fresh:
            rpt_errors = extract_rpt_errors(rpt_path)

    for line in rpt_errors:
        if line not in errors:
            errors.append(line)

    if rpt_errors and not RPT_ERROR_CODE_RE.search(detail):
        detail = f"{detail} | {rpt_errors[0]}"
    return detail


def extract_rpt_errors(rpt_path: Optional[str]) -> List[str]:
    """
    Read an RPT file and return its lines that report a numbered engine error.

    :param rpt_path: Path to the RPT file
    :return: Stripped error lines (empty if the file is missing or unreadable)
    """
    if not rpt_path:
        return []
    errors: List[str] = []
    try:
        with open(rpt_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if RPT_ERROR_CODE_RE.search(line):
                    stripped = line.strip()
                    if stripped not in errors:
                        errors.append(stripped)
    except OSError:
        return []
    return errors
