import json
from unittest.mock import MagicMock

from driver_wattcycle.config import load_config
from driver_wattcycle.driver import identity_topics
from driver_wattcycle.publisher import HEALTH_TOPIC, INFO_TOPIC, Publisher


def test_identity_publishes_driver_only_without_name() -> None:
    config = load_config()
    identity = identity_topics(config)
    assert identity == {"renewvan/battery/wattcycle/driver": json.dumps("wattcycle")}


def test_identity_publishes_name_when_configured(tmp_path) -> None:
    local = tmp_path / "config.ini"
    local.write_text("[driver]\nid = house\nname = House bank\n")
    identity = identity_topics(load_config(local_path=local))
    assert identity == {
        "renewvan/battery/house/driver": '"wattcycle"',
        "renewvan/battery/house/name": '"House bank"',
    }


def test_on_connect_republishes_identity_retained_between_info_and_online() -> None:
    config = load_config()
    identity = identity_topics(config)
    publisher = Publisher(config.mqtt, '{"driver": "wattcycle"}', identity)
    client = MagicMock()
    publisher._on_connect(client, None, {}, 0)
    topics = [c.args[0] for c in client.publish.call_args_list]
    assert topics == [INFO_TOPIC, "renewvan/battery/wattcycle/driver", HEALTH_TOPIC]
    for call in client.publish.call_args_list:
        assert call.kwargs["retain"] is True
