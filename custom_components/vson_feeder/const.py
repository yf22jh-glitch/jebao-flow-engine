"""Constants for the VSON Smart feeder integration."""

from datetime import timedelta

DOMAIN = "vson_feeder"
CONF_MAC = "mac"
CONF_DEVICE_ID = "device_id"
CONF_TOKEN = "account_token"
API_BASE = "https://apis.myvsoncloud.com:16082/vc-air-ws-1.0.2/api/air/"
POLL_INTERVAL = timedelta(seconds=30)
PLATFORMS = ("binary_sensor", "sensor", "button")
