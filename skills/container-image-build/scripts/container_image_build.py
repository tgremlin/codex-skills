from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
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
BUILDER_SYSTEM_PACKAGES = ["python3-dev", "libffi-dev"]
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


def _needs_archive_unauth(python_target: str) -> bool:
    match = re.match(r"^(\d+)\.(\d+)", python_target)
    if not match:
        return False
    major = int(match.group(1))
    minor = int(match.group(2))
    return major == 3 and minor <= 8


def _default_tmp_dir(repo_dir: Path) -> Path:
    env_dir = os.environ.get("PF_TMP_DIR")
    if env_dir:
        return Path(env_dir)
    return repo_dir / DEFAULT_TMP_DIR_NAME


def _image_digest(client, tag: str) -> Optional[str]:
    try:
        image = client.images.get(tag)
    except DockerException:
        return None
    digests = image.attrs.get("RepoDigests") or []
    return digests[0] if digests else None


def _parse_dataset_info(repo_dir: Path) -> dict[str, Optional[str]]:
    parts = repo_dir.parts
    if "bugsinpy" in parts:
        idx = parts.index("bugsinpy")
        if len(parts) > idx + 3:
            return {
                "dataset": "bugsinpy",
                "project": parts[idx + 1],
                "bug_id": parts[idx + 2],
                "variant": parts[idx + 3],
            }
    return {"dataset": None, "project": None, "bug_id": None, "variant": None}


def _unsupported_registry_path(repo_dir: Path) -> Path:
    info = _parse_dataset_info(repo_dir)
    if info.get("dataset") == "bugsinpy":
        root = Path(*repo_dir.parts[: repo_dir.parts.index("bugsinpy") + 1])
        return root / "unsupported_registry.json"
    return repo_dir / ".pf_manifest" / "unsupported_registry.json"


def _classify_build_failure(log_text: str, error_text: str) -> tuple[str, str, str, bool]:
    text = (log_text or "") + "\n" + (error_text or "")
    lower = text.lower()
    if "profile status is unsupported" in lower or "no install_cmds" in lower:
        return "missing_install_mechanism", "infra_issue", "missing_install_mechanism", True
    if "pkg-resources==0.0.0" in lower:
        return "invalid_requirement", "dataset_metadata_issue", "drop_invalid_requirement", False
    if "pywin32" in lower and (
        "no matching distribution found" in lower
        or "could not find a version that satisfies the requirement" in lower
    ):
        return "platform_incompatible_dependency", "dataset_metadata_issue", "platform_incompatible_dependency", False
    if "no matching distribution found" in lower or "could not find a version that satisfies the requirement" in lower:
        return "invalid_requirement", "dataset_metadata_issue", "invalid_requirement", False
    if "apt-get" in lower and "non-zero code" in lower:
        return "apt_failure", "infra_issue", "apt_failure", True
    return "build_failure", "infra_issue", "build_failure", True


def _update_build_registry(
    repo_dir: Path,
    profile: dict[str, Any],
    failure_reason: str,
    actionability: str,
    suggested_remediation: str,
    retry_on_policy_change: bool,
    report_path: Path,
) -> None:
    if actionability not in {"dataset_metadata_issue", "infra_issue"}:
        return
    info = _parse_dataset_info(repo_dir)
    key = {
        "dataset": info.get("dataset") or "unknown",
        "project": info.get("project") or repo_dir.name,
        "bug_id": info.get("bug_id"),
        "variant": info.get("variant"),
        "failure_class": "build_failure",
        "failure_reason": failure_reason,
        "python_version": profile.get("python_version_target"),
        "registry_bucket": actionability,
        "stage": "image_build",
        "policy_profile": profile.get("policy_profile"),
    }
    registry_path = _unsupported_registry_path(repo_dir)
    registry = {"entries": []}
    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            registry = {"entries": []}
    entries = registry.get("entries") or []

    def _matches(entry: dict[str, Any]) -> bool:
        for k, v in key.items():
            if k == "registry_bucket":
                entry_bucket = entry.get(k) or "dataset_metadata_issue"
                if entry_bucket != v:
                    return False
                continue
            if k == "stage":
                entry_stage = entry.get(k) or "gates_run"
                if entry_stage != v:
                    return False
                continue
            if entry.get(k) != v:
                return False
        return True

    existing = next((entry for entry in entries if _matches(entry)), None)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if existing:
        existing["last_seen_at"] = now
        existing["count"] = int(existing.get("count", 1)) + 1
        existing["report_path"] = str(report_path)
        existing["suggested_remediation"] = suggested_remediation
        existing["retry_on_policy_change"] = retry_on_policy_change
    else:
        entries.append(
            {
                **key,
                "first_seen_at": now,
                "last_seen_at": now,
                "count": 1,
                "report_path": str(report_path),
                "suggested_remediation": suggested_remediation,
                "retry_on_policy_change": retry_on_policy_change,
            }
        )
    registry["entries"] = entries
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(registry_path, json.dumps(registry, indent=2))


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


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def _dockerfile_content(
    profile: dict[str, Any],
    repo_dir: Path,
    builder_tier: bool,
    use_archive_apt: bool,
    allow_unauth: bool,
) -> str:
    python_target = _select_python_version(profile.get("python_version_target"))
    install_cmds = profile.get("install_cmds") or []
    if not isinstance(install_cmds, list):
        raise RuntimeError("profile.install_cmds must be a list")

    env = profile.get("env") or {}
    if not isinstance(env, dict):
        raise RuntimeError("profile.env must be a dict")

    packages = list(BASE_SYSTEM_PACKAGES)
    if builder_tier:
        packages.extend(BUILDER_SYSTEM_PACKAGES)
    system_packages = " ".join(packages)
    lines = [f"FROM python:{python_target}-slim"]
    if use_archive_apt:
        lines.extend(
            [
                "RUN sed -i 's|deb.debian.org/debian|archive.debian.org/debian|g' /etc/apt/sources.list \\",
                "    && sed -i 's|security.debian.org/debian-security|archive.debian.org/debian-security|g' /etc/apt/sources.list \\",
                "    && sed -i '/-updates/d' /etc/apt/sources.list \\",
                "    && echo 'Acquire::Check-Valid-Until \"false\";' > /etc/apt/apt.conf.d/99no-check-valid-until \\",
                "    && echo 'Acquire::AllowInsecureRepositories \"true\";' > /etc/apt/apt.conf.d/99allow-insecure \\",
                "    && echo 'Acquire::AllowDowngradeToInsecureRepositories \"true\";' >> /etc/apt/apt.conf.d/99allow-insecure",
            ]
        )
    install_cmd = "apt-get install -y"
    if use_archive_apt and allow_unauth:
        install_cmd = "apt-get install -y --allow-unauthenticated"
    lines.extend(
        [
            "RUN apt-get update && "
            f"{install_cmd} "
            f"{system_packages} \\"
            "\n    && rm -rf /var/lib/apt/lists/*",
            "WORKDIR /workspace",
            "COPY . /workspace",
        ]
    )

    for key, value in _normalize_env(env):
        escaped = value.replace("\"", "\\\"")
        lines.append(f"ENV {key}=\"{escaped}\"")

    for cmd in install_cmds:
        cmd = cmd.strip()
        if cmd:
            lines.append(f"RUN {cmd}")

    lines.append("")
    return "\n".join(lines)


def _should_retry_with_builder(log_text: str) -> bool:
    patterns = [
        r"longintrepr\.h",
        r"Python\.h",
        r"fatal error: .*: No such file or directory",
        r"Could not build wheels for cffi",
        r"Could not build wheels for typed-ast",
        r"Could not build wheels for yarl",
    ]
    for pattern in patterns:
        if re.search(pattern, log_text):
            return True
    return False


def _should_retry_with_archive(log_text: str) -> bool:
    patterns = [
        r"does not have a Release file",
        r"404  Not Found",
        r"Err:.*debian.*Release",
        r"Err:.*security\.debian\.org",
    ]
    for pattern in patterns:
        if re.search(pattern, log_text):
            return True
    return False


def _should_retry_with_unauth(log_text: str) -> bool:
    patterns = [
        r"unauthenticated packages",
        r"no_pubkey",
        r"not signed",
        r"EXPKEYSIG",
    ]
    for pattern in patterns:
        if re.search(pattern, log_text, re.IGNORECASE):
            return True
    return False


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
        build_log_path = repo_dir / ".pf_manifest" / "image_build" / "build.log"
        _update_build_registry(
            repo_dir,
            profile,
            failure_reason="missing_install_mechanism",
            actionability="infra_issue",
            suggested_remediation="missing_install_mechanism",
            retry_on_policy_change=True,
            report_path=build_log_path,
        )
        raise RuntimeError("Profile status is unsupported; cannot build image.")

    install_cmds = profile.get("install_cmds") or []
    if not install_cmds:
        build_log_path = repo_dir / ".pf_manifest" / "image_build" / "build.log"
        _update_build_registry(
            repo_dir,
            profile,
            failure_reason="missing_install_mechanism",
            actionability="infra_issue",
            suggested_remediation="missing_install_mechanism",
            retry_on_policy_change=True,
            report_path=build_log_path,
        )
        raise RuntimeError("Profile has no install_cmds.")

    image_tag = f"patchfoundry/{profile_id}:latest"
    artifacts_dir = repo_dir / ".pf_manifest" / "image_build"
    manifest_path = artifacts_dir / "manifest.json"
    dockerfile_path = artifacts_dir / "Dockerfile"
    dockerfile_builder_path = artifacts_dir / "Dockerfile.builder"
    dockerfile_archive_path = artifacts_dir / "Dockerfile.archive"
    dockerfile_archive_unauth_path = artifacts_dir / "Dockerfile.archive-unauth"
    dockerfile_builder_archive_path = artifacts_dir / "Dockerfile.builder-archive"
    dockerfile_builder_archive_unauth_path = artifacts_dir / "Dockerfile.builder-archive-unauth"
    python_target = _select_python_version(profile.get("python_version_target"))
    base_image_tag = f"python:{python_target}-slim"
    allow_unauthenticated_apt = bool(profile.get("allow_unauthenticated_apt", True))
    archive_allow_unauth = _needs_archive_unauth(python_target) and allow_unauthenticated_apt
    dockerfile_content = _dockerfile_content(
        profile, repo_dir, builder_tier=False, use_archive_apt=False, allow_unauth=False
    )
    dockerfile_builder_content = _dockerfile_content(
        profile, repo_dir, builder_tier=True, use_archive_apt=False, allow_unauth=False
    )
    dockerfile_archive_content = _dockerfile_content(
        profile, repo_dir, builder_tier=False, use_archive_apt=True, allow_unauth=False
    )
    dockerfile_archive_unauth_content = _dockerfile_content(
        profile, repo_dir, builder_tier=False, use_archive_apt=True, allow_unauth=True
    )
    dockerfile_builder_archive_content = _dockerfile_content(
        profile, repo_dir, builder_tier=True, use_archive_apt=True, allow_unauth=False
    )
    dockerfile_builder_archive_unauth_content = _dockerfile_content(
        profile, repo_dir, builder_tier=True, use_archive_apt=True, allow_unauth=True
    )
    _atomic_write_text(dockerfile_path, dockerfile_content)
    _atomic_write_text(dockerfile_builder_path, dockerfile_builder_content)
    _atomic_write_text(dockerfile_archive_path, dockerfile_archive_content)
    _atomic_write_text(dockerfile_archive_unauth_path, dockerfile_archive_unauth_content)
    _atomic_write_text(dockerfile_builder_archive_path, dockerfile_builder_archive_content)
    _atomic_write_text(
        dockerfile_builder_archive_unauth_path, dockerfile_builder_archive_unauth_content
    )

    client = _get_docker_client()

    build_log_path = artifacts_dir / "build.log"
    cache_dir = None
    if request.image_cache_dir:
        cache_dir = Path(request.image_cache_dir)
    else:
        cache_dir = _default_tmp_dir(repo_dir) / "image-cache"
    if not request.force_rebuild and _image_exists(client, image_tag):
        _atomic_write_text(build_log_path, f"Reused existing image {image_tag}.\n")
        manifest_payload = {}
        if manifest_path.exists():
            try:
                manifest_payload = _read_json(manifest_path)
            except Exception:
                manifest_payload = {}
        base_image_digest = manifest_payload.get("base_image_digest") or _image_digest(
            client, base_image_tag
        )
        policy_profile = profile.get("policy_profile")
        manifest_payload.update(
            {
                "image_tag": image_tag,
                "profile_id": profile_id,
                "reused_cache": True,
                "build_variant": manifest_payload.get("build_variant"),
                "builder_used": manifest_payload.get("builder_used"),
                "archive_used": manifest_payload.get("archive_used"),
                "apt_security_mode": manifest_payload.get("apt_security_mode"),
                "python_version_target": profile.get("python_version_target"),
                "base_image_tag": base_image_tag,
                "base_image_digest": base_image_digest,
                "policy_profile": policy_profile,
            }
        )
        _atomic_write_json(manifest_path, manifest_payload)
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
            manifest_payload = {}
            if manifest_path.exists():
                try:
                    manifest_payload = _read_json(manifest_path)
                except Exception:
                    manifest_payload = {}
            base_image_digest = manifest_payload.get("base_image_digest") or _image_digest(
                client, base_image_tag
            )
            policy_profile = profile.get("policy_profile")
            manifest_payload.update(
                {
                    "image_tag": image_tag,
                    "profile_id": profile_id,
                    "reused_cache": True,
                    "build_variant": manifest_payload.get("build_variant"),
                    "builder_used": manifest_payload.get("builder_used"),
                    "archive_used": manifest_payload.get("archive_used"),
                    "apt_security_mode": manifest_payload.get("apt_security_mode"),
                    "python_version_target": profile.get("python_version_target"),
                    "base_image_tag": base_image_tag,
                    "base_image_digest": base_image_digest,
                    "policy_profile": policy_profile,
                }
            )
            _atomic_write_json(manifest_path, manifest_payload)
            return ImageBuildResponse(
                image_tag=image_tag,
                profile_id=profile_id,
                reused_cache=True,
                build_log_path=str(build_log_path),
            )
        if _image_exists(client, image_tag) and image_tag not in cache_from:
            cache_from.append(image_tag)

    dockerfile_rel = os.path.relpath(dockerfile_path, repo_dir)
    builder_rel = os.path.relpath(dockerfile_builder_path, repo_dir)
    archive_rel = os.path.relpath(dockerfile_archive_path, repo_dir)
    archive_unauth_rel = os.path.relpath(dockerfile_archive_unauth_path, repo_dir)
    builder_archive_rel = os.path.relpath(dockerfile_builder_archive_path, repo_dir)
    builder_archive_unauth_rel = os.path.relpath(
        dockerfile_builder_archive_unauth_path, repo_dir
    )
    build_variant = "standard"
    archive_used = False
    builder_used = False
    apt_security_mode = "standard"
    try:
        try:
            _build_image(
                client,
                repo_dir,
                dockerfile_rel,
                image_tag,
                build_log_path,
                cache_from=cache_from,
                force_rebuild=request.force_rebuild,
            )
        except RuntimeError:
            log_text = ""
            try:
                log_text = Path(build_log_path).read_text(
                    encoding="utf-8", errors="ignore"
                )
            except OSError:
                log_text = ""
            if _should_retry_with_archive(log_text):
                archive_used = True
                if archive_allow_unauth:
                    apt_security_mode = "archive_unauthenticated"
                else:
                    apt_security_mode = "archive"
                _atomic_write_text(
                    build_log_path,
                    log_text
                    + "\n[retry] detected apt repository errors; rebuilding with archive sources\n",
                )
                try:
                    _build_image(
                        client,
                        repo_dir,
                        archive_unauth_rel if archive_allow_unauth else archive_rel,
                        image_tag,
                        build_log_path,
                        cache_from=cache_from,
                        force_rebuild=True,
                    )
                    build_variant = "archive-unauth" if archive_allow_unauth else "archive"
                except RuntimeError:
                    retry_log = ""
                    try:
                        retry_log = Path(build_log_path).read_text(
                            encoding="utf-8", errors="ignore"
                        )
                    except OSError:
                        retry_log = ""
                    if not archive_allow_unauth and _should_retry_with_unauth(retry_log):
                        apt_security_mode = "archive_unauthenticated"
                        _atomic_write_text(
                            build_log_path,
                            retry_log
                            + "\n[retry] detected unauthenticated archive packages; rebuilding with archive + unauthenticated\n",
                        )
                        _build_image(
                            client,
                            repo_dir,
                            archive_unauth_rel,
                            image_tag,
                            build_log_path,
                            cache_from=cache_from,
                            force_rebuild=True,
                        )
                        build_variant = "archive-unauth"
                        retry_log = Path(build_log_path).read_text(
                            encoding="utf-8", errors="ignore"
                        )
                    if _should_retry_with_builder(retry_log):
                        builder_used = True
                        _atomic_write_text(
                            build_log_path,
                            retry_log
                            + "\n[retry] detected missing headers; rebuilding with builder tier + archive sources\n",
                        )
                        _build_image(
                            client,
                            repo_dir,
                            builder_archive_unauth_rel
                            if apt_security_mode == "archive_unauthenticated"
                            else builder_archive_rel,
                            image_tag,
                            build_log_path,
                            cache_from=cache_from,
                            force_rebuild=True,
                        )
                        build_variant = (
                            "builder-archive-unauth"
                            if apt_security_mode == "archive_unauthenticated"
                            else "builder-archive"
                        )
                    else:
                        raise
            elif _should_retry_with_builder(log_text):
                builder_used = True
                _atomic_write_text(
                    build_log_path,
                    log_text
                    + "\n[retry] detected missing headers; rebuilding with builder tier\n",
                )
                _build_image(
                    client,
                    repo_dir,
                    builder_rel,
                    image_tag,
                    build_log_path,
                    cache_from=cache_from,
                    force_rebuild=True,
                )
                build_variant = "builder"
            else:
                raise
    except Exception as exc:
        log_text = ""
        try:
            log_text = Path(build_log_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            log_text = ""
        failure_reason, actionability, suggested, retry_on_policy_change = _classify_build_failure(
            log_text, str(exc)
        )
        _update_build_registry(
            repo_dir,
            profile,
            failure_reason=failure_reason,
            actionability=actionability,
            suggested_remediation=suggested,
            retry_on_policy_change=retry_on_policy_change,
            report_path=build_log_path,
        )
        raise

    if cache_dir:
        cache_path = cache_dir / f"{profile_id}.tar"
        _save_image_cache(client, image_tag, cache_path)

        base_image_digest = _image_digest(client, base_image_tag)
        _atomic_write_json(
            manifest_path,
            {
                "image_tag": image_tag,
                "profile_id": profile_id,
                "reused_cache": False,
                "build_variant": build_variant,
                "builder_used": builder_used,
                "archive_used": archive_used,
                "apt_security_mode": apt_security_mode,
                "python_version_target": profile.get("python_version_target"),
                "base_image_tag": base_image_tag,
                "base_image_digest": base_image_digest,
                "policy_profile": profile.get("policy_profile"),
            },
        )

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
