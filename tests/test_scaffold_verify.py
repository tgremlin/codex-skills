from pathlib import Path

from swarm_skills.catalog import resolve_template
from swarm_skills.commands.scaffold_verify import _resolve_port


def test_resolve_template_by_id():
    workspace = Path(__file__).resolve().parents[1]
    template = resolve_template("local-node-http-crud", workspace)
    assert template.id == "local-node-http-crud"
    assert template.is_bootable is True


def test_resolve_template_path():
    workspace = Path(__file__).resolve().parents[1]
    template = resolve_template("templates/local-node-http-crud", workspace)
    assert template.path.name == "local-node-http-crud"


def test_resolve_port_auto():
    port = _resolve_port("auto", 3210)
    assert isinstance(port, int)
    assert port > 0


def test_resolve_port_explicit():
    assert _resolve_port("4321", 3210) == 4321
