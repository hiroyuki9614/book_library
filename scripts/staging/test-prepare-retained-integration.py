#!/usr/bin/env python3
"""Opt-in i5 integration: prepare -> signed finalize -> existing retained executor.

This exercises real disposable rootless Docker/PostgreSQL resources. The fixed
fixture app images serve only a mock HTTP health page, and the migration image
applies SQL from the PostgreSQL integration fixture; this does not build or claim
to run the BeLib application. Every project/container/network/volume/image is
operation-scoped and removed by cleanup. The pre-existing i5 project is read
for its pinned images only; its containers, networks and volumes are not changed.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

WORKSPACE = Path(__file__).resolve().parents[2]
HELPER_ROOT = Path(os.environ.get("BELIB_INTEGRATION_HELPER_ROOT", WORKSPACE))
STAGE = HELPER_ROOT / "scripts/staging"
PREP_SOURCE = Path(os.environ.get("BELIB_PREP_SOURCE", Path(__file__).with_name("prepare-retained.py")))
ENTRYPOINT_TEST = STAGE / "test-retained-entrypoint.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load disposable fixture: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load(ENTRYPOINT_TEST, "entrypoint_disposable_fixture")
OLD_IMAGE = entry.OLD_IMAGE


@unittest.skipUnless(os.environ.get("BELIB_DISPOSABLE_PREPARE_TEST") == "1",
                     "explicit disposable i5 PostgreSQL integration opt-in required")
class PrepareFinalizeExecutorIntegration(entry.RetainedEntrypointTests):
    """Exercise actual preparation/finalization plus the installed executor fixture."""

    # The fixture base is opt-in skipped. This integration has its own explicit
    # opt-in and must not inherit that base marker.
    __unittest_skip__ = False
    __unittest_skip_why__ = ""

    @unittest.skip("covered by the existing retained-entrypoint suite")
    def test_real_entry_interruption_restores_old_binding_without_old_db_mutation(self):
        pass

    @unittest.skip("covered by the existing retained-entrypoint suite")
    def test_real_entry_positive_wrong_signature_and_completed_reconciliation(self):
        pass

    def make_network(self, project, kind="isolated"):
        # The preparer requires explicit Compose network labels; give the
        # disposable fixture the same immutable identity labels as Compose.
        name = project + "_" + kind
        args = ["network", "create", "--label", "com.docker.compose.project=" + project,
                "--label", "com.docker.compose.network=" + kind]
        if kind == "isolated":
            args.append("--internal")
        result = self.docker(*args, name).decode().strip()
        self.networks.append(result)
        return result

    def docker(self, *args, **kwargs):
        if len(args) >= 3 and args[0:2] == ("volume", "create"):
            name = args[-1]
            if hasattr(self, "project") and name in {
                    self.project + "_staging-book-files", self.project + "_staging-postgres-data"}:
                kind = "staging-book-files" if name.endswith("staging-book-files") else "staging-postgres-data"
                args = (*args[:-1], "--label", "com.docker.compose.project=" + self.project,
                        "--label", "com.docker.compose.volume=" + kind, name)
        return super().docker(*args, **kwargs)

    def make_db(self, project, network):
        volume = project + "_staging-postgres-data"
        self.docker("volume", "create", "--label", "belib.disposable-test=prepare-integration", volume)
        self.volumes.append(volume)
        identity = self.docker("run", "-d", "--pull=never", "--network", network,
            "--network-alias", "db", "--label", "com.docker.compose.project=" + project,
            "--label", "com.docker.compose.service=db", "--mount",
            "type=volume,src=" + volume + ",dst=/var/lib/postgresql/data",
            "--health-cmd", "pg_isready -h 127.0.0.1 -U " + self.pg.BOOTSTRAP,
            "--health-interval", "1s", "--health-timeout", "1s", "--health-retries", "30",
            "--env", "POSTGRES_HOST_AUTH_METHOD=trust", "--env", "POSTGRES_USER=" + self.pg.BOOTSTRAP,
            "--env", "POSTGRES_DB=" + self.pg.DATABASE, self.pg.IMAGE).decode().strip()
        self.containers.append(identity)
        for _ in range(80):
            try:
                self.docker("exec", identity, "pg_isready", "-h", "127.0.0.1", "-U", self.pg.BOOTSTRAP)
                info = json.loads(self.docker("inspect", identity))[0]
                if info.get("State", {}).get("Health", {}).get("Status") == "healthy":
                    return identity
            except RuntimeError:
                pass
            time.sleep(.25)
        self.fail("fixture DB did not reach healthy baseline")

    def setUp(self):
        # The preparer is still being authored in its own lane. Use a disposable
        # staging overlay so its source bytes enter the temporary Git fixture;
        # no writer tree is edited.
        self.overlay_temp = tempfile.TemporaryDirectory(prefix="belib-prepare-overlay-")
        overlay = Path(self.overlay_temp.name) / "scripts/staging"
        shutil.copytree(STAGE, overlay)
        shutil.copyfile(PREP_SOURCE, overlay / "prepare-retained.py")
        runtime = tuple(entry.RUNTIME) + (() if "prepare-retained.py" in entry.RUNTIME else ("prepare-retained.py",))
        with patch.object(entry, "STAGE", overlay), patch.object(entry, "RUNTIME", runtime):
            super().setUp()
        self.addCleanup(self.overlay_temp.cleanup)
        self.prep_operation = uuid.uuid4().hex
        self.prepared = self.root / self.prep_operation
        self.prepared.mkdir(mode=0o700)
        self.addCleanup(self.cleanup_prepare_project)
        self.image_tags = {}
        self.fixture_build_tags = {}
        self.fixture_base_tags = {}
        self.fixture_tagged_images = set()
        self.prep_module = load(self.source / "scripts/staging/prepare-retained.py", "prepare_fixture")
        self.configure_preparer()
        self.prep_docker = self.prep_module.Docker()
        self.prep_original_run = self.prep_docker.run
        self.prep_calls = []
        self.prep_docker.run = self.counted_docker
        self.image_tags = {name: "belib-retained:" + self.prep_operation + "-" + name
                           for name in ("backend", "frontend", "migrate")}
        self.build_fixture_images()
        self.original_database = self.source_port.fingerprint()
        self.admission = {"source_root": str(self.source), "source_sha": self.sha,
                          "operation_id": self.prep_operation, "prepared_directory": str(self.prepared),
                          "env_file": str(self.env)}

    def counted_docker(self, *args, **kwargs):
        self.prep_calls.append(tuple(args))
        try:
            return self.prep_original_run(*args, **kwargs)
        except self.prep_module.PreparationBlocked:
            if args and args[0] == "run" and any(str(item).startswith("belib.retained-book-inventory=") for item in args):
                probe = subprocess.run([*self.prep_docker.prefix, *args], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=self.prep_docker.env, stdin=subprocess.DEVNULL,
                    timeout=kwargs.get("timeout", 900), check=False)
                # The command only scans the disposable, read-only books fixture;
                # keep its bounded error output local to this opt-in test.
                print("FIXTURE BOOK HELPER EXIT " + str(probe.returncode) + " STDERR " +
                      probe.stderr[:4096].decode("utf-8", "replace"), file=sys.stderr)
            raise

    def configure_preparer(self):
        prep = self.prep_module
        prep.PROJECT = self.project
        prep.OLD_DB_ID = self.old_db
        prep.OLD_APP_SHA = self.fixture_module.upgrade.OLD_APPLICATION_SHA
        prep.OLD_BACKEND_IMAGE = OLD_IMAGE
        prep.OLD_FRONTEND_IMAGE = OLD_IMAGE
        prep.BOOK_VOLUME = self.project + "_staging-book-files"
        prep.DB_VOLUME = self.project + "_staging-postgres-data"
        prep.OLD_DB_IMAGE = self.pg.IMAGE
        load_original = prep._load

        def fixture_load(path, name):
            module = load_original(path, name)
            if name == "prepare_seed":
                module.COMPOSE_PROJECT = self.project
            elif name == "prepare_pg":
                module.PROJECT = self.project
                module.RETAINED_ID = self.old_db
                module.IMAGE = self.pg.IMAGE
            elif name in ("prepare_cutover", "finalize_cutover"):
                module.PROJECT = self.project
            elif name == "prepare_launch_guard":
                module.PROJECT = self.project
                module.PROJECT_LOCK_PATH = self.root / "prepare-project.lock"
            elif name == "finalize_upgrade":
                self.configure_upgrade(module)
            return module

        prep._load = fixture_load

    def configure_upgrade(self, upgrade):
        prep = self.prep_module
        upgrade.EXPECTED_PROJECT = self.project
        upgrade.OLD_APPLICATION_SHA = prep.OLD_APP_SHA
        upgrade.OLD_BACKEND_IMAGE = prep.OLD_BACKEND_IMAGE
        upgrade.OLD_FRONTEND_IMAGE = prep.OLD_FRONTEND_IMAGE
        upgrade.OLD_DATABASE_LINEAGE = prep.PRIOR_SOURCE if hasattr(prep, "PRIOR_SOURCE") else upgrade.OLD_DATABASE_LINEAGE
        upgrade.OLD_DB_CONTAINER_ID = self.old_db
        upgrade.OLD_DB_IMAGE = prep.OLD_DB_IMAGE
        upgrade.RETAINED_VOLUMES = (prep.BOOK_VOLUME, prep.DB_VOLUME)

    def build_fixture_images(self):
        """Build tiny local Node health images; skip expensive BeLib app builds."""
        old_tags = json.loads(self.docker("image", "inspect", OLD_IMAGE))[0].get("RepoTags") or []
        if not old_tags:
            self.fail("Disposable Node fixture base needs an existing local tag")
        context = self.root / ("fixture-images-" + self.prep_operation)
        context.mkdir()
        for service, port in (("backend", 3000), ("frontend", 8080)):
            cmd = ("require('http').createServer((q,r)=>{r.writeHead(200);r.end('fixture health')})"
                   f".listen({port},'0.0.0.0')")
            dockerfile = (f"FROM {old_tags[0]}\n"
                          f"LABEL org.opencontainers.image.revision={self.sha}\n"
                          f"CMD [\"node\",\"-e\",{json.dumps(cmd)}]\n")
            build = context / service
            build.mkdir()
            (build / "Dockerfile").write_text(dockerfile)
            build_tag = "belib-prep-fixture:" + self.prep_operation + "-built-" + service
            image = self.docker("build", "--network", "none", "--quiet", "--tag", build_tag,
                                str(build)).decode().strip()
            self.images.append(image)
            self.fixture_build_tags[service] = build_tag
            self.fixture_tagged_images.add(image)
            setattr(self, service + "_fixture_image", image)
        self.fixture_images = {"backend": self.backend_fixture_image,
                               "frontend": self.frontend_fixture_image,
                               "migrate": self.migrationimage}
        # Seed a tiny synthetic book into this run's unique disposable volume;
        # the production inventory helper then hashes the real mounted bytes.
        self.docker("run", "--rm", "--pull=never", "--network", "none", "--volume",
                    self.books + ":/home/node", "--entrypoint", "node", self.backend_fixture_image,
                    "-e", "const fs=require('fs');fs.mkdirSync('/home/node/books',{recursive:true});"+
                         "fs.writeFileSync('/home/node/books/disposable-fixture.epub','fixture bytes')")

    def fixture_compose(self, directory, source, env_file, project, override, docker, *args, timeout=1800):
        if args and args[0] == "build":
            # Build is the sole mocked phase. Use fixed, local fixture images;
            # actual Compose create/start, PostgreSQL, backup and restore run.
            return b"fixture application build skipped\n"
        return self.prep_module._compose_original(directory, source, env_file, project, override,
                                                  docker, *args, timeout=timeout)

    def prepare_call(self, admission, *, lose_build_response=False, lose_create_response=False):
        prep = self.prep_module
        compose_original = prep._compose
        prep._compose_original = compose_original
        contract_original = prep._container_contract
        lost = {"build": False, "create": False}

        def diagnose_contract(*args):
            try:
                return contract_original(*args)
            except Exception:
                _docker, info, service, _project, _intent, image_id, _source, _values = args
                config = info.get("Config", {})
                image_config = json.loads(_docker.run("image", "inspect", image_id))[0].get("Config", {})
                print("FIXTURE CONTRACT DIFF " + service + " " + repr({
                    key: (image_config.get(key), config.get(key))
                    for key in ("Entrypoint", "Cmd", "WorkingDir", "User")
                }), file=sys.stderr)
                raise

        def compose(directory, source, env_file, project, override, docker, *args, timeout=1800):
            if args and args[0] == "build":
                journal = json.loads((self.prepared / "preparation-journal.json").read_text())
                self.assertEqual(journal["phase"], "building")
                intent = journal["intent"]
                for service, image in self.fixture_images.items():
                    tag = self.image_tags[service]
                    # Build a tiny local derivative so the fake app images carry
                    # the same durable operation/source labels Compose BuildKit
                    # would attach. This makes response-loss recovery exercise
                    # real Docker image metadata and exact operation tags.
                    build = self.root / ("fixture-recovery-" + self.prep_operation + "-" + service)
                    build.mkdir(exist_ok=True)
                    base_tag = "belib-prep-fixture:" + self.prep_operation + "-base-" + service
                    if base_tag not in self.fixture_base_tags.values():
                        self.docker("image", "tag", image, base_tag)
                        self.fixture_base_tags[service] = base_tag
                        # Removing the unique base tag in our cleanup may also
                        # delete an otherwise untagged migration fixture image;
                        # do not let inherited cleanup retry its old image ID.
                        self.fixture_tagged_images.add(image)
                    labels = [f"belib.retained-prepare-intent={intent}",
                              f"belib.retained-prepare-source={self.sha}"]
                    if service in ("backend", "frontend"):
                        labels.append(f"org.opencontainers.image.revision={self.sha}")
                    dockerfile = f"FROM {base_tag}\n" + "".join(f"LABEL {label}\n" for label in labels)
                    (build / "Dockerfile").write_text(dockerfile)
                    existing = subprocess.run([*self.prep_docker.prefix, "image", "inspect", tag],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.prep_docker.env,
                        stdin=subprocess.DEVNULL, timeout=30, check=False)
                    if existing.returncode != 0:
                        tagged_id = self.docker("build", "--network", "none", "--quiet", "--tag", tag,
                                                str(build)).decode().strip()
                        self.images.append(tagged_id)
                        self.fixture_tagged_images.add(tagged_id)
                    tagged = json.loads(self.docker("image", "inspect", tag))[0]
                    image_labels = tagged.get("Config", {}).get("Labels") or {}
                    self.assertEqual(image_labels.get("belib.retained-prepare-intent"), intent)
                    self.assertEqual(image_labels.get("belib.retained-prepare-source"), self.sha)
                    self.assertEqual(image_labels.get("org.opencontainers.image.revision"),
                                     self.sha if service in ("backend", "frontend") else None)
                if lose_build_response and not lost["build"]:
                    lost["build"] = True
                    raise RuntimeError("simulated lost response after disposable image build/tag effects")
                return b"fixture application build skipped\n"
            result = compose_original(directory, source, env_file, project, override, docker, *args,
                                     timeout=timeout)
            if lose_create_response and args and args[0] == "create" and not lost["create"]:
                lost["create"] = True
                raise RuntimeError("simulated lost response after successful disposable Compose create")
            return result

        with patch.object(prep, "_compose", side_effect=compose), \
             patch.object(prep, "_container_contract", side_effect=diagnose_contract):
            return prep.prepare(admission, docker=self.prep_docker)

    def cleanup_prepare_project(self):
        project = self.project + "-upgrade-" + self.prep_operation
        # Remove only this unique project and its exact operation-owned resources.
        try:
            rows = self.docker("ps", "--all", "--no-trunc", "--filter",
                               "label=com.docker.compose.project=" + project, "--format", "{{.ID}}").decode().splitlines()
            for identity in rows:
                if len(identity) == 64 and all(c in "0123456789abcdef" for c in identity):
                    self.docker("rm", "--force", identity)
        except Exception:
            pass
        for name in (project + "_isolated", project + "_ingress"):
            try:
                self.docker("network", "rm", name)
            except Exception:
                pass
        try:
            self.docker("volume", "rm", project + "_staging-postgres-data")
        except Exception:
            pass
        for tag in (*self.image_tags.values(), *self.fixture_build_tags.values(),
                    *self.fixture_base_tags.values()):
            try:
                self.docker("image", "rm", tag)
            except Exception:
                pass
        self.images = [image for image in self.images if image not in self.fixture_tagged_images]

    def sign_prepared_evidence(self):
        f = self.fixture
        upgrade = self.fixture_module.upgrade
        now = int(time.time())
        def payload(name):
            return json.loads((self.prepared / name).read_text())["payload_template"]
        network_payload = {**payload("network-inventory-request.json"),
                           "evidence_issuer": f.network_issuer,
                           "issued_at": now, "expires_at": now + 120}
        roots_payload = {**payload("backup-roots-request.json"),
                         "evidence_issuer": f.root_issuer,
                         "issued_at": now, "expires_at": now + 120}
        restore_payload = {**payload("restore-attestation-request.json"),
                           "evidence_issuer": f.issuer,
                           "issued_at": now, "expires_at": now + 120}
        envelopes = {
            "network-inventory.json": f.sign_with(f.network_private_key, network_payload),
            "backup-roots.json": f.sign_with(f.root_private_key, roots_payload),
            "restore-attestation.json": f.sign_with(f.private_key, restore_payload),
        }
        for name, envelope in envelopes.items():
            path = self.prepared / name
            path.write_bytes(upgrade.canonical_json(envelope))
            path.chmod(0o600)
        authorities = {
            "network": {"issuer": f.network_issuer, "public_key_pem": f.network_public_pem.decode(),
                        "fingerprint": f.network_fingerprint},
            "roots": {"issuer": f.root_issuer, "public_key_pem": f.root_public_pem.decode(),
                      "fingerprint": f.root_fingerprint},
            "restore": {"issuer": f.issuer, "public_key_pem": f.public_pem.decode(),
                        "fingerprint": f.fingerprint},
        }
        finalized = self.prep_module.finalize(self.prepared, authorities)
        candidate = json.loads((self.prepared / "candidate-config.json").read_text())
        self.assertFalse(candidate["enabled"], "finalization must leave candidate disabled")
        self.assertEqual(finalized["manifest_sha256"], hashlib.sha256(
            upgrade.canonical_json(json.loads((self.prepared / "manifest.json").read_text()))).hexdigest())
        # Model the separate root adoption as a new config file. Keep the
        # finalizer's disabled artifact unchanged and hand only the copied
        # root-adopted fixture to the existing operator/bootstrap test seam.
        adopted = json.loads(json.dumps(candidate))
        adopted["enabled"] = True
        self.retained_config = self.root / "root-adopted-retained.json"
        self.retained_config.write_bytes(upgrade.canonical_json(adopted))
        self.retained_config.chmod(0o600)
        self.bootstrap.RETAINED_CONFIG = self.retained_config
        self.fixture.manifest = json.loads((self.prepared / "manifest.json").read_text())
        self.fixture.manifest_sha = finalized["manifest_sha256"]
        self.fixture.backup_path = self.prepared / "backup.dump"
        self.fixture.backup_sha = finalized["backup_sha256"]
        self.fixture.query_report = upgrade.canonical_json(json.loads((self.prepared / "query-report.json").read_text()))
        self.fixture.network_inventory = envelopes["network-inventory.json"]
        self.fixture.root_contract = envelopes["backup-roots.json"]
        self.fixture.envelope = envelopes["restore-attestation.json"]
        self.fixture.payload = restore_payload
        self.fixture.source_sha = self.sha
        self.fixture.network_ids = network_payload["network_ids"]
        self.fixture.network_public_pem = f.network_public_pem
        self.fixture.network_fingerprint = f.network_fingerprint
        self.fixture.root_public_pem = f.root_public_pem
        self.fixture.root_fingerprint = f.root_fingerprint
        self.fixture.public_pem = f.public_pem
        self.fixture.fingerprint = f.fingerprint
        self.fixture.issuer = f.issuer
        self.fixture_module.upgrade.EXPECTED_PROJECT = self.project
        self.fixture_module.upgrade.OLD_DB_CONTAINER_ID = self.old_db
        self.fixture_module.upgrade.OLD_DATABASE_LINEAGE = self.fixture.manifest["database_lineage"]
        self.fixture_module.upgrade.RETAINED_VOLUMES = (self.project + "_staging-book-files",
                                                        self.project + "_staging-postgres-data")
        return finalized

    def test_prepare_finalize_execute_replay_and_response_loss(self):
        # Wrong source is rejected before the first Docker command/resource effect.
        bad = dict(self.admission, source_sha="0" * 40)
        with self.assertRaises(self.prep_module.PreparationBlocked):
            self.prepare_call(bad)
        self.assertEqual(self.prep_calls, [])

        # Build-response loss occurs after all three exact operation tags have
        # real image labels. Re-entry accepts only those intent/source-bound
        # tags and resumes the same build phase.
        with self.assertRaisesRegex(RuntimeError, "lost response after disposable image build"):
            self.prepare_call(self.admission, lose_build_response=True)
        build_journal = json.loads((self.prepared / "preparation-journal.json").read_text())
        self.assertEqual(build_journal["phase"], "building")
        self.assertRegex(build_journal["intent"], r"^[0-9a-f]{64}$")
        for service, tag in self.image_tags.items():
            image = json.loads(self.docker("image", "inspect", tag))[0]
            labels = image.get("Config", {}).get("Labels") or {}
            self.assertEqual(labels.get("belib.retained-prepare-intent"), build_journal["intent"])
            self.assertEqual(labels.get("belib.retained-prepare-source"), self.sha)
        build_project = self.project + "-upgrade-" + self.prep_operation
        self.assertEqual(self.docker("ps", "--all", "--filter",
            "label=com.docker.compose.project=" + build_project, "--format", "{{.ID}}").decode().strip(), "")

        # Create-response loss occurs after actual Compose created all four
        # candidate containers. Re-entry adopts only that complete exact set.
        with self.assertRaisesRegex(RuntimeError, "lost response"):
            self.prepare_call(self.admission, lose_create_response=True)
        candidate_project = self.project + "-upgrade-" + self.prep_operation
        ids = self.docker("ps", "--all", "--no-trunc", "--filter",
                          "label=com.docker.compose.project=" + candidate_project,
                          "--format", "{{.ID}}").decode().splitlines()
        self.assertEqual(len(ids), 4)
        receipt = self.prepare_call(self.admission)
        self.assertTrue(receipt["candidate_apps_stopped"])
        self.assertTrue(receipt["candidate_database_started"])
        self.assertFalse(receipt["original_database_mutated"])
        self.assertTrue((self.prepared / "backup.dump").is_file())
        self.assertTrue((self.prepared / "restore-report.json").is_file())
        self.assertTrue((self.prepared / "query-report.json").is_file())
        for service, identity in receipt["candidate_container_ids"].items():
            state = json.loads(self.docker("inspect", identity))[0]["State"]
            self.assertEqual(state["Running"], service == "db", service)
        self.assertEqual(self.source_port.fingerprint(), self.original_database)

        finalized = self.sign_prepared_evidence()
        self.assertFalse(json.loads((self.prepared / "candidate-config.json").read_text())["enabled"])
        self.digest = finalized["backup_sha256"]
        self.original = self.original_database
        self.fixture.manifest_sha = finalized["manifest_sha256"]
        self.fixture_module.upgrade.OLD_APPLICATION_SHA = self.prep_module.OLD_APP_SHA
        self.fixture_module.upgrade.OLD_BACKEND_IMAGE = self.prep_module.OLD_BACKEND_IMAGE
        self.fixture_module.upgrade.OLD_FRONTEND_IMAGE = self.prep_module.OLD_FRONTEND_IMAGE
        self.fixture_module.upgrade.OLD_DB_IMAGE = self.prep_module.OLD_DB_IMAGE
        self.fixture_module.upgrade.RETAINED_VOLUMES = (self.prep_module.BOOK_VOLUME, self.prep_module.DB_VOLUME)
        self.fixture_module.upgrade.EXPECTED_PROJECT = self.project
        self.fixture_module.upgrade.OLD_DB_CONTAINER_ID = self.old_db
        self.fixture_module.upgrade.OLD_DATABASE_LINEAGE = self.fixture.manifest["database_lineage"]
        self.fixture_module.upgrade.OLD_APPLICATION_SHA = self.prep_module.OLD_APP_SHA
        # The reused executor proof module must share this test fixture's
        # synthetic prior OCI identities (same fixed local image for both apps).
        proof = self.executor.proof
        proof.EXPECTED_PROJECT = self.project
        proof.OLD_DB_CONTAINER_ID = self.old_db
        proof.OLD_DATABASE_LINEAGE = self.fixture.manifest["database_lineage"]
        proof.OLD_APPLICATION_SHA = self.prep_module.OLD_APP_SHA
        proof.OLD_BACKEND_IMAGE = self.prep_module.OLD_BACKEND_IMAGE
        proof.OLD_FRONTEND_IMAGE = self.prep_module.OLD_FRONTEND_IMAGE
        proof.OLD_DB_IMAGE = self.prep_module.OLD_DB_IMAGE
        proof.RETAINED_VOLUMES = (self.prep_module.BOOK_VOLUME, self.prep_module.DB_VOLUME)
        # A successful grant traverses the existing bootstrap -> launcher ->
        # executor path using the signed preparation artifacts and real Docker/PG.
        status, output, errors, grant = self.invoke()
        self.assertEqual(status, 0, errors + "\n" + output)
        self.assertEqual(self.source_port.fingerprint(), self.original_database)
        result = json.loads((self.state / (grant["nonce"] + ".result.json")).read_text())
        self.assertEqual(result["exit_code"], 0)
        used = json.loads((self.state / (grant["nonce"] + ".used")).read_text())
        self.assertEqual(used["source_sha"], self.sha)

        # Replay the exact same nonce and signed grant payload; bootstrap rejects
        # before another executor effect or receipt rewrite.
        prior_result = (self.state / (grant["nonce"] + ".result.json")).read_bytes()
        prior_source = self.source_port.fingerprint()
        with patch.object(entry.uuid, "uuid4", side_effect=[
                SimpleNamespace(hex=grant["nonce"][:32]), SimpleNamespace(hex=grant["nonce"][32:])]), \
             patch.object(entry.time, "time", return_value=grant["issued_at"]):
            replay_status, _, _, replay_grant = self.invoke()
        self.assertEqual(replay_grant, grant)
        self.assertEqual(replay_status, 1)
        self.assertEqual((self.state / (grant["nonce"] + ".result.json")).read_bytes(), prior_result)
        self.assertEqual(self.source_port.fingerprint(), prior_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
