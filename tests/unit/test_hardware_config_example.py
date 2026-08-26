from pathlib import Path

from jebao_flow.config import load_config
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def test_hardware_example_targets_only_local_wavemaker_pro() -> None:
    config = load_config(Path(__file__).parents[2] / "config.hardware-test.example.yaml")

    writers = [device for device in config.devices if device.control.allow_hardware_writes]

    assert len(writers) == 2
    assert all(device.product_key == LOCAL_WAVEMAKER_PRO.product_key for device in writers)
