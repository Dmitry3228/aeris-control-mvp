import json
from datetime import UTC, datetime, timedelta
from itertools import islice

import paho.mqtt.client as mqtt
import pytest

from aeris_simulator import main as simulator_main
from aeris_simulator.fleet import Fleet, create_fleet


NOW = datetime(2026, 10, 2, 8, tzinfo=UTC)


def test_seeded_fleet_has_stable_identifiers_and_telemetry() -> None:
    first = create_fleet(3, seed=17)
    second = create_fleet(3, seed=17)

    assert [device.device_id for device in first.devices] == ["AHU-201", "AHU-202", "AHU-203"]
    assert first.telemetry_messages(sequence=4, timestamp=NOW) == second.telemetry_messages(
        sequence=4, timestamp=NOW
    )


def test_telemetry_message_has_device_topic_and_valid_payload() -> None:
    fleet = create_fleet(1, seed=17)

    [(topic, encoded)] = fleet.telemetry_messages(sequence=0, timestamp=NOW)
    payload = json.loads(encoded)

    assert topic == "devices/AHU-201/telemetry"
    assert payload == {
        "deviceId": "AHU-201",
        "timestamp": "2026-10-02T08:00:00Z",
        "temperature": 19.7,
        "pressure": 5.1,
        "power": 2.5,
        "connected": True,
    }


def test_ahu_204_reopens_a_high_pressure_alarm_after_a_recovery_window() -> None:
    fleet = create_fleet(4, seed=17)
    high = json.loads(fleet.devices[3].telemetry(sequence=0, timestamp=NOW)[1])
    recovered = json.loads(fleet.devices[3].telemetry(sequence=8, timestamp=NOW)[1])
    reopened = json.loads(fleet.devices[3].telemetry(sequence=10, timestamp=NOW)[1])

    assert high["pressure"] > 7.5
    assert recovered["pressure"] < 7.5
    assert reopened["pressure"] > 7.5


def test_command_acknowledgement_is_deterministic() -> None:
    fleet = Fleet(create_fleet(1, seed=17).devices)
    command = json.dumps(
        {"commandId": "12345678-1234-5678-1234-567812345678", "kind": "START", "value": None}
    ).encode()

    first = fleet.acknowledge("devices/AHU-201/commands", command)
    second = fleet.acknowledge("devices/AHU-201/commands", command)

    assert first == second
    assert first is not None
    topic, encoded = first
    assert topic == "devices/AHU-201/command-acks"
    assert json.loads(encoded) == {
        "commandId": "12345678-1234-5678-1234-567812345678",
        "accepted": True,
    }


def test_default_simulation_clock_produces_byte_identical_batches() -> None:
    first = list(
        islice(simulator_main.telemetry_batches(create_fleet(2, seed=17), interval=1), 3)
    )
    second = list(
        islice(simulator_main.telemetry_batches(create_fleet(2, seed=17), interval=1), 3)
    )

    assert first == second
    assert json.loads(first[2][0][1])["timestamp"] == "2026-10-02T08:00:02Z"


def test_recent_history_batches_span_the_previous_hour_without_duplicating_live_start() -> None:
    batches = simulator_main.startup_history_batches(
        create_fleet(1, seed=17),
        end=NOW,
        recent_history_present=False,
        points=12,
        duration=timedelta(hours=1),
    )

    timestamps = [json.loads(batch[0][1])["timestamp"] for batch in batches]
    first_live = next(simulator_main.telemetry_batches(create_fleet(1, seed=17), interval=1, start=NOW))
    live_timestamp = json.loads(first_live[0][1])["timestamp"]

    assert len(batches) == 12
    assert timestamps[0] == "2026-10-02T07:00:00Z"
    assert timestamps[1] == "2026-10-02T07:05:00Z"
    assert timestamps[-1] == "2026-10-02T07:55:00Z"
    assert live_timestamp == "2026-10-02T08:00:00Z"
    assert len({*timestamps, live_timestamp}) == 13


def test_restart_with_persisted_history_skips_recent_history_backfill() -> None:
    batches = simulator_main.startup_history_batches(
        create_fleet(1, seed=17),
        end=NOW,
        recent_history_present=True,
        points=13,
        duration=timedelta(hours=1),
    )

    assert batches == []


class FailedPublish:
    rc = mqtt.MQTT_ERR_NO_CONN


class FailingClient:
    def publish(self, topic: str, payload: bytes, qos: int) -> FailedPublish:
        del topic, payload, qos
        return FailedPublish()


def test_telemetry_publish_failure_is_fail_fast() -> None:
    with pytest.raises(RuntimeError, match="MQTT publish failed"):
        simulator_main.publish_messages(
            FailingClient(),  # type: ignore[arg-type]
            [("devices/AHU-201/telemetry", b"{}")],
        )


def test_acknowledgement_publish_failure_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fleet = create_fleet(1, seed=17)
    client = simulator_main._client(fleet)
    monkeypatch.setattr(client, "publish", FailingClient().publish)
    message = mqtt.MQTTMessage(mid=1)
    message._topic = b"devices/AHU-201/commands"
    message.payload = json.dumps(
        {
            "commandId": "12345678-1234-5678-1234-567812345678",
            "kind": "START",
            "value": None,
        }
    ).encode()
    callback = client.on_message
    assert callback is not None

    callback(client, None, message)

    assert "MQTT publish failed" in caplog.text
