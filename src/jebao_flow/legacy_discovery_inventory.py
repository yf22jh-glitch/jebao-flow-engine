"""Bounded evidence models for pre-namespace Home Assistant discovery records.

This module is deliberately broker-neutral.  It accepts already-delivered MQTT message facts,
keeps only positively observed retained Jebao discovery configurations, and writes owner-only
artifacts.  It never infers that an unobserved retained record is absent and exposes no broker
mutation operation.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import stat
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Final

INVENTORY_SCHEMA_VERSION: Final = 1
SELECTION_SCHEMA_VERSION: Final = 1
INVENTORY_SOURCE_KIND: Final = "mqtt_retained_window_observation"
INVENTORY_COVERAGE: Final = "bounded_window_observation_only"
INVENTORY_STATUS: Final = "bounded_observation_persisted"
SELECTION_STATUS: Final = "review_selection_persisted"
SELECTION_SCOPE: Final = "observed_inventory_subset_only"
LEGACY_FORMAT_SOURCE_COMMIT: Final = "cc68af25fa73104d06688ab296b7f5d0c6c2a9f0"

DEFAULT_MAX_RECORDS: Final = 4096
DEFAULT_MAX_TOPIC_BYTES: Final = 4096
DEFAULT_MAX_PAYLOAD_BYTES: Final = 256 * 1024
DEFAULT_MAX_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
MAX_JSON_NODES: Final = 4096
MAX_JSON_DEPTH: Final = 16
MAX_JSON_STRING_BYTES: Final = 128 * 1024

_INVENTORY_ID_RE = re.compile(r"legacy-inventory-[0-9a-f]{32}\Z")
_SELECTION_ID_RE = re.compile(r"legacy-selection-[0-9a-f]{32}\Z")
_RECORD_ID_RE = re.compile(r"legacy-record-[0-9a-f]{64}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TOPIC_PREFIX_RE = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
_OBJECT_ID_RE = re.compile(r"jebao_flow_local_[a-z0-9_]+\Z")
_CURRENT_NAMESPACE_RE = re.compile(r"jebao_flow_local_instance_[0-9a-f]{64}_[a-z0-9_]+\Z")
_SLOT_RE = re.compile(r"jebao_flow_local_.+_slot_(?:[0-3][0-9]|4[0-7])_flow\Z")
_PRINTABLE_ASCII_RE = re.compile(r"[\x20-\x7e]+\Z")
_UTC_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_ALLOWED_PLATFORMS = frozenset({"button", "number", "select", "sensor", "switch"})
_TOPIC_VALUE_KEYS = frozenset(
    {
        "availability_topic",
        "command_topic",
        "json_attributes_topic",
        "state_topic",
        "topic",
    }
)


class LegacyDiscoveryInventoryError(RuntimeError):
    """A stable, redacted failure at the inventory artifact boundary."""

    def __init__(self, code: str) -> None:
        if _PRINTABLE_ASCII_RE.fullmatch(code) is None:
            raise ValueError("legacy discovery failure code must be printable ASCII")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class InventoryLimits:
    max_records: int = DEFAULT_MAX_RECORDS
    max_topic_bytes: int = DEFAULT_MAX_TOPIC_BYTES
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        values = (
            self.max_records,
            self.max_topic_bytes,
            self.max_payload_bytes,
            self.max_artifact_bytes,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("inventory limits must be positive integers")
        if self.max_topic_bytes > self.max_artifact_bytes:
            raise ValueError("topic limit cannot exceed artifact limit")
        if self.max_payload_bytes > self.max_artifact_bytes:
            raise ValueError("payload limit cannot exceed artifact limit")


@dataclass(frozen=True, slots=True, repr=False)
class InventoryContext:
    """Exact private inputs and public-safe hashes that bind an observation."""

    discovery_prefix: str = field(repr=False)
    topic_prefix: str = field(repr=False)
    config_sha256: str
    discovery_prefix_sha256: str
    topic_prefix_sha256: str
    subscription_filter_sha256: str

    @classmethod
    def build(
        cls,
        *,
        config_bytes: bytes,
        discovery_prefix: str,
        topic_prefix: str,
    ) -> InventoryContext:
        if not isinstance(config_bytes, bytes):
            raise TypeError("config_bytes must be bytes")
        _require_topic_prefix(discovery_prefix)
        _require_topic_prefix(topic_prefix)
        discovery_bytes = discovery_prefix.encode("ascii")
        topic_bytes = topic_prefix.encode("ascii")
        subscription = f"{discovery_prefix}/+/+/config".encode("ascii")
        return cls(
            discovery_prefix=discovery_prefix,
            topic_prefix=topic_prefix,
            config_sha256=_digest(config_bytes),
            discovery_prefix_sha256=_digest(discovery_bytes),
            topic_prefix_sha256=_digest(topic_bytes),
            subscription_filter_sha256=_digest(subscription),
        )

    @property
    def subscription_filter(self) -> str:
        return f"{self.discovery_prefix}/+/+/config"

    def binding_dict(self) -> dict[str, str]:
        return {
            "config_sha256": self.config_sha256,
            "discovery_prefix_sha256": self.discovery_prefix_sha256,
            "subscription_filter_sha256": self.subscription_filter_sha256,
            "topic_prefix_sha256": self.topic_prefix_sha256,
        }

    def __post_init__(self) -> None:
        _require_topic_prefix(self.discovery_prefix)
        _require_topic_prefix(self.topic_prefix)
        for value in self.binding_dict().values():
            _require_sha256(value)
        if self.discovery_prefix_sha256 != _digest(self.discovery_prefix.encode("ascii")):
            raise ValueError("discovery prefix binding is inconsistent")
        if self.topic_prefix_sha256 != _digest(self.topic_prefix.encode("ascii")):
            raise ValueError("topic prefix binding is inconsistent")
        expected_filter = f"{self.discovery_prefix}/+/+/config".encode("ascii")
        if self.subscription_filter_sha256 != _digest(expected_filter):
            raise ValueError("subscription filter binding is inconsistent")


@dataclass(frozen=True, slots=True)
class RecordClassification:
    identity_generation: str
    family: str | None
    platform: str
    payload_shape: str
    topic_prefix_binding: str
    unique_id_matches_object_id: bool
    ambiguous_topic: bool
    selection_eligible: bool

    def __post_init__(self) -> None:
        if self.identity_generation not in {
            "current_namespace",
            "pre_namespace",
            "unknown_identity",
        }:
            raise ValueError("unknown discovery identity generation")
        if self.family not in {
            None,
            "device_function",
            "group_actual_output",
            "group_member_resume",
            "group_switch",
            "schedule_slot",
            "system_function",
        }:
            raise ValueError("unknown legacy discovery family")
        if self.platform not in {*_ALLOWED_PLATFORMS, "unknown"}:
            raise ValueError("unknown discovery platform")
        if self.payload_shape not in {"malformed", "valid"}:
            raise ValueError("unknown discovery payload shape")
        if self.topic_prefix_binding not in {
            "different",
            "exact",
            "missing",
            "unavailable",
        }:
            raise ValueError("unknown discovery topic-prefix binding")
        if any(
            type(value) is not bool
            for value in (
                self.unique_id_matches_object_id,
                self.ambiguous_topic,
                self.selection_eligible,
            )
        ):
            raise TypeError("discovery classification flags must be bool")
        expected_eligible = (
            self.identity_generation == "pre_namespace"
            and self.family is not None
            and self.payload_shape == "valid"
            and self.topic_prefix_binding == "exact"
            and self.unique_id_matches_object_id
            and not self.ambiguous_topic
        )
        if self.selection_eligible != expected_eligible:
            raise ValueError("discovery selection eligibility is inconsistent")

    def public_dict(self) -> dict[str, object]:
        return {
            "ambiguous_topic": self.ambiguous_topic,
            "family": self.family,
            "identity_generation": self.identity_generation,
            "payload_shape": self.payload_shape,
            "platform": self.platform,
            "selection_eligible": self.selection_eligible,
            "topic_prefix_binding": self.topic_prefix_binding,
            "unique_id_matches_object_id": self.unique_id_matches_object_id,
        }


@dataclass(frozen=True, slots=True, repr=False)
class LegacyDiscoveryRecord:
    record_id: str
    topic: bytes = field(repr=False)
    payload: bytes = field(repr=False)
    qos_values: tuple[int, ...]
    retain: bool
    classification: RecordClassification

    @property
    def topic_sha256(self) -> str:
        return _digest(self.topic)

    @property
    def payload_sha256(self) -> str:
        return _digest(self.payload)

    def public_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.public_dict(),
            "payload_sha256": self.payload_sha256,
            "qos_values": list(self.qos_values),
            "record_id": self.record_id,
            "retain": self.retain,
            "topic_sha256": self.topic_sha256,
        }

    def private_dict(self) -> dict[str, object]:
        value = self.public_dict()
        value.update(
            {
                "payload_base64": base64.b64encode(self.payload).decode("ascii"),
                "topic_utf8_base64": base64.b64encode(self.topic).decode("ascii"),
            }
        )
        return value


@dataclass(frozen=True, slots=True, repr=False)
class InventoryObservation:
    context: InventoryContext = field(repr=False)
    utc_started: str
    utc_ended: str
    window_milliseconds: int
    message_count: int
    ignored_message_count: int
    records: tuple[LegacyDiscoveryRecord, ...] = field(repr=False)

    @property
    def candidate_count(self) -> int:
        return sum(record.classification.selection_eligible for record in self.records)

    @property
    def ambiguous_topic_count(self) -> int:
        return len(
            {record.topic for record in self.records if record.classification.ambiguous_topic}
        )


@dataclass(frozen=True, slots=True, repr=False)
class StoredInventory:
    artifact_id: str
    artifact_sha256: str
    observation: InventoryObservation = field(repr=False)

    @property
    def records(self) -> tuple[LegacyDiscoveryRecord, ...]:
        return self.observation.records

    @property
    def context(self) -> InventoryContext:
        return self.observation.context

    def public_dict(self) -> dict[str, object]:
        observation = self.observation
        value: dict[str, object] = {
            "ambiguous_topic_count": observation.ambiguous_topic_count,
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "bindings": observation.context.binding_dict(),
            "candidate_count": observation.candidate_count,
            "classification_source_commit": LEGACY_FORMAT_SOURCE_COMMIT,
            "coverage": INVENTORY_COVERAGE,
            "ignored_message_count": observation.ignored_message_count,
            "message_count": observation.message_count,
            "record_count": len(observation.records),
            "records": [record.public_dict() for record in observation.records],
            "schema_version": INVENTORY_SCHEMA_VERSION,
            "source_kind": INVENTORY_SOURCE_KIND,
            "status": INVENTORY_STATUS,
            "utc_ended": observation.utc_ended,
            "utc_started": observation.utc_started,
            "window_milliseconds": observation.window_milliseconds,
        }
        _require_public_ascii(value)
        return value


@dataclass(frozen=True, slots=True)
class SelectionRequestEntry:
    record_id: str
    topic_sha256: str
    payload_sha256: str

    def public_dict(self) -> dict[str, str]:
        return {
            "payload_sha256": self.payload_sha256,
            "record_id": self.record_id,
            "topic_sha256": self.topic_sha256,
        }


@dataclass(frozen=True, slots=True)
class SelectionRequest:
    inventory_artifact_id: str
    inventory_sha256: str
    bindings: Mapping[str, str]
    records: tuple[SelectionRequestEntry, ...]

    @classmethod
    def from_mapping(cls, value: object) -> SelectionRequest:
        mapping = _require_mapping(value, "selection_request_invalid")
        _require_exact_keys(
            mapping,
            {
                "bindings",
                "inventory_artifact_id",
                "inventory_sha256",
                "request_kind",
                "records",
                "schema_version",
            },
            "selection_request_invalid",
        )
        if mapping["schema_version"] != SELECTION_SCHEMA_VERSION:
            raise LegacyDiscoveryInventoryError("selection_request_invalid")
        if mapping["request_kind"] != "maintainer_exact_observed_record_selection":
            raise LegacyDiscoveryInventoryError("selection_request_invalid")
        inventory_id = _require_inventory_id(mapping["inventory_artifact_id"])
        inventory_sha = _require_sha256_value(mapping["inventory_sha256"])
        bindings = _decode_bindings(mapping["bindings"], "selection_request_invalid")
        raw_records = _require_sequence(mapping["records"], "selection_request_invalid")
        entries: list[SelectionRequestEntry] = []
        for raw_entry in raw_records:
            entry = _require_mapping(raw_entry, "selection_request_invalid")
            _require_exact_keys(
                entry,
                {"payload_sha256", "record_id", "topic_sha256"},
                "selection_request_invalid",
            )
            record_id = _require_record_id(entry["record_id"])
            entries.append(
                SelectionRequestEntry(
                    record_id=record_id,
                    topic_sha256=_require_sha256_value(entry["topic_sha256"]),
                    payload_sha256=_require_sha256_value(entry["payload_sha256"]),
                )
            )
        if len({entry.record_id for entry in entries}) != len(entries):
            raise LegacyDiscoveryInventoryError("selection_request_duplicate_record")
        return cls(
            inventory_artifact_id=inventory_id,
            inventory_sha256=inventory_sha,
            bindings=bindings,
            records=tuple(entries),
        )


@dataclass(frozen=True, slots=True)
class SelectedRecord:
    record_id: str
    topic_sha256: str
    payload_sha256: str
    classification: RecordClassification

    def __post_init__(self) -> None:
        _require_record_id(self.record_id)
        _require_sha256(self.topic_sha256)
        _require_sha256(self.payload_sha256)
        if not self.classification.selection_eligible:
            raise ValueError("selected discovery record must be eligible")

    def public_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.public_dict(),
            "payload_sha256": self.payload_sha256,
            "record_id": self.record_id,
            "topic_sha256": self.topic_sha256,
        }


@dataclass(frozen=True, slots=True, repr=False)
class SelectionObservation:
    inventory_artifact_id: str
    inventory_sha256: str
    context: InventoryContext = field(repr=False)
    records: tuple[SelectedRecord, ...]


@dataclass(frozen=True, slots=True, repr=False)
class StoredSelection:
    artifact_id: str
    artifact_sha256: str
    selection: SelectionObservation = field(repr=False)

    def public_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "artifact_id": self.artifact_id,
            "artifact_sha256": self.artifact_sha256,
            "bindings": self.selection.context.binding_dict(),
            "inventory_artifact_id": self.selection.inventory_artifact_id,
            "inventory_sha256": self.selection.inventory_sha256,
            "mutation_authorized": False,
            "record_count": len(self.selection.records),
            "records": [record.public_dict() for record in self.selection.records],
            "schema_version": SELECTION_SCHEMA_VERSION,
            "scope": SELECTION_SCOPE,
            "status": SELECTION_STATUS,
        }
        _require_public_ascii(value)
        return value


class LegacyDiscoveryInventoryBuilder:
    """Filter and bound delivered message facts before any durable write occurs."""

    def __init__(
        self,
        context: InventoryContext,
        *,
        utc_started: str,
        window_milliseconds: int,
        limits: InventoryLimits | None = None,
    ) -> None:
        _require_utc(utc_started)
        if type(window_milliseconds) is not int or window_milliseconds <= 0:
            raise ValueError("window_milliseconds must be positive")
        self._context = context
        self._utc_started = utc_started
        self._window_milliseconds = window_milliseconds
        self._limits = limits or InventoryLimits()
        self._message_count = 0
        self._ignored_count = 0
        self._observed: dict[tuple[bytes, bytes], set[int]] = {}
        self._raw_bytes = 0
        self._finished = False
        self._failed = False

    def observe(
        self,
        *,
        topic: str,
        payload: bytes,
        retain: bool,
        qos: int,
    ) -> bool:
        if self._finished or self._failed:
            raise LegacyDiscoveryInventoryError("inventory_builder_finished")
        self._message_count += 1
        if type(retain) is not bool or type(qos) is not int or qos not in {0, 1, 2}:
            raise LegacyDiscoveryInventoryError("mqtt_message_fact_invalid")
        if not isinstance(topic, str) or not isinstance(payload, bytes):
            raise LegacyDiscoveryInventoryError("mqtt_message_fact_invalid")
        try:
            topic_bytes = topic.encode("utf-8")
        except UnicodeError:
            self._ignored_count += 1
            return False
        topic_parts = _discovery_topic_parts(topic, self._context.discovery_prefix)
        if not retain or not payload or topic_parts is None:
            self._ignored_count += 1
            return False
        _platform, object_id = topic_parts
        if not object_id.startswith("jebao_flow_local_"):
            self._ignored_count += 1
            return False
        if len(topic_bytes) > self._limits.max_topic_bytes:
            self._failed = True
            raise LegacyDiscoveryInventoryError("inventory_topic_limit_exceeded")
        if len(payload) > self._limits.max_payload_bytes:
            self._failed = True
            raise LegacyDiscoveryInventoryError("inventory_payload_limit_exceeded")

        key = (topic_bytes, payload)
        existing = self._observed.get(key)
        if existing is not None:
            existing.add(qos)
            return True
        if len(self._observed) >= self._limits.max_records:
            self._failed = True
            raise LegacyDiscoveryInventoryError("inventory_record_limit_exceeded")
        added_bytes = len(topic_bytes) + len(payload)
        if self._raw_bytes + added_bytes > self._limits.max_artifact_bytes:
            self._failed = True
            raise LegacyDiscoveryInventoryError("inventory_byte_limit_exceeded")
        self._observed[key] = {qos}
        self._raw_bytes += added_bytes
        return True

    def finish(self, *, utc_ended: str) -> InventoryObservation:
        if self._failed:
            raise LegacyDiscoveryInventoryError("inventory_builder_failed")
        if self._finished:
            raise LegacyDiscoveryInventoryError("inventory_builder_finished")
        _require_utc(utc_ended)
        self._finished = True
        payloads_by_topic: dict[bytes, set[bytes]] = defaultdict(set)
        for topic, payload in self._observed:
            payloads_by_topic[topic].add(payload)

        records: list[LegacyDiscoveryRecord] = []
        for (topic, payload), qos_values in sorted(self._observed.items()):
            ambiguous = len(payloads_by_topic[topic]) > 1
            records.append(
                _build_record(
                    topic,
                    payload,
                    tuple(sorted(qos_values)),
                    context=self._context,
                    ambiguous_topic=ambiguous,
                )
            )
        return InventoryObservation(
            context=self._context,
            utc_started=self._utc_started,
            utc_ended=utc_ended,
            window_milliseconds=self._window_milliseconds,
            message_count=self._message_count,
            ignored_message_count=self._ignored_count,
            records=tuple(records),
        )


class LegacyDiscoveryArtifactStore:
    """Owner-only, no-clobber storage for inventory and selection generations."""

    def __init__(
        self,
        root: str | Path,
        *,
        limits: InventoryLimits | None = None,
    ) -> None:
        self._root = Path(root).absolute()
        self._limits = limits or InventoryLimits()
        descriptor = self._open_root()
        os.close(descriptor)

    def write_inventory(
        self,
        observation: InventoryObservation,
        *,
        artifact_id: str | None = None,
    ) -> StoredInventory:
        identifier = _require_inventory_id(
            artifact_id or f"legacy-inventory-{secrets.token_hex(16)}"
        )
        value = _inventory_private_dict(identifier, observation)
        payload = _canonical_json(value)
        verified = self._decode_inventory(identifier, payload, observation.context)
        self._commit_payload(identifier, "inventory", payload)
        return verified

    def read_inventory(
        self,
        artifact_id: str,
        *,
        context: InventoryContext,
    ) -> StoredInventory:
        identifier = _require_inventory_id(artifact_id)
        payload = self._load_payload(identifier, "inventory")
        return self._decode_inventory(identifier, payload, context)

    def write_selection(
        self,
        inventory: StoredInventory,
        request: SelectionRequest,
        *,
        artifact_id: str | None = None,
    ) -> StoredSelection:
        selection = create_selection(inventory, request)
        identifier = _require_selection_id(
            artifact_id or f"legacy-selection-{secrets.token_hex(16)}"
        )
        value = _selection_private_dict(identifier, selection)
        payload = _canonical_json(value)
        verified = self._decode_selection(identifier, payload, selection.context)
        self._commit_payload(identifier, "selection", payload)
        return verified

    def read_selection(
        self,
        artifact_id: str,
        *,
        inventory: StoredInventory,
    ) -> StoredSelection:
        identifier = _require_selection_id(artifact_id)
        payload = self._load_payload(identifier, "selection")
        stored = self._decode_selection(identifier, payload, inventory.context)
        _validate_stored_selection(stored, inventory)
        return stored

    def _decode_inventory(
        self,
        artifact_id: str,
        payload: bytes,
        context: InventoryContext,
    ) -> StoredInventory:
        value = _decode_canonical_json(payload, "inventory_artifact_invalid")
        mapping = _require_mapping(value, "inventory_artifact_invalid")
        _require_exact_keys(
            mapping,
            {
                "artifact_id",
                "bindings",
                "classification_source_commit",
                "coverage",
                "ignored_message_count",
                "message_count",
                "records",
                "schema_version",
                "source_kind",
                "status",
                "utc_ended",
                "utc_started",
                "window_milliseconds",
            },
            "inventory_artifact_invalid",
        )
        if (
            mapping["artifact_id"] != artifact_id
            or mapping["schema_version"] != INVENTORY_SCHEMA_VERSION
            or mapping["classification_source_commit"] != LEGACY_FORMAT_SOURCE_COMMIT
            or mapping["source_kind"] != INVENTORY_SOURCE_KIND
            or mapping["coverage"] != INVENTORY_COVERAGE
            or mapping["status"] != INVENTORY_STATUS
        ):
            raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
        if _decode_bindings(mapping["bindings"], "inventory_artifact_invalid") != (
            context.binding_dict()
        ):
            raise LegacyDiscoveryInventoryError("inventory_binding_mismatch")
        utc_started = _require_utc_value(mapping["utc_started"])
        utc_ended = _require_utc_value(mapping["utc_ended"])
        window = _require_positive_int(mapping["window_milliseconds"], "inventory_artifact_invalid")
        message_count = _require_nonnegative_int(
            mapping["message_count"], "inventory_artifact_invalid"
        )
        ignored_count = _require_nonnegative_int(
            mapping["ignored_message_count"], "inventory_artifact_invalid"
        )
        raw_records = _require_sequence(mapping["records"], "inventory_artifact_invalid")
        if len(raw_records) > self._limits.max_records:
            raise LegacyDiscoveryInventoryError("inventory_artifact_limit_exceeded")
        decoded: list[tuple[bytes, bytes, tuple[int, ...], Mapping[str, object]]] = []
        raw_size = 0
        for raw_record in raw_records:
            record_mapping = _require_mapping(raw_record, "inventory_artifact_invalid")
            topic, record_payload, qos_values, classification = _decode_record_fields(
                record_mapping,
                limits=self._limits,
            )
            raw_size += len(topic) + len(record_payload)
            if raw_size > self._limits.max_artifact_bytes:
                raise LegacyDiscoveryInventoryError("inventory_artifact_limit_exceeded")
            decoded.append((topic, record_payload, qos_values, classification))
        if [(topic, record_payload) for topic, record_payload, _, _ in decoded] != sorted(
            (topic, record_payload) for topic, record_payload, _, _ in decoded
        ):
            raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
        if len({(topic, item) for topic, item, _, _ in decoded}) != len(decoded):
            raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")

        payloads_by_topic: dict[bytes, set[bytes]] = defaultdict(set)
        for topic, record_payload, _, _ in decoded:
            payloads_by_topic[topic].add(record_payload)
        records: list[LegacyDiscoveryRecord] = []
        for topic, record_payload, qos_values, stored_classification in decoded:
            record = _build_record(
                topic,
                record_payload,
                qos_values,
                context=context,
                ambiguous_topic=len(payloads_by_topic[topic]) > 1,
            )
            if stored_classification != record.classification.public_dict():
                raise LegacyDiscoveryInventoryError("inventory_classification_mismatch")
            records.append(record)
        if message_count < len(records) or ignored_count > message_count:
            raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
        observation = InventoryObservation(
            context=context,
            utc_started=utc_started,
            utc_ended=utc_ended,
            window_milliseconds=window,
            message_count=message_count,
            ignored_message_count=ignored_count,
            records=tuple(records),
        )
        return StoredInventory(
            artifact_id=artifact_id,
            artifact_sha256=_digest(payload),
            observation=observation,
        )

    def _decode_selection(
        self,
        artifact_id: str,
        payload: bytes,
        context: InventoryContext,
    ) -> StoredSelection:
        value = _decode_canonical_json(payload, "selection_artifact_invalid")
        mapping = _require_mapping(value, "selection_artifact_invalid")
        _require_exact_keys(
            mapping,
            {
                "artifact_id",
                "bindings",
                "inventory_artifact_id",
                "inventory_sha256",
                "mutation_authorized",
                "records",
                "schema_version",
                "scope",
                "status",
            },
            "selection_artifact_invalid",
        )
        if (
            mapping["artifact_id"] != artifact_id
            or mapping["schema_version"] != SELECTION_SCHEMA_VERSION
            or mapping["scope"] != SELECTION_SCOPE
            or mapping["status"] != SELECTION_STATUS
            or mapping["mutation_authorized"] is not False
        ):
            raise LegacyDiscoveryInventoryError("selection_artifact_invalid")
        if _decode_bindings(mapping["bindings"], "selection_artifact_invalid") != (
            context.binding_dict()
        ):
            raise LegacyDiscoveryInventoryError("selection_binding_mismatch")
        inventory_id = _require_inventory_id(mapping["inventory_artifact_id"])
        inventory_sha = _require_sha256_value(mapping["inventory_sha256"])
        raw_records = _require_sequence(mapping["records"], "selection_artifact_invalid")
        records: list[LegacyDiscoveryRecord] = []
        seen: set[str] = set()
        for raw_record in raw_records:
            record_mapping = _require_mapping(raw_record, "selection_artifact_invalid")
            _require_exact_keys(
                record_mapping,
                {
                    "classification",
                    "payload_sha256",
                    "record_id",
                    "topic_sha256",
                },
                "selection_artifact_invalid",
            )
            record_id = _require_record_id(record_mapping["record_id"])
            if record_id in seen:
                raise LegacyDiscoveryInventoryError("selection_artifact_invalid")
            seen.add(record_id)
            classification = _decode_classification(
                record_mapping["classification"],
                "selection_artifact_invalid",
            )
            if not classification.selection_eligible:
                raise LegacyDiscoveryInventoryError("selection_artifact_invalid")
            topic_sha256 = _require_sha256_value(record_mapping["topic_sha256"])
            payload_sha256 = _require_sha256_value(record_mapping["payload_sha256"])
            records.append(
                SelectedRecord(
                    record_id=record_id,
                    topic_sha256=topic_sha256,
                    payload_sha256=payload_sha256,
                    classification=classification,
                )
            )
        selection = SelectionObservation(
            inventory_artifact_id=inventory_id,
            inventory_sha256=inventory_sha,
            context=context,
            records=tuple(records),
        )
        return StoredSelection(
            artifact_id=artifact_id,
            artifact_sha256=_digest(payload),
            selection=selection,
        )

    def _commit_payload(self, artifact_id: str, kind: str, payload: bytes) -> None:
        if len(payload) > self._limits.max_artifact_bytes:
            raise LegacyDiscoveryInventoryError("artifact_byte_limit_exceeded")
        root_descriptor = self._open_root()
        generation_descriptor = -1
        try:
            try:
                os.mkdir(artifact_id, 0o700, dir_fd=root_descriptor)
            except FileExistsError:
                raise LegacyDiscoveryInventoryError("artifact_already_exists") from None
            except OSError:
                raise LegacyDiscoveryInventoryError("artifact_generation_create_failed") from None
            generation_descriptor = self._open_generation(root_descriptor, artifact_id)
            try:
                os.fchmod(generation_descriptor, 0o700)
                self._validate_generation(root_descriptor, generation_descriptor, artifact_id)
                self._write_private_file(generation_descriptor, "artifact.json", payload)
                os.fsync(generation_descriptor)
                os.fsync(root_descriptor)
                commit_payload = _canonical_json(
                    {
                        "artifact_id": artifact_id,
                        "artifact_sha256": _digest(payload),
                        "kind": kind,
                        "schema_version": 1,
                    }
                )
                self._write_private_file(
                    generation_descriptor,
                    "commit.json",
                    commit_payload,
                )
                os.fsync(generation_descriptor)
                os.fsync(root_descriptor)
                self._validate_generation(root_descriptor, generation_descriptor, artifact_id)
            except LegacyDiscoveryInventoryError:
                raise
            except OSError:
                raise LegacyDiscoveryInventoryError("artifact_durability_unconfirmed") from None
        finally:
            if generation_descriptor >= 0:
                os.close(generation_descriptor)
            os.close(root_descriptor)

    def _load_payload(self, artifact_id: str, kind: str) -> bytes:
        root_descriptor = self._open_root()
        generation_descriptor = -1
        try:
            generation_descriptor = self._open_generation(root_descriptor, artifact_id)
            artifact_payload = self._read_private_file(
                generation_descriptor,
                "artifact.json",
                self._limits.max_artifact_bytes,
            )
            commit_payload = self._read_private_file(
                generation_descriptor,
                "commit.json",
                16 * 1024,
            )
            commit = _require_mapping(
                _decode_canonical_json(commit_payload, "artifact_commit_invalid"),
                "artifact_commit_invalid",
            )
            _require_exact_keys(
                commit,
                {"artifact_id", "artifact_sha256", "kind", "schema_version"},
                "artifact_commit_invalid",
            )
            if (
                commit["artifact_id"] != artifact_id
                or commit["artifact_sha256"] != _digest(artifact_payload)
                or commit["kind"] != kind
                or commit["schema_version"] != 1
            ):
                raise LegacyDiscoveryInventoryError("artifact_commit_invalid")
            return artifact_payload
        finally:
            if generation_descriptor >= 0:
                os.close(generation_descriptor)
            os.close(root_descriptor)

    def _open_root(self) -> int:
        if not hasattr(os, "O_NOFOLLOW"):
            raise LegacyDiscoveryInventoryError("artifact_nofollow_unavailable")
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
        descriptor = -1
        try:
            descriptor = os.open(self._root, flags)
            opened = os.fstat(descriptor)
            named = self._root.lstat()
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise LegacyDiscoveryInventoryError("artifact_root_invalid") from None
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or opened.st_uid != os.geteuid()
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            os.close(descriptor)
            raise LegacyDiscoveryInventoryError("artifact_root_invalid")
        return descriptor

    def _open_generation(self, root_descriptor: int, artifact_id: str) -> int:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0)
        descriptor = -1
        try:
            descriptor = os.open(artifact_id, flags, dir_fd=root_descriptor)
            self._validate_generation(root_descriptor, descriptor, artifact_id)
            return descriptor
        except LegacyDiscoveryInventoryError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise LegacyDiscoveryInventoryError("artifact_generation_invalid") from None

    @staticmethod
    def _validate_generation(
        root_descriptor: int,
        descriptor: int,
        artifact_id: str,
    ) -> None:
        try:
            opened = os.fstat(descriptor)
            named = os.stat(artifact_id, dir_fd=root_descriptor, follow_symlinks=False)
        except OSError:
            raise LegacyDiscoveryInventoryError("artifact_generation_invalid") from None
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o700
            or opened.st_uid != os.geteuid()
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise LegacyDiscoveryInventoryError("artifact_generation_invalid")

    @staticmethod
    def _write_private_file(parent_descriptor: int, name: str, payload: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        descriptor = -1
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_descriptor)
            os.fchmod(descriptor, 0o600)
            _validate_private_file(parent_descriptor, descriptor, name, 0)
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(descriptor)
            _validate_private_file(parent_descriptor, descriptor, name, len(payload))
        except FileExistsError:
            raise LegacyDiscoveryInventoryError("artifact_file_already_exists") from None
        except LegacyDiscoveryInventoryError:
            raise
        except OSError:
            raise LegacyDiscoveryInventoryError("artifact_file_write_failed") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _read_private_file(parent_descriptor: int, name: str, max_bytes: int) -> bytes:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor = -1
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
            metadata = os.fstat(descriptor)
            _validate_private_file(parent_descriptor, descriptor, name, metadata.st_size)
            if metadata.st_size > max_bytes:
                raise LegacyDiscoveryInventoryError("artifact_file_too_large")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            if len(payload) > max_bytes:
                raise LegacyDiscoveryInventoryError("artifact_file_too_large")
            _validate_private_file(parent_descriptor, descriptor, name, len(payload))
            return payload
        except LegacyDiscoveryInventoryError:
            raise
        except OSError:
            raise LegacyDiscoveryInventoryError("artifact_file_invalid") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def create_selection(
    inventory: StoredInventory,
    request: SelectionRequest,
) -> SelectionObservation:
    if request.inventory_artifact_id != inventory.artifact_id:
        raise LegacyDiscoveryInventoryError("selection_inventory_id_mismatch")
    if request.inventory_sha256 != inventory.artifact_sha256:
        raise LegacyDiscoveryInventoryError("selection_inventory_digest_mismatch")
    if dict(request.bindings) != inventory.context.binding_dict():
        raise LegacyDiscoveryInventoryError("selection_context_binding_mismatch")
    records_by_id = {record.record_id: record for record in inventory.records}
    selected: list[SelectedRecord] = []
    for entry in request.records:
        record = records_by_id.get(entry.record_id)
        if record is None:
            raise LegacyDiscoveryInventoryError("selection_record_not_observed")
        if not record.classification.selection_eligible:
            raise LegacyDiscoveryInventoryError("selection_record_not_eligible")
        if record.topic_sha256 != entry.topic_sha256:
            raise LegacyDiscoveryInventoryError("selection_topic_digest_mismatch")
        if record.payload_sha256 != entry.payload_sha256:
            raise LegacyDiscoveryInventoryError("selection_payload_digest_mismatch")
        selected.append(
            SelectedRecord(
                record_id=record.record_id,
                topic_sha256=record.topic_sha256,
                payload_sha256=record.payload_sha256,
                classification=record.classification,
            )
        )
    return SelectionObservation(
        inventory_artifact_id=inventory.artifact_id,
        inventory_sha256=inventory.artifact_sha256,
        context=inventory.context,
        records=tuple(selected),
    )


def _validate_stored_selection(
    stored: StoredSelection,
    inventory: StoredInventory,
) -> None:
    selection = stored.selection
    if (
        selection.inventory_artifact_id != inventory.artifact_id
        or selection.inventory_sha256 != inventory.artifact_sha256
        or selection.context.binding_dict() != inventory.context.binding_dict()
    ):
        raise LegacyDiscoveryInventoryError("selection_artifact_inventory_mismatch")
    inventory_records = {record.record_id: record for record in inventory.records}
    for selected in selection.records:
        record = inventory_records.get(selected.record_id)
        if (
            record is None
            or not record.classification.selection_eligible
            or selected.topic_sha256 != record.topic_sha256
            or selected.payload_sha256 != record.payload_sha256
            or selected.classification != record.classification
        ):
            raise LegacyDiscoveryInventoryError("selection_artifact_record_mismatch")


def load_selection_request(path: str | Path, *, max_bytes: int = 1024 * 1024) -> SelectionRequest:
    selected = Path(path).absolute()
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if not hasattr(os, "O_NOFOLLOW"):
        raise LegacyDiscoveryInventoryError("selection_request_nofollow_unavailable")
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor = os.open(
            selected.parent,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0),
        )
        descriptor = os.open(
            selected.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        named = os.stat(selected.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(named.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            or opened.st_size > max_bytes
        ):
            raise LegacyDiscoveryInventoryError("selection_request_file_invalid")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise LegacyDiscoveryInventoryError("selection_request_file_invalid")
        _validate_private_file(parent_descriptor, descriptor, selected.name, len(payload))
        try:
            value = _strict_json_loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            raise LegacyDiscoveryInventoryError("selection_request_invalid") from None
        return SelectionRequest.from_mapping(value)
    except LegacyDiscoveryInventoryError:
        raise
    except OSError:
        raise LegacyDiscoveryInventoryError("selection_request_file_invalid") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _inventory_private_dict(
    artifact_id: str,
    observation: InventoryObservation,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "bindings": observation.context.binding_dict(),
        "classification_source_commit": LEGACY_FORMAT_SOURCE_COMMIT,
        "coverage": INVENTORY_COVERAGE,
        "ignored_message_count": observation.ignored_message_count,
        "message_count": observation.message_count,
        "records": [record.private_dict() for record in observation.records],
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "source_kind": INVENTORY_SOURCE_KIND,
        "status": INVENTORY_STATUS,
        "utc_ended": observation.utc_ended,
        "utc_started": observation.utc_started,
        "window_milliseconds": observation.window_milliseconds,
    }


def _selection_private_dict(
    artifact_id: str,
    selection: SelectionObservation,
) -> dict[str, object]:
    return {
        "artifact_id": artifact_id,
        "bindings": selection.context.binding_dict(),
        "inventory_artifact_id": selection.inventory_artifact_id,
        "inventory_sha256": selection.inventory_sha256,
        "mutation_authorized": False,
        "records": [record.public_dict() for record in selection.records],
        "schema_version": SELECTION_SCHEMA_VERSION,
        "scope": SELECTION_SCOPE,
        "status": SELECTION_STATUS,
    }


def _build_record(
    topic: bytes,
    payload: bytes,
    qos_values: tuple[int, ...],
    *,
    context: InventoryContext,
    ambiguous_topic: bool,
) -> LegacyDiscoveryRecord:
    try:
        topic_text = topic.decode("utf-8")
    except UnicodeError:
        raise LegacyDiscoveryInventoryError("inventory_record_topic_invalid") from None
    parts = _discovery_topic_parts(topic_text, context.discovery_prefix)
    if parts is None or not payload:
        raise LegacyDiscoveryInventoryError("inventory_record_outside_filter")
    platform, object_id = parts
    if not object_id.startswith("jebao_flow_local_"):
        raise LegacyDiscoveryInventoryError("inventory_record_outside_filter")
    classification = _classify(
        platform,
        object_id,
        payload,
        topic_prefix=context.topic_prefix,
        ambiguous_topic=ambiguous_topic,
    )
    return LegacyDiscoveryRecord(
        record_id=_record_id(topic, payload),
        topic=topic,
        payload=payload,
        qos_values=qos_values,
        retain=True,
        classification=classification,
    )


def _classify(
    platform: str,
    object_id: str,
    payload: bytes,
    *,
    topic_prefix: str,
    ambiguous_topic: bool,
) -> RecordClassification:
    if _CURRENT_NAMESPACE_RE.fullmatch(object_id):
        generation = "current_namespace"
    elif _OBJECT_ID_RE.fullmatch(object_id):
        generation = "pre_namespace"
    else:
        generation = "unknown_identity"

    family = _legacy_family(platform, object_id) if generation == "pre_namespace" else None
    payload_shape = "valid"
    unique_id_matches = False
    prefix_binding = "missing"
    try:
        decoded = _strict_json_loads(payload.decode("utf-8"))
        _validate_json_shape(decoded)
        if not isinstance(decoded, dict):
            raise ValueError
        unique_id = decoded.get("unique_id")
        if not isinstance(unique_id, str) or _PRINTABLE_ASCII_RE.fullmatch(unique_id) is None:
            raise ValueError
        unique_id_matches = unique_id == object_id
        topic_values = _topic_values(decoded)
        if topic_values:
            marker = f"{topic_prefix}/"
            prefix_binding = (
                "exact" if all(value.startswith(marker) for value in topic_values) else "different"
            )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        payload_shape = "malformed"
        prefix_binding = "unavailable"

    eligible = (
        generation == "pre_namespace"
        and family is not None
        and payload_shape == "valid"
        and unique_id_matches
        and prefix_binding == "exact"
        and not ambiguous_topic
    )
    return RecordClassification(
        identity_generation=generation,
        family=family,
        platform=platform if platform in _ALLOWED_PLATFORMS else "unknown",
        payload_shape=payload_shape,
        topic_prefix_binding=prefix_binding,
        unique_id_matches_object_id=unique_id_matches,
        ambiguous_topic=ambiguous_topic,
        selection_eligible=eligible,
    )


def _legacy_family(platform: str, object_id: str) -> str | None:
    if platform == "sensor" and object_id.startswith("jebao_flow_local_system_"):
        return "system_function"
    if (
        platform == "sensor"
        and object_id.startswith("jebao_flow_local_group_")
        and object_id.endswith("_actual_output")
    ):
        return "group_actual_output"
    if (
        platform == "switch"
        and object_id.startswith("jebao_flow_local_group_")
        and object_id.endswith("_control")
    ):
        return "group_switch"
    if (
        platform == "button"
        and object_id.startswith("jebao_flow_local_group_")
        and object_id.endswith("_resume")
    ):
        return "group_member_resume"
    if platform == "number" and _SLOT_RE.fullmatch(object_id):
        return "schedule_slot"
    if platform in _ALLOWED_PLATFORMS and _OBJECT_ID_RE.fullmatch(object_id):
        return "device_function"
    return None


def _topic_values(value: object) -> tuple[str, ...]:
    found: list[str] = []

    def walk(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in _TOPIC_VALUE_KEYS:
                    if not isinstance(child, str):
                        raise ValueError
                    found.append(child)
                else:
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value)
    return tuple(found)


def _validate_json_shape(value: object) -> None:
    nodes = 0

    def walk(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError
        if item is None or isinstance(item, bool | int):
            return
        if isinstance(item, float):
            if not (float("-inf") < item < float("inf")):
                raise ValueError
            return
        if isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_JSON_STRING_BYTES:
                raise ValueError
            return
        if isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key.encode("utf-8")) > MAX_JSON_STRING_BYTES:
                    raise ValueError
                walk(child, depth + 1)
            return
        raise ValueError

    walk(value, 0)


def _decode_record_fields(
    value: Mapping[str, object],
    *,
    limits: InventoryLimits,
) -> tuple[bytes, bytes, tuple[int, ...], Mapping[str, object]]:
    _require_exact_keys(
        value,
        {
            "classification",
            "payload_base64",
            "payload_sha256",
            "qos_values",
            "record_id",
            "retain",
            "topic_sha256",
            "topic_utf8_base64",
        },
        "inventory_artifact_invalid",
    )
    topic = _decode_base64(value["topic_utf8_base64"], "inventory_artifact_invalid")
    payload = _decode_base64(value["payload_base64"], "inventory_artifact_invalid")
    if len(topic) > limits.max_topic_bytes or len(payload) > limits.max_payload_bytes:
        raise LegacyDiscoveryInventoryError("inventory_artifact_limit_exceeded")
    try:
        topic.decode("utf-8")
    except UnicodeError:
        raise LegacyDiscoveryInventoryError("inventory_artifact_invalid") from None
    record_id = _require_record_id(value["record_id"])
    if (
        record_id != _record_id(topic, payload)
        or value["topic_sha256"] != _digest(topic)
        or value["payload_sha256"] != _digest(payload)
        or value["retain"] is not True
    ):
        raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
    raw_qos = _require_sequence(value["qos_values"], "inventory_artifact_invalid")
    if not raw_qos or any(type(qos) is not int or qos not in {0, 1, 2} for qos in raw_qos):
        raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
    qos_values = tuple(raw_qos)
    if qos_values != tuple(sorted(set(qos_values))):
        raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
    classification = _require_mapping(value["classification"], "inventory_artifact_invalid")
    _decode_classification(classification, "inventory_artifact_invalid")
    return topic, payload, qos_values, classification


def _decode_classification(value: object, code: str) -> RecordClassification:
    mapping = _require_mapping(value, code)
    expected = {
        "ambiguous_topic",
        "family",
        "identity_generation",
        "payload_shape",
        "platform",
        "selection_eligible",
        "topic_prefix_binding",
        "unique_id_matches_object_id",
    }
    _require_exact_keys(mapping, expected, code)
    text_fields = (
        "identity_generation",
        "payload_shape",
        "platform",
        "topic_prefix_binding",
    )
    if any(not isinstance(mapping[key], str) for key in text_fields):
        raise LegacyDiscoveryInventoryError(code)
    family = mapping["family"]
    if family is not None and not isinstance(family, str):
        raise LegacyDiscoveryInventoryError(code)
    bool_fields = (
        "ambiguous_topic",
        "selection_eligible",
        "unique_id_matches_object_id",
    )
    if any(type(mapping[key]) is not bool for key in bool_fields):
        raise LegacyDiscoveryInventoryError(code)
    try:
        return RecordClassification(
            identity_generation=str(mapping["identity_generation"]),
            family=None if family is None else str(family),
            platform=str(mapping["platform"]),
            payload_shape=str(mapping["payload_shape"]),
            topic_prefix_binding=str(mapping["topic_prefix_binding"]),
            unique_id_matches_object_id=bool(mapping["unique_id_matches_object_id"]),
            ambiguous_topic=bool(mapping["ambiguous_topic"]),
            selection_eligible=bool(mapping["selection_eligible"]),
        )
    except (TypeError, ValueError):
        raise LegacyDiscoveryInventoryError(code) from None


def _selection_request_private_dict(request: SelectionRequest) -> dict[str, object]:
    return {
        "bindings": dict(request.bindings),
        "inventory_artifact_id": request.inventory_artifact_id,
        "inventory_sha256": request.inventory_sha256,
        "records": [entry.public_dict() for entry in request.records],
        "request_kind": "maintainer_exact_observed_record_selection",
        "schema_version": SELECTION_SCHEMA_VERSION,
    }


def selection_request_bytes(request: SelectionRequest) -> bytes:
    """Return canonical request bytes for an operator-controlled private request file."""

    return _canonical_json(_selection_request_private_dict(request))


def _discovery_topic_parts(topic: str, discovery_prefix: str) -> tuple[str, str] | None:
    marker = f"{discovery_prefix}/"
    if not topic.startswith(marker):
        return None
    suffix = topic[len(marker) :]
    pieces = suffix.split("/")
    if len(pieces) != 3 or pieces[2] != "config":
        return None
    platform, object_id, _config = pieces
    if not platform or not object_id:
        return None
    return platform, object_id


def _record_id(topic: bytes, payload: bytes) -> str:
    framed = len(topic).to_bytes(8, "big") + topic + payload
    return f"legacy-record-{_digest(framed)}"


def _canonical_json(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
    except (RecursionError, TypeError, ValueError, UnicodeError):
        raise LegacyDiscoveryInventoryError("artifact_json_invalid") from None


def _strict_json_loads(value: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError
            result[key] = item
        return result

    return json.loads(
        value,
        object_pairs_hook=unique_object,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
    )


def _decode_canonical_json(payload: bytes, code: str) -> object:
    try:
        value = _strict_json_loads(payload.decode("ascii"))
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise LegacyDiscoveryInventoryError(code) from None
    if _canonical_json(value) != payload:
        raise LegacyDiscoveryInventoryError(code)
    return value


def _decode_base64(value: object, code: str) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise LegacyDiscoveryInventoryError(code)
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise LegacyDiscoveryInventoryError(code) from None


def _decode_bindings(value: object, code: str) -> dict[str, str]:
    mapping = _require_mapping(value, code)
    keys = {
        "config_sha256",
        "discovery_prefix_sha256",
        "subscription_filter_sha256",
        "topic_prefix_sha256",
    }
    _require_exact_keys(mapping, keys, code)
    return {key: _require_sha256_value(mapping[key]) for key in sorted(keys)}


def _validate_private_file(
    parent_descriptor: int,
    descriptor: int,
    name: str,
    expected_size: int,
) -> None:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except OSError:
        raise LegacyDiscoveryInventoryError("artifact_file_invalid") from None
    if (
        not stat.S_ISREG(opened.st_mode)
        or stat.S_ISLNK(named.st_mode)
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or opened.st_size != expected_size
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise LegacyDiscoveryInventoryError("artifact_file_invalid")


def _require_topic_prefix(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > 1024
        or _TOPIC_PREFIX_RE.fullmatch(value.strip("/")) is None
    ):
        raise ValueError("MQTT topic prefix must use bounded printable ASCII segments")


def _require_public_ascii(value: object) -> None:
    if isinstance(value, str):
        if _PRINTABLE_ASCII_RE.fullmatch(value) is None:
            raise LegacyDiscoveryInventoryError("public_output_not_ascii")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _require_public_ascii(key)
            _require_public_ascii(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _require_public_ascii(item)


def _require_mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LegacyDiscoveryInventoryError(code)
    return value


def _require_sequence(value: object, code: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise LegacyDiscoveryInventoryError(code)
    return value


def _require_exact_keys(value: Mapping[str, object], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise LegacyDiscoveryInventoryError(code)


def _require_positive_int(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise LegacyDiscoveryInventoryError(code)
    return value


def _require_nonnegative_int(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise LegacyDiscoveryInventoryError(code)
    return value


def _require_inventory_id(value: object) -> str:
    if not isinstance(value, str) or _INVENTORY_ID_RE.fullmatch(value) is None:
        raise LegacyDiscoveryInventoryError("inventory_artifact_id_invalid")
    return value


def _require_selection_id(value: object) -> str:
    if not isinstance(value, str) or _SELECTION_ID_RE.fullmatch(value) is None:
        raise LegacyDiscoveryInventoryError("selection_artifact_id_invalid")
    return value


def _require_record_id(value: object) -> str:
    if not isinstance(value, str) or _RECORD_ID_RE.fullmatch(value) is None:
        raise LegacyDiscoveryInventoryError("selection_record_id_invalid")
    return value


def _require_sha256(value: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("SHA-256 value must be lowercase hexadecimal")


def _require_sha256_value(value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise LegacyDiscoveryInventoryError("sha256_value_invalid")
    return value


def _require_utc(value: str) -> None:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ValueError("UTC timestamp must be bounded RFC3339 text")


def _require_utc_value(value: object) -> str:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise LegacyDiscoveryInventoryError("inventory_artifact_invalid")
    return value


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


__all__ = [
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_MAX_TOPIC_BYTES",
    "INVENTORY_COVERAGE",
    "INVENTORY_SOURCE_KIND",
    "InventoryObservation",
    "InventoryContext",
    "InventoryLimits",
    "LegacyDiscoveryArtifactStore",
    "LegacyDiscoveryInventoryBuilder",
    "LegacyDiscoveryInventoryError",
    "LegacyDiscoveryRecord",
    "LEGACY_FORMAT_SOURCE_COMMIT",
    "RecordClassification",
    "SelectionRequest",
    "SelectionRequestEntry",
    "StoredInventory",
    "StoredSelection",
    "create_selection",
    "load_selection_request",
    "selection_request_bytes",
]
