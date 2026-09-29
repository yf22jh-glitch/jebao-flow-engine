"""One attended Pro manual-mode/linked-flow trial phase using journalled scalar writes.

Run from the current local-control container on stdin.  The first invocation is
preview-only; --run performs exactly release, slave Flow, master Mode, relink.
It does not retry an uncertain command or automatically start the next phase.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.device_writer import (
    configured_writer_factory,
    extract_current_pro_schedule_image,
)
from jebao_flow.local_control.install import load_current_private_config
from jebao_flow.local_control.journal import LocalControlJournalStore
from jebao_flow.local_control.lease import LocalControlLease
from jebao_flow.local_control.models import LocalControlIntent
from jebao_flow.local_control.qualification import load_current_qualifications
from jebao_flow.local_control.qualification_producer import load_current_install_manifest
from jebao_flow.local_control_cli import (
    _activate_current_hardware_safety_volume_identity,
    _resolver,
    _runtime_catalog,
)
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule_wire import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY

MASTER_MEMBER = "flow_pro_d7a8"
SLAVE_MEMBER = "flow_pro_d0a4"
MASTER_IDENTITY_SHA256 = "4b60797d5fa86d879410c03a4c508665ee0f7a693fc54bd67c462c9a664bafea"
SLAVE_IDENTITY_SHA256 = "97040a73bd12c3295dfb1cef77f6435949e78cc9e65bd2b28d93742e1e88cb1c"
PHASES = {
    "sine": ("pulse", 38, "sine", 39),
    "pulse": ("sine", 39, "pulse", 40),
}


class _ReportAwareWriter:
    """Keep the normal writer, accepting a slave's action-4 state reports."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def connect_and_read(self):
        return (await self.inner.connect_and_capture(accept_reports=True)).status_payload

    async def readback(self, lease):
        return (await self.inner.capture_readback(lease, accept_reports=True)).status_payload


def _snapshot(capture, journal, target):
    values = get_product_schema(target.product_key).decode_status(capture.status_payload)
    return {
        "action": capture.action,
        "linkage": values["Linkage"],
        "mode": values["Mode"],
        "flow": values["Flow"],
        "frequency": values["Frequency"],
        "power": values["SwitchON"],
        "timer": values["TimerON"],
        "faults": sorted(k for k, v in values.items() if k.startswith("Fault_") and v),
        "image_sha256": hashlib.sha256(
            extract_current_pro_schedule_image(capture.status_payload)
        ).hexdigest(),
        "raw_blob_sha256": journal.install_blob(capture.wire_frame),
    }


async def _fresh_pair(config, resolver, journal, master, slave):
    endpoints = await resolver.resolve_many((master, slave))
    factory = configured_writer_factory(config)
    writers = (factory(endpoints[0]), factory(endpoints[1]))
    try:
        first = await writers[0].connect_and_capture(accept_reports=False)
        second = await writers[1].connect_and_capture(accept_reports=True)
        return (
            _snapshot(first, journal, master),
            _snapshot(second, journal, slave),
        )
    finally:
        await writers[1].close()
        await writers[0].close()


def _require_pair(master, slave, *, master_role):
    if master["action"] != 3 or master["linkage"] != master_role:
        raise ValueError("master role or explicit reply changed")
    if slave["action"] not in (3, 4) or slave["linkage"] != "async_slave":
        raise ValueError("slave role or state response changed")
    if not master["power"] or not slave["power"] or master["faults"] or slave["faults"]:
        raise ValueError("power or fault state changed")


async def _run(phase: str, send: bool):
    _activate_current_hardware_safety_volume_identity()
    print(json.dumps({"event": "safety_identity_loaded"}), flush=True)
    config = load_current_private_config(Path(os.environ["JEBAO_FLOW_CONFIG"]))
    print(json.dumps({"event": "config_loaded"}), flush=True)
    manifest = load_current_install_manifest(Path(os.environ["JEBAO_FLOW_LOCAL_INSTALL_MANIFEST"]))
    print(json.dumps({"event": "manifest_loaded"}), flush=True)
    qualification_path = os.environ.get("JEBAO_FLOW_LOCAL_QUALIFICATIONS", "").strip()
    qualifications = (
        load_current_qualifications(Path(qualification_path)) if qualification_path else ()
    )
    print(json.dumps({"event": "qualifications_loaded"}), flush=True)
    catalog = _runtime_catalog(
        qualifications, manifest.content, config=config, manifest=manifest, write_enabled=True
    )
    print(json.dumps({"event": "inputs_loaded", "phase": phase}), flush=True)
    members = {item.logical_member: item for item in manifest.devices}
    master, slave = members[MASTER_MEMBER], members[SLAVE_MEMBER]
    if (
        master.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
        or slave.product_key != LOCAL_WAVEMAKER_PRO_PRODUCT_KEY
        or master.physical_identity_key != MASTER_IDENTITY_SHA256
        or slave.physical_identity_key != SLAVE_IDENTITY_SHA256
        or master == slave
    ):
        raise ValueError("current manifest does not match the two approved Pro identities")
    journal = LocalControlJournalStore()
    if journal.unfinished_for((master.physical_identity_key, slave.physical_identity_key)):
        raise ValueError("one of the two devices has an unfinished operation")
    print(json.dumps({"event": "journal_clear"}), flush=True)
    resolver = _resolver(config)
    normal_factory = configured_writer_factory(config)

    def writer_factory(endpoint):
        writer = normal_factory(endpoint)
        return _ReportAwareWriter(writer) if endpoint.target == slave else writer

    coordinator = LocalControlCoordinator(
        catalog=catalog,
        resolver=resolver,
        journal=journal,
        lease=LocalControlLease(),
        writer_factory=writer_factory,
    )
    expected_mode, expected_flow, next_mode, next_flow = PHASES[phase]
    base = f"pro_mode_flow_{phase}_20260929_01"
    intents = (
        LocalControlIntent(
            request_id=f"{base}_release", target=master,
            function="linkage_role", value="independent",
        ),
        LocalControlIntent(
            request_id=f"{base}_flow", target=slave,
            function="slave_common_flow", value=next_flow,
        ),
        LocalControlIntent(
            request_id=f"{base}_mode", target=master,
            function="manual_mode", value=next_mode,
        ),
        LocalControlIntent(
            request_id=f"{base}_relink", target=master,
            function="linkage_role", value="master",
        ),
    )
    endpoints = await resolver.resolve_many((master, slave))
    endpoint_by_member = {item.target.logical_member: item for item in endpoints}
    for intent in intents:
        if journal.load(intent.request_id) is not None:
            raise ValueError("a planned request id already exists")
        descriptor = catalog.validate_intent(intent)
        coordinator._require_current_control_authority(
            descriptor, endpoint_by_member[intent.target.logical_member]
        )
    print(json.dumps({"event": "control_authority_checked"}), flush=True)
    bounds = {item.id: item.limits for item in config.devices}[SLAVE_MEMBER]
    if not bounds.min_power <= next_flow <= min(bounds.max_power, 45):
        raise ValueError("target Flow is outside configured bounds or trial ceiling")
    before_master, before_slave = await _fresh_pair(config, resolver, journal, master, slave)
    print(json.dumps({"event": "fresh_pair", "master": before_master,
                      "slave": before_slave}), flush=True)
    _require_pair(before_master, before_slave, master_role="master")
    if before_master["mode"] != expected_mode or before_slave["flow"] != expected_flow:
        raise ValueError("current mode or Flow is not the expected phase baseline")
    images = (before_master["image_sha256"], before_slave["image_sha256"])
    print(json.dumps({"event": "baseline", "phase": phase, "master": before_master,
                      "slave": before_slave, "planned": ["release", "flow", "mode", "relink"]}), flush=True)
    if not send:
        return

    for index, intent in enumerate(intents):
        if index:
            current_master, current_slave = await _fresh_pair(
                config, resolver, journal, master, slave
            )
            _require_pair(current_master, current_slave, master_role="independent")
            if (current_master["image_sha256"], current_slave["image_sha256"]) != images:
                raise ValueError("a schedule image changed during the test")
            if index == 1 and abs(next_flow - current_slave["flow"]) != 1:
                raise ValueError("Flow after release is not one step from target")
            if index >= 2 and current_slave["flow"] != next_flow:
                raise ValueError("slave Flow changed after its verified command")
        result = await coordinator.execute(intent)
        print(json.dumps({"event": "child", "step": index, **result.public_dict()}), flush=True)
        if not result.accepted or result.physical_write_count != 1:
            raise ValueError("child did not verify as one physical write")
    after_master, after_slave = await _fresh_pair(config, resolver, journal, master, slave)
    _require_pair(after_master, after_slave, master_role="master")
    print(json.dumps({"event": "final", "phase": phase, "master": after_master,
                      "slave": after_slave,
                      "schedule_images_unchanged": (
                          after_master["image_sha256"], after_slave["image_sha256"]
                      ) == images,
                      "target_mode_reported": after_master["mode"] == next_mode,
                      "target_flow_reported": after_slave["flow"] == next_flow}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=tuple(PHASES), required=True)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.phase, args.run))
    except Exception as error:
        print(json.dumps({"event": "stopped", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
