from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

try:
    from pydantic import BaseModel, Field, ConfigDict
except Exception:  # pragma: no cover - pydantic v1
    from pydantic import BaseModel, Field
    ConfigDict = None

try:
    import docker
    from docker.errors import DockerException, ImageNotFound, BuildError, APIError
except Exception:  # pragma: no cover - docker not installed
    docker = None
    DockerException = ImageNotFound = BuildError = APIError = Exception


BASE_SYSTEM_PACKAGES = ["git", "build-essential", "curl"]
DEFAULT_PYTHON_VERSION = "3.11"
DEFAULT_TMP_DIR_NAME = ".tmp-test"


class ImageBuildRequest(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    repo_dir: str = Field(..., description="Checked-out repository directory")
    profile_path: str = Field(..., description="Path to repo_profile.json")
    image_cache_dir: Optional[str] = Field(None, description="Optional cache dir for image tar")
    force_rebuild: bool = Field(False, description="Force rebuild even if image exists")


class ImageBuildResponse(BaseModel):
    if ConfigDict:
        model_config = ConfigDict(extra="forbid")
    else:  # pragma: no cover - pydantic v1
        class Config:
            extra = "forbid"

    image_tag: str
    profile_id: str
    reused_cache: bool
    build_log_path: str


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(profile)
    normalized["working_dir"] = "."
    return normalized


def _profile_id(profile: dict[str, Any]) -> str:
    normalized = _normalize_profile(profile)
    digest = hashlib.sha256(_stable_json(normalized).encode("utf-8")).hexdigest()
    return digest


def _select_python_version(target: Optional[str]) -> str:
    if not target:
        return DEFAULT_PYTHON_VERSION

    target = target.strip()
    if re.match(r"^\d+\.\d+\.\d+$", target):
        return target
    if re.match(r"^\d+\.\d+$", target):
        return target

    match = re.search(r"(\d+\.\d+\.\d+)", target)
    if match:
        return match.group(1)
    match = re.search(r"(\d+\.\d+)", target)
    if match:
        return match.group(1)
    match = re.search(r"(\d+)", target)
    if match:
        return f"{match.group(1)}.0"
    return DEFAULT_PYTHON_VERSION


def _default_tmp_dir(repo_dir: Path) -> Path:
    env_dir = os.environ.get("PF_TMP_DIR")
    if env_dir:
        return Path(env_dir)
    return repo_dir / DEFAULT_TMP_DIR_NAME


def _normalize_env(env: dict[str, str]) -> list[tuple[str, str]]:
    return sorted(((key, str(value)) for key, value in env.items()), key=lambda item: item[0])


def _docker_available() -> bool:
    if docker is None:
        return False
    try:
        client = docker.from_env()
        client.ping()
        return True
    except DockerException:
        return False


def _get_docker_client():
    if docker is None:
        raise RuntimeError("docker SDK for Python is not installed.")
    try:
        client = docker.from_env()
        client.ping()
        return client
    except DockerException as exc:
        raise RuntimeError("Docker is not available.") from exc


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _dockerfile_content(profile: dict[str, Any], repo_dir: Path) -> str:
    python_target = _select_python_version(profile.get("python_version_target"))
    install_cmds = profile.get("install_cmds") or []
    if not isinstance(install_cmds, list):
        raise RuntimeError("profile.install_cmds must be a list")

    env = profile.get("env") or {}
    if not isinstance(env, dict):
        raise RuntimeError("profile.env must be a dict")

    system_packages = " ".join(BASE_SYSTEM_PACKAGES)
    lines = [
        f"FROM python:{python_target}-slim",
        "RUN apt-get update && apt-get install -y "
        f"{system_packages} \\"
        "\n    && rm -rf /var/lib/apt/lists/*",
        "WORKDIR /workspace",
        "COPY . /workspace",
    ]

    for key, value in _normalize_env(env):
        escaped = value.replace("\"", "\\\"")
        lines.append(f"ENV {key}=\"{escaped}\"")

    for cmd in install_cmds:
        cmd = cmd.strip()
        if cmd:
            lines.append(f"RUN {cmd}")

    lines.append("")
    return "\n".join(lines)


def _image_exists(client, tag: str) -> bool:
    try:
        client.images.get(tag)
        return True
    except ImageNotFound:
        return False
    except DockerException:
        return False


def _load_image_cache(client, cache_path: Path) -> bool:
    if not cache_path.exists():
        return False
    try:
        data = cache_path.read_bytes()
        client.images.load(data)
        return True
    except DockerException:
        return False


def _save_image_cache(client, tag: str, cache_path: Path) -> None:
    try:
        image = client.images.get(tag)
    except DockerException:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "wb") as handle:
        for chunk in image.save(named=True):
            handle.write(chunk)


def _build_image(
    client,
    repo_dir: Path,
    dockerfile_rel: str,
    tag: str,
    build_log_path: Path,
    cache_from: Optional[list[str]],
    force_rebuild: bool,
) -> None:
    build_log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(build_log_path, "w", encoding="utf-8") as handle:
        try:
            output = client.api.build(
                path=str(repo_dir),
                dockerfile=dockerfile_rel,
                tag=tag,
                decode=True,
                rm=True,
                forcerm=True,
                nocache=force_rebuild,
                cache_from=cache_from or None,
            )
            for entry in output:
                if "stream" in entry:
                    handle.write(entry["stream"])
                if "error" in entry:
                    raise RuntimeError(entry["error"])
        except (BuildError, APIError, DockerException) as exc:
            raise RuntimeError("Docker build failed.") from exc


def build_container_image(request: ImageBuildRequest) -> ImageBuildResponse:
    repo_dir = Path(request.repo_dir).resolve()
    if not repo_dir.exists():
        raise FileNotFoundError(f"Repo dir '{repo_dir}' does not exist.")
    if not repo_dir.is_dir():
        raise NotADirectoryError(f"Repo dir '{repo_dir}' is not a directory.")

    profile_path = Path(request.profile_path)
    if not profile_path.exists():
        raise FileNotFoundError(f"Profile path '{profile_path}' does not exist.")

    payload = _read_json(profile_path)
    profile = payload.get("profile") or payload
    profile_id = payload.get("profile_id") or _profile_id(profile)

    status = profile.get("status")
    if status == "unsupported":
        raise RuntimeError("Profile status is unsupported; cannot build image.")

    install_cmds = profile.get("install_cmds") or []
    if not install_cmds:
        raise RuntimeError("Profile has no install_cmds.")

    image_tag = f"patchfoundry/{profile_id}:latest"
    artifacts_dir = repo_dir / ".pf_manifest" / "image_build"
    dockerfile_path = artifacts_dir / "Dockerfile"
    dockerfile_content = _dockerfile_content(profile, repo_dir)
    _atomic_write_text(dockerfile_path, dockerfile_content)

    client = _get_docker_client()

    build_log_path = artifacts_dir / "build.log"
    cache_dir = None
    if request.image_cache_dir:
        cache_dir = Path(request.image_cache_dir)
    else:
        cache_dir = _default_tmp_dir(repo_dir) / "image-cache"
    if not request.force_rebuild and _image_exists(client, image_tag):
        _atomic_write_text(build_log_path, f"Reused existing image {image_tag}.\n")
        return ImageBuildResponse(
            image_tag=image_tag,
            profile_id=profile_id,
            reused_cache=True,
            build_log_path=str(build_log_path),
        )

    cache_from = []
    if _image_exists(client, image_tag):
        cache_from.append(image_tag)

    if cache_dir:
        cache_path = cache_dir / f"{profile_id}.tar"
        loaded = _load_image_cache(client, cache_path)
        if loaded and not request.force_rebuild and _image_exists(client, image_tag):
            _atomic_write_text(build_log_path, f"Loaded cached image {image_tag}.\n")
            return ImageBuildResponse(
                image_tag=image_tag,
                profile_id=profile_id,
                reused_cache=True,
                build_log_path=str(build_log_path),
            )
        if _image_exists(client, image_tag) and image_tag not in cache_from:
            cache_from.append(image_tag)

    _build_image(
        client,
        repo_dir,
        os.path.relpath(dockerfile_path, repo_dir),
        image_tag,
        build_log_path,
        cache_from=cache_from,
        force_rebuild=request.force_rebuild,
    )

    if cache_dir:
        cache_path = cache_dir / f"{profile_id}.tar"
        _save_image_cache(client, image_tag, cache_path)

    return ImageBuildResponse(
        image_tag=image_tag,
        profile_id=profile_id,
        reused_cache=False,
        build_log_path=str(build_log_path),
    )


if __name__ == "__main__":
    raw = json.loads(sys.stdin.read())
    req = ImageBuildRequest(**raw)
    resp = build_container_image(req)
    print(json.dumps(_model_dump(resp), indent=2))
