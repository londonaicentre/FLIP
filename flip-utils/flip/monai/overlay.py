# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The ``monai-flip.yaml`` overlay, schema v0.1 (FLIP#1105).

The overlay is the one file a researcher adds to a MONAI training bundle to run it on FLIP. It binds
the bundle to FLIP's data (a cohort query and an XNAT imaging resource), states what the federated
exchange carries, and names the governance profile the bundle is checked against. Everything that
carries clinical meaning stays in the bundle's own ``configs/``; the overlay never restates it.

Every model forbids unknown keys. The schema is a governance input, so a misspelt or unsupported key
must fail the load rather than be silently ignored (the same strictness the trust governance document
applies, FLIP#1259). In particular, which class drives the round loop is an adapter detail settled by
FLIP#1107; an overlay that tries to pin it (``client_algo``, ``stats_algo``) is rejected.

This module imports neither torch nor MONAI, so it can be used wherever flip-utils' base dependencies
are installed.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, field_validator, model_validator

from flip.constants.flip_constants import ResourceType

PLACEHOLDER_PREFIX = "<FILL_ME"
"""Marker the skeleton generator writes for values only the researcher can supply."""

CONFIG_SUFFIXES = (".json", ".yaml", ".yml")
"""MONAI bundles ship configs in either format, so both are accepted."""

GOVERNANCE_PROFILE_V0_1: Literal["monai-flip-v0.1"] = "monai-flip-v0.1"


class BundleSource(StrEnum):
    """Where the bundle content came from."""

    MONAI_MODEL_ZOO = "monai-model-zoo"
    LOCAL = "local"
    HUB_MIRROR = "hub-mirror"


class OverlayError(ValueError):
    """An overlay file could not be read or does not satisfy the v0.1 schema."""


def _reject_placeholder(value: str, field: str) -> str:
    if value.strip().startswith(PLACEHOLDER_PREFIX):
        raise ValueError(f"{field} still holds the skeleton placeholder {value!r}; fill it in before upload")
    return value


def _relative_path(value: str, field: str) -> str:
    """Validate a path relative to the bundle root: POSIX, non-empty, no absolute or parent segments."""
    _reject_placeholder(value, field)
    if not value or not value.strip():
        raise ValueError(f"{field} must be a non-empty path relative to the bundle root")
    if "\\" in value:
        raise ValueError(f"{field} must use '/' separators, got {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must stay inside the bundle (no absolute path, no '..'), got {value!r}")
    return value


def _config_path(value: str) -> str:
    _relative_path(value, "bundle.configs entry")
    path = PurePosixPath(value)
    if path.parts[0] != "configs":
        raise ValueError(f"bundle.configs entries must live under configs/, got {value!r}")
    if path.suffix not in CONFIG_SUFFIXES:
        raise ValueError(f"bundle.configs entries must be one of {', '.join(CONFIG_SUFFIXES)}, got {value!r}")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ConfigRef = Annotated[str, Field(min_length=1)]


class BundleConfigs(_Strict):
    """The bundle config files MONAI's ``ConfigParser`` should load, per workflow.

    A list is merged in order, later files overriding earlier ones, which is how MONAI composes
    ``evaluate.json`` on top of ``train.json``.
    """

    train: ConfigRef | list[ConfigRef]
    evaluate: ConfigRef | list[ConfigRef] | None = None

    @field_validator("train", "evaluate")
    @classmethod
    def _check_paths(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return None
        if isinstance(value, list):
            if not value:
                raise ValueError("a config list must name at least one file")
            return [_config_path(v) for v in value]
        return _config_path(value)


class BundleRef(_Strict):
    """Identity of the MONAI bundle the overlay applies to."""

    source: BundleSource
    name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=128)]
    version: Annotated[str, Field(min_length=1, max_length=64)]
    configs: BundleConfigs

    @field_validator("name", "version", mode="before")
    @classmethod
    def _no_placeholder(cls, value: Any, info: Any) -> Any:
        if isinstance(value, str):
            _reject_placeholder(value, f"bundle.{info.field_name}")
        return value


class ImagingKey(_Strict):
    """One datalist key (``image``, ``label``, ...) and the files that feed it.

    An object rather than a bare string so a later per-key form (``{resource: SEG}``) can be added
    without breaking a v0.1 overlay. It is not in v0.1: per-key resources rely on imaging-api putting
    every resource type of an accession in one folder, a contract that is not yet asserted (FLIP#1400).
    """

    pattern: Annotated[str, Field(min_length=1)]

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, value: str) -> str:
        _reject_placeholder(value, "flip.imaging.keys.*.pattern")
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError(f"pattern is a file-name glob matched inside one accession folder, got {value!r}")
        # Exactly one '*' and no other glob syntax: the text '*' matches in the image file is the stem
        # that names every sibling, so pairing is a substitution, never a second search that could
        # match zero or several files (see flip.monai.datalist).
        if value.count("*") != 1 or any(ch in value for ch in "?[]"):
            raise ValueError(f"pattern must contain exactly one '*' and no '?', '[' or ']', got {value!r}")
        return value


class ImagingBinding(_Strict):
    """Which XNAT resource the bundle reads and how its files map onto datalist keys."""

    resource: ResourceType
    keys: dict[str, ImagingKey]

    @field_validator("resource", mode="before")
    @classmethod
    def _check_resource(cls, value: Any) -> Any:
        if isinstance(value, str):
            _reject_placeholder(value, "flip.imaging.resource")
        if value in (ResourceType.ALL, ResourceType.ALL.value):
            raise ValueError("flip.imaging.resource must name one resource; ALL cannot be paired file by file")
        return value

    @field_validator("keys")
    @classmethod
    def _check_keys(cls, value: dict[str, ImagingKey]) -> dict[str, ImagingKey]:
        if "image" not in value:
            raise ValueError("flip.imaging.keys must declare an 'image' key")
        for key in value:
            if not key.isidentifier():
                raise ValueError(f"flip.imaging.keys names must be identifiers (datalist keys), got {key!r}")
        patterns = [k.pattern for k in value.values()]
        if len(set(patterns)) != len(patterns):
            raise ValueError("flip.imaging.keys patterns must be distinct, or two keys would read the same files")
        return value


class Exchange(_Strict):
    """What each round sends back to the server. Draft until the adapter spike (FLIP#1107) lands."""

    weights: Literal["weight_diff", "weights"] = "weight_diff"
    metrics: list[Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]] = Field(default_factory=list)
    statistics: list[Literal["data_count", "fail_count", "data_stats"]] = Field(default_factory=list)


class FLSettings(_Strict):
    """Federated settings that are FLIP's to own, not the bundle's."""

    local_epochs: PositiveInt = 1
    exchange: Exchange = Field(default_factory=Exchange)


class Governance(_Strict):
    """The governance profile the bundle is checked against and the outputs it may emit."""

    profile: Literal["monai-flip-v0.1"] = GOVERNANCE_PROFILE_V0_1
    allowed_outputs: Annotated[list[Literal["metrics", "model"]], Field(min_length=1)]


class FlipSection(_Strict):
    """FLIP's half of the overlay: data binding, federated settings, governance."""

    query_ref: str
    imaging: ImagingBinding
    fl: FLSettings = Field(default_factory=FLSettings)
    governance: Governance

    @field_validator("query_ref")
    @classmethod
    def _check_query_ref(cls, value: str) -> str:
        _relative_path(value, "flip.query_ref")
        if PurePosixPath(value).suffix != ".sql":
            raise ValueError(f"flip.query_ref must name a .sql file, got {value!r}")
        return value


class MonaiFlipOverlay(_Strict):
    """A complete ``monai-flip.yaml``, schema v0.1."""

    bundle: BundleRef
    flip: FlipSection

    @model_validator(mode="after")
    def _outputs_match_exchange(self) -> MonaiFlipOverlay:
        exchange = self.flip.fl.exchange
        if exchange.metrics and "metrics" not in self.flip.governance.allowed_outputs:
            raise ValueError("flip.fl.exchange.metrics is set but 'metrics' is not in governance.allowed_outputs")
        return self


def parse_overlay(data: Any, source: str = "<overlay>") -> MonaiFlipOverlay:
    """Validate an already-parsed overlay mapping.

    Args:
        data: The overlay as a mapping, e.g. from ``yaml.safe_load``.
        source: Where it came from, used in error messages.

    Returns:
        MonaiFlipOverlay: The validated overlay.

    Raises:
        OverlayError: If ``data`` is not a mapping or fails the schema. The message lists every
            failing field by its dotted path.
    """
    if not isinstance(data, dict):
        raise OverlayError(f"{source}: the overlay must be a YAML mapping, got {type(data).__name__}")
    try:
        return MonaiFlipOverlay.model_validate(data)
    except ValidationError as exc:
        problems = "\n".join(
            f"  - {'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        )
        raise OverlayError(f"{source}: invalid monai-flip overlay:\n{problems}") from exc


def load_overlay(path: str | Path) -> MonaiFlipOverlay:
    """Read and validate a ``monai-flip.yaml`` file.

    Args:
        path: Path to the overlay file.

    Returns:
        MonaiFlipOverlay: The validated overlay.

    Raises:
        OverlayError: If the file cannot be read, is not valid YAML, or fails the schema.
    """
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OverlayError(f"{path}: cannot read overlay: {exc}") from exc
    except yaml.YAMLError as exc:
        raise OverlayError(f"{path}: not valid YAML: {exc}") from exc
    return parse_overlay(data, source=str(path))
