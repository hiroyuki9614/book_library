#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("operator_packet", HERE / "package-operator-adoption.py")
packet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packet)
KEY = b"-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAw1k8oqGkMOUb8KJb6thJY5GUtENwWuLGvWrKi6oBXaA=\n-----END PUBLIC KEY-----\n"


class PacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repository"
        subprocess.run(["git", "init", "--template=", str(self.repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=packet.ENV)
        (self.repo / "scripts/staging").mkdir(parents=True)
        (self.repo / packet.BOOTSTRAP).write_text(
            f"PUBLIC_KEY = {KEY!r}\nPUBLIC_FINGERPRINT = {packet.PUBLIC_FINGERPRINT!r}\n")
        (self.repo / "scripts/staging/launch-guard.py").write_text("# launcher fixture\n")
        (self.repo / "read me.txt").write_text("exact committed bytes\n")
        (self.repo / "tool.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.repo / "tool.sh").chmod(0o755)
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
        self.parent_sha = self.git("rev-parse", "HEAD").decode().strip()
        (self.repo / "second.txt").write_text("second commit\n")
        self.git("add", "second.txt")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "second")
        self.sha = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args):
        return packet.git(self.repo, *args)

    def test_exact_commit_determinism_dirty_exclusion_clean_git_reconstruction(self):
        marker = self.root / "hook-executed"
        hook = self.repo / ".git/hooks/post-checkout"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        self.git("config", "credential.helper", "!touch should-not-run")
        (self.repo / "read me.txt").write_text("uncommitted bytes\n")
        (self.repo / "private-untracked").write_text("must not package\n")
        one, two = self.root / "one", self.root / "two"
        packet.package(self.repo, self.sha, one)
        packet.package(self.repo, self.sha, two)
        self.assertEqual({p.name: p.read_bytes() for p in one.iterdir()},
                         {p.name: p.read_bytes() for p in two.iterdir()})
        config = json.loads((one / "config-template.json").read_bytes())
        self.assertFalse(config["enabled"])
        self.assertNotIn("private-untracked", config["manifest"])
        self.assertEqual(config["manifest"]["tool.sh"]["mode"], 0o755)
        clean = self.root / "clean"
        subprocess.run([*packet.GIT, "clone", "--template=", "--branch=adoption-source", str(one / "source.bundle"), str(clean)],
                       env=packet.ENV, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(packet.git(clean, "rev-parse", "HEAD").decode().strip(), self.sha)
        self.assertEqual(packet.git(clean, "rev-parse", "HEAD^").decode().strip(), self.parent_sha)
        self.assertEqual(packet.git(clean, "status", "--porcelain"), b"")
        self.assertEqual((clean / "read me.txt").read_text(), "exact committed bytes\n")
        self.assertFalse((clean / ".git/hooks/post-checkout").exists())
        self.assertNotIn("credential", (clean / ".git/config").read_text())
        self.assertFalse(marker.exists())
        expected = {path: {"sha256": packet.digest((clean / path).read_bytes()),
                           "mode": (clean / path).stat().st_mode & 0o777}
                    for path in config["manifest"]}
        self.assertEqual(config["manifest"], expected)

    def test_reviewed_config_is_explicit_and_must_match_all_source_fields(self):
        template_dir = self.root / "template"
        packet.package(self.repo, self.sha, template_dir)
        config = json.loads((template_dir / "config-template.json").read_bytes())
        config["enabled"] = True
        supplied = self.root / "reviewed.json"
        supplied.write_bytes(packet.canonical(config))
        output = self.root / "configured"
        result = packet.package(self.repo, self.sha, output, supplied)
        self.assertTrue(result["explicit_config_provided"])
        self.assertEqual((output / "operator-config.json").read_bytes(), supplied.read_bytes())
        config["enabled"] = 1
        supplied.write_bytes(packet.canonical(config))
        with self.assertRaises(packet.InvalidPacket):
            packet.package(self.repo, self.sha, self.root / "invalid-boolean", supplied)
        config["enabled"] = True
        config["source_sha"] = "f" * 40
        supplied.write_bytes(packet.canonical(config))
        with self.assertRaises(packet.InvalidPacket):
            packet.package(self.repo, self.sha, self.root / "mismatch", supplied)
        self.assertFalse((self.root / "mismatch").exists())

    def test_ref_alias_existing_output_key_change_and_symlink_are_rejected(self):
        with self.assertRaises(packet.InvalidPacket):
            packet.package(self.repo, "HEAD", self.root / "alias")
        with self.assertRaises(packet.InvalidPacket):
            packet.package(self.repo, self.sha, self.repo)
        (self.repo / packet.BOOTSTRAP).write_text("PUBLIC_KEY = b'changed'\nPUBLIC_FINGERPRINT = 'changed'\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "bad key")
        bad = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaises(packet.InvalidPacket):
            packet.package(self.repo, bad, self.root / "bad-key")
        os.symlink("/etc/passwd", self.repo / "unsafe-link")
        self.git("add", "unsafe-link")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "bad link")
        bad = self.git("rev-parse", "HEAD").decode().strip()
        with self.assertRaises(packet.InvalidPacket):
            packet.package(self.repo, bad, self.root / "bad-link")

    def test_packet_checksums_and_no_install_effects(self):
        output = self.root / "packet"
        result = packet.package(self.repo, self.sha, output)
        self.assertEqual(result["status"], "candidate_not_installed")
        self.assertFalse((output / "operator-config.json").exists())
        for line in (output / "SHA256SUMS").read_text().splitlines():
            sha, name = line.split("  ", 1)
            self.assertEqual(sha, packet.digest((output / name).read_bytes()))
        steps = (output / "OPERATOR-STEPS.md").read_text()
        self.assertIn("test -f operator-config.json", steps)
        self.assertIn("Config supplied and matched to this source: false", steps)
        self.assertIn("retained-upgrade topology config is NOT supplied", steps)
        self.assertIn("Preserve operator nonce/result state", steps)

    def test_operator_precheck_is_isolated_from_hostile_python_environment(self):
        output = self.root / "packet"
        packet.package(self.repo, self.sha, output)
        steps = (output / "OPERATOR-STEPS.md").read_text()
        self.assertLess(steps.index("export PATH=/usr/bin:/bin"), steps.index('test "$(hostname)"'))
        marker = self.root / "injected"
        (self.root / "pathlib.py").write_text(f"open({str(marker)!r}, 'w').write('executed')\nraise RuntimeError('injected')\n")
        command = '/usr/bin/python3 -I -B - "$PACKET" <<\'PY\'\n'
        code = steps.split(command, 1)[1].split("\nPY\n", 1)[0]
        result = subprocess.run(["/usr/bin/python3", "-I", "-B", "-", str(output)],
                                input=code.encode(), cwd=self.root, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={**os.environ, "PYTHONPATH": str(self.root),
                                "PYTHONOPTIMIZE": "1", "PATH": str(self.root)}, timeout=5)
        # /tmp is writable by others, so this review fixture must never become trusted.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"owner/mode is unsafe", result.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
