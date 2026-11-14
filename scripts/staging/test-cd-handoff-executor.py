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
from unittest.mock import patch
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


def upgrade_runtime_fixture(source_sha: str = SOURCE_SHA) -> tuple[dict, dict, dict, dict]:
    """Build a root-bootstrap-shaped expected plan and exact read-only Docker fixture."""
    operation_id = "9" * 32
    old_project = executor.PROJECT
    new_project = old_project + "-upgrade-" + operation_id
    ids = {name: f"{index:064x}" for index, name in enumerate(range(1, 9), start=1)}
    old_ids = dict(zip(("db", "backend", "frontend", "nginx"), [ids[i] for i in range(1, 5)]))
    old_ids["db"] = executor.RETAINED_DATABASE_ID
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
            mounts = ([{"Type": "volume", "Name": old_project + "_staging-book-files",
                        "Destination": "/home/node"}] if service == "backend" else [])
        labels = {"com.docker.compose.project": project, "com.docker.compose.service": service,
                  "com.docker.compose.oneoff": "False"}
        host = {"PortBindings": ({"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18083"}]}
                                  if service == "nginx" else {})}
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
            revision = executor.OLD_APPLICATION if old else source_sha
            image = {"Id": image_id, "RepoDigests": [],
                     "Config": {"Labels": {"org.opencontainers.image.revision": revision}}}
        elif service == "db":
            image = {"Id": image_id,
                     "RepoDigests": ["postgres@" + executor.DATABASE_IMAGE.split("@", 1)[1]],
                     "Config": {"Labels": {}}}
        else:
            image = {"Id": image_id,
                     "RepoDigests": ["nginx@" + executor.NGINX_IMAGE.split("@", 1)[1]],
                     "Config": {"Labels": {}}}
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

    cutover = {"schema": "belib-isolated-copy-cutover/v1", "source_sha": source_sha,
               "operation_id": operation_id, "port": 18083,
               "old": app_plan["old"], "new": app_plan["new"]}
    candidate = {"id": new_db["Id"], "configuration_sha256": executor._configuration_digest(new_db),
                 "volume_name": new_project + "_staging-postgres-data", "network_id": new_network_id}
    source_db = {"id": old_db["Id"], "configuration_sha256": executor._configuration_digest(old_db),
                 "volume_name": old_project + "_staging-postgres-data",
                 "network_ids": {old_project + "_isolated": old_network_id}}
    config = {"cutover_plan": cutover, "candidate_database": candidate, "source_database": source_db}
    bootstrap_spec = importlib.util.spec_from_file_location(
        "operator_bootstrap_fixture", Path(__file__).with_name("operator-bootstrap.py"))
    assert bootstrap_spec is not None and bootstrap_spec.loader is not None
    bootstrap = importlib.util.module_from_spec(bootstrap_spec)
    bootstrap_spec.loader.exec_module(bootstrap)
    expected_runtime = bootstrap.expected_retained_runtime(config)
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
