from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from jebao_flow import legacy_discovery_inventory as inventory_module
from jebao_flow.legacy_discovery_inventory import (
    INVENTORY_COVERAGE,
    InventoryContext,
    InventoryLimits,
    LegacyDiscoveryArtifactStore,
    LegacyDiscoveryInventoryBuilder,
    LegacyDiscoveryInventoryError,
    SelectionRequest,
    SelectionRequestEntry,
    create_selection,
    load_selection_request,
    selection_request_bytes,
)

PRIVATE_DISCOVERY_PREFIX = "private-discovery"
PRIVATE_TOPIC_PREFIX = "private-site/main"
PRIVATE_CONFIG = b"private-config-containing-credential-reference"
UTC_START = "2026-09-09T00:00:00.000000Z"
UTC_END = "2026-09-09T00:00:01.000000Z"


def _context(
    *,
    config_bytes: bytes = PRIVATE_CONFIG,
    discovery_prefix: str = PRIVATE_DISCOVERY_PREFIX,
    topic_prefix: str = PRIVATE_TOPIC_PREFIX,
) -> InventoryContext:
    return InventoryContext.build(
        config_bytes=config_bytes,
        discovery_prefix=discovery_prefix,
        topic_prefix=topic_prefix,
    )


def _payload(
    object_id: str,
    *,
    topic_prefix: str = PRIVATE_TOPIC_PREFIX,
    marker: str = "one",
) -> bytes:
    return json.dumps(
        {
            "availability": [{"topic": f"{topic_prefix}/local/availability"}],
            "marker": marker,
            "state_topic": f"{topic_prefix}/local/state",
            "unique_id": object_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _topic(platform: str, object_id: str, *, prefix: str = PRIVATE_DISCOVERY_PREFIX) -> str:
    return f"{prefix}/{platform}/{object_id}/config"


def _builder(
    *,
    context: InventoryContext | None = None,
    limits: InventoryLimits | None = None,
) -> LegacyDiscoveryInventoryBuilder:
    return LegacyDiscoveryInventoryBuilder(
        context or _context(),
        utc_started=UTC_START,
        window_milliseconds=1000,
        limits=limits,
    )


def _observe(
    builder: LegacyDiscoveryInventoryBuilder,
    platform: str,
    object_id: str,
    *,
    payload: bytes | None = None,
    retain: bool = True,
    qos: int = 1,
    prefix: str = PRIVATE_DISCOVERY_PREFIX,
) -> bool:
    return builder.observe(
        topic=_topic(platform, object_id, prefix=prefix),
        payload=_payload(object_id) if payload is None else payload,
        retain=retain,
        qos=qos,
    )


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "private-artifacts"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    return root


def _stored_inventory(tmp_path: Path):
    builder = _builder()
    _observe(builder, "number", "jebao_flow_local_pump_manual_flow")
    observation = builder.finish(utc_ended=UTC_END)
    store = LegacyDiscoveryArtifactStore(_private_root(tmp_path))
    return store, store.write_inventory(
        observation,
        artifact_id="legacy-inventory-" + "a" * 32,
    )


def _request_for(stored, *records) -> SelectionRequest:
    return SelectionRequest(
        inventory_artifact_id=stored.artifact_id,
        inventory_sha256=stored.artifact_sha256,
        bindings=stored.context.binding_dict(),
        records=tuple(
            SelectionRequestEntry(
                record_id=record.record_id,
                topic_sha256=record.topic_sha256,
                payload_sha256=record.payload_sha256,
            )
            for record in records
        ),
    )


def test_capture_filters_before_artifact_and_classifies_six_legacy_families() -> None:
    builder = _builder()
    legacy = {
        ("sensor", "jebao_flow_local_system_device_local_time"): "system_function",
        ("sensor", "jebao_flow_local_group_main_flow_actual_output"): "group_actual_output",
        ("switch", "jebao_flow_local_group_main_flow_control"): "group_switch",
        ("button", "jebao_flow_local_group_main_flow_left_resume"): "group_member_resume",
        ("number", "jebao_flow_local_master_async_slave_slot_00_flow"): "schedule_slot",
        ("number", "jebao_flow_local_pump_manual_flow"): "device_function",
    }
    for (platform, object_id), _family in legacy.items():
        assert _observe(builder, platform, object_id)

    assert not _observe(
        builder,
        "number",
        "jebao_flow_local_live_only",
        retain=False,
    )
    assert not _observe(builder, "number", "jebao_flow_local_deleted", payload=b"")
    assert not builder.observe(
        topic=f"{PRIVATE_DISCOVERY_PREFIX}/number/node/jebao_flow_local_too_deep/config",
        payload=_payload("jebao_flow_local_too_deep"),
        retain=True,
        qos=1,
    )
    assert not builder.observe(
        topic=f"{PRIVATE_DISCOVERY_PREFIX}/sensor/third_party/config",
        payload=b"private third-party bytes",
        retain=True,
        qos=1,
    )
    assert not _observe(
        builder,
        "number",
        "jebao_flow_local_other_prefix",
        prefix="unrelated-discovery",
    )

    observation = builder.finish(utc_ended=UTC_END)

    assert observation.message_count == 11
    assert observation.ignored_message_count == 5
    assert len(observation.records) == 6
    assert {record.classification.family for record in observation.records} == set(legacy.values())
    assert all(record.classification.selection_eligible for record in observation.records)
    assert all(record.retain is True for record in observation.records)


def test_classification_isolates_namespaced_malformed_other_prefix_and_unknown_records() -> None:
    builder = _builder()
    namespaced = f"jebao_flow_local_instance_{'a' * 64}_pump_manual_flow"
    _observe(builder, "number", namespaced)
    _observe(builder, "number", "jebao_flow_local_malformed", payload=b"not-json")
    _observe(
        builder,
        "number",
        "jebao_flow_local_duplicate_key",
        payload=(
            b'{"unique_id":"jebao_flow_local_other","unique_id":"jebao_flow_local_duplicate_key"}'
        ),
    )
    _observe(
        builder,
        "number",
        "jebao_flow_local_other_binding",
        payload=_payload("jebao_flow_local_other_binding", topic_prefix="another/site"),
    )
    _observe(builder, "binary_sensor", "jebao_flow_local_unknown_family")

    records = builder.finish(utc_ended=UTC_END).records
    by_generation = {record.classification.identity_generation for record in records}

    assert "current_namespace" in by_generation
    assert sum(record.classification.payload_shape == "malformed" for record in records) == 2
    assert any(record.classification.topic_prefix_binding == "different" for record in records)
    assert any(record.classification.family is None for record in records)
    assert not any(record.classification.selection_eligible for record in records)


def test_identical_payloads_deduplicate_qos_facts_but_conflicts_are_preserved_and_blocked() -> None:
    object_id = "jebao_flow_local_pump_manual_flow"
    builder = _builder()
    first = _payload(object_id, marker="first")
    second = _payload(object_id, marker="second")
    _observe(builder, "number", object_id, payload=first, qos=0)
    _observe(builder, "number", object_id, payload=first, qos=1)
    _observe(builder, "number", object_id, payload=first, qos=1)
    _observe(builder, "number", object_id, payload=second, qos=1)

    observation = builder.finish(utc_ended=UTC_END)

    assert len(observation.records) == 2
    assert {record.payload for record in observation.records} == {first, second}
    assert next(record for record in observation.records if record.payload == first).qos_values == (
        0,
        1,
    )
    assert observation.ambiguous_topic_count == 1
    assert observation.candidate_count == 0
    assert all(record.classification.ambiguous_topic for record in observation.records)
    assert not any(record.classification.selection_eligible for record in observation.records)


def test_inventory_round_trip_preserves_exact_raw_bytes_and_public_output_is_redacted(
    tmp_path: Path,
) -> None:
    private_marker = "private payload marker ☃"
    object_id = "jebao_flow_local_pump_manual_flow"
    payload = json.dumps(
        {
            "state_topic": f"{PRIVATE_TOPIC_PREFIX}/local/state",
            "unique_id": object_id,
            "value": private_marker,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    builder = _builder()
    _observe(builder, "number", object_id, payload=payload)
    root = _private_root(tmp_path)
    store = LegacyDiscoveryArtifactStore(root)

    stored = store.write_inventory(
        builder.finish(utc_ended=UTC_END),
        artifact_id="legacy-inventory-" + "b" * 32,
    )
    loaded = store.read_inventory(stored.artifact_id, context=_context())

    assert loaded.records[0].topic == _topic("number", object_id).encode("utf-8")
    assert loaded.records[0].payload == payload
    assert loaded.records[0].qos_values == (1,)
    assert loaded.artifact_sha256 == stored.artifact_sha256
    private = json.loads((root / stored.artifact_id / "artifact.json").read_text())
    assert base64.b64decode(private["records"][0]["payload_base64"]) == payload
    assert base64.b64decode(private["records"][0]["topic_utf8_base64"]) == (
        _topic("number", object_id).encode("utf-8")
    )
    public_text = json.dumps(stored.public_dict(), ensure_ascii=True, sort_keys=True)
    assert stored.public_dict()["coverage"] == INVENTORY_COVERAGE
    assert stored.public_dict()["status"] == "bounded_observation_persisted"
    assert private_marker not in public_text
    assert PRIVATE_DISCOVERY_PREFIX not in public_text
    assert PRIVATE_TOPIC_PREFIX not in public_text
    assert str(root) not in public_text
    assert public_text.isascii()
    assert "complete" not in public_text
    assert "absent" not in public_text
    assert "exhaustive" not in public_text


def test_artifact_store_uses_private_modes_single_links_and_no_clobber(tmp_path: Path) -> None:
    store, stored = _stored_inventory(tmp_path)
    directory = store._root / stored.artifact_id

    assert stat.S_IMODE(store._root.stat().st_mode) == 0o700
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for name in ("artifact.json", "commit.json"):
        metadata = (directory / name).stat()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1

    builder = _builder()
    _observe(builder, "number", "jebao_flow_local_second_manual_flow")
    with pytest.raises(LegacyDiscoveryInventoryError, match="artifact_already_exists"):
        store.write_inventory(
            builder.finish(utc_ended=UTC_END),
            artifact_id=stored.artifact_id,
        )
    assert store.read_inventory(stored.artifact_id, context=_context()).artifact_sha256 == (
        stored.artifact_sha256
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        "root_mode",
        "root_symlink",
        "generation_mode",
        "file_mode",
        "file_symlink",
        "hardlink",
    ],
)
def test_store_rejects_unsafe_filesystem_metadata(
    tmp_path: Path,
    unsafe: str,
) -> None:
    if unsafe == "root_symlink":
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        root = tmp_path / "root"
        root.symlink_to(real, target_is_directory=True)
        with pytest.raises(LegacyDiscoveryInventoryError, match="artifact_root_invalid"):
            LegacyDiscoveryArtifactStore(root)
        return
    root = _private_root(tmp_path)
    if unsafe == "root_mode":
        root.chmod(0o755)
        with pytest.raises(LegacyDiscoveryInventoryError, match="artifact_root_invalid"):
            LegacyDiscoveryArtifactStore(root)
        return

    builder = _builder()
    _observe(builder, "number", "jebao_flow_local_pump_manual_flow")
    store = LegacyDiscoveryArtifactStore(root)
    stored = store.write_inventory(
        builder.finish(utc_ended=UTC_END),
        artifact_id="legacy-inventory-" + "c" * 32,
    )
    directory = root / stored.artifact_id
    artifact = directory / "artifact.json"
    if unsafe == "generation_mode":
        directory.chmod(0o755)
    elif unsafe == "file_mode":
        artifact.chmod(0o644)
    elif unsafe == "file_symlink":
        target = tmp_path / "target"
        target.write_text("unchanged", encoding="utf-8")
        artifact.unlink()
        artifact.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(artifact, tmp_path / "second-link")

    with pytest.raises(LegacyDiscoveryInventoryError):
        store.read_inventory(stored.artifact_id, context=_context())


@pytest.mark.parametrize("failure_target", ["file", "parent"])
def test_fsync_fault_never_returns_a_usable_artifact_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_target: str,
) -> None:
    root = _private_root(tmp_path)
    store = LegacyDiscoveryArtifactStore(root)
    builder = _builder()
    _observe(builder, "number", "jebao_flow_local_pump_manual_flow")
    observation = builder.finish(utc_ended=UTC_END)
    real_fsync = inventory_module.os.fsync
    failed = False

    def fail_once(descriptor: int) -> None:
        nonlocal failed
        is_file = stat.S_ISREG(os.fstat(descriptor).st_mode)
        if not failed and (
            (failure_target == "file" and is_file) or (failure_target == "parent" and not is_file)
        ):
            failed = True
            raise OSError("private fault text")
        real_fsync(descriptor)

    monkeypatch.setattr(inventory_module.os, "fsync", fail_once)
    artifact_id = "legacy-inventory-" + "d" * 32

    with pytest.raises(LegacyDiscoveryInventoryError) as raised:
        store.write_inventory(observation, artifact_id=artifact_id)

    assert failed is True
    assert "private fault text" not in str(raised.value)
    with pytest.raises(LegacyDiscoveryInventoryError):
        store.read_inventory(artifact_id, context=_context())


def test_limits_fail_without_truncation_or_partial_artifact(tmp_path: Path) -> None:
    limits = InventoryLimits(
        max_records=1,
        max_topic_bytes=4096,
        max_payload_bytes=4096,
        max_artifact_bytes=8192,
    )
    builder = _builder(limits=limits)
    _observe(builder, "number", "jebao_flow_local_first_manual_flow")

    with pytest.raises(LegacyDiscoveryInventoryError, match="inventory_record_limit_exceeded"):
        _observe(builder, "number", "jebao_flow_local_second_manual_flow")
    with pytest.raises(LegacyDiscoveryInventoryError, match="inventory_builder_failed"):
        builder.finish(utc_ended=UTC_END)

    root = _private_root(tmp_path)
    assert list(root.iterdir()) == []


def test_selection_is_an_exact_digest_bound_subset_and_round_trips_without_raw_bytes(
    tmp_path: Path,
) -> None:
    store, inventory = _stored_inventory(tmp_path)
    record = inventory.records[0]
    request = _request_for(inventory, record)
    request_path = tmp_path / "selection-request.json"
    request_path.write_bytes(selection_request_bytes(request))
    request_path.chmod(0o600)

    loaded_request = load_selection_request(request_path)
    stored = store.write_selection(
        inventory,
        loaded_request,
        artifact_id="legacy-selection-" + "e" * 32,
    )
    loaded = store.read_selection(stored.artifact_id, inventory=inventory)

    assert loaded.public_dict() == stored.public_dict()
    assert loaded.public_dict()["mutation_authorized"] is False
    assert loaded.public_dict()["scope"] == "observed_inventory_subset_only"
    assert loaded.public_dict()["records"] == [
        {
            "classification": record.classification.public_dict(),
            "payload_sha256": record.payload_sha256,
            "record_id": record.record_id,
            "topic_sha256": record.topic_sha256,
        }
    ]
    private_text = (store._root / stored.artifact_id / "artifact.json").read_text()
    assert PRIVATE_DISCOVERY_PREFIX not in private_text
    assert PRIVATE_TOPIC_PREFIX not in private_text
    assert base64.b64encode(record.topic).decode("ascii") not in private_text
    assert base64.b64encode(record.payload).decode("ascii") not in private_text


@pytest.mark.parametrize(
    ("field", "failure"),
    [
        ("inventory_artifact_id", "selection_inventory_id_mismatch"),
        ("inventory_sha256", "selection_inventory_digest_mismatch"),
        ("bindings", "selection_context_binding_mismatch"),
        ("topic_sha256", "selection_topic_digest_mismatch"),
        ("payload_sha256", "selection_payload_digest_mismatch"),
    ],
)
def test_selection_rejects_every_binding_mismatch(
    tmp_path: Path,
    field: str,
    failure: str,
) -> None:
    _store, inventory = _stored_inventory(tmp_path)
    record = inventory.records[0]
    request = _request_for(inventory, record)
    if field == "inventory_artifact_id":
        request = replace(request, inventory_artifact_id="legacy-inventory-" + "f" * 32)
    elif field == "inventory_sha256":
        request = replace(request, inventory_sha256="f" * 64)
    elif field == "bindings":
        request = replace(request, bindings=request.bindings | {"config_sha256": "f" * 64})
    else:
        entry = replace(request.records[0], **{field: "f" * 64})
        request = replace(request, records=(entry,))

    with pytest.raises(LegacyDiscoveryInventoryError, match=failure):
        create_selection(inventory, request)


def test_conflicting_and_namespaced_records_can_never_enter_a_selection(tmp_path: Path) -> None:
    builder = _builder()
    object_id = "jebao_flow_local_pump_manual_flow"
    _observe(builder, "number", object_id, payload=_payload(object_id, marker="one"))
    _observe(builder, "number", object_id, payload=_payload(object_id, marker="two"))
    current = f"jebao_flow_local_instance_{'a' * 64}_pump_manual_flow"
    _observe(builder, "number", current)
    store = LegacyDiscoveryArtifactStore(_private_root(tmp_path))
    inventory = store.write_inventory(builder.finish(utc_ended=UTC_END))

    for record in inventory.records:
        with pytest.raises(LegacyDiscoveryInventoryError, match="selection_record_not_eligible"):
            create_selection(inventory, _request_for(inventory, record))


def test_selection_request_rejects_symlinks_wrong_mode_and_duplicate_ids(tmp_path: Path) -> None:
    _store, inventory = _stored_inventory(tmp_path)
    request = _request_for(inventory, inventory.records[0])
    request_path = tmp_path / "request.json"
    request_path.write_bytes(selection_request_bytes(request))
    request_path.chmod(0o644)
    with pytest.raises(LegacyDiscoveryInventoryError, match="selection_request_file_invalid"):
        load_selection_request(request_path)

    request_path.chmod(0o600)
    link = tmp_path / "request-link.json"
    link.symlink_to(request_path)
    with pytest.raises(LegacyDiscoveryInventoryError, match="selection_request_file_invalid"):
        load_selection_request(link)

    duplicate = replace(request, records=(request.records[0], request.records[0]))
    request_path.write_bytes(selection_request_bytes(duplicate))
    request_path.chmod(0o600)
    with pytest.raises(LegacyDiscoveryInventoryError, match="selection_request_duplicate_record"):
        load_selection_request(request_path)
