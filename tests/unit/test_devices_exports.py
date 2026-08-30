import subprocess
import sys

_FROZEN_ASYNC_MODULES = {
    "jebao_flow.devices.linkage",
    "jebao_flow.devices.schedule_flow_experiment",
    "jebao_flow.devices.schedule_linkage",
    "jebao_flow.devices.schedule_transaction",
}


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )


def test_public_device_export_map_is_complete_without_resolving_frozen_exports() -> None:
    script = (
        "import jebao_flow.devices as devices; "
        "assert len(devices.__all__) == len(set(devices.__all__)); "
        "assert set(devices.__all__) == set(devices._EXPORTS); "
        "assert all(module.startswith('jebao_flow.devices.') and attribute == name "
        "for name, (module, attribute) in devices._EXPORTS.items())"
    )

    _run_isolated(script)


def test_lan_and_identity_imports_do_not_load_frozen_async_modules() -> None:
    frozen = repr(sorted(_FROZEN_ASYNC_MODULES))
    script = (
        "import sys; "
        "from jebao_flow.devices import LanJebaoDevice, PhysicalDeviceBinding; "
        "assert LanJebaoDevice is not None; "
        "assert PhysicalDeviceBinding is not None; "
        f"frozen = {frozen}; "
        "loaded = sorted(name for name in frozen if name in sys.modules); "
        "assert not loaded, loaded"
    )

    _run_isolated(script)


def test_legacy_device_identity_export_is_the_canonical_runtime_class() -> None:
    script = (
        "from jebao_flow.devices.identity import PhysicalDeviceBinding as legacy; "
        "from jebao_flow.physical_identity import PhysicalDeviceBinding as canonical; "
        "assert legacy is canonical"
    )

    _run_isolated(script)
