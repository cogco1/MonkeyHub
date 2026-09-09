"""The local user's saved preferences, shared by HTTP and file validation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserSettingsDto(BaseModel):
    """PUT replaces the saved preferences; omitted or null fields use defaults."""

    model_config = ConfigDict(
        populate_by_name=True, frozen=True, extra="forbid", strict=True,
        str_strip_whitespace=True, allow_inf_nan=False,
    )

    language: Literal["en", "zh-CN"] | None = None
    theme: Literal["dark", "light", "system"] | None = None
    font_scale: Literal[0.9, 1.0, 1.1] | None = Field(default=None, alias="fontScale")
    intent_provider: Literal["deterministic", "codex", "anthropic"] | None = Field(
        default=None, alias="intentProvider",
    )
    intent_model: str | None = Field(
        default=None, alias="intentModel", min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    intent_timeout_s: float | None = Field(default=None, alias="intentTimeoutS", gt=0)

    @field_validator("font_scale", mode="before")
    @classmethod
    def numeric_font_scale(cls, value: object) -> object:
        # A JSON boolean is not the numeric 1.0, even though Python compares them equal.
        if isinstance(value, bool):
            raise ValueError("fontScale must be 0.9, 1 or 1.1")
        return value
