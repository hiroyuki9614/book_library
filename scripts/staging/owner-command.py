#!/usr/bin/python3
"""Print one digest-pinned owner-loader command; this generator performs no privileged action."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import sys
from pathlib import Path

HOME = Path("/home/hiroyuki")
PLAN_PATH = HOME / ".local/state/belib-staging/tls-work-context/owner-plan.json"
MAX_HELPER = 128 * 1024
MAX_PLAN = 16 * 1024
PROJECT = "belib-isolated-i5"


class PackageError(Exception):
    pass


def read_verified(path: Path, *, limit: int, expected_sha: str, uid: int, mode: int | tuple[int, ...]) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
        before = os.fstat(fd)
        mode_ok = stat.S_IMODE(before.st_mode) in mode if isinstance(mode, tuple) else stat.S_IMODE(before.st_mode) == mode
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != uid
                or not mode_ok or before.st_size > limit):
            raise PackageError("Pinned input must be a bounded regular file with exact owner/mode")
        chunks = bytearray()
        while len(chunks) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
        after = os.fstat(fd)
        if (len(chunks) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise PackageError("Pinned input changed while being read")
        raw = bytes(chunks)
    except OSError as exc:
        raise PackageError("Cannot safely read fixed package input") from exc
    finally:
        if "fd" in locals():
            os.close(fd)
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise PackageError("Pinned input digest does not match caller-provided review value")
    return raw


def make_loader_command(source_sha: str, helper_sha: str, plan_sha: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise PackageError("Source SHA must be a full lowercase Git SHA")
    if not re.fullmatch(r"[0-9a-f]{64}", helper_sha) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha):
        raise PackageError("Helper and plan digests must be lowercase SHA-256")
    source_root = HOME / ".local/state/belib-staging/snapshots" / source_sha
    helper_path = source_root / "scripts/staging/owner-serve-cas.py"
    helper_raw = read_verified(helper_path, limit=MAX_HELPER, expected_sha=helper_sha, uid=1000, mode=(0o644, 0o644))
    plan_raw = read_verified(PLAN_PATH, limit=MAX_PLAN, expected_sha=plan_sha, uid=1000, mode=0o600)
    try:
        plan = json.loads(plan_raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError("Fixed owner plan is malformed") from exc
    if (not isinstance(plan, dict) or plan.get("source_sha") != source_sha
            or set(plan) != {"schema", "disposition", "source_sha", "prior_application_sha", "retained_state_sha256",
                             "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256",
                             "nonce", "expires_at"}
            or plan.get("schema") != "belib-tls-owner-retirement-plan/v1"
            or plan.get("disposition") != "retire-old-fedora-bri-image-publication-handler-only"
            or json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() != plan_raw):
        raise PackageError("Owner plan does not bind the selected admitted source")
    if hashlib.sha256(helper_raw).hexdigest() != helper_sha or hashlib.sha256(plan_raw).hexdigest() != plan_sha:
        raise PackageError("Pinned bytes failed a second package-generation digest check")

    helper_literal = json.dumps(str(helper_path))
    plan_literal = json.dumps(str(PLAN_PATH))
    helper_hash_literal = json.dumps(helper_sha)
    plan_hash_literal = json.dumps(plan_sha)
    loader = "\n".join((
        "import hashlib, json, os, stat",
        "os.chdir('/')",
        "os.umask(0o077)",
        f"HP={helper_literal}; PP={plan_literal}; HH={helper_hash_literal}; PH={plan_hash_literal}",
        "def read(path, limit, sha, mode):",
        " flags=os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK",
        " fd=os.open(path, flags)",
        " try:",
        "  st=os.fstat(fd)",
        "  mode_ok=stat.S_IMODE(st.st_mode) in mode if isinstance(mode,tuple) else stat.S_IMODE(st.st_mode)==mode",
        "  if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=1000 or not mode_ok or st.st_size>limit: raise RuntimeError('unsafe pinned input')",
        "  chunks=bytearray()",
        "  while len(chunks)<=limit:",
        "   part=os.read(fd,min(65536,limit+1-len(chunks)))",
        "   if not part: break",
        "   chunks.extend(part)",
        "  after=os.fstat(fd)",
        "  if len(chunks)!=st.st_size or (st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns): raise RuntimeError('pinned input changed')",
        "  data=bytes(chunks)",
        " finally: os.close(fd)",
        " if hashlib.sha256(data).hexdigest()!=sha: raise RuntimeError('pinned digest mismatch')",
        " return data",
        "helper=read(HP,131072,HH,(0o644,0o644)); plan=read(PP,16384,PH,0o600)",
        "ns={'__name__':'_belib_reviewed_owner_helper','__file__':HP}",
        "exec(compile(helper,HP,'exec'),ns,ns)",
        "print(json.dumps(ns['run'](plan,PH),sort_keys=True,separators=(',',':')))",
    ))
    return "/usr/bin/sudo -- /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C /usr/bin/python3 -I -S -B -c " + shlex.quote(loader)


def make_loader_command_v2(source_sha: str, helper_sha: str, plan_sha: str,
                           owner_nonce: str, prepare_receipt_sha256: str) -> str:
    """Pin the v2 owner plan and candidate receipt from one nonce-scoped directory."""
    if (not re.fullmatch(r"[0-9a-f]{40}", source_sha)
            or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in
                   (helper_sha, plan_sha, prepare_receipt_sha256))
            or not re.fullmatch(r"[0-9a-f]{64}", owner_nonce)):
        raise PackageError("V2 source, digests, or owner nonce are malformed")
    source_root = HOME / ".local/state/belib-staging/snapshots" / source_sha
    helper_path = source_root / "scripts/staging/owner-serve-cas.py"
    context = HOME / ".local/state/belib-staging/tls-work-context" / owner_nonce
    plan_path = context / "owner-plan.json"
    prepared_path = context / "prepare-receipt.json"
    helper_raw = read_verified(helper_path, limit=MAX_HELPER, expected_sha=helper_sha, uid=0, mode=(0o644,))
    plan_raw = read_verified(plan_path, limit=MAX_PLAN, expected_sha=plan_sha, uid=1000, mode=0o600)
    prepared_raw = read_verified(prepared_path, limit=MAX_PLAN, expected_sha=prepare_receipt_sha256, uid=1000, mode=0o600)
    try:
        plan = json.loads(plan_raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
        receipt = json.loads(prepared_raw.decode("utf-8", "strict"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError("V2 owner inputs are malformed") from exc
    plan_fields = {"schema", "disposition", "source_sha", "prior_application_sha", "retained_state_sha256",
        "serve_config_sha256", "serve_etag", "certificate_sha256", "tls_config_sha256", "nonce", "expires_at",
        "upgrade_receipt_nonce", "upgrade_receipt_sha256", "operation_id", "project"}
    receipt_fields = {"schema", "plan_sha256", "retained_state_sha256", "nginx_id", "source_sha", "tls_config_sha256",
        "certificate_sha256", "approval_nonce", "evidence_sha256", "prepared", "owner_nonce",
        "upgrade_receipt_nonce", "upgrade_receipt_sha256", "operation_id", "project", "previous_nginx_id",
        "nginx_configuration_sha256", "nginx_image_id", "nginx_network_ids", "unchanged_runtime_sha256"}
    operation = plan.get("operation_id") if isinstance(plan, dict) else None
    project = PROJECT + "-upgrade-" + str(operation)
    if (not isinstance(plan, dict) or set(plan) != plan_fields or plan.get("schema") != "belib-tls-owner-retirement-plan/v2"
            or plan.get("disposition") != "retire-old-fedora-bri-image-publication-handler-only"
            or plan.get("source_sha") != source_sha or plan.get("project") != project
            or plan.get("prior_application_sha") != source_sha
            or plan.get("nonce") != owner_nonce
            or json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() != plan_raw
            or not isinstance(receipt, dict) or set(receipt) != receipt_fields
            or receipt.get("schema") != "belib-staging-tls-prepare-receipt/v2"
            or receipt.get("owner_nonce") != owner_nonce or receipt.get("plan_sha256") != plan_sha
            or receipt.get("source_sha") != source_sha or receipt.get("project") != project
            or receipt.get("operation_id") != operation or receipt.get("prepared") is not True
            or receipt.get("retained_state_sha256") != plan.get("retained_state_sha256")
            or json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() != prepared_raw):
        raise PackageError("V2 owner plan and prepare receipt do not form the exact pinned pair")
    helper_literal = json.dumps(str(helper_path))
    plan_literal = json.dumps(str(plan_path))
    receipt_literal = json.dumps(str(prepared_path))
    hashes = tuple(json.dumps(value) for value in (helper_sha, plan_sha, prepare_receipt_sha256))
    loader = "\n".join((
        "import hashlib, json, os, stat", "os.chdir('/')", "os.umask(0o077)",
        f"HP={helper_literal}; PP={plan_literal}; RP={receipt_literal}; HH={hashes[0]}; PH={hashes[1]}; RH={hashes[2]}",
        "def read(path,limit,sha,uid,mode):",
        " flags=os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK; fd=os.open(path,flags)",
        " try:", "  st=os.fstat(fd)",
        "  if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=uid or stat.S_IMODE(st.st_mode)!=mode or st.st_size>limit: raise RuntimeError('unsafe pinned input')",
        "  data=os.read(fd,limit+1)", "  if len(data)!=st.st_size or hashlib.sha256(data).hexdigest()!=sha: raise RuntimeError('pinned digest mismatch')",
        "  return data", " finally: os.close(fd)",
        "helper=read(HP,131072,HH,0,0o644); plan=read(PP,16384,PH,1000,0o600); receipt=read(RP,16384,RH,1000,0o600)",
        "ns={'__name__':'_belib_reviewed_owner_helper','__file__':HP}", "exec(compile(helper,HP,'exec'),ns,ns)",
        "print(json.dumps(ns['run'](plan,PH,receipt,RH),sort_keys=True,separators=(',',':')))",
    ))
    return "/usr/bin/sudo -- /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root LANG=C LC_ALL=C /usr/bin/python3 -I -S -B -c " + shlex.quote(loader)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PackageError("Owner plan contains duplicate keys")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--helper-sha256", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--owner-nonce")
    parser.add_argument("--prepare-receipt-sha256")
    args = parser.parse_args(argv)
    if args.owner_nonce is None and args.prepare_receipt_sha256 is None:
        command = make_loader_command(args.source_sha, args.helper_sha256, args.plan_sha256)
    elif args.owner_nonce is not None and args.prepare_receipt_sha256 is not None:
        command = make_loader_command_v2(args.source_sha, args.helper_sha256, args.plan_sha256,
                                         args.owner_nonce, args.prepare_receipt_sha256)
    else:
        raise PackageError("V2 owner nonce and prepare-receipt digest must be supplied together")
    print(command)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackageError as exc:
        print(f"owner-command: {exc}", file=sys.stderr)
        raise SystemExit(2)
