"""Constants for the Jebao Flow Engine integration."""

from homeassistant.const import Platform

DOMAIN = "jebao_flow"

CONF_TOPIC_PREFIX = "topic_prefix"
DEFAULT_TOPIC_PREFIX = "jebao-flow/main"

ATTR_GROUP_ID = "jebao_flow_group_id"
ATTR_CONTROL = "jebao_flow_control"
ATTR_DEVICE_ID = "jebao_flow_device_id"
ATTR_DEVICE_TYPE = "jebao_flow_device_type"
ATTR_INSTANCE_ID = "jebao_flow_instance_id"

CARD_URL = "/jebao-flow/jebao-flow-card.js"

PLATFORMS = (
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)
