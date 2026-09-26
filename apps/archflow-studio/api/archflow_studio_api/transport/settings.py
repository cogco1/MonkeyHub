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
    # How the interface is drawn (#328): Classic is the original look and the
    # default; the others are the visual directions a person can try.
    ui_style: Literal["classic", "quiet", "titleblock", "night"] | None = Field(default=None, alias="uiStyle")
    intent_provider: Literal["deterministic", "codex", "anthropic"] | None = Field(
        default=None, alias="intentProvider",
    )
    intent_model: str | None = Field(
        default=None, alias="intentModel", min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    intent_timeout_s: float | None = Field(default=None, alias="intentTimeoutS", gt=0)
    render_provider: Literal["off", "gemini"] | None = Field(default=None, alias="renderProvider")
    render_model: str | None = Field(
        default=None, alias="renderModel", min_length=1, max_length=160, pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    render_timeout_s: float | None = Field(default=None, alias="renderTimeoutS", ge=1, le=300)
    # What a new Hub conversation starts with. An existing conversation keeps
    # the connection and native session it was created with; changing these
    # never reaches one.
    chat_provider: Literal["codex", "claude", "coding-plan"] | None = Field(
        default=None, alias="chatProvider",
    )
    chat_model: str | None = Field(
        default=None, alias="chatModel", min_length=1, pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    # The Anthropic-compatible endpoint a Coding Plan conversation talks to (#334).
    # An address, not a secret: the token that goes with it is kept in the
    # account's credential store by the Hub and is never a preference.
    coding_plan_base_url: str | None = Field(
        default=None, alias="codingPlanBaseUrl", min_length=1, max_length=512, pattern=r"^https?://[^\s?#@]+$",
    )
    # MonkeyHub's automatic desktop updates; absent means on. The Hub reads
    # and writes it; the Project Runtime ignores it.
    auto_update: bool | None = Field(default=None, alias="autoUpdate")

    @field_validator("font_scale", mode="before")
    @classmethod
    def numeric_font_scale(cls, value: object) -> object:
        # A JSON boolean is not the numeric 1.0, even though Python compares them equal.
        if isinstance(value, bool):
            raise ValueError("fontScale must be 0.9, 1 or 1.1")
        return value


class ApplicationSettingsDto(BaseModel):
    """Local app launch choices, separate from appearance and project records."""

    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="forbid", strict=True)

    project_dir: str | None = Field(default=None, alias="projectDir", min_length=1)
    # Where new projects are created. A location, not a project: the chosen
    # project stays `project_dir`, and this never becomes a second project store.
    workspace_dir: str | None = Field(default=None, alias="workspaceDir", min_length=1)
    reference_run: str | None = Field(default=None, alias="referenceRun", min_length=1)
    cad_export: Literal["occt", "rhino", "off"] = Field(default="occt", alias="cadExport")
    studio_port: int = Field(default=8789, alias="studioPort", ge=1024, le=65535)
    monitor_port: int = Field(default=8788, alias="monitorPort", ge=1024, le=65535)

    @field_validator("project_dir", "workspace_dir")
    @classmethod
    def absolute_project(cls, value: str | None) -> str | None:
        if value is not None:
            from pathlib import Path
            if not Path(value).is_absolute():
                raise ValueError("projectDir and workspaceDir must be absolute paths")
        return value
