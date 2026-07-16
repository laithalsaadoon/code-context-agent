"""Base Pydantic models for the project."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model with strict validation.

    This model:
    - Allows mutation (frozen=False)
    - Validates assignments
    - Forbids extra fields
    - Strips whitespace from strings
    """

    model_config = ConfigDict(
        frozen=False,
        validate_assignment=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class FrozenModel(BaseModel):
    """Immutable model (like frozen dataclass).

    This model:
    - Prevents mutation (frozen=True)
    - Validates assignments
    - Forbids extra fields

    Use for result objects and immutable data structures.
    """

    model_config = ConfigDict(
        frozen=True,
        validate_assignment=True,
        extra="forbid",
    )
