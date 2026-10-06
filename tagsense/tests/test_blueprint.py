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
