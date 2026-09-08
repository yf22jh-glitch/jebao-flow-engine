"""Positive protocol catalog for write-free packet inspection.

Only shapes explicitly listed here are called known. Unknown commands, serial actions and selected
datapoints remain visible as bounded diagnostics rather than being guessed into a capability.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from jebao_flow.operator_capture.model import ProtocolSummary, UnknownRegion
from jebao_flow.protocol.codec import GizwitsCommand, GizwitsFrame
from jebao_flow.protocol.profiles import KNOWN_SCHEMAS
from jebao_flow.protocol.schema import Datapoint, ProductSchema
from jebao_flow.protocol.session import (
    CONTROL_ACTION,
    READ_STATE_ACTION,
    STATE_REPLY_ACTION,
    STATE_REPORT_ACTION,
)


@dataclass(frozen=True, slots=True)
class BinaryCapabilityRange:
    first_datapoint_id: int
    count: int
    value_offset: int
    item_size: int
    name_prefix: str

    def contains(self, datapoint_id: int) -> bool:
        return self.first_datapoint_id <= datapoint_id < self.first_datapoint_id + self.count

    def name_for(self, datapoint_id: int) -> str:
        return f"{self.name_prefix}{datapoint_id - self.first_datapoint_id:02d}"


_BINARY_CAPABILITY_RANGES: dict[str, tuple[BinaryCapabilityRange, ...]] = {
    # Local Wavemaker Pro: 48 app schedule slots, nine bytes each.
    "50dbc92221fd4d33ae69a1fedd43b555": (BinaryCapabilityRange(13, 48, 11, 9, "AutoTime"),),
    # Local Wavemaker: 48 app schedule slots, eight bytes each.
    "1d8c63eaccac4205b92c84d77d5a08fb": (BinaryCapabilityRange(14, 48, 8, 8, "AutoTime"),),
    # DC Pump Pro: 48 app schedule slots, eight bytes each.
    "0696a19599bc484f8e1866f5ccf4ee7e": (BinaryCapabilityRange(10, 48, 9, 8, "AutoTime"),),
    # Aquarium Pump: clock bytes precede 48 six-byte app schedule slots.
    "6a5c47b3ea364ecb841b47f5997a1775": (BinaryCapabilityRange(11, 48, 13, 6, "AutoTime"),),
    # Dosing Pump: four channel schedules, each 24 big-endian four-byte rows.
    "5b3c136fd4b74f3fb2a366a254c76c9a": (BinaryCapabilityRange(16, 4, 7, 96, "ChannelSchedule"),),
}

_SERIAL_COMMANDS = {
    int(GizwitsCommand.SERIAL_TRANSMIT_REQUEST),
    int(GizwitsCommand.SERIAL_TRANSMIT_RESPONSE),
    int(GizwitsCommand.SERIAL_CONTROL_REQUEST),
    int(GizwitsCommand.SERIAL_CONTROL_RESPONSE),
}
_ACTION_NAMES = {
    CONTROL_ACTION: "control",
    READ_STATE_ACTION: "read_state",
    STATE_REPLY_ACTION: "state_reply",
    STATE_REPORT_ACTION: "state_report",
}
_EMPTY_COMMANDS = {
    int(GizwitsCommand.DISCOVER_REQUEST),
    int(GizwitsCommand.PASSCODE_REQUEST),
    int(GizwitsCommand.HEARTBEAT_REQUEST),
    int(GizwitsCommand.HEARTBEAT_RESPONSE),
}
_OPAQUE_COMMANDS = {
    int(GizwitsCommand.DISCOVER_RESPONSE),
    int(GizwitsCommand.PASSCODE_RESPONSE),
    int(GizwitsCommand.LOGIN_REQUEST),
    int(GizwitsCommand.LOGIN_RESPONSE),
}


def summarize_frame(
    frame: GizwitsFrame,
    *,
    product_key: str | None = None,
) -> ProtocolSummary:
    """Describe one decoded frame without returning its wire bytes."""

    try:
        command_name = GizwitsCommand(frame.command).name.lower()
    except ValueError:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=None,
            action=None,
            action_name=None,
            payload_length=len(frame.payload),
            unknown_regions=(
                UnknownRegion(
                    offset=0, length=max(1, len(frame.payload)), reason="unknown_command"
                ),
            ),
        )

    if frame.command in _EMPTY_COMMANDS:
        if frame.payload:
            return _malformed(frame, command_name, "unexpected_command_payload", 0)
        return ProtocolSummary.create(
            disposition="known",
            command=frame.command,
            command_name=command_name,
            action=None,
            action_name=None,
            payload_length=len(frame.payload),
        )
    if frame.command in _OPAQUE_COMMANDS:
        return ProtocolSummary.create(
            disposition="known",
            command=frame.command,
            command_name=command_name,
            action=None,
            action_name=None,
            payload_length=len(frame.payload),
            semantic={"opaque_payload": True},
        )
    if frame.command not in _SERIAL_COMMANDS:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=None,
            action_name=None,
            payload_length=len(frame.payload),
            unknown_regions=(
                UnknownRegion(
                    offset=0,
                    length=max(1, len(frame.payload)),
                    reason="command_not_in_positive_catalog",
                ),
            ),
        )

    if frame.command == GizwitsCommand.SERIAL_CONTROL_REQUEST:
        return _summarize_control_request(frame, command_name, product_key)
    if frame.command == GizwitsCommand.SERIAL_CONTROL_RESPONSE:
        return _summarize_control_response(frame, command_name)
    return _summarize_serial_state(frame, command_name, product_key)


def _summarize_control_request(
    frame: GizwitsFrame,
    command_name: str,
    product_key: str | None,
) -> ProtocolSummary:
    if len(frame.payload) < 5:
        return _malformed(frame, command_name, "control_request_header", 0)
    sequence = int.from_bytes(frame.payload[:4], "big")
    action = frame.payload[4]
    if action != CONTROL_ACTION:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name=_ACTION_NAMES.get(action),
            payload_length=len(frame.payload),
            semantic={"sequence": sequence},
            unknown_regions=(
                UnknownRegion(
                    offset=4, length=len(frame.payload) - 4, reason="unknown_control_action"
                ),
            ),
        )
    if product_key is None:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name="control",
            payload_length=len(frame.payload),
            semantic={"sequence": sequence},
            unknown_regions=(
                UnknownRegion(
                    offset=5,
                    length=max(1, len(frame.payload) - 5),
                    reason="product_binding_required",
                ),
            ),
        )
    schema = KNOWN_SCHEMAS.get(product_key)
    if schema is None:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name="control",
            payload_length=len(frame.payload),
            semantic={"sequence": sequence, "product_known": False},
            unknown_regions=(
                UnknownRegion(
                    offset=5,
                    length=max(1, len(frame.payload) - 5),
                    reason="unknown_product",
                ),
            ),
        )

    expected = 5 + schema.attribute_flags_size + schema.attribute_values_size
    if len(frame.payload) != expected:
        return _malformed(frame, command_name, "control_payload_size", 5, action=action)
    flags = frame.payload[5 : 5 + schema.attribute_flags_size][::-1]
    values = frame.payload[5 + schema.attribute_flags_size :]
    selected_ids = tuple(
        datapoint_id
        for datapoint_id in range(schema.attribute_flags_size * 8)
        if flags[datapoint_id // 8] & (1 << (datapoint_id % 8))
    )
    decoded, unknown = _decode_selected_values(schema, selected_ids, values)
    return ProtocolSummary.create(
        disposition="unknown" if unknown else "known",
        command=frame.command,
        command_name=command_name,
        action=action,
        action_name="control",
        payload_length=len(frame.payload),
        semantic={
            "sequence": sequence,
            "product_key": product_key,
            "selected_datapoint_ids": list(selected_ids),
            "changes": decoded,
        },
        unknown_regions=tuple(unknown),
    )


def _summarize_control_response(frame: GizwitsFrame, command_name: str) -> ProtocolSummary:
    if len(frame.payload) < 4:
        return _malformed(frame, command_name, "control_response_sequence", 0)
    return ProtocolSummary.create(
        disposition="known",
        command=frame.command,
        command_name=command_name,
        action=None,
        action_name=None,
        payload_length=len(frame.payload),
        semantic={
            "sequence": int.from_bytes(frame.payload[:4], "big"),
            "ack_body_length": len(frame.payload) - 4,
            "ack_body_sha256": hashlib.sha256(frame.payload[4:]).hexdigest(),
        },
    )


def _summarize_serial_state(
    frame: GizwitsFrame,
    command_name: str,
    product_key: str | None,
) -> ProtocolSummary:
    if not frame.payload:
        return _malformed(frame, command_name, "serial_action", 0)
    action = frame.payload[0]
    action_name = _ACTION_NAMES.get(action)
    if action_name is None:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name=None,
            payload_length=len(frame.payload),
            unknown_regions=(
                UnknownRegion(offset=0, length=len(frame.payload), reason="unknown_serial_action"),
            ),
        )

    valid_actions = (
        {READ_STATE_ACTION}
        if frame.command == GizwitsCommand.SERIAL_TRANSMIT_REQUEST
        else {STATE_REPLY_ACTION, STATE_REPORT_ACTION}
    )
    if action not in valid_actions:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name=action_name,
            payload_length=len(frame.payload),
            unknown_regions=(
                UnknownRegion(
                    offset=0, length=len(frame.payload), reason="command_action_pair_unknown"
                ),
            ),
        )
    if action == READ_STATE_ACTION:
        if len(frame.payload) != 1:
            return _malformed(frame, command_name, "read_state_payload", 1, action=action)
        return ProtocolSummary.create(
            disposition="known",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name=action_name,
            payload_length=1,
        )

    status = frame.payload[1:]
    semantic: dict[str, object] = {
        "status_length": len(status),
        "status_sha256": hashlib.sha256(status).hexdigest(),
    }
    if product_key is None:
        return ProtocolSummary.create(
            disposition="unknown",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name=action_name,
            payload_length=len(frame.payload),
            semantic=semantic,
            unknown_regions=(
                UnknownRegion(
                    offset=1,
                    length=max(1, len(status)),
                    reason="product_binding_required",
                ),
            ),
        )
    schema = KNOWN_SCHEMAS.get(product_key)
    if schema is None or len(status) != schema.raw_status_size:
        reason = "unknown_product" if schema is None else "status_payload_size"
        return ProtocolSummary.create(
            disposition="unknown" if schema is None else "malformed",
            command=frame.command,
            command_name=command_name,
            action=action,
            action_name=action_name,
            payload_length=len(frame.payload),
            semantic=semantic,
            unknown_regions=(UnknownRegion(offset=1, length=max(1, len(status)), reason=reason),),
        )
    semantic["product_key"] = product_key
    semantic["operational_state"] = {
        name: (
            {
                "length": len(value),
                "value_sha256": hashlib.sha256(value).hexdigest(),
            }
            if isinstance(value, bytes)
            else value
        )
        for name, value in schema.decode_status(status).items()
    }
    return ProtocolSummary.create(
        disposition="known",
        command=frame.command,
        command_name=command_name,
        action=action,
        action_name=action_name,
        payload_length=len(frame.payload),
        semantic=semantic,
    )


def _decode_selected_values(
    schema: ProductSchema,
    selected_ids: tuple[int, ...],
    values: bytes,
) -> tuple[dict[str, object], list[UnknownRegion]]:
    attributes = {attribute.id: attribute for attribute in schema.attributes if attribute.writable}
    binary_ranges = _BINARY_CAPABILITY_RANGES.get(schema.product_key, ())
    decoded: dict[str, object] = {}
    unknown: list[UnknownRegion] = []
    for datapoint_id in selected_ids:
        attribute = attributes.get(datapoint_id)
        if attribute is not None:
            decoded[attribute.name] = _decode_attribute_value(schema, attribute, values)
            continue
        binary_range = next((item for item in binary_ranges if item.contains(datapoint_id)), None)
        if binary_range is None:
            unknown.append(
                UnknownRegion(
                    offset=5,
                    length=1,
                    reason=f"unattributed_datapoint_{datapoint_id}",
                )
            )
            continue
        item_index = datapoint_id - binary_range.first_datapoint_id
        offset = binary_range.value_offset + item_index * binary_range.item_size
        raw = values[offset : offset + binary_range.item_size]
        decoded[binary_range.name_for(datapoint_id)] = {
            "offset": offset,
            "length": len(raw),
            "value_sha256": hashlib.sha256(raw).hexdigest(),
        }
    return decoded, unknown


def _decode_attribute_value(
    schema: ProductSchema,
    attribute: Datapoint,
    values: bytes,
) -> object:
    # ProductSchema owns the audited bit/byte interpretation. Build a same-size observation buffer
    # instead of importing any control encoder or write-capable session.
    padded = values + bytes(max(0, schema.raw_status_size - len(values)))
    selected = schema.decode_status(padded[: schema.raw_status_size])[attribute.name]
    if isinstance(selected, bytes):
        return {
            "length": len(selected),
            "value_sha256": hashlib.sha256(selected).hexdigest(),
        }
    return selected


def _malformed(
    frame: GizwitsFrame,
    command_name: str,
    reason: str,
    offset: int,
    *,
    action: int | None = None,
) -> ProtocolSummary:
    return ProtocolSummary.create(
        disposition="malformed",
        command=frame.command,
        command_name=command_name,
        action=action,
        action_name=_ACTION_NAMES.get(action),
        payload_length=len(frame.payload),
        unknown_regions=(
            UnknownRegion(
                offset=offset,
                length=max(1, len(frame.payload) - offset),
                reason=reason,
            ),
        ),
    )
