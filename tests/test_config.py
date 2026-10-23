from pathlib import Path

from driver_wattcycle.config import load_config


def test_load_config_defaults() -> None:
    config = load_config()
    assert config.mqtt.port == 1883
    assert config.driver.id == "wattcycle"
    assert config.driver.mac == ""
    assert config.driver.uuid_variant == "auto"
    assert config.driver.hci_adapter == "hci0"
    assert config.driver.connect_timeout_s == 10.0
    assert config.driver.retry_delay_s == 5.0
    assert config.driver.name is None


def test_load_config_local_override(tmp_path: Path) -> None:
    local = tmp_path / "config.ini"
    local.write_text("[mqtt]\nhost = 192.168.1.50\n")
    config = load_config(local_path=local)
    assert config.mqtt.host == "192.168.1.50"


def test_load_config_driver_name_override(tmp_path: Path) -> None:
    local = tmp_path / "config.ini"
    local.write_text("[driver]\nid = house\nname = House bank\n")
    config = load_config(local_path=local)
    assert config.driver.id == "house"
    assert config.driver.name == "House bank"
