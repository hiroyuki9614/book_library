#!/usr/bin/env python3
"""Build a reviewable, offline adoption packet; never install or grant authority."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import shutil
import subprocess
import tempfile

MAX_FILES = 50000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024
PUBLIC_FINGERPRINT = "8f49fad277be0d1c05bf0b75246a5895a8b3489ddaaeef4f43a3e8b90e25e629"
BOOTSTRAP = "scripts/staging/operator-bootstrap.py"
ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C",
       "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": "/dev/null",
       "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1"}
GIT = ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
       "-c", "core.attributesFile=/dev/null", "-c", "core.autocrlf=false",
       "-c", "pack.compression=9", "-c", "pack.useBitmaps=false"]


class InvalidPacket(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(repository, *args):
    return subprocess.run([*GIT, "-C", str(repository), *args], env=ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=120, check=True).stdout


def checked_json(path):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise InvalidPacket("duplicate config field")
            result[key] = value
        return result
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise InvalidPacket("config must be a bounded regular JSON file")
    return json.loads(path.read_bytes(), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(InvalidPacket("invalid number")))


def source_manifest(repository, revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise InvalidPacket("a full lowercase 40-character commit SHA is required")
    if git(repository, "cat-file", "-t", revision).strip() != b"commit":
        raise InvalidPacket("revision must identify a commit")
    # Plumbing reads only committed objects: no checkout, filters, hooks, or dirty files.
    entries = git(repository, "ls-tree", "-r", "-z", "--full-tree", revision).split(b"\0")
    manifest, bootstrap, total = {}, None, 0
    for entry in filter(None, entries):
        metadata, raw_path = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        path = raw_path.decode("utf-8")
        parts = PurePosixPath(path).parts
        if (mode not in {b"100644", b"100755"} or kind != b"blob" or not parts
                or path.startswith("/") or any(p in {".", "..", ".git"} for p in parts)
                or any(c in path for c in "\r\n\0\\") or path != PurePosixPath(path).as_posix()):
            raise InvalidPacket("source contains an unsafe path, symlink, or submodule")
        size = int(git(repository, "cat-file", "-s", object_id.decode()))
        total += size
        if size > MAX_FILE_BYTES or total > MAX_BYTES or len(manifest) >= MAX_FILES:
            raise InvalidPacket("source exceeds bootstrap size bounds")
        data = git(repository, "cat-file", "blob", object_id.decode())
        if len(data) != size:
            raise InvalidPacket("source object readback mismatch")
        manifest[path] = {"sha256": digest(data), "mode": 0o755 if mode == b"100755" else 0o644}
        if path == BOOTSTRAP:
            bootstrap = data
    if not manifest or bootstrap is None or "scripts/staging/launch-guard.py" not in manifest:
        raise InvalidPacket("fixed source does not contain the operator and launcher")
    # Inspect constants without importing or executing candidate code.
    constants = {}
    for node in ast.parse(bootstrap).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"PUBLIC_KEY", "PUBLIC_FINGERPRINT"}:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
    if (constants.get("PUBLIC_FINGERPRINT") != PUBLIC_FINGERPRINT
            or not isinstance(constants.get("PUBLIC_KEY"), bytes)
            or digest(constants["PUBLIC_KEY"]) != PUBLIC_FINGERPRINT):
        raise InvalidPacket("existing signer public-key pin changed")
    return manifest, bootstrap


def operator_steps(revision, configured):
    return f'''# Conditional adoption: {revision}

This packet is a candidate, not an installation or signed grant. The existing
authorized operator must review this exact commit, packet SHA256SUMS and config.
Target: ubuntu-worker, child principal hiroyuki (UID/GID 1000), staging only.
No new signer, key, principal, sudo rule, service or scheduler is created.
The retained-upgrade topology config is NOT supplied or adopted by this packet.
Its independent reviewed config is required before an upgrade-retained operation.

Config supplied and matched to this source: {str(configured).lower()}.
Without operator-config.json, installation below intentionally cannot proceed.
The generated config-template.json is disabled; editing enabled is not approval.
No grant is packaged. After adoption use the existing external signer and the
operator's --grant PATH / --receipt NONCE entry points with current approval.

Before using the commands: the authorized operator must first copy the fixed
packet files into a root-owned, non-writable-by-others directory. Pin that absolute
directory in PACKET and verify its externally reviewed SHA256SUMS digest (the
checksum file alone is not a trust source). Verify no staging operation is active,
and use the already-authorized root operator. Never run these commands merely
because this document exists. Package creation needs no root privileges.

```bash
set -euo pipefail
export PATH=/usr/bin:/bin
test "$(hostname)" = ubuntu-worker
test "$(id -u)" = 0
test "$(id -u hiroyuki)" = 1000
test "$(id -g hiroyuki)" = 1000
: "${{PACKET:?set the absolute reviewed packet directory}}"
test "${{PACKET:0:1}}" = /
/usr/bin/python3 -I -B - "$PACKET" <<'PY'
import os, stat, sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.is_absolute() or p.resolve() != p:
    raise SystemExit('packet path is not absolute and symlink-free')
targets = [Path('/opt/belib-staging/sources'), Path('/etc/belib-staging/operator-bootstrap.json'),
           Path('/usr/local/libexec/belib-staging/operator-bootstrap.py'),
           Path('/var/lib/belib-staging/operator'), Path('/var/lib/belib-staging/adoption-backups')]
existing = [item for target in targets for item in [*target.parents, target]
            if item.exists() or item.is_symlink()]
for item in [*p.parents, p, *p.iterdir(), *existing]:
    s = item.lstat()
    if s.st_uid != 0 or stat.S_ISLNK(s.st_mode) or s.st_mode & 0o022:
        raise SystemExit('packet or target path owner/mode is unsafe')
    if not (stat.S_ISDIR(s.st_mode) or (stat.S_ISREG(s.st_mode) and s.st_nlink == 1)):
        raise SystemExit('packet or target path type is unsafe')
PY
cd "$PACKET"
sha256sum --check SHA256SUMS
test -f operator-config.json
SHA={revision}
SRC=/opt/belib-staging/sources/$SHA
BACKUP=/var/lib/belib-staging/adoption-backups/$SHA
OP=/usr/local/libexec/belib-staging/operator-bootstrap.py
CFG=/etc/belib-staging/operator-bootstrap.json
install -d -m 0700 /var/lib/belib-staging/operator /var/lib/belib-staging/adoption-backups
exec 9>/var/lib/belib-staging/operator/project.lock
flock -n 9
test ! -e "$SRC"
test ! -e "$BACKUP"
install -d -m 0700 "$BACKUP"
for entry in "$OP" "$CFG"; do
  if test -e "$entry"; then cp --parents --preserve=mode,timestamps "$entry" "$BACKUP/"; fi
done
install -d -m 0755 /opt/belib-staging/sources /usr/local/libexec/belib-staging /etc/belib-staging
git_clean() {{ env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null GIT_NO_REPLACE_OBJECTS=1 /usr/bin/git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.autocrlf=false "$@"; }}
umask 022
git_clean init --template= "$SRC"
git_clean -C "$SRC" fetch --no-tags "$PACKET/source.bundle" refs/heads/adoption-source
git_clean -C "$SRC" checkout --detach "$SHA"
test "$(git_clean -C "$SRC" rev-parse HEAD)" = "$SHA"
test -z "$(git_clean -C "$SRC" status --porcelain=v1 --untracked-files=all)"
(cd "$SRC" && sha256sum --check "$PACKET/source-files.sha256")
cmp "$PACKET/operator-bootstrap.py" "$SRC/scripts/staging/operator-bootstrap.py"
install -m 0600 "$PACKET/config-template.json" "$CFG.pending"
mv "$CFG.pending" "$CFG"
install -m 0755 "$PACKET/operator-bootstrap.py" "$OP.pending"
mv "$OP.pending" "$OP"
install -m 0600 "$PACKET/operator-config.json" "$CFG.pending"
mv "$CFG.pending" "$CFG"
cmp "$PACKET/operator-config.json" "$CFG"
cmp "$PACKET/operator-bootstrap.py" "$OP"
```

All trusted path ancestors must be root-owned and not group/other-writable;
source directories must be traversable by UID 1000 and files must retain manifest
modes. Verify these properties before any signed call. The bundle carries Git
objects and one fixed ref, never the source checkout's config/hooks. Runtime
verify_source additionally checks ownership, modes, manifest and clean Git HEAD.
No application start, DB migration or ingress change is performed by installation.

Revoke future admissions under the same project lock:
```bash
install -m 0600 "$PACKET/config-template.json" "$CFG.pending"
mv "$CFG.pending" "$CFG"
cmp "$PACKET/config-template.json" "$CFG"
```
This does not terminate an already-running operation. Preserve operator nonce/result state across all changes.

Rollback under the same lock restores only saved operator/config files:
```bash
for entry in "$OP" "$CFG"; do
  if test -f "$BACKUP$entry"; then
    cp --preserve=mode,timestamps "$BACKUP$entry" "$entry.rollback"
    cmp "$BACKUP$entry" "$entry.rollback"
    mv "$entry.rollback" "$entry"
    cmp "$BACKUP$entry" "$entry"
  else
    rm -f -- "$entry"
  fi
done
```
Keep the new immutable source and rollback snapshot for investigation. This restores
admission code/config only; it never rolls back application/DB effects. Record the
adopted commit and receipt with the existing case, not a duplicate controller.
'''


def _pack_limit():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))


def package(repository, revision, output, config_path=None):
    repository, output = Path(repository).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise InvalidPacket("output must not already exist")
    manifest, bootstrap = source_manifest(repository, revision)
    template = {"schema": "belib-staging-operator/v1", "enabled": False,
                "source_sha": revision, "manifest": manifest}
    configured = None
    if config_path is not None:
        configured = checked_json(Path(config_path))
        if canonical(configured) != canonical({**template, "enabled": True}):
            raise InvalidPacket("explicit reviewed config must match enabled source/manifest exactly")
    if len(canonical(template)) > 4 * 1024 * 1024:
        raise InvalidPacket("source manifest exceeds operator config bound")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="operator-packet-", dir=output.parent) as temporary:
        root = Path(temporary)
        bundle = root / "source.bundle"
        with bundle.open("wb") as handle:
            handle.write(f"# v2 git bundle\n{revision} refs/heads/adoption-source\n\n".encode())
            handle.flush()
            subprocess.run([*GIT, "-C", str(repository), "pack-objects", "--stdout", "--revs",
                            "--no-reuse-delta", "--no-reuse-object", "--threads=1", "--window=0"],
                           input=(revision + "\n").encode(), stdout=handle, stderr=subprocess.PIPE,
                           env=ENV, timeout=120, preexec_fn=_pack_limit, check=True)
        if bundle.stat().st_size > MAX_BYTES:
            raise InvalidPacket("bundle exceeds size bound")
        # A clean scratch Git directory verifies completeness; no user config/hooks are copied.
        verify = root / "verify.git"
        subprocess.run([*GIT, "init", "--bare", "--template=", str(verify)], env=ENV,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=10)
        git(verify, "bundle", "verify", str(bundle))
        git(verify, "fetch", "--no-tags", str(bundle), "refs/heads/adoption-source:refs/heads/adoption-source")
        git(verify, "fsck", "--full", "--strict", "--no-reflogs")
        if git(verify, "rev-parse", "refs/heads/adoption-source").decode().strip() != revision:
            raise InvalidPacket("bundle commit readback mismatch")
        shutil.rmtree(verify)
        (root / "operator-bootstrap.py").write_bytes(bootstrap)
        (root / "config-template.json").write_bytes(canonical(template))
        if configured is not None:
            (root / "operator-config.json").write_bytes(canonical(configured))
        (root / "source-files.sha256").write_text("".join(
            f"{row['sha256']}  {path}\n" for path, row in sorted(manifest.items())))
        (root / "OPERATOR-STEPS.md").write_text(operator_steps(revision, configured is not None))
        packet = {"schema": "belib-staging-adoption-packet/v1", "status": "candidate_not_installed",
                  "repository": "hiroyuki9614/book_library", "source_sha": revision,
                  "signer_public_key_sha256": PUBLIC_FINGERPRINT,
                  "explicit_config_provided": configured is not None,
                  "files": {p.name: digest(p.read_bytes()) for p in sorted(root.iterdir())}}
        (root / "manifest.json").write_bytes(canonical(packet))
        (root / "SHA256SUMS").write_text("".join(
            f"{digest(p.read_bytes())}  {p.name}\n" for p in sorted(root.iterdir())))
        for path in root.iterdir():
            os.chmod(path, 0o600)
        os.rename(root, output)
    return packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewed-config", type=Path)
    args = parser.parse_args()
    result = package(args.repository, args.source_sha, args.output, args.reviewed_config)
    print(json.dumps({"status": result["status"], "source_sha": result["source_sha"],
                      "explicit_config_provided": result["explicit_config_provided"]}, sort_keys=True))


if __name__ == "__main__":
    main()
