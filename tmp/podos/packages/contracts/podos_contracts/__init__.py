"""Versioned PodOS contracts shared by the API, agents and workers.

JSON Schemas live in ``schemas/``; Pydantic models in ``models``. Schema version
``1.0`` is the only supported version for the POC.
"""

from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schemas"
SCHEMA_VERSION = "1.0"
