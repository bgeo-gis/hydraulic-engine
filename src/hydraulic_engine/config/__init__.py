"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.

Package configuration module.

Re-exports the config submodule so ``from hydraulic_engine import config`` and
``from hydraulic_engine.config import config`` both resolve to the same module.
"""
# -*- coding: utf-8 -*-
from . import config as config

__all__ = ["config"]
