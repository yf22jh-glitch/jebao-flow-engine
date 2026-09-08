import hashlib

from jebao_flow.operator_capture.app_catalog import public_app_catalog
from jebao_flow.operator_capture.catalog import summarize_frame
from jebao_flow.protocol.codec import GizwitsCommand, GizwitsFrame
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.protocol.session import CONTROL_ACTION, READ_STATE_ACTION, STATE_REPLY_ACTION


def test_positive_catalog_uses_command_and_action_pair() -> None:
    known = summarize_frame(
        GizwitsFrame(
            command=GizwitsCommand.SERIAL_TRANSMIT_REQUEST,
            payload=bytes([READ_STATE_ACTION]),
        )
    )
    denied_pair = summarize_frame(
        GizwitsFrame(
            command=GizwitsCommand.SERIAL_TRANSMIT_REQUEST,
            payload=bytes([CONTROL_ACTION]),
        )
    )

    assert known.disposition == "known"
    assert known.action_name == "read_state"
    assert denied_pair.disposition == "unknown"
    assert denied_pair.unknown_regions[0].reason == "command_action_pair_unknown"


def test_unknown_command_is_bounded_by_offset_and_length() -> None:
    summary = summarize_frame(GizwitsFrame(command=0x7777, payload=b"abc"))

    assert summary.disposition == "unknown"
    assert summary.unknown_regions[0].public_dict() == {
        "offset": 0,
        "length": 3,
        "reason": "unknown_command",
    }


def test_known_but_unlisted_command_is_not_promoted_by_enum_membership() -> None:
    summary = summarize_frame(GizwitsFrame(command=GizwitsCommand.ONBOARD_REQUEST))

    assert summary.disposition == "unknown"
    assert summary.unknown_regions[0].reason == "command_not_in_positive_catalog"


def test_empty_command_with_payload_is_malformed() -> None:
    summary = summarize_frame(
        GizwitsFrame(command=GizwitsCommand.HEARTBEAT_REQUEST, payload=b"unexpected")
    )

    assert summary.disposition == "malformed"
    assert summary.unknown_regions[0].reason == "unexpected_command_payload"


def test_control_request_requires_product_binding() -> None:
    payload = b"\x00\x00\x00\x01" + bytes([CONTROL_ACTION]) + b"payload"

    summary = summarize_frame(
        GizwitsFrame(command=GizwitsCommand.SERIAL_CONTROL_REQUEST, payload=payload)
    )

    assert summary.disposition == "unknown"
    assert summary.unknown_regions[0].reason == "product_binding_required"


def test_control_request_decodes_selected_operational_value_without_encoder() -> None:
    flags = bytes.fromhex("0000000000000010")
    values = bytearray(LOCAL_WAVEMAKER_PRO.attribute_values_size)
    values[2] = 55
    payload = b"\x00\x00\x00\x01" + bytes([CONTROL_ACTION]) + flags + bytes(values)

    summary = summarize_frame(
        GizwitsFrame(command=GizwitsCommand.SERIAL_CONTROL_REQUEST, payload=payload),
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
    )

    assert summary.disposition == "known"
    assert summary.semantic["selected_datapoint_ids"] == [4]
    assert summary.semantic["changes"] == {"Flow": 55}


def test_state_reply_is_decoded_only_for_exact_product_size() -> None:
    status = bytes(LOCAL_WAVEMAKER_PRO.raw_status_size)
    summary = summarize_frame(
        GizwitsFrame(
            command=GizwitsCommand.SERIAL_TRANSMIT_RESPONSE,
            payload=bytes([STATE_REPLY_ACTION]) + status,
        ),
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
    )

    assert summary.disposition == "known"
    assert summary.semantic["status_sha256"] == hashlib.sha256(status).hexdigest()
    assert summary.semantic["operational_state"]["Flow"] == 0


def test_registered_app_catalog_covers_every_observed_product_family() -> None:
    catalog = public_app_catalog()
    products = {product["family"]: product for product in catalog["products"]}

    assert set(products) == {
        "local_wavemaker_pro",
        "local_wavemaker",
        "dc_pump_pro",
        "aquarium_pump",
        "dosing_pump_four_channel",
    }
    pro_functions = {item["name"]: item for item in products["local_wavemaker_pro"]["functions"]}
    extension = pro_functions["slave_per_slot_flow_extension"]
    assert extension["count"] == 48
    assert extension["confidence"] == "accepted_capture_required"
    assert "UNKNOWN" in extension["caveat"]


def test_public_app_catalog_is_english_ascii_only() -> None:
    catalog = public_app_catalog()

    def assert_ascii(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                assert key.isascii()
                assert_ascii(item)
        elif isinstance(value, list):
            for item in value:
                assert_ascii(item)
        elif isinstance(value, str):
            assert value.isascii()

    assert catalog["language"] == "en"
    assert_ascii(catalog)


def test_full_cached_template_host_bridge_surface_is_cataloged() -> None:
    catalog = public_app_catalog()

    assert set(catalog["host_bridge_calls"]) == {
        "checkHomeOwner",
        "deleteDevice",
        "deviceGroup",
        "editGroup",
        "getDeviceStatus",
        "getSwitchNames",
        "getUniversalMenu",
        "goEditRooms",
        "goToDeviceScenes",
        "goToUpdate",
        "isSupportUniversalMenu",
        "renameDevice",
        "sendCmd",
        "toast",
    }
    shell = {item["name"] for item in catalog["app_shell_functions"]}
    assert {
        "ownership_permission_check",
        "device_group_view",
        "group_edit",
        "device_status_refresh",
        "channel_name_lookup",
        "universal_menu",
        "firmware_update_entry",
    } <= shell


def test_active_template_version_is_not_guessed_from_newest_cache() -> None:
    assert public_app_catalog()["active_template_version"] == "unresolved"
