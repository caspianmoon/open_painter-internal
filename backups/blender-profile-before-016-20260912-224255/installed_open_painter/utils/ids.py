"""Persistent identity independent of datablock names."""

from uuid import uuid4


def new_id() -> str:
    return uuid4().hex
