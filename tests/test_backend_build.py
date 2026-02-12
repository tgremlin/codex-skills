from swarm_skills.commands.backend_build import Endpoint, _compute_coverage


def test_backend_coverage_pass_case():
    contract = [
        {
            "method": "GET",
            "path": "/api/health",
            "normalized_path": "/api/health",
            "required": True,
            "source_id": "C1",
        },
        {
            "method": "GET",
            "path": "/api/todos",
            "normalized_path": "/api/todos",
            "required": True,
            "source_id": "C2",
        },
    ]
    inventory = [Endpoint(method="GET", path="/api/health"), Endpoint(method="GET", path="/api/todos")]
    coverage = _compute_coverage(contract, inventory)
    assert coverage["missing_required"] == []
    assert coverage["missing_optional"] == []
    assert [row["match_type"] for row in coverage["contract_endpoint_matches"]] == ["exact_match", "exact_match"]


def test_backend_coverage_fail_case_missing_required():
    contract = [
        {
            "method": "GET",
            "path": "/api/health",
            "normalized_path": "/api/health",
            "required": True,
            "source_id": "C1",
        },
        {
            "method": "POST",
            "path": "/api/todos",
            "normalized_path": "/api/todos",
            "required": True,
            "source_id": "C2",
        },
    ]
    inventory = [Endpoint(method="GET", path="/api/health")]
    coverage = _compute_coverage(contract, inventory)
    assert len(coverage["missing_required"]) == 1
    assert coverage["missing_required"][0]["method"] == "POST"


def test_backend_coverage_warn_case_missing_optional_only():
    contract = [
        {
            "method": "GET",
            "path": "/api/health",
            "normalized_path": "/api/health",
            "required": True,
            "source_id": "C1",
        },
        {
            "method": "DELETE",
            "path": "/api/todos/{id}",
            "normalized_path": "/api/todos/{param}",
            "required": False,
            "source_id": "C2",
        },
    ]
    inventory = [Endpoint(method="GET", path="/api/health")]
    coverage = _compute_coverage(contract, inventory)
    assert coverage["missing_required"] == []
    assert len(coverage["missing_optional"]) == 1


def test_backend_coverage_normalized_param_match():
    contract = [
        {
            "method": "PUT",
            "path": "/api/todos/{id}",
            "normalized_path": "/api/todos/{param}",
            "required": True,
            "source_id": "C1",
        }
    ]
    inventory = [Endpoint(method="PUT", path="/api/todos/:todoId")]
    coverage = _compute_coverage(contract, inventory)
    assert coverage["missing_required"] == []
    assert coverage["contract_endpoint_matches"][0]["match_type"] == "normalized_match"
    assert coverage["contract_endpoint_matches"][0]["confidence"] == 0.9


def test_backend_coverage_normalized_trailing_slash_match():
    contract = [
        {
            "method": "GET",
            "path": "/api/todos",
            "normalized_path": "/api/todos",
            "required": True,
            "source_id": "C1",
        }
    ]
    inventory = [Endpoint(method="GET", path="/api/todos/")]
    coverage = _compute_coverage(contract, inventory)
    assert coverage["missing_required"] == []
    assert coverage["contract_endpoint_matches"][0]["match_type"] == "exact_match"


def test_backend_coverage_fuzzy_match_warns_for_required():
    contract = [
        {
            "method": "GET",
            "path": "/api/todos/{id}",
            "normalized_path": "/api/todos/{param}",
            "required": True,
            "source_id": "C1",
        }
    ]
    inventory = [Endpoint(method="GET", path="/api/todo/123")]
    coverage = _compute_coverage(contract, inventory)
    assert coverage["missing_required"] == []
    assert coverage["contract_endpoint_matches"][0]["match_type"] == "fuzzy_match"
    assert len(coverage["required_fuzzy_matches"]) == 1
