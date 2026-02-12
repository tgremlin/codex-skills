from swarm_skills.commands.template_select import choose_template, score_template
from swarm_skills.catalog import load_templates


def test_ambiguous_defaults_to_active_template(tmp_path):
    workspace = tmp_path
    templates_dir = workspace / "templates"
    templates_dir.mkdir(parents=True)

    # Reuse real template manifests from repo via symlink-like copy behavior.
    from pathlib import Path
    import shutil

    repo_root = Path(__file__).resolve().parents[1]
    shutil.copytree(repo_root / "templates", templates_dir, dirs_exist_ok=True)

    templates = load_templates(workspace)
    selected = choose_template(templates, required={})
    assert selected.template.id == "local-node-http-crud"


def test_required_realtime_prefers_matching_template(tmp_path):
    from pathlib import Path
    import shutil

    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path
    shutil.copytree(repo_root / "templates", workspace / "templates")

    templates = load_templates(workspace)
    scores = [score_template(template, {"realtime": True}) for template in templates]
    best = sorted(scores, key=lambda item: (-item.score, item.template.id))[0]
    assert best.template.id == "nextjs-fastapi-postgres"
