from __future__ import annotations

import json
from pathlib import Path


def test_swarm_integration_recipe_exists_and_has_required_keys():
    recipe_path = Path("skills/swarm_integration_recipe.json")
    assert recipe_path.exists()

    payload = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["preferred_flow"] in {"option_a", "option_b"}
    assert isinstance(payload["flows"], dict) and payload["flows"]
    assert "option_a" in payload["flows"]
    assert "option_b" in payload["flows"]
    assert "output_contract" in payload
    assert payload["output_contract"].get("primary") == "artifacts/pipeline/latest/pipeline_result.json"
