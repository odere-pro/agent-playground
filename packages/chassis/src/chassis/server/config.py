"""`ChassisConfig`: what `chassis serve` reads. Loaded from a YAML file or a dict."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from chassis.ports.engine import Lane
from chassis.profiles import AdapterSpec, Profile, check_lane


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str


class EngineSpec(BaseModel):
    """`spec.engine`: the lane and whatever the connector needs, passed as is to `setup`."""

    model_config = ConfigDict(extra="allow")
    connector: Lane = "inprocess"
    handle: str | None = None

    def as_mapping(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: str = "fake-route"


class PromptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str | None = None


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapters: AdapterSpec | None = None
    engine: EngineSpec = Field(default_factory=EngineSpec)
    model: ModelSpec = Field(default_factory=ModelSpec)
    prompt: PromptSpec = Field(default_factory=PromptSpec)


class ChassisConfig(BaseModel):
    """The whole config. `version` is the file's content hash when the file does not name one."""

    model_config = ConfigDict(extra="forbid")
    version: str | None = None
    profile: Profile
    agent: AgentSpec
    spec: Spec = Field(default_factory=Spec)

    @model_validator(mode="after")
    def _lane_fits_profile(self) -> ChassisConfig:
        check_lane(self.spec.engine.connector, self.profile)
        return self


def content_hash(data: Mapping[str, Any] | str | bytes) -> str:
    """A short, stable hash of the config content."""
    if isinstance(data, Mapping):
        raw = json.dumps(data, sort_keys=True, default=str).encode()
    elif isinstance(data, str):
        raw = data.encode()
    else:
        raw = data
    return hashlib.sha256(raw).hexdigest()[:12]


def load_config(source: str | Path | Mapping[str, Any]) -> ChassisConfig:
    """Load from a YAML path or a dict. Fills `version` with the content hash when missing."""
    if isinstance(source, Mapping):
        data = dict(source)
        raw: str | Mapping[str, Any] = data
    else:
        text = Path(source).read_text()
        data = yaml.safe_load(text) or {}
        raw = text
    if not isinstance(data, dict):
        raise ValueError("config must be a mapping")
    data.setdefault("version", content_hash(raw))
    return ChassisConfig.model_validate(data)
