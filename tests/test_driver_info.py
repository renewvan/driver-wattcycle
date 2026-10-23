import json
from pathlib import Path

from driver_wattcycle.driver_info import build_info
from driver_wattcycle.publisher import DRIVER_KEY, HEALTH_TOPIC, INFO_TOPIC

MANIFEST = Path(__file__).resolve().parent.parent / "manifest.json"


def test_manifest_driver_key_matches_publisher_key() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["driver"] == DRIVER_KEY == "wattcycle"
    assert "device" not in manifest


def test_info_and_health_topics_are_named_for_the_driver() -> None:
    assert INFO_TOPIC == "renewvan/wattcycle/info"
    assert HEALTH_TOPIC == "renewvan/wattcycle/health"


def test_build_info_carries_driver_key_and_version() -> None:
    info = json.loads(build_info("1.2.3", "abc123", MANIFEST))
    assert info["driver"] == "wattcycle"
    assert info["publishes"] == ["battery"]
    assert info["version"] == "1.2.3"
    assert info["board_serial"] == "abc123"
