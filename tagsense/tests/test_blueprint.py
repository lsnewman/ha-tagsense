"""The shipped blueprint parses and uses the confirm step."""
from pathlib import Path

import pytest
import yaml

BP = Path(__file__).resolve().parents[2] / "blueprints/automation/tagsense/unlock_on_confirmed_code.yaml"


def test_blueprint_parses_and_confirms_before_unlocking():
    if not BP.exists():
        pytest.skip("blueprint not in this checkout")

    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!input", lambda loader, node: ("input", loader.construct_scalar(node)))
    bp = yaml.load(BP.read_text(), Loader=Loader)
    assert set(bp["blueprint"]["input"]) >= {"code_event", "lock_entity"}
    steps = [a.get("action") or next(iter(a)) for a in bp["actions"]]
    assert steps.index("rest_command.tagsense_confirm") < steps.index("lock.unlock")
    # The relock runs in its own parallel branch, apart from the user's extra actions.
    branches = bp["actions"][-1]["parallel"]
    relock = [a.get("action") for a in branches[0]["sequence"]]
    assert "lock.lock" in relock and branches[1]["sequence"] == ("input", "on_confirmed")
    assert bp["actions"][1]["condition"] == "template" and "confirmed" in bp["actions"][1]["value_template"]
    # Panel test scans are skipped before anything else.
    assert "test" in bp["conditions"][0]["value_template"]


def _bp():
    class Loader(yaml.SafeLoader):
        pass
    Loader.add_constructor("!input", lambda loader, node: ("input", loader.construct_scalar(node)))
    return yaml.load(BP.read_text(), Loader=Loader)


def test_blueprint_takes_several_locks_and_relocks_only_unlocked_ones():
    if not BP.exists():
        pytest.skip("blueprint not in this checkout")
    jinja2 = pytest.importorskip("jinja2")
    bp = _bp()
    assert bp["blueprint"]["input"]["lock_entity"]["selector"]["entity"]["multiple"] is True
    states = {"lock.deadbolt": "unlocked", "lock.handle": "locked", "lock.gate": "open"}
    env = jinja2.Environment()
    env.tests["is_state"] = lambda eid, wanted: states.get(eid) in (wanted if isinstance(wanted, list) else [wanted])

    def render(tpl, **ctx):
        return env.from_string(tpl).render(**ctx).strip()

    locks_tpl = bp["variables"]["locks"]
    # One lock (automations made before 0.5.1) or several.
    assert render(locks_tpl, lock_entity="lock.deadbolt") == "['lock.deadbolt']"
    assert render(locks_tpl, lock_entity=["lock.deadbolt", "lock.handle"]) == "['lock.deadbolt', 'lock.handle']"
    relock = bp["actions"][-1]["parallel"][0]["sequence"]
    wait = relock[0]
    assert wait["continue_on_timeout"] is True        # one offline lock cannot stop the relock
    locks = ["lock.deadbolt", "lock.handle", "lock.gate"]
    assert render(wait["wait_template"], locks=locks) == "True"
    still = next(a for a in relock if "variables" in a)["variables"]["still_unlocked"]
    assert render(still, locks=locks) == "['lock.deadbolt', 'lock.gate']"
    assert relock[-1]["action"] == "lock.lock" and "still_unlocked" in relock[-1]["target"]["entity_id"]
