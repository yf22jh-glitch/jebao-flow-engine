"""Operator-only Pro Async Flow change using the observed release/change/relink sequence.

This is not a direct write to a linked slave. Each of the three app-shaped scalar
commands uses the existing identity, output-bound, journal and single-send path.
The master is independent between the first and last commands. An uncertain child
is never retried here; its request id remains available to the normal recovery path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import (
    configured_writer_factory,
    extract_current_pro_schedule_image,
)
from jebao_flow.local_control.install import load_current_private_config
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease, LocalControlLeaseBusyError
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.qualification_producer import load_current_install_manifest
from jebao_flow.local_control_cli import (
    _activate_current_hardware_safety_volume_identity,
    _resolver,
    _runtime_catalog,
)
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY


@dataclass(frozen=True, slots=True)
class PumpSnapshot:
    action: int
    linkage: str
    flow: int
    mode: str
    power: bool
    timer: bool
    image_sha256: str
    faults: tuple[str, ...]


def _snapshot(action: int, status: bytes, product_key: str) -> PumpSnapshot:
    values = get_product_schema(product_key).decode_status(status)
    return PumpSnapshot(
        action=action,
        linkage=values["Linkage"],
        flow=values["Flow"],
        mode=values["Mode"],
        power=values["SwitchON"],
        timer=values["TimerON"],
        image_sha256=hashlib.sha256(extract_current_pro_schedule_image(status)).hexdigest(),
        faults=tuple(
            sorted(key for key, value in values.items() if key.startswith("Fault_") and value)
        ),
    )


async def _fresh_pair(config, resolver, master: LocalDeviceBinding, slave: LocalDeviceBinding):
    master_endpoint, slave_endpoint = await resolver.resolve_many((master, slave))
    factory = configured_writer_factory(config)
    master_writer = factory(master_endpoint)
    slave_writer = factory(slave_endpoint)
    try:
        master_capture = await master_writer.connect_and_capture(accept_reports=False)
        master_state = _snapshot(
            master_capture.action, master_capture.status_payload, master.product_key
        )
        # A linked slave usually supplies action 0x04 reports rather than a direct 0x03 reply.
        slave_capture = await slave_writer.connect_and_capture(
            accept_reports=master_state.linkage == "master"
        )
        slave_state = _snapshot(
            slave_capture.action, slave_capture.status_payload, slave.product_key
        )
        return master_state, slave_state
    finally:
        await slave_writer.close()
        await master_writer.close()


def _require_pair(
    master: PumpSnapshot,
    slave: PumpSnapshot,
    *,
    master_role: str,
    expected_flow: int | None = None,
    expected_images: tuple[str, str] | None = None,
) -> None:
    if master.action != 3 or master.linkage != master_role or not master.power or not master.timer:
        raise ValueError("master is not in the required powered scheduled role")
    if (
        slave.action not in (3, 4)
        or slave.linkage != "async_slave"
        or not slave.power
        or slave.timer
    ):
        raise ValueError("slave is not in the required powered Async manual role")
    if master.faults or slave.faults:
        raise ValueError("a pump reports a fault; no Flow change is sent")
    if expected_flow is not None and slave.flow != expected_flow:
        raise ValueError("fresh slave Flow does not match the requested value")
    if (
        expected_images is not None
        and (
            master.image_sha256,
            slave.image_sha256,
        )
        != expected_images
    ):
        raise ValueError("a schedule image changed during the Flow operation")


def _child_id(base: str, suffix: str) -> str:
    value = f"{base}_{suffix}"
    if len(value) > 128:
        raise ValueError("request id is too long for child operations")
    return value


def _emit_progress(phase: str, journal_sha256: str) -> None:
    print(json.dumps({"phase": phase, "journal_sha256": journal_sha256}), flush=True)


async def _execute_after_read_lock(
    coordinator: LocalControlCoordinator,
    journal: LocalControlJournalStore,
    intent: LocalControlIntent,
    *,
    config,
    resolver,
    master: LocalDeviceBinding,
    slave: LocalDeviceBinding,
    master_role: str,
    expected_flow: int,
    expected_images: tuple[str, str],
):
    """Wait only for a pre-journal device lock collision, never for an uncertain send."""

    for attempt in range(8):
        if journal.load(intent.request_id) is not None:
            raise ValueError("a child request id already exists; inspect its durable outcome")
        try:
            return await coordinator.execute(intent)
        except LocalControlLeaseBusyError:
            # Lease acquisition precedes journal creation and the first physical send.
            # A record appearing here is not an admissible retry, even if the exception
            # looked like ordinary read/write lock contention.
            if journal.load(intent.request_id) is not None or attempt == 7:
                raise
            current_master, current_slave = await _fresh_pair(config, resolver, master, slave)
            _require_pair(
                current_master,
                current_slave,
                master_role=master_role,
                expected_flow=expected_flow,
                expected_images=expected_images,
            )
            await asyncio.sleep(1)
    raise AssertionError("bounded lease wait must return or raise")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master-member", required=True)
    parser.add_argument("--slave-member", required=True)
    parser.add_argument("--flow", type=int, required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--write-enabled", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    _activate_current_hardware_safety_volume_identity()
    config = load_current_private_config(Path(os.environ["JEBAO_FLOW_CONFIG"]))
    manifest = load_current_install_manifest(Path(os.environ["JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"]))
    qualification_path = os.environ.get("JEBAO_FLOW_LOCAL_QUALIFICATIONS", "").strip()
    qualifications = (
        load_current_qualifications(Path(qualification_path)) if qualification_path else ()
    )
    catalog = _runtime_catalog(
        qualifications,
        manifest.content,
        config=config,
        manifest=manifest,
        write_enabled=True,
    )
    by_member = {item.logical_member: item for item in manifest.devices}
    master = by_member[args.master_member]
    slave = by_member[args.slave_member]
    if (
        master == slave
        or master.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
        or slave.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
    ):
        raise ValueError("two distinct Pro identities are required")
    request_ids = tuple(
        _child_id(args.request_id, suffix) for suffix in ("release", "flow", "relink")
    )
    intents = (
        LocalControlIntent(
            request_id=request_ids[0], target=master, function="linkage_role", value="independent"
        ),
        LocalControlIntent(
            request_id=request_ids[1], target=slave, function="slave_common_flow", value=args.flow
        ),
        LocalControlIntent(
            request_id=request_ids[2], target=master, function="linkage_role", value="master"
        ),
    )
    journal = LocalControlJournalStore()
    if journal.unfinished_for((master.physical_identity_key, slave.physical_identity_key)):
        raise ValueError("a pump has an unfinished operation; recover it first")
    resolver = _resolver(config)
    endpoints = await resolver.resolve_many((master, slave))
    coordinator = LocalControlCoordinator(
        catalog=catalog,
        resolver=resolver,
        journal=journal,
        lease=LocalControlLease(),
        writer_factory=configured_writer_factory(config),
    )
    for intent, endpoint in zip(intents, (endpoints[0], endpoints[1], endpoints[0]), strict=True):
        coordinator._require_current_control_authority(catalog.validate_intent(intent), endpoint)
    initial_master, initial_slave = await _fresh_pair(config, resolver, master, slave)
    _require_pair(initial_master, initial_slave, master_role="master")
    images = (initial_master.image_sha256, initial_slave.image_sha256)
    limits = {item.id: item.limits for item in config.devices}[slave.logical_member]
    if not limits.min_power <= args.flow <= min(limits.max_power, 45):
        raise ValueError("requested Flow is outside configured bounds or the 45 trial ceiling")
    if abs(args.flow - initial_slave.flow) > 5:
        raise ValueError("requested Flow differs from the fresh value by more than five")
    if initial_slave.flow == args.flow:
        return {"outcome": "already_at_target", "flow": args.flow, "device_writes": 0}
    if not args.write_enabled:
        return {
            "outcome": "preview_only",
            "initial_flow": initial_slave.flow,
            "requested_flow": args.flow,
            "actions": ["master_independent", "slave_flow", "master_relink"],
            "device_writes": 0,
        }
    if any(journal.load(request_id) is not None for request_id in request_ids):
        raise ValueError("a child request id already exists; inspect its durable outcome")

    results = []
    result = await _execute_after_read_lock(
        coordinator,
        journal,
        intents[0],
        config=config,
        resolver=resolver,
        master=master,
        slave=slave,
        master_role="master",
        expected_flow=initial_slave.flow,
        expected_images=images,
    )
    if not result.accepted:
        raise ValueError("master release did not verify")
    results.append(result)
    _emit_progress("master_released", result.journal_sha256)
    released_master, released_slave = await _fresh_pair(config, resolver, master, slave)
    _require_pair(
        released_master,
        released_slave,
        master_role="independent",
        expected_images=images,
    )
    if abs(args.flow - released_slave.flow) > 5:
        raise ValueError("post-release Flow requires a step larger than five")

    result = await _execute_after_read_lock(
        coordinator,
        journal,
        intents[1],
        config=config,
        resolver=resolver,
        master=master,
        slave=slave,
        master_role="independent",
        expected_flow=released_slave.flow,
        expected_images=images,
    )
    if not result.accepted:
        raise ValueError("slave Flow did not verify")
    results.append(result)
    _emit_progress("slave_flow_changed", result.journal_sha256)
    changed_master, changed_slave = await _fresh_pair(config, resolver, master, slave)
    _require_pair(
        changed_master,
        changed_slave,
        master_role="independent",
        expected_flow=args.flow,
        expected_images=images,
    )

    result = await _execute_after_read_lock(
        coordinator,
        journal,
        intents[2],
        config=config,
        resolver=resolver,
        master=master,
        slave=slave,
        master_role="independent",
        expected_flow=args.flow,
        expected_images=images,
    )
    if not result.accepted:
        raise ValueError("master relink did not verify")
    results.append(result)
    _emit_progress("master_relinked", result.journal_sha256)
    final_master, final_slave = await _fresh_pair(config, resolver, master, slave)
    _require_pair(
        final_master,
        final_slave,
        master_role="master",
        expected_flow=args.flow,
        expected_images=images,
    )
    return {
        "outcome": "verified",
        "flow": final_slave.flow,
        "master_role": final_master.linkage,
        "slave_role": final_slave.linkage,
        "device_writes": sum(item.physical_write_count for item in results),
        "journal_sha256": [item.journal_sha256 for item in results],
        "schedule_images_unchanged": True,
    }


def main() -> int:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_run(args))
    except Exception as error:
        # Library errors may contain an endpoint address, so never echo their text.
        print(
            json.dumps(
                {
                    "outcome": "stopped",
                    "error": type(error).__name__,
                    "next_step": "inspect_child_journals_and_fresh_pair_before_retry",
                }
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
