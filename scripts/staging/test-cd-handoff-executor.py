#!/usr/bin/env python3
"""Offline tests for the local handoff-to-installed-bootstrap executor."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import Mock, patch
from pathlib import Path


SCRIPT = Path(__file__).with_name("cd-handoff-executor.py")
SPEC = importlib.util.spec_from_file_location("cd_handoff_executor", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
executor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(executor)
HANDOFF_TEST_PATH = Path(__file__).with_name("test-cd-handoff.py")
HANDOFF_TEST_SPEC = importlib.util.spec_from_file_location("cd_handoff_regression_fixtures", HANDOFF_TEST_PATH)
assert HANDOFF_TEST_SPEC is not None and HANDOFF_TEST_SPEC.loader is not None
handoff_test_fixtures = importlib.util.module_from_spec(HANDOFF_TEST_SPEC)
HANDOFF_TEST_SPEC.loader.exec_module(handoff_test_fixtures)

SOURCE_SHA = "a" * 40
NOW = 2_000_000_000


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def make_handoff(source_sha: str = SOURCE_SHA) -> dict:
    receipt_id = "belib-staging-" + "1" * 64
    workflow_provenance = {
        "authority_ref": "refs/heads/main",
        "authority_sha": "b" * 40,
        "cd_workflow": {
            "path": ".github/workflows/staging-cd.yml",
            "ref": "hiroyuki9614/book_library/.github/workflows/staging-cd.yml@refs/heads/main",
            "sha": "b" * 40,
            "blob_sha": "c" * 40,
        },
        "candidate_workflows": {
            ".github/workflows/staging-ci.yml": {
                "trusted_blob_sha": "d" * 40,
                "candidate_blob_sha": "d" * 40,
                "matches_authoritative_main": True,
            },
            ".github/workflows/release-ci.yml": {
                "trusted_blob_sha": "e" * 40,
                "candidate_blob_sha": "e" * 40,
                "matches_authoritative_main": True,
            },
        },
        "status": "candidate-definitions-identical-to-authoritative-main",
    }
    ci = {
        "trust_class": "candidate-source-ci-only",
        "workflow_path": ".github/workflows/staging-ci.yml",
        "run_id": 501,
        "run_attempt": 1,
        "run_head_sha": "f" * 40,
        "source_sha": source_sha,
        "run_url": "https://github.com/hiroyuki9614/book_library/actions/runs/501",
        "required_jobs": ["Staging Backend"],
    }
    release = {
        **ci,
        "trust_class": "authoritative-workflow-definition-with-candidate-source",
        "workflow_path": ".github/workflows/release-ci.yml",
        "run_id": 601,
        "run_url": "https://github.com/hiroyuki9614/book_library/actions/runs/601",
        "required_jobs": ["Backend tests"],
    }
    return {
        "schema": "belib-staging-handoff/v1",
        "receipt_id": receipt_id,
        "status": "candidate-ci-passed-awaiting-existing-supervisor-verification",
        "source": {"repository": executor.REPOSITORY, "pull_request": executor.PULL_REQUEST,
                   "sha": source_sha},
        "evidence": {
            "staging_ci": ci,
            "release_ci": release,
            "workflow_provenance": workflow_provenance,
            "independent_review": {
                "login": "reviewer",
                "review_id": 8801,
                "author_association": "COLLABORATOR",
                "commit_sha": source_sha,
                "submitted_at": "2026-11-14T00:00:00Z",
            },
            "owner_request_for_supervisor_review": {
                "login": "hiroyuki9614", "actions_run_id": 7001, "actions_run_attempt": 1,
            },
        },
        "lock": {"github_concurrency_group": executor.LOCK_GROUP, "receipt_id": receipt_id},
        "target": {"host": executor.HOST, "principal": executor.PRINCIPAL,
                   "compose_project": executor.PROJECT, "transport": "existing staging supervisor"},
        "supervisor_verification": {"required": True,
                                    "status": "pending-independent-provenance-verification",
                                    "deployment_authorized": False},
        "deployment": {"started": False, "asserted": False, "authorized": False},
    }


def make_grant(source_sha: str = SOURCE_SHA, *, operation: str = "deploy",
               issued_at: int = NOW, expires_at: int = NOW + 300) -> dict:
    payload = {
        **executor.TARGET,
        "schema": "belib-staging-bootstrap-approval/v1",
        "source_sha": source_sha,
        "operation": operation,
        "nonce": "2" * 64,
        "evidence_sha256": "3" * 64,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    return payload


def valid_runtime_readback(source_sha: str = SOURCE_SHA, port: int = 18083) -> dict:
    return {
        "status": "verified",
        "source_sha": source_sha,
        "container_ids": {name: (str(index) * 64) for index, name in enumerate(
            ("backend", "db", "frontend", "nginx"), start=1
        )},
        "image_digests": {name: "sha256:" + (str(index) * 64) for index, name in enumerate(
            ("backend", "db", "frontend", "nginx"), start=1
        )},
        "app_image_revisions": {"backend": source_sha, "frontend": source_sha},
        "nginx_loopback_binding": {"host_ip": "127.0.0.1", "host_port": port, "container_port": "8080/tcp"},
    }


def runtime_snapshot(source_sha: str = SOURCE_SHA, *, wrong_revision: bool = False,
                     wrong_binding: bool = False) -> tuple[list[str], dict[str, dict], dict[str, dict]]:
    services = ("db", "backend", "frontend", "nginx")
    ids = {service: str(index) * 64 for index, service in enumerate(services, start=1)}
    image_ids = {service: "sha256:" + str(index + 4) * 64
                 for index, service in enumerate(services, start=1)}
    containers = {}
    images = {}
    for service in services:
        networks = {executor.ISOLATED_NETWORK: {"NetworkID": "n" * 64}}
        if service == "nginx":
            networks[executor.INGRESS_NETWORK] = {"NetworkID": "m" * 64}
        labels = {"com.docker.compose.project": executor.PROJECT,
                  "com.docker.compose.service": service, "com.docker.compose.oneoff": "False"}
        config_image = {"db": executor.DATABASE_IMAGE, "nginx": executor.NGINX_IMAGE}.get(
            service, f"{executor.PROJECT}-{service}"
        )
        port = "18084" if wrong_binding and service == "nginx" else "18083"
        containers[ids[service]] = {
            "Id": ids[service], "Image": image_ids[service],
            "Config": {"Labels": labels, "Image": config_image},
            "State": {"Status": "running", **({"Health": {"Status": "healthy"}} if service == "db" else {})},
            "NetworkSettings": {"Networks": networks},
            "HostConfig": {"PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]}}
                if service == "nginx" else {"PortBindings": {}},
            "Mounts": [],
        }
        revision = source_sha
        if wrong_revision and service == "backend":
            revision = "b" * 40
        repo_digests = []
        if service == "db":
            repo_digests = ["postgres@" + executor.DATABASE_IMAGE.split("@", 1)[1]]
        elif service == "nginx":
            repo_digests = ["nginx@" + executor.NGINX_IMAGE.split("@", 1)[1]]
        images[image_ids[service]] = {
            "Id": image_ids[service], "RepoDigests": repo_digests,
            "Config": {"Labels": {"org.opencontainers.image.revision": revision}
                       if service in {"backend", "frontend"} else {}},
        }
    return list(ids.values()), containers, images


def upgrade_runtime_fixture(source_sha: str = SOURCE_SHA, *, generation: bool = False) -> tuple[dict, dict, dict, dict]:
    """Build a root-bootstrap-shaped expected plan and exact read-only Docker fixture."""
    operation_id = "9" * 32
    prior_operation = "7" * 32
    old_project = executor.PROJECT + "-upgrade-" + prior_operation if generation else executor.PROJECT
    new_project = executor.PROJECT + "-upgrade-" + operation_id
    ids = {name: f"{index:064x}" for index, name in enumerate(range(1, 9), start=1)}
    old_ids = dict(zip(("db", "backend", "frontend", "nginx"), [ids[i] for i in range(1, 5)]))
    old_ids["db"] = "9" * 64 if generation else executor.RETAINED_DATABASE_ID
    new_ids = dict(zip(("db", "backend", "frontend", "nginx"), [ids[i] for i in range(5, 9)]))
    old_network_id, old_ingress_id = "a" * 64, "b" * 64
    new_network_id, new_ingress_id = "c" * 64, "d" * 64
    networks = {
        old_project + "_isolated": old_network_id,
        old_project + "_ingress": old_ingress_id,
        new_project + "_isolated": new_network_id,
        new_project + "_ingress": new_ingress_id,
    }
    containers: dict[str, dict] = {}
    images: dict[str, dict] = {}
    app_plan = {"old": {}, "new": {}}

    def add_container(project: str, service: str, container_id: str, *, old: bool,
                      state: str, db_network: str | None = None) -> dict:
        net_id = db_network or networks[project + "_isolated"]
        attachment = {project + "_isolated": {"NetworkID": net_id}}
        if service == "nginx":
            attachment[project + "_ingress"] = {"NetworkID": networks[project + "_ingress"]}
        if service == "db":
            config_image = executor.DATABASE_IMAGE
            mount_name = project + "_staging-postgres-data"
            mounts = [{"Type": "volume", "Name": mount_name, "Destination": "/var/lib/postgresql/data"}]
        elif service == "nginx":
            config_image = executor.NGINX_IMAGE
            mounts = []
        else:
            config_image = f"{project}-{service}"
            mounts = ([{"Type": "volume", "Name": executor.PROJECT + "_staging-book-files",
                        "Destination": "/home/node"}] if service == "backend" else [])
        labels = {"com.docker.compose.project": project, "com.docker.compose.service": service,
                  "com.docker.compose.oneoff": "False"}
        if not old:
            labels["belib.retained-prepare-intent"] = "e" * 64
        if service == "nginx":
            labels["maintainer"] = "NGINX Docker Maintainers <docker-maint@nginx.com>"
        ports = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18083"}]} if service == "nginx" else {}
        if service == "nginx" and old and generation:
            ports["8443/tcp"] = [{"HostIp":"127.0.0.1","HostPort":"18443"}]
        host = {"PortBindings": ports}
        container = {
            "Id": container_id, "Image": "sha256:" + (
                {("old", "db"): "1", ("old", "backend"): "2", ("old", "frontend"): "3", ("old", "nginx"): "4",
                 ("new", "db"): "5", ("new", "backend"): "6", ("new", "frontend"): "7", ("new", "nginx"): "8"}[("old" if old else "new", service)] * 64),
            "Config": {"Labels": labels, "Image": config_image}, "HostConfig": host, "Mounts": mounts,
            "State": {"Status": state, **({"Health": {"Status": "healthy"}} if service == "db" else {})},
            "NetworkSettings": {"Networks": attachment},
        }
        image_id = container["Image"]
        if service in {"backend", "frontend"}:
            revision = ("6" * 40 if generation else executor.OLD_APPLICATION) if old else source_sha
            image = {"Id": image_id, "RepoDigests": [],
                     "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}
        elif service == "db":
            image = {"Id": image_id,
                     "RepoDigests": ["postgres@" + executor.DATABASE_IMAGE.split("@", 1)[1]],
                     "Config": {"Labels": {}}}
        else:
            image = {"Id": image_id,
                     "RepoDigests": ["nginx@" + executor.NGINX_IMAGE.split("@", 1)[1]],
                     "Config": {"Labels": {"maintainer": "NGINX Docker Maintainers <docker-maint@nginx.com>"}}}
        containers[container_id] = container
        images[image_id] = image
        return container

    # The existing database and candidate database each have one isolated network.
    old_db = add_container(old_project, "db", old_ids["db"], old=True, state="running")
    new_db = add_container(new_project, "db", new_ids["db"], old=False, state="running")
    app_ids = {"old": old_ids, "new": new_ids}
    for side, project in (("old", old_project), ("new", new_project)):
        for service in ("backend", "frontend", "nginx"):
            active = side == "new"
            container = add_container(project, service, app_ids[side][service], old=side == "old",
                                      state="running" if active else "exited")
            network_ids = {name: item["NetworkID"] for name, item in
                           container["NetworkSettings"]["Networks"].items()}
            app_plan[side][service] = {"id": container["Id"],
                                       "configuration_sha256": executor._configuration_digest(container),
                                       "network_ids": network_ids}

    cutover = {"schema": "belib-isolated-copy-cutover/v2" if generation else "belib-isolated-copy-cutover/v1",
               "source_sha": source_sha,"operation_id": operation_id,"port": 18083,
               "old": app_plan["old"], "new": app_plan["new"]}
    if generation:
        for service in ("backend","frontend","nginx"):
            cutover["old"][service]["image_id"] = containers[old_ids[service]]["Image"]
        cutover.update(prior_project=old_project,prior_application_sha="6"*40,
                       prior_generation_sha256="8"*64,prior_http_port=18083,prior_tls_loopback_port=18443)
    candidate = {"id": new_db["Id"], "configuration_sha256": executor._configuration_digest(new_db),
                 "volume_name": new_project + "_staging-postgres-data", "network_id": new_network_id}
    source_db = {"id": old_db["Id"], "configuration_sha256": executor._configuration_digest(old_db),
                 "volume_name": old_project + "_staging-postgres-data",
                 "network_ids": {old_project + "_isolated": old_network_id}}
    if generation:
        source_db["image_id"] = old_db["Image"]
    config = {"schema":"belib-retained-operator/v2" if generation else "belib-retained-operator/v1",
              "enabled":True,"source_sha":source_sha,"topology":"isolated-copy","prepared_directory":"/tmp/prepared",
              "migration_image":"sha256:"+"e"*64,
              "cutover_plan": cutover, "candidate_database": candidate, "source_database": source_db,
              "authorities": {name: {"issuer": name + ".example", "public_key_pem": "test-key-" + name,
                  "fingerprint": (str(index) * 64)}
                  for index, name in enumerate(("restore", "network", "roots"), start=1)}}
    if generation:
        config.update(prior_project=old_project, prior_generation_sha256="8"*64,
                      environment_transition={"prior_sha256":"1"*64,"candidate_sha256":"2"*64,
                          "prior_source_sha":"6"*40,"candidate_source_sha":source_sha})
    bootstrap_spec = importlib.util.spec_from_file_location(
        "operator_bootstrap_fixture", Path(__file__).with_name("operator-bootstrap.py"))
    assert bootstrap_spec is not None and bootstrap_spec.loader is not None
    bootstrap = importlib.util.module_from_spec(bootstrap_spec)
    bootstrap_spec.loader.exec_module(bootstrap)
    prior_completion = None
    active_generation = None
    if generation:
        prior_completion = {"owner_nonce": "a"*64, "source_sha": "6"*40,
            "project": old_project, "serve_config_sha256": "3"*64,
            "serve_etag": "prior-active-serve-etag", "certificate_sha256": "4"*64}
        active_generation = {"tls_owner_nonce": prior_completion["owner_nonce"],
            "tls_completion_sha256": hashlib.sha256(bootstrap.canonical(prior_completion)).hexdigest(),
            "source_sha": "6"*40, "project": old_project}
    expected_runtime = bootstrap.expected_retained_runtime(config, active_generation, prior_completion)
    network_objects = {}
    for full_name, network_id in networks.items():
        project, network_name = full_name.rsplit("_", 1)[0], full_name.rsplit("_", 1)[1]
        network_objects[full_name] = {"Id": network_id, "Name": full_name, "Driver": "bridge",
                                      "Labels": {"com.docker.compose.project": project,
                                                 "com.docker.compose.network": network_name},
                                      "Internal": network_name == "isolated"}
    all_ids = list(old_ids.values()) + list(new_ids.values())
    return expected_runtime, containers, images, {"ids": {old_project: list(old_ids.values()),
                                                            new_project: list(new_ids.values())},
                                                  "networks": network_objects, "all_ids": all_ids}


class SignedFixtureBootstrap:
    """Mock the fixed CLI, validating an actual test-key Ed25519 grant first."""

    def __init__(self, public_key: Path, *, now: int):
        self.public_key = public_key
        self.now = now
        self.config = {
            "schema": "belib-staging-operator/v1",
            "enabled": True,
            "source_sha": SOURCE_SHA,
            "manifest": {
                "scripts/staging/launch-guard.py": {"sha256": "4" * 64, "mode": 0o644},
            },
        }
        self.calls = []
        self.effects = []
        self.receipts = {}
        self.operation_exit_code = 0
        self.expected_runtime = None

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[:7] != [executor.SUDO, "-n", "--", executor.PYTHON, "-I", "-B", executor.INSTALL]:
            return subprocess.CompletedProcess(command, 2, "", "wrong entry")
        if command[7] == "--receipt":
            nonce = command[8]
            value = self.receipts.get(nonce)
            if value is None:
                return subprocess.CompletedProcess(command, 1, "", "receipt missing")
            return subprocess.CompletedProcess(command, 0, json.dumps(value, sort_keys=True), "")
        if command[7] != "--grant" or len(command) != 9:
            return subprocess.CompletedProcess(command, 2, "", "wrong arguments")
        grant_path = Path(command[8])
        envelope = json.loads(grant_path.read_text(encoding="utf-8"))
        payload_raw = base64.b64decode(envelope["payload"], validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        payload = json.loads(payload_raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "payload").write_bytes(payload_raw)
            (root / "signature").write_bytes(signature)
            verified = subprocess.run(
                ["/usr/bin/openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(self.public_key),
                 "-rawin", "-in", str(root / "payload"), "-sigfile", str(root / "signature")],
                capture_output=True, check=False,
            )
        if (verified.returncode != 0 or payload["source_sha"] != self.config["source_sha"]
                or not (payload["issued_at"] <= self.now < payload["expires_at"])):
            return subprocess.CompletedProcess(command, 1, "", "grant rejected before execution")
        self.effects.append(payload["operation"])
        result = {
            "schema": "belib-staging-operation-result/v1",
            "nonce": payload["nonce"],
            "grant_sha256": hashlib.sha256(payload_raw).hexdigest(),
            "source_sha": payload["source_sha"],
            "operation": payload["operation"],
            "host": executor.HOST,
            "principal": executor.PRINCIPAL,
            "uid": executor.UID,
            "project": executor.PROJECT,
            "exit_code": self.operation_exit_code,
            "completed_at": self.now + 1,
            "nonce_consumed": True,
        }
        if payload["operation"] == "upgrade-retained" and self.operation_exit_code == 0:
            result["expected_runtime"] = self.expected_runtime
        self.receipts[payload["nonce"]] = result
        return subprocess.CompletedProcess(command, 0, "STAGING_PHASE_PASS http-health\n" +
                                           json.dumps(result, sort_keys=True), "")


class CDHandoffExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.private = self.root / "private.pem"
        self.public = self.root / "public.pem"
        subprocess.run(["/usr/bin/openssl", "genpkey", "-algorithm", "ED25519", "-out", str(self.private)],
                       check=True, capture_output=True)
        subprocess.run(["/usr/bin/openssl", "pkey", "-in", str(self.private), "-pubout", "-out", str(self.public)],
                       check=True, capture_output=True)
        self.handoff_path = self.root / "handoff.json"
        self.grant_path = self.root / "grant.json"
        self.env_path = self.root / "i5.env"
        self.handoff_path.write_bytes(canonical(make_handoff()) + b"\n")
        self.handoff_path.chmod(0o600)
        self.env_path.write_text(
            "BELIB_STAGE_DB_BOOTSTRAP_PASSWORD=" + "5" * 64 + "\n"
            "BELIB_STAGE_DB_APP_PASSWORD=" + "6" * 64 + "\n"
            "BELIB_STAGE_DB_MIGRATE_PASSWORD=" + "7" * 64 + "\n"
            "BELIB_STAGE_AUTH_SECRET=" + "8" * 64 + "\n"
            "BELIB_STAGE_VCS_REF=" + SOURCE_SHA + "\n"
            "BELIB_STAGE_PORT=18083\n",
            encoding="utf-8",
        )
        self.env_path.chmod(0o600)
        self.now = NOW
        self.docker_ids, self.docker_containers, self.docker_images = runtime_snapshot()

    def test_expected_runtime_requires_authority_binding(self):
        expected, _, _, _ = upgrade_runtime_fixture()
        with self.assertRaisesRegex(executor.ExecutorError, "legacy receipt is unsupported"):
            executor.validate_expected_runtime({key: value for key, value in expected.items()
                                                if key != "authority_pins_sha256"})
        for malformed in ("not-a-sha", "A" * 64):
            with self.subTest(malformed=malformed), self.assertRaisesRegex(
                    executor.ExecutorError, "Expected retained runtime pins are malformed"):
                executor.validate_expected_runtime({**expected, "authority_pins_sha256": malformed})

    def signed_grant(self, payload: dict) -> None:
        payload_raw = canonical(payload)
        payload_path = self.root / "payload"
        signature_path = self.root / "signature"
        payload_path.write_bytes(payload_raw)
        subprocess.run(["/usr/bin/openssl", "pkeyutl", "-sign", "-inkey", str(self.private), "-rawin",
                        "-in", str(payload_path), "-out", str(signature_path)], check=True, capture_output=True)
        envelope = {"payload": base64.b64encode(payload_raw).decode("ascii"),
                    "signature": base64.b64encode(signature_path.read_bytes()).decode("ascii")}
        self.grant_path.write_bytes(canonical(envelope))
        self.grant_path.chmod(0o600)

    def execute(self, runner, *, runtime_readback=None):
        return executor.execute_handoff(
            self.handoff_path, self.grant_path, runner=runner,
            health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}",
                                       "http_status": 200},
            runtime_readback=(runtime_readback or
                              (lambda source, port, **kwargs: valid_runtime_readback(source, port))),
            readback_lock=contextlib.nullcontext,
            now=self.now, hostname=executor.HOST, uid=executor.UID, username=executor.PRINCIPAL,
            file_owner_uid=os.getuid(), env_file_path=self.env_path,
        )

    def test_readonly_tls_handoff_queries_both_root_records_without_grant_effect(self):
        expected, containers, images, inventory = upgrade_runtime_fixture()
        operation_id = expected["operation_id"]
        upgrade_nonce, owner_nonce = "a" * 64, "b" * 64
        root_upgrade = {
            "schema": "belib-staging-operation-result/v1", "nonce": upgrade_nonce,
            "grant_sha256": "c" * 64, "source_sha": SOURCE_SHA, "operation": "upgrade-retained",
            "host": executor.HOST, "principal": executor.PRINCIPAL, "uid": executor.UID,
            "project": executor.PROJECT, "exit_code": 0, "completed_at": NOW,
            "nonce_consumed": True, "expected_runtime": expected,
        }
        stable_sha = executor._unchanged_runtime_digest(expected)
        cutover = expected["plan"]["cutover_plan"]
        completion = {
            "schema": executor.TLS_COMPLETION_SCHEMA, "owner_nonce": owner_nonce,
            "plan_sha256": "d" * 64, "prepare_receipt_sha256": "e" * 64,
            "upgrade_receipt_nonce": upgrade_nonce,
            "upgrade_receipt_sha256": hashlib.sha256(canonical(root_upgrade)).hexdigest(),
            "source_sha": SOURCE_SHA, "operation_id": operation_id, "project": expected["project"],
            "previous_nginx_id": expected["services"]["nginx"], "nginx_id": "f" * 64,
            "nginx_configuration_sha256": "1" * 64,
            "nginx_image_id": executor.NGINX_IMAGE.split("@", 1)[1],
            "nginx_network_ids": cutover["new"]["nginx"]["network_ids"],
            "tls_config_sha256": "2" * 64, "certificate_sha256": "3" * 64,
            "unchanged_runtime_sha256": stable_sha, "approval_nonce": "4" * 64,
            "evidence_sha256": "5" * 64, "serve_config_sha256": "6" * 64,
            "serve_etag": "opaque-etag", "completed_at": NOW, "status": "complete",
        }
        calls = []
        def root_query(command, **kwargs):
            calls.append(command)
            if "--receipt" in command:
                value = root_upgrade
            elif "--tls-receipt" in command:
                value = completion
            else:
                raise AssertionError("read-only TLS mode attempted a non-receipt bootstrap command")
            return subprocess.CompletedProcess(command, 0, json.dumps(value, sort_keys=True), "")
        observed = {"status": "verified", "source_sha": SOURCE_SHA,
                    "operation_id": operation_id, "project": expected["project"]}
        result = executor.readback_tls_handoff(
            self.handoff_path, upgrade_nonce, owner_nonce, runner=root_query,
            health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}",
                                       "http_status": 200},
            runtime_readback=lambda upgrade, adopted, port, **kwargs: observed,
            ingress_probe=lambda fingerprint: {"status": 200, "certificate_sha256": fingerprint},
            readback_lock=contextlib.nullcontext, env_file_path=self.env_path,
            hostname=executor.HOST, uid=executor.UID, username=executor.PRINCIPAL,
            file_owner_uid=os.getuid(),
        )
        self.assertEqual(result["schema"], "belib-staging-cd-tls-readback/v1")
        self.assertEqual(result["status"], "LOCAL_TLS_DEPLOYMENT_CONFIRMED")
        self.assertEqual(result["operation"], "tls-readback")
        self.assertEqual(result["https_ingress_acceptance"], "verified")
        self.assertEqual(set(result), {"schema", "handoff_receipt_id", "admitted_source_sha", "operation",
                                      "host", "principal", "project", "status", "operator_receipt",
                                      "tls_completion", "loopback_health", "running_revision_readback",
                                      "browser_acceptance", "https_ingress_acceptance"})
        self.assertEqual([command[7] for command in calls], ["--receipt", "--tls-receipt"])
        self.assertFalse(any("--grant" in command for command in calls))
        original_env = self.env_path.read_bytes()
        self.env_path.write_bytes(original_env.replace(SOURCE_SHA.encode(), b"d" * 40))
        self.env_path.chmod(0o600)
        with self.assertRaisesRegex(executor.ExecutorError, "source differs"):
            executor.readback_tls_handoff(
                self.handoff_path, upgrade_nonce, owner_nonce, runner=root_query,
                health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}",
                                           "http_status": 200},
                runtime_readback=lambda *args, **kwargs: observed,
                ingress_probe=lambda fingerprint: {"status": 200, "certificate_sha256": fingerprint},
                readback_lock=contextlib.nullcontext, env_file_path=self.env_path,
                hostname=executor.HOST, uid=executor.UID, username=executor.PRINCIPAL,
                file_owner_uid=os.getuid(),
            )
        self.env_path.write_bytes(original_env)
        self.env_path.chmod(0o600)
        def change_env_mid_probe(fingerprint):
            changed = original_env.replace(b"BELIB_STAGE_PORT=18083", b"BELIB_STAGE_PORT=18084")
            self.env_path.write_bytes(changed)
            self.env_path.chmod(0o600)
            return {"status": 200, "certificate_sha256": fingerprint}
        with self.assertRaisesRegex(executor.ExecutorError, "changed during TLS probes"):
            executor.readback_tls_handoff(
                self.handoff_path, upgrade_nonce, owner_nonce, runner=root_query,
                health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}",
                                           "http_status": 200},
                runtime_readback=lambda *args, **kwargs: observed,
                ingress_probe=change_env_mid_probe,
                readback_lock=contextlib.nullcontext, env_file_path=self.env_path,
                hostname=executor.HOST, uid=executor.UID, username=executor.PRINCIPAL,
                file_owner_uid=os.getuid(),
            )
        self.env_path.write_bytes(original_env)
        self.env_path.chmod(0o600)

    def test_tls_ingress_rejects_a_successful_disallowed_sni(self):
        class FakeConnection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return type("Response", (), {"status": 200, "read": lambda self, *_: b""})()
            def close(self): pass
        with patch.object(executor.http.client, "HTTPSConnection", FakeConnection), \
                patch.object(executor, "_negative_tls_sni_rejected",
                             side_effect=executor.ExecutorError("disallowed SNI unexpectedly succeeded")), \
                self.assertRaisesRegex(executor.ExecutorError, "unexpectedly succeeded"):
            executor.verify_tls_ingress("a" * 64)

    def test_tls_ingress_negative_sni_probe_fails_when_listener_refuses_or_times_out(self):
        class FakeConnection:
            def __init__(self, *args, **kwargs): pass
            def request(self, *args, **kwargs): pass
            def getresponse(self): return type("Response", (), {"status": 200, "read": lambda self, *_: b""})()
            def close(self): pass
        for failure in (ConnectionRefusedError("listener refused"), TimeoutError("listener timed out")):
            with self.subTest(failure=type(failure).__name__), \
                    patch.object(executor.http.client, "HTTPSConnection", FakeConnection), \
                    patch.object(executor.socket, "create_connection", side_effect=failure), \
                    self.assertRaisesRegex(executor.ExecutorError, "TLS ingress health probe failed"):
                # The positive health request succeeds above; an unavailable
                # endpoint during the subsequent negative probe must still fail.
                executor.verify_tls_ingress("a" * 64)

    def test_tls_ingress_negative_sni_probe_accepts_only_the_expected_alert(self):
        class ExpectedAlert(executor.ssl.SSLError):
            def __init__(self, reason): self.expected_reason = reason
            @property
            def reason(self): return self.expected_reason
        class UnexpectedAlert(executor.ssl.SSLError):
            @property
            def reason(self): return "SSL_ERROR_SSL"
        class FakeSocket:
            def __init__(self): self.closed = False
            def close(self): self.closed = True
        class FakeContext:
            check_hostname = True
            verify_mode = executor.ssl.CERT_REQUIRED
            def __init__(self, error): self.error = error
            def wrap_socket(self, raw, *, server_hostname): raise self.error
        for reason in ("TLSV1_ALERT_UNRECOGNIZED_NAME", "TLSV1_UNRECOGNIZED_NAME"):
            raw = FakeSocket()
            with self.subTest(reason=reason), \
                    patch.object(executor.socket, "create_connection", return_value=raw), \
                    patch.object(executor.ssl, "create_default_context", return_value=FakeContext(ExpectedAlert(reason))):
                executor._negative_tls_sni_rejected("unknown.invalid")
            self.assertTrue(raw.closed)
        raw = FakeSocket()
        with patch.object(executor.socket, "create_connection", return_value=raw), \
                patch.object(executor.ssl, "create_default_context", return_value=FakeContext(UnexpectedAlert())):
            with self.assertRaisesRegex(executor.ExecutorError, "expected rejection alert"):
                executor._negative_tls_sni_rejected("unknown.invalid")
        self.assertTrue(raw.closed)

    def test_readonly_tls_cli_never_accepts_or_executes_a_grant(self):
        result = {"schema": "belib-staging-cd-tls-readback/v1",
                  "status": "LOCAL_TLS_DEPLOYMENT_CONFIRMED"}
        argv = ["--handoff", str(self.handoff_path), "--upgrade-receipt", "a" * 64,
                "--owner-nonce", "b" * 64, "--result", str(self.root / "result.json")]
        with patch.object(executor, "readback_tls_handoff", return_value=result) as readback, \
                patch.object(executor, "write_result") as writer:
            self.assertEqual(executor.main(argv), 0)
        self.assertEqual(readback.call_args.args, (self.handoff_path, "a" * 64, "b" * 64))
        writer.assert_called_once_with(Path(str(self.root / "result.json")), result)
        with patch.object(executor, "readback_tls_handoff",
                          side_effect=executor.ExecutorError("root receipt query unavailable")), \
                patch.object(executor, "write_result") as failed_writer, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(executor.main(argv), 1)
        failed_writer.assert_not_called()
        with patch.object(executor, "readback_tls_handoff") as readback, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            executor.main(["--handoff", str(self.handoff_path), "--grant", str(self.grant_path),
                           "--upgrade-receipt", "a" * 64, "--owner-nonce", "b" * 64,
                           "--result", str(self.root / "second.json")])
        readback.assert_not_called()

    def test_tls_completion_chain_rejects_changed_original_nginx_or_stable_runtime(self):
        expected, _, _, _ = upgrade_runtime_fixture()
        cutover = expected["plan"]["cutover_plan"]
        root_upgrade = {
            "schema": "belib-staging-operation-result/v1", "nonce": "a" * 64,
            "grant_sha256": "c" * 64, "source_sha": SOURCE_SHA, "operation": "upgrade-retained",
            "host": executor.HOST, "principal": executor.PRINCIPAL, "uid": executor.UID,
            "project": executor.PROJECT, "exit_code": 0, "completed_at": NOW,
            "nonce_consumed": True, "expected_runtime": expected,
        }
        completion = {"schema": executor.TLS_COMPLETION_SCHEMA, "owner_nonce": "b" * 64,
                      "upgrade_receipt_nonce": "a" * 64,
                      "upgrade_receipt_sha256": hashlib.sha256(canonical(root_upgrade)).hexdigest(),
                      "source_sha": SOURCE_SHA, "operation_id": expected["operation_id"],
                      "project": expected["project"], "previous_nginx_id": expected["services"]["nginx"],
                      "nginx_network_ids": cutover["new"]["nginx"]["network_ids"],
                      "nginx_id": "f" * 64,
                      "unchanged_runtime_sha256": executor._unchanged_runtime_digest(expected)}
        executor.validate_tls_completion_links(completion, root_upgrade, "b" * 64)
        for field, value in (("previous_nginx_id", "0" * 64),
                             ("unchanged_runtime_sha256", "0" * 64),
                             ("nginx_network_ids", {})):
            with self.subTest(field=field), self.assertRaises(executor.ExecutorError):
                executor.validate_tls_completion_links({**completion, field: value}, root_upgrade, "b" * 64)

    def test_completed_generation_inspector_links_receipts_and_reads_live_projection(self):
        expected, _, _, _ = upgrade_runtime_fixture()
        upgrade = {"schema": "belib-staging-operation-result/v1", "nonce": "a" * 64,
            "grant_sha256": "c" * 64, "source_sha": SOURCE_SHA, "operation": "upgrade-retained",
            "host": executor.HOST, "principal": executor.PRINCIPAL, "uid": executor.UID,
            "project": executor.PROJECT, "exit_code": 0, "completed_at": NOW,
            "nonce_consumed": True, "expected_runtime": expected}
        cutover = expected["plan"]["cutover_plan"]
        completion = {"schema": executor.TLS_COMPLETION_SCHEMA, "owner_nonce": "b" * 64, "upgrade_receipt_nonce": "a" * 64,
            "upgrade_receipt_sha256": hashlib.sha256(canonical(upgrade)).hexdigest(),
            "source_sha": SOURCE_SHA, "operation_id": expected["operation_id"],
            "project": expected["project"], "previous_nginx_id": expected["services"]["nginx"],
            "nginx_network_ids": cutover["new"]["nginx"]["network_ids"], "nginx_id": "f" * 64,
            "unchanged_runtime_sha256": executor._unchanged_runtime_digest(expected),
            "certificate_sha256": "3" * 64}
        live = {"status": "verified", "source_sha": SOURCE_SHA,
            "operation_id": expected["operation_id"], "project": expected["project"],
            "container_ids": {"candidate": {"db": expected["services"]["db"],
                "backend": expected["services"]["backend"], "frontend": expected["services"]["frontend"],
                "nginx": completion["nginx_id"]}},
            "image_digests": {"candidate": {name: "sha256:" + str(index) * 64
                for index, name in enumerate(("db", "backend", "frontend", "nginx"), 1)}},
            "app_image_revisions": {"backend": SOURCE_SHA, "frontend": SOURCE_SHA}}
        wrapper = {"status":"verified","source_sha":SOURCE_SHA,"operation_id":expected["operation_id"],
            "project":expected["project"],"completion":completion,"observed_runtime":live}
        readback = Mock(return_value=wrapper)
        ingress = Mock(return_value={
            "status": 200, "certificate_sha256": completion["certificate_sha256"]})
        result = executor.inspect_completed_generation(upgrade, completion,
            runtime_readback=readback, ingress_probe=ingress,
            health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}", "http_status": 200},
            env_file_path=self.env_path, file_owner_uid=os.getuid(),
            readback_lock=contextlib.nullcontext)
        readback.assert_called_once_with(upgrade, completion, expected["port"])
        self.assertEqual(result["observed_runtime"], live)
        with self.assertRaisesRegex(executor.ExecutorError, "differs from its linked receipts"):
            executor.inspect_completed_generation(upgrade, completion,
                runtime_readback=lambda *args: {**wrapper, "project": "wrong"},
                ingress_probe=lambda fingerprint: {"status": 200, "certificate_sha256": fingerprint},
                health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}", "http_status": 200},
                env_file_path=self.env_path, file_owner_uid=os.getuid(),
                readback_lock=contextlib.nullcontext)

    def test_completed_generation_inspector_rejects_unsealed_descriptor(self):
        fd = os.memfd_create("unsealed-generation-test", os.MFD_ALLOW_SEALING)
        try:
            os.write(fd, canonical({"schema": "belib-active-generation-inspection/v1",
                                   "upgrade_receipt": {}, "tls_completion": {}}))
            with self.assertRaisesRegex(executor.ExecutorError, "root-sealed"):
                executor.inspect_completed_generation_fd(fd)
        finally:
            os.close(fd)

    def test_valid_signed_fixture_traverses_fixed_bootstrap_and_checks_receipts_health(self):
        self.signed_grant(make_grant())
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        result = self.execute(bootstrap)
        self.assertEqual(bootstrap.effects, ["deploy"])
        self.assertEqual(result["status"], "LOCAL_DEPLOYMENT_CONFIRMED")
        self.assertEqual(result["admitted_source_sha"], SOURCE_SHA)
        self.assertEqual(result["host"], executor.HOST)
        self.assertEqual(result["operation"], "deploy")
        self.assertEqual(result["operator_receipt"]["project"], executor.PROJECT)
        self.assertEqual(result["loopback_health"]["http_status"], 200)
        self.assertEqual(result["running_revision_readback"]["source_sha"], SOURCE_SHA)
        self.assertEqual(bootstrap.calls[0][0], [executor.SUDO, "-n", "--", executor.PYTHON,
                                                 "-I", "-B", executor.INSTALL,
                                                 "--grant", str(self.grant_path)])
        self.assertEqual(bootstrap.calls[1][0][7:], ["--receipt", "2" * 64])
        self.assertEqual(result["browser_acceptance"], "not-performed")

    def test_actual_handoff_emitter_receipt_matches_executor_contract(self):
        fixture = handoff_test_fixtures.CDHandoffTests()
        fixture.setUp()
        receipt = fixture.validate()
        self.assertEqual(executor.validate_handoff(receipt)[0], SOURCE_SHA)

    def test_malformed_handoff_fails_before_operator_entry(self):
        self.signed_grant(make_grant())
        bad = make_handoff()
        bad["deployment"]["authorized"] = True
        self.handoff_path.write_bytes(canonical(bad))
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        with self.assertRaises(executor.ExecutorError):
            self.execute(bootstrap)
        self.assertEqual(bootstrap.calls, [])
        self.assertEqual(bootstrap.effects, [])

    def test_changed_candidate_head_fails_before_operator_entry(self):
        self.signed_grant(make_grant("b" * 40))
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        with self.assertRaisesRegex(executor.ExecutorError, "does not match the handoff"):
            self.execute(bootstrap)
        self.assertEqual(bootstrap.calls, [])
        self.assertEqual(bootstrap.effects, [])

    def test_expired_signed_grant_fails_before_operator_entry(self):
        self.signed_grant(make_grant(issued_at=NOW - 301, expires_at=NOW - 1))
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        with self.assertRaisesRegex(executor.ExecutorError, "expired"):
            self.execute(bootstrap)
        self.assertEqual(bootstrap.calls, [])
        self.assertEqual(bootstrap.effects, [])

    def test_bad_signature_is_rejected_by_fixed_bootstrap_before_effect(self):
        self.signed_grant(make_grant())
        envelope = json.loads(self.grant_path.read_text(encoding="utf-8"))
        envelope["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
        self.grant_path.write_bytes(canonical(envelope))
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        with self.assertRaises(executor.ExecutorError):
            self.execute(bootstrap)
        self.assertEqual(bootstrap.effects, [])

    def test_wrong_operator_receipt_fails_closed_after_command(self):
        self.signed_grant(make_grant())

        def wrong_receipt_runner(command, **kwargs):
            if command[7] == "--grant":
                result = {
                    "schema": "belib-staging-operation-result/v1", "nonce": "2" * 64,
                    "grant_sha256": hashlib.sha256(canonical(make_grant())).hexdigest(),
                    "source_sha": SOURCE_SHA, "operation": "resume-ingress", "host": executor.HOST,
                    "principal": executor.PRINCIPAL, "uid": executor.UID, "project": executor.PROJECT,
                    "exit_code": 0, "completed_at": NOW + 1, "nonce_consumed": True,
                }
            else:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

        health_calls = []
        with self.assertRaisesRegex(executor.ExecutorError, "does not match"):
            executor.execute_handoff(
                self.handoff_path, self.grant_path, runner=wrong_receipt_runner,
                health_probe=lambda port: health_calls.append(True), now=self.now,
                hostname=executor.HOST, uid=executor.UID, username=executor.PRINCIPAL,
                file_owner_uid=os.getuid(), env_file_path=self.env_path,
                readback_lock=contextlib.nullcontext,
            )
        self.assertEqual(health_calls, [])

    def test_loopback_failure_is_reported_without_claiming_local_pass(self):
        self.signed_grant(make_grant())
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        result = executor.execute_handoff(
            self.handoff_path, self.grant_path, runner=bootstrap,
            health_probe=lambda port: (_ for _ in ()).throw(executor.ExecutorError("offline")),
            now=self.now, hostname=executor.HOST, uid=executor.UID,
            username=executor.PRINCIPAL, file_owner_uid=os.getuid(), env_file_path=self.env_path,
            runtime_readback=lambda source, port, **kwargs: valid_runtime_readback(source, port),
            readback_lock=contextlib.nullcontext,
        )
        self.assertEqual(bootstrap.effects, ["deploy"])
        self.assertEqual(result["status"], "LOOPBACK_HEALTH_FAILED_REVISION_UNCONFIRMED")
        self.assertIsNone(result["loopback_health"]["http_status"])

    def test_failed_operator_receipt_skips_health_and_runtime_readback(self):
        self.signed_grant(make_grant())
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        bootstrap.operation_exit_code = 1
        calls = []
        result = executor.execute_handoff(
            self.handoff_path, self.grant_path, runner=bootstrap,
            health_probe=lambda port: calls.append(("health", port)),
            runtime_readback=lambda source, port, **kwargs: calls.append(("runtime", source, port)),
            readback_lock=contextlib.nullcontext,
            now=self.now, hostname=executor.HOST, uid=executor.UID,
            username=executor.PRINCIPAL, file_owner_uid=os.getuid(), env_file_path=self.env_path,
        )
        self.assertEqual(result["status"], "OPERATION_FAILED")
        self.assertEqual(calls, [])
        self.assertEqual(executor.execution_exit_code(result), 1)

    def test_runtime_readback_rejects_wrong_image_revision_and_nginx_binding(self):
        for wrong_revision, wrong_binding, message in (
            (False, False, None), (True, False, "revision"), (False, True, "binding"),
        ):
            ids, containers, images = runtime_snapshot(wrong_revision=wrong_revision,
                                                       wrong_binding=wrong_binding)

            def fake_runner(command, **kwargs):
                if "publish=18083" in command:
                    return subprocess.CompletedProcess(command, 0, ids[-1], "")
                if command[1:4] == ["--host", executor.DOCKER_ENDPOINT, "ps"]:
                    return subprocess.CompletedProcess(command, 0, "\n".join(ids), "")
                if command[1:4] == ["inspect", "--format", "{{json .}}"]:
                    payload = containers[command[-1]]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                if command[1:5] == ["image", "inspect", "--format", "{{json .}}"]:
                    payload = images[command[-1]]
                    return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
                self.fail(f"Unexpected Docker command: {command}")

            if message is None:
                result = executor.collect_runtime_readback(SOURCE_SHA, 18083, runner=fake_runner,
                                                           endpoint_check=lambda: None)
                self.assertEqual(result["status"], "verified")
                self.assertEqual(result["source_sha"], SOURCE_SHA)
                self.assertEqual(result["nginx_loopback_binding"]["host_port"], 18083)
            else:
                with self.assertRaisesRegex(executor.ExecutorError, message):
                    executor.collect_runtime_readback(SOURCE_SHA, 18083, runner=fake_runner,
                                                      endpoint_check=lambda: None)

    def test_configuration_digest_normalizes_only_moby_start_resolved_host_fields(self):
        before_start = {
            "Id": "1" * 64,
            "Image": "sha256:" + "2" * 64,
            "Config": {"Image": "candidate", "Env": ["MODE=staging"]},
            "HostConfig": {"OomScoreAdj": 0, "OomKillDisable": False,
                           "Privileged": False, "CapDrop": ["ALL"],
                           "PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1",
                                                            "HostPort": "18083"}]}},
            "Mounts": [{"Type": "volume", "Name": "books", "Destination": "/home/node"}],
            "NetworkSettings": {"Networks": {"belib-isolated-i5_isolated": {}}},
        }
        after_start = json.loads(json.dumps(before_start))
        after_start["HostConfig"]["OomScoreAdj"] = 200
        after_start["HostConfig"].pop("OomKillDisable")
        digest = executor._configuration_digest(before_start)
        self.assertEqual(executor._configuration_digest(after_start), digest)

        mutations = (
            ("HostConfig", "Privileged", True),
            ("HostConfig", "CapDrop", []),
            ("HostConfig", "PortBindings", {}),
            ("Config", "Env", ["MODE=production"]),
            ("Mounts", None, []),
            ("NetworkSettings", "Networks", {"unexpected": {}}),
        )
        for section, key, value in mutations:
            with self.subTest(section=section, key=key):
                changed = json.loads(json.dumps(before_start))
                if key is None:
                    changed[section] = value
                else:
                    changed[section][key] = value
                self.assertNotEqual(executor._configuration_digest(changed), digest)

    def test_root_expected_upgrade_receipt_drives_exact_isolated_copy_readback(self):
        expected_runtime, containers, images, inventory = upgrade_runtime_fixture()
        grant = make_grant(operation="upgrade-retained") | {
            "prior_application_sha": executor.OLD_APPLICATION,
            "retained_state_sha256": "4" * 64,
            "backup_sha256": "5" * 64,
            "restore_attestation_sha256": "6" * 64,
        }
        grant_sha = hashlib.sha256(canonical(grant)).hexdigest()
        receipt = {
            "schema": "belib-staging-operation-result/v1", "nonce": grant["nonce"],
            "grant_sha256": grant_sha, "source_sha": SOURCE_SHA, "operation": "upgrade-retained",
            "host": executor.HOST, "principal": executor.PRINCIPAL, "uid": executor.UID,
            "project": executor.PROJECT, "exit_code": 0, "completed_at": NOW + 1,
            "nonce_consumed": True, "expected_runtime": expected_runtime,
        }
        self.assertEqual(executor.validate_bootstrap_result(receipt, grant, grant_sha), receipt)

        def fake_runner(command, **kwargs):
            if command[3] == "ps" and "publish=18083" in command:
                # Docker's active `ps` summary omits stopped containers: the
                # daemon clears NetworkSettings.Ports after a container exits.
                self.assertNotIn("--all", command)
                return subprocess.CompletedProcess(
                    command, 0, expected_runtime["services"]["nginx"], "")
            if command[3] == "ps":
                project = command[command.index("--filter") + 1].rsplit("=", 1)[1]
                return subprocess.CompletedProcess(command, 0, "\n".join(inventory["ids"][project]), "")
            if command[1:4] == ["inspect", "--format", "{{json .}}"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(containers[command[-1]]), "")
            if command[1:5] == ["image", "inspect", "--format", "{{json .}}"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(images[command[-1]]), "")
            if command[1:5] == ["network", "inspect", "--format", "{{json .}}"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(inventory["networks"][command[-1]]), "")
            self.fail(f"Unexpected Docker command: {command}")

        observed = executor.collect_runtime_readback(SOURCE_SHA, 18083,
                                                     expected_runtime=receipt["expected_runtime"],
                                                     runner=fake_runner, endpoint_check=lambda: None)
        self.assertEqual(observed["status"], "verified")
        self.assertEqual(observed["topology"], "isolated-copy")
        self.assertEqual(observed["plan_sha256"], expected_runtime["plan_sha256"])
        self.assertEqual(observed["container_ids"]["candidate"], expected_runtime["services"])
        self.assertEqual(observed["app_image_revisions"], {"backend": SOURCE_SHA, "frontend": SOURCE_SHA})
        self.assertEqual(observed["retained_app_states"], {name: "exited" for name in ("backend", "frontend", "nginx")})

    def test_v2_expected_runtime_binds_dynamic_predecessor_and_tls_topology(self):
        expected, _containers, _images, _inventory = upgrade_runtime_fixture(generation=True)
        validated = executor.validate_expected_runtime(expected)
        self.assertEqual(validated["prior_project"], executor.PROJECT + "-upgrade-" + "7"*32)
        self.assertEqual(validated["retained"]["db"], "9"*64)
        self.assertEqual(validated["plan"]["cutover_plan"]["prior_generation_sha256"], "8"*64)
        changed = json.loads(json.dumps(expected))
        changed["plan"]["cutover_plan"]["prior_tls_loopback_port"] = 18444
        with self.assertRaises(executor.ExecutorError):
            executor.validate_expected_runtime(changed)

    def test_v2_tls_completion_requires_exact_predecessor_tls_receipt_links(self):
        expected, _, _, _ = upgrade_runtime_fixture(generation=True)
        upgrade = {"schema": "belib-staging-operation-result/v1", "nonce": "a"*64,
            "grant_sha256": "b"*64, "source_sha": SOURCE_SHA, "operation": "upgrade-retained",
            "host": executor.HOST, "principal": executor.PRINCIPAL, "uid": executor.UID,
            "project": executor.PROJECT, "exit_code": 0, "completed_at": NOW,
            "nonce_consumed": True, "expected_runtime": expected}
        prior = expected["prior_tls_completion"]
        completion = {"schema": executor.TLS_COMPLETION_SCHEMA_V2, "owner_nonce": "c"*64,
            "plan_sha256": "d"*64, "prepare_receipt_sha256": "e"*64,
            "upgrade_receipt_nonce": upgrade["nonce"],
            "upgrade_receipt_sha256": hashlib.sha256(canonical(upgrade)).hexdigest(),
            "source_sha": SOURCE_SHA, "operation_id": expected["operation_id"],
            "project": expected["project"], "previous_nginx_id": expected["services"]["nginx"],
            "nginx_id": "f"*64, "nginx_configuration_sha256": "1"*64,
            "nginx_image_id": "sha256:"+"2"*64,
            "nginx_network_ids": expected["plan"]["cutover_plan"]["new"]["nginx"]["network_ids"],
            "tls_config_sha256": "3"*64, "certificate_sha256": "4"*64,
            "unchanged_runtime_sha256": executor._unchanged_runtime_digest(expected),
            "approval_nonce": "5"*64, "evidence_sha256": "6"*64,
            "serve_config_sha256": prior["serve_config_sha256"], "serve_etag": prior["serve_etag"],
            "completed_at": NOW, "status": "complete",
            "prior_generation_sha256": expected["prior_generation_sha256"],
            "prior_tls_owner_nonce": prior["owner_nonce"],
            "prior_tls_completion_sha256": prior["completion_sha256"]}
        self.assertEqual(executor.validate_tls_completion_links(completion, upgrade, completion["owner_nonce"]), expected)
        changed = {**completion, "prior_tls_completion_sha256": "9"*64}
        with self.assertRaisesRegex(executor.ExecutorError, "active TLS predecessor"):
            executor.validate_tls_completion_links(changed, upgrade, completion["owner_nonce"])
        changed = json.loads(json.dumps(expected))
        changed["prior_generation_sha256"] = "f"*64
        with self.assertRaises(executor.ExecutorError):
            executor.validate_expected_runtime(changed)

    def test_v2_retained_grant_requires_predecessor_hash(self):
        grant={**make_grant(operation="upgrade-retained"),
            "schema":"belib-staging-bootstrap-approval/v2","prior_application_sha":"7"*40,
            "prior_generation_sha256":"8"*64,"retained_state_sha256":"9"*64,
            "backup_sha256":"1"*64,"restore_attestation_sha256":"2"*64}
        envelope={"payload":base64.b64encode(canonical(grant)).decode(),
                  "signature":base64.b64encode(b"x"*64).decode()}
        decoded,_=executor.decode_grant(canonical(envelope),expected_source_sha=SOURCE_SHA,now=NOW)
        self.assertEqual(decoded["prior_generation_sha256"],"8"*64)
        missing={key:value for key,value in grant.items() if key!="prior_generation_sha256"}
        envelope["payload"]=base64.b64encode(canonical(missing)).decode()
        with self.assertRaises(executor.ExecutorError):
            executor.decode_grant(canonical(envelope),expected_source_sha=SOURCE_SHA,now=NOW)

    def test_candidate_env_retry_uses_only_durable_receipt_and_never_replays_grant(self):
        prior_source = "6"*40
        grant = {**make_grant(SOURCE_SHA, operation="upgrade-retained"),
            "schema":"belib-staging-bootstrap-approval/v2", "prior_application_sha":prior_source,
            "prior_generation_sha256":"8"*64, "retained_state_sha256":"9"*64,
            "backup_sha256":"1"*64, "restore_attestation_sha256":"2"*64}
        self.signed_grant(grant)
        expected, _, _, _ = upgrade_runtime_fixture(generation=True)
        prior_raw = self.env_path.read_bytes()
        # Make the predecessor distinct from the now-committed candidate.
        prior_raw = prior_raw.replace(b"BELIB_STAGE_VCS_REF="+SOURCE_SHA.encode(),
            b"BELIB_STAGE_VCS_REF="+prior_source.encode())
        candidate_raw = prior_raw.replace(b"BELIB_STAGE_VCS_REF="+prior_source.encode(),
            b"BELIB_STAGE_VCS_REF="+SOURCE_SHA.encode())
        self.env_path.write_bytes(candidate_raw)
        transition = expected["plan"]["environment_transition"]
        transition.update(prior_sha256=hashlib.sha256(prior_raw).hexdigest(),
            candidate_sha256=hashlib.sha256(candidate_raw).hexdigest(),
            prior_source_sha=prior_source, candidate_source_sha=SOURCE_SHA)
        expected["plan_sha256"] = hashlib.sha256(canonical(expected["plan"])).hexdigest()
        durable = {"schema":"belib-staging-operation-result/v1", "nonce":grant["nonce"],
            "grant_sha256":hashlib.sha256(canonical(grant)).hexdigest(), "source_sha":SOURCE_SHA,
            "operation":"upgrade-retained", "host":executor.HOST, "principal":executor.PRINCIPAL,
            "uid":executor.UID, "project":executor.PROJECT, "exit_code":0, "completed_at":NOW,
            "nonce_consumed":True, "expected_runtime":expected}
        def read_only_receipt(command, **_kwargs):
            self.assertEqual(command[7:9], ["--receipt", grant["nonce"]])
            return subprocess.CompletedProcess(command, 0, json.dumps(durable), "")
        observed = {"status":"verified", "source_sha":SOURCE_SHA,
            "container_ids":{"candidate":{},"retained":{}},
            "image_digests":{"candidate":{},"retained":{}},
            "app_image_revisions":{"backend":SOURCE_SHA,"frontend":SOURCE_SHA},
            "nginx_loopback_binding":{"host_ip":"127.0.0.1","host_port":18083,
                "container_port":"8080/tcp"}}
        result = self.execute(read_only_receipt,
            runtime_readback=lambda *_args, **_kwargs: observed)
        self.assertEqual(result["status"], "LOCAL_DEPLOYMENT_CONFIRMED")

    def test_tls_candidate_readback_allows_only_pinned_nginx_transition(self):
        expected, containers, images, inventory = upgrade_runtime_fixture()
        old_id = expected["services"]["nginx"]
        candidate_id = "f" * 64
        candidate = json.loads(json.dumps(containers[old_id]))
        candidate["Id"] = candidate_id
        candidate["Config"]["Labels"]["belib.tls-owner-nonce"] = "a" * 64
        candidate["HostConfig"]["PortBindings"]["8443/tcp"] = [
            {"HostIp": "127.0.0.1", "HostPort": str(executor.TLS_LOOPBACK_PORT)}]
        del containers[old_id]
        containers[candidate_id] = candidate
        project = expected["project"]
        inventory["ids"][project] = [candidate_id if value == old_id else value
                                      for value in inventory["ids"][project]]
        config_sha = executor._configuration_digest(candidate)
        transition = {
            "previous_nginx_id": old_id, "nginx_id": candidate_id,
            "nginx_configuration_sha256": config_sha,
            "nginx_image_id": candidate["Image"],
            "nginx_network_ids": expected["plan"]["cutover_plan"]["new"]["nginx"]["network_ids"],
        }

        def fake_runner(command, **kwargs):
            if command[3] == "ps" and "publish=18083" in command:
                return subprocess.CompletedProcess(command, 0, candidate_id, "")
            if command[3] == "ps" and f"publish={executor.TLS_LOOPBACK_PORT}" in command:
                return subprocess.CompletedProcess(command, 0, candidate_id, "")
            if command[3] == "ps":
                name = command[command.index("--filter") + 1].rsplit("=", 1)[1]
                return subprocess.CompletedProcess(command, 0, "\n".join(inventory["ids"][name]), "")
            if command[1:4] == ["inspect", "--format", "{{json .}}"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(containers[command[-1]]), "")
            if command[1:5] == ["image", "inspect", "--format", "{{json .}}"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(images[command[-1]]), "")
            if command[1:5] == ["network", "inspect", "--format", "{{json .}}"]:
                return subprocess.CompletedProcess(command, 0, json.dumps(inventory["networks"][command[-1]]), "")
            self.fail(f"Unexpected Docker command: {command}")

        result = executor.collect_tls_candidate_readback(
            SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
            runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="a" * 64,
        )
        self.assertEqual(result["status"], "candidate-verified")
        self.assertEqual(result["observed_runtime"]["container_ids"]["candidate"]["nginx"], candidate_id)
        del candidate["HostConfig"]["PortBindings"]["8443/tcp"]
        transition["nginx_configuration_sha256"] = executor._configuration_digest(candidate)
        http_rollback = executor.collect_tls_candidate_readback(
            SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
            runner=fake_runner, endpoint_check=lambda: None, mode="http", owner_nonce="a" * 64,
        )
        self.assertEqual(http_rollback["status"], "candidate-verified")
        candidate["Config"]["Labels"]["belib.retained-prepare-intent"] = "f" * 64
        transition["nginx_configuration_sha256"] = executor._configuration_digest(candidate)
        with self.assertRaisesRegex(executor.ExecutorError, "do not share the adopted preparation intent"):
            executor.collect_tls_candidate_readback(
                SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
                runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="a" * 64,
            )
        candidate["Config"]["Labels"]["belib.retained-prepare-intent"] = "e" * 64
        candidate["HostConfig"]["PortBindings"]["8443/tcp"] = [
            {"HostIp": "127.0.0.1", "HostPort": str(executor.TLS_LOOPBACK_PORT)}]
        transition["nginx_configuration_sha256"] = executor._configuration_digest(candidate)
        changed_networks = {**transition, "nginx_network_ids": {project + "_isolated": "0" * 64,
                                                                  project + "_ingress": "1" * 64}}
        with self.assertRaisesRegex(executor.ExecutorError, "changed the adopted network"):
            executor.collect_tls_candidate_readback(
                SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=changed_networks,
                runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="a" * 64,
            )
        with self.assertRaisesRegex(executor.ExecutorError, "owner label"):
            executor.collect_tls_candidate_readback(
                SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
                runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="b" * 64,
            )
        candidate["Config"]["Labels"]["unexpected.extra"] = "denied"
        transition["nginx_configuration_sha256"] = executor._configuration_digest(candidate)
        with self.assertRaisesRegex(executor.ExecutorError, "unexpected"):
            executor.collect_tls_candidate_readback(
                SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
                runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="a" * 64,
            )
        candidate["Config"]["Labels"].pop("unexpected.extra")
        candidate["Config"]["Labels"]["maintainer"] = "overwritten image label"
        transition["nginx_configuration_sha256"] = executor._configuration_digest(candidate)
        with self.assertRaisesRegex(executor.ExecutorError, "changed_image_labels"):
            executor.collect_tls_candidate_readback(
                SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
                runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="a" * 64,
            )
        candidate["Config"]["Labels"]["maintainer"] = "NGINX Docker Maintainers <docker-maint@nginx.com>"
        candidate["HostConfig"]["PortBindings"]["9999/tcp"] = [
            {"HostIp": "127.0.0.1", "HostPort": "19999"}]
        transition["nginx_configuration_sha256"] = executor._configuration_digest(candidate)
        with self.assertRaisesRegex(executor.ExecutorError, "exact loopback endpoints"):
            executor.collect_tls_candidate_readback(
                SOURCE_SHA, 18083, expected_runtime=expected, nginx_transition=transition,
                runner=fake_runner, endpoint_check=lambda: None, mode="tls", owner_nonce="a" * 64,
            )

    def test_signed_upgrade_uses_root_receipt_topology_before_local_confirmation(self):
        expected_runtime, _, _, _ = upgrade_runtime_fixture()
        grant = make_grant(operation="upgrade-retained") | {
            "prior_application_sha": executor.OLD_APPLICATION,
            "retained_state_sha256": "4" * 64,
            "backup_sha256": "5" * 64,
            "restore_attestation_sha256": "6" * 64,
        }
        self.signed_grant(grant)
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        bootstrap.expected_runtime = expected_runtime
        reads = []

        def runtime_readback(source, port, *, expected_runtime):
            reads.append((source, port, expected_runtime))
            return {
                "status": "verified", "source_sha": source,
                "container_ids": {"candidate": {"db": "1"}, "retained": {"db": "2"}},
                "image_digests": {"candidate": {}, "retained": {}},
                "app_image_revisions": {"backend": source, "frontend": source},
                "nginx_loopback_binding": {"host_ip": "127.0.0.1", "host_port": port,
                                            "container_port": "8080/tcp"},
            }

        result = self.execute(bootstrap, runtime_readback=runtime_readback)
        self.assertEqual(bootstrap.effects, ["upgrade-retained"])
        self.assertEqual(len(reads), 1)
        self.assertEqual(reads[0], (SOURCE_SHA, 18083, expected_runtime))
        self.assertEqual(result["status"], "LOCAL_DEPLOYMENT_CONFIRMED")
        self.assertEqual(result["operator_receipt"]["expected_runtime"], expected_runtime)

    def test_upgrade_readback_rejects_unplanned_project_container(self):
        expected_runtime, containers, images, inventory = upgrade_runtime_fixture()
        new_project = expected_runtime["project"]
        inventory["ids"][new_project].append("f" * 64)

        def fake_runner(command, **kwargs):
            if command[3] == "ps":
                project = command[command.index("--filter") + 1].rsplit("=", 1)[1]
                return subprocess.CompletedProcess(command, 0, "\n".join(inventory["ids"][project]), "")
            self.fail(f"Inventory must fail before inspect: {command}")

        with self.assertRaisesRegex(executor.ExecutorError, "unexpected containers"):
            executor.collect_runtime_readback(SOURCE_SHA, 18083, expected_runtime=expected_runtime,
                                              runner=fake_runner, endpoint_check=lambda: None)

    def test_upgrade_readback_rejects_unexpected_loopback_publisher(self):
        expected_runtime, _, _, inventory = upgrade_runtime_fixture()

        def fake_runner(command, **kwargs):
            if command[3] == "ps" and "publish=18083" in command:
                self.assertNotIn("--all", command)
                return subprocess.CompletedProcess(command, 0, "\n".join((
                    expected_runtime["services"]["nginx"], "f" * 64,
                )), "")
            if command[3] == "ps":
                project = command[command.index("--filter") + 1].rsplit("=", 1)[1]
                return subprocess.CompletedProcess(command, 0, "\n".join(inventory["ids"][project]), "")
            self.fail(f"Unexpected Docker command before publisher rejection: {command}")

        with self.assertRaisesRegex(executor.ExecutorError, "publish the fixed staging loopback port"):
            executor.collect_runtime_readback(SOURCE_SHA, 18083, expected_runtime=expected_runtime,
                                              runner=fake_runner, endpoint_check=lambda: None)

    def test_upgrade_readback_rejects_reactivated_retained_nginx_publisher(self):
        expected_runtime, _, _, inventory = upgrade_runtime_fixture()
        old_nginx = expected_runtime["retained"]["stopped_application_ids"][2]

        def fake_runner(command, **kwargs):
            if command[3] == "ps" and "publish=18083" in command:
                return subprocess.CompletedProcess(command, 0, "\n".join((
                    expected_runtime["services"]["nginx"], old_nginx,
                )), "")
            if command[3] == "ps":
                project = command[command.index("--filter") + 1].rsplit("=", 1)[1]
                return subprocess.CompletedProcess(command, 0, "\n".join(inventory["ids"][project]), "")
            self.fail(f"Unexpected Docker command before publisher rejection: {command}")

        with self.assertRaisesRegex(executor.ExecutorError, "publish the fixed staging loopback port"):
            executor.collect_runtime_readback(SOURCE_SHA, 18083, expected_runtime=expected_runtime,
                                              runner=fake_runner, endpoint_check=lambda: None)

    def test_health_and_admitted_sha_without_observed_image_readback_are_not_a_pass(self):
        self.signed_grant(make_grant())
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        result = executor.execute_handoff(
            self.handoff_path, self.grant_path, runner=bootstrap,
            health_probe=lambda port: {"url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}",
                                       "http_status": 200},
            runtime_readback=lambda source, port, **kwargs: (_ for _ in ()).throw(
                executor.ExecutorError("wrong or unavailable runtime identity")
            ),
            readback_lock=contextlib.nullcontext,
            now=self.now, hostname=executor.HOST, uid=executor.UID,
            username=executor.PRINCIPAL, file_owner_uid=os.getuid(), env_file_path=self.env_path,
        )
        self.assertEqual(result["status"], "HEALTH_PASS_REVISION_UNCONFIRMED")
        self.assertIsNone(result["running_revision_readback"]["source_sha"])
        self.assertEqual(executor.execution_exit_code(result), 1)

    def test_cli_returns_nonzero_for_health_pass_without_revision_readback(self):
        result = {"schema": "belib-staging-cd-execution/v1", "status": "HEALTH_PASS_REVISION_UNCONFIRMED",
                  "admitted_source_sha": SOURCE_SHA, "running_revision_readback": {"status": "unconfirmed"}}
        output = io.StringIO()
        result_path = self.root / "unconfirmed-result.json"
        with patch.object(executor, "execute_handoff", return_value=result), contextlib.redirect_stdout(output):
            code = executor.main(["--handoff", str(self.handoff_path), "--grant", str(self.grant_path),
                                  "--result", str(result_path)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result)
        self.assertIn("HEALTH_PASS_REVISION_UNCONFIRMED", output.getvalue())

    def test_health_probe_uses_verified_port_from_fixed_staging_env(self):
        self.signed_grant(make_grant())
        self.env_path.write_text(self.env_path.read_text(encoding="utf-8").replace(
            "BELIB_STAGE_PORT=18083", "BELIB_STAGE_PORT=19443"
        ), encoding="utf-8")
        self.env_path.chmod(0o600)
        bootstrap = SignedFixtureBootstrap(self.public, now=self.now)
        probes = []
        result = executor.execute_handoff(
            self.handoff_path, self.grant_path, runner=bootstrap,
            health_probe=lambda port: probes.append(port) or {
                "url": f"http://127.0.0.1:{port}{executor.HEALTH_PATH}", "http_status": 200,
            },
            runtime_readback=lambda source, port, **kwargs: valid_runtime_readback(source, port),
            readback_lock=contextlib.nullcontext,
            now=self.now, hostname=executor.HOST, uid=executor.UID,
            username=executor.PRINCIPAL, file_owner_uid=os.getuid(), env_file_path=self.env_path,
        )
        self.assertEqual(probes, [19443])
        self.assertEqual(result["loopback_health"]["url"], "http://127.0.0.1:19443/health")


if __name__ == "__main__":
    unittest.main(verbosity=2)
