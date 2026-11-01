"""Implemented task families; unsupported families fail closed."""
from .registry import FAMILIES, generate_task

__all__ = ["FAMILIES", "generate_task"]
