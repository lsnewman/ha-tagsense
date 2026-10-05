"""Panel user lookup: the ingress header plus Home Assistant's user list, fail closed."""
import json
import sys
import types

import pytest

from app import users
from app.users import HaUser, UserDirectory, fetch_users_ws

HA_USERS = [
    {"id": "u-owner", "name": "Owner", "is_owner": True, "is_active": True, "group_ids": []},
    {"id": "u-admin", "name": "Admin", "is_owner": False, "is_active": True,
     "group_ids": ["system-admin"]},
    {"id": "u-kid", "name": "Kid", "is_owner": False, "is_active": True, "group_ids": ["system-users"]},
    {"id": "u-gone", "name": "Gone", "is_owner": False, "is_active": False,
     "group_ids": ["system-admin"]},
    {"id": "u-sup", "name": "Supervisor", "system_generated": True, "is_active": True,
     "group_ids": ["system-admin"]},
]


class FakeWs:
    def __init__(self, replies):
        self.replies, self.sent, self.closed = list(replies), [], False

    def recv(self):
        return json.dumps(self.replies.pop(0))

    def send(self, data):
        self.sent.append(json.loads(data))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_websocket(monkeypatch):
    holder = {}

    def create_connection(url, timeout):
        holder["url"] = url
        return holder["ws"]
    monkeypatch.setitem(sys.modules, "websocket", types.SimpleNamespace(create_connection=create_connection))
    return holder


def test_ws_protocol_and_admin_rules(fake_websocket):
    ws = fake_websocket["ws"] = FakeWs([
        {"type": "auth_required"}, {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True, "result": HA_USERS}])
    got = {u.id: u for u in fetch_users_ws("TOKEN")}
    assert ws.sent[0] == {"type": "auth", "access_token": "TOKEN"}
    assert ws.sent[1] == {"id": 1, "type": "config/auth/list"} and ws.closed
    assert fake_websocket["url"] == users.WS_URL
    assert got["u-owner"].is_admin and got["u-admin"].is_admin and not got["u-kid"].is_admin
    assert "u-sup" not in got                     # system users are left out


def test_ws_refusals_raise(fake_websocket):
    fake_websocket["ws"] = FakeWs([{"type": "auth_required"}, {"type": "auth_invalid"}])
    with pytest.raises(RuntimeError, match="authentication refused"):
        fetch_users_ws("BAD")
    fake_websocket["ws"] = FakeWs([{"type": "auth_required"}, {"type": "auth_ok"},
                                   {"id": 1, "success": False, "error": {"code": "unauthorized"}}])
    with pytest.raises(RuntimeError, match="unauthorized"):
        fetch_users_ws("TOKEN")


def directory(result):
    calls = []

    def fetch():
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result
    return UserDirectory(fetch=fetch, token="x"), calls


def test_admin_checks_fail_closed():
    d, _ = directory([HaUser("a", "A", True, True), HaUser("k", "K", False, True),
                      HaUser("g", "G", True, False)])
    assert d.is_admin("a")
    assert not d.is_admin("k")             # not in the admin group
    assert not d.is_admin("g")             # inactive
    assert not d.is_admin("nobody") and not d.is_admin(None) and not d.is_admin("")
    broken, _ = directory(ConnectionError("down"))
    assert not broken.is_admin("a") and "down" in broken.error


def test_cache_and_refresh_for_unknown_user():
    d, calls = directory([HaUser("a", "A", True, True)])
    d.is_admin("a"); d.is_admin("a")
    assert len(calls) == 1                 # cached
    d.is_admin("new")                      # unknown id: look again (a user just added)
    assert len(calls) == 2


def test_whoami_api(tmp_path):
    from app.main import App, DEFAULT_OPTIONS
    from app.web import Api
    from test_web import FakeMqtt, fake_source
    app = App(dict(DEFAULT_OPTIONS), mqtt=FakeMqtt(), source_factory=fake_source,
              data_dir=str(tmp_path))
    app.users, _ = directory([HaUser("a", "Alice", True, True), HaUser("k", "Kid", False, True)])
    api = Api(app)
    assert api.whoami({"X-Remote-User-Id": "a", "X-Remote-User-Display-Name": "Alice"}) == {
        "header_present": True, "name": "Alice", "found": True, "admin": True,
        "lookup": app.users.source, "lookup_error": None,
        "users": [{"name": "Alice", "admin": True, "active": True},
                  {"name": "Kid", "admin": False, "active": True}]}
    kid = api.whoami({"X-Remote-User-Id": "k", "X-Remote-User-Name": "kid"})
    assert kid["admin"] is False and kid["users"] == []      # only admins see the list
    w = api.whoami({})
    assert w["header_present"] is False and w["admin"] is False
