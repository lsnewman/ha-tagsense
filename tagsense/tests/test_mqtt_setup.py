"""MQTT setup problems never stop the app, and the panel can say what is wrong."""
import time

from paho.mqtt.reasoncodes import ReasonCode
from paho.mqtt.packettypes import PacketTypes

from app import main as main_mod
from app import mqtt_ha as m
from app.main import App, DEFAULT_OPTIONS
from app.objects import ObjectStore, parse_list

from test_web import FakeMqtt, fake_source, wait_for


def client(cfg):
    return m.MqttClient(cfg, "test", on_connected=lambda first: None, on_setting=lambda *a: None,
                        on_command=lambda *a: None, on_ha_restart=lambda: None)


def test_no_broker_keeps_asking_then_connects(monkeypatch):
    monkeypatch.setattr(m, "BROKER_RETRY_S", 0.05)
    calls = []

    def source():
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("MQTT service not available; is the Mosquitto app installed?")
        return m.MqttConfig("127.0.0.1", 1)
    c = client(source)
    started = []
    monkeypatch.setattr(c.client, "connect_async", lambda *a, **k: started.append(a))
    monkeypatch.setattr(c.client, "loop_start", lambda: None)
    c.start()
    assert wait_for(lambda: c.status()["problem"] == m.NO_BROKER)
    assert "Mosquitto" in c.status()["detail"]
    assert wait_for(lambda: started)                       # a broker appeared: connecting
    assert c.status() == {"connected": False, "problem": None, "detail": None, "broker": "127.0.0.1:1"}
    c._stop.set()


def test_login_refused_unreachable_and_connected():
    c = client(m.MqttConfig("broker", 1883, "u", "p"))
    c.cfg = c._cfg_source()
    c._handle_connect_fail(None, None)
    assert c.status()["problem"] == m.UNREACHABLE and "broker:1883" in c.status()["detail"]
    c._handle_connect(c.client, None, None, ReasonCode(PacketTypes.CONNACK, identifier=135), None)
    assert c.status()["problem"] == m.LOGIN_REFUSED
    sent = []
    c.client.publish = lambda *a, **k: sent.append(a)
    c.client.subscribe = lambda *a, **k: None
    c.pub("tagsense/x", "1")
    assert not sent                                         # not connected: dropped
    c._handle_connect(c.client, None, None, ReasonCode(PacketTypes.CONNACK, identifier=0), None)
    assert c.status()["connected"] and c.status()["problem"] is None
    assert sent                                             # availability published
    c._handle_disconnect(c.client, None, None, ReasonCode(PacketTypes.DISCONNECT, identifier=0), None)
    assert not c.status()["connected"]


def test_app_starts_without_any_broker(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    monkeypatch.setattr(m, "BROKER_RETRY_S", 0.05)
    a = App(dict(DEFAULT_OPTIONS), source_factory=fake_source, data_dir=str(tmp_path))
    a.mqtt.start()
    assert wait_for(lambda: a.mqtt_status()["problem"] == m.NO_BROKER)
    a.mqtt.stop()


class FakeEntities:
    def __init__(self, ids, integration=True):
        self.ids, self.integration = ids, integration

    def registered(self):
        return self.ids

    def mqtt_integration(self):
        return self.integration


class StatusMqtt(FakeMqtt):
    def status(self):
        return {"connected": True, "problem": None, "detail": None, "broker": "core-mosquitto:1883"}


def test_missing_entities_after_the_grace_time(tmp_path):
    ObjectStore(str(tmp_path)).save(parse_list([
        {"name": "Bin", "tag_id": 5, "source": "go2rtc", "go2rtc_stream": "cam"}]))
    a = App(dict(DEFAULT_OPTIONS, go2rtc_url="http://x"), mqtt=StatusMqtt(), source_factory=fake_source,
            data_dir=str(tmp_path))
    a._on_connected(True)
    a.entities = FakeEntities({})
    assert a.mqtt_status()["missing_entities"] == []        # just published: give HA time
    a.objects["bin"].discovered_at = time.monotonic() - main_mod.ENTITY_GRACE_S - 1
    st = a.mqtt_status()
    assert st["missing_entities"] == ["Bin"] and st["mqtt_integration"] is True
    a.entities = FakeEntities({}, integration=False)
    assert a.mqtt_status()["mqtt_integration"] is False
    a.entities = FakeEntities({"tagsense_bin_presence": "binary_sensor.tagsense_bin"})
    assert a.mqtt_status()["missing_entities"] == []
    a.entities = FakeEntities(None)                          # registry unreadable: no claim
    assert a.mqtt_status()["missing_entities"] == []
