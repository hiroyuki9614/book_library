#!/usr/bin/env python3
"""Check the pinned staging nginx TLS configuration in an ephemeral CI container."""
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

IMAGE = "nginx:1.27-alpine@sha256:62223d644fa234c3a1cc785ee14242ec47a77364226f1c811d2f669f96dc2ac8"


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise SystemExit("This synthetic Docker check is restricted to the existing GitHub-hosted CI job.")
    root = Path(__file__).resolve().parents[2]
    if IMAGE not in (root / "compose.staging.yaml").read_text():
        raise SystemExit("Pinned nginx image differs from the staging Compose source.")
    configs = {
        "nginx-tls.conf": "/etc/nginx/nginx.conf",
        "nginx-tls-default.conf": "/etc/nginx/conf.d/default.conf",
        "nginx-tls-locations.conf": "/etc/nginx/conf.d/belib-proxy-locations.conf",
    }
    for name in configs:
        source = root / "scripts/staging" / name
        if not source.is_file() or source.is_symlink() or "," in str(source):
            raise SystemExit("TLS config must be a regular source file.")
    subprocess.run(["docker", "pull", "--platform", "linux/amd64", IMAGE], check=True, timeout=180)
    name = "belib-tls-syntax-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="belib-tls-ci-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
        directory = Path(temporary)
        cert, key = directory / "fullchain.pem", directory / "privkey.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=stg-bl.hiroyuki9614.com",
            "-addext", "subjectAltName=DNS:stg-bl.hiroyuki9614.com",
            "-keyout", str(key), "-out", str(cert),
        ], check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # Only this disposable CI key is readable by the capability-restricted container.
        # The enclosing temporary directory remains private; real i5 keys stay 0600.
        key.chmod(0o444)
        command = [
            "docker", "run", "--rm", "--name", name, "--pull", "never",
            "--platform", "linux/amd64", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--cap-add", "CHOWN", "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,nosuid,nodev,mode=1777",
            "--tmpfs", "/var/cache/nginx:rw,nosuid,nodev,mode=0755",
            "--add-host", "backend:127.0.0.1", "--add-host", "frontend:127.0.0.1",
            "--add-host", "ubuntu-worker.tail63cd2d.ts.net:100.109.171.70",
        ]
        for source_name, target in configs.items():
            command += ["--mount", f"type=bind,src={root / 'scripts/staging' / source_name},dst={target},readonly"]
        for source, target in [(cert, "/run/belib-tls/fullchain.pem"), (key, "/run/belib-tls/privkey.pem")]:
            command += ["--mount", f"type=bind,src={source},dst={target},readonly"]
        command += ["--entrypoint", "nginx", IMAGE, "-t"]
        try:
            subprocess.run(command, check=True, timeout=45)
        finally:
            subprocess.run(["docker", "rm", "-f", name], timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    print("NGINX_TLS_SYNTAX_PASS: synthetic certificate only; no runtime TLS acceptance.")


if __name__ == "__main__":
    main()
