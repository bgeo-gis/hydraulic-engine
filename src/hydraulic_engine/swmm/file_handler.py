"""
Copyright © 2026 by BGEO. All rights reserved.
The program is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License as published by the Free Software Foundation, either version 3 of the License,
or (at your option) any later version.
"""
# -*- coding: utf-8 -*-
import os
import tempfile

from typing import Optional
from abc import ABC, abstractmethod
from pathlib import Path
from swmm_api import read_out_file
from swmm_api import read_rpt_file
from swmm_api import read_inp_file

from ..utils import tools_log
from ..utils.tools_exceptions import format_exception_chain
from os import PathLike

from ..exceptions import FileLoadError, UnsupportedFileTypeError


class SwmmFileHandler:
    """
    Handler for SWMM files.
    
    Provides functionality to read and parse SWMM files.     
    """

    def __init__(self):
        self.file_path: Optional[str] = None
        self.file_object = None
        self.error_msg: Optional[str] = None
        self._temp_files: list[Path] = []

    def load_file(self, file_path: str) -> bool:
        """
        Read and parse a result file.
        
        :param file_path: Path to file
        :return: True if successful
        """
        if not isinstance(file_path, (str, PathLike)):
            self.error_msg = f"Invalid file path type: {type(file_path).__name__}"
            raise FileLoadError(self.error_msg)

        file_path = os.fspath(file_path)

        if not os.path.isfile(file_path):
            self.error_msg = f"File not found: {file_path}"
            tools_log.log_error(self.error_msg)
            raise FileLoadError(self.error_msg)

        try:
            self.file_path = file_path
            if file_path.endswith(".out"):
                self.file_object = read_out_file(file_path)
            elif file_path.endswith(".rpt"):
                self.file_object = read_rpt_file(file_path)
            elif file_path.endswith(".inp"):
                self.file_object = read_inp_file(file_path)
            else:
                self.error_msg = f"Unsupported file type: {file_path}"
                tools_log.log_error(self.error_msg)
                raise UnsupportedFileTypeError(self.error_msg)
            tools_log.log_info(f"Successfully read file: {file_path}")
            return True

        except (FileLoadError, UnsupportedFileTypeError):
            raise
        except Exception as e:
            detail = format_exception_chain(e)
            self.error_msg = detail
            tools_log.log_error(f"Error reading file: {detail}")
            raise FileLoadError(f"Error reading file '{file_path}': {detail}") from e

    def is_loaded(self) -> bool:
        """Check if a file is loaded."""
        return self.file_object is not None

    def get_file_path(self, output_path: Optional[str], extension: str) -> Path:
        """Return output path or create a temporary file with given extension."""
        if output_path:
            return Path(output_path)

        if not extension.startswith("."):
            extension = f".{extension}"

        tmp = tempfile.NamedTemporaryFile(
            suffix=extension,
            delete=False
        )
        tmp.close()

        path = Path(tmp.name)
        self._temp_files.append(path)
        return path

    def cleanup(self):
        """Cleanup temporary files."""
        for p in self._temp_files:
            p.unlink(missing_ok=True)
        self._temp_files.clear()

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc, tb):
        """Exit context manager."""
        self.cleanup()

class SwmmResultHandler(ABC):
    """
    Handler for SWMM result files.
    
    Defines the exporting methods for result files.
    """

    @abstractmethod
    def export_to_database(self) -> bool:
        pass

    @abstractmethod
    def export_to_frost(self) -> bool:
        pass
