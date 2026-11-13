#!/usr/bin/env python3
"""Read-only resource feasibility screen; it does not validate or perform recovery."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


# Sanity floors reject vacuous operator inputs. They are not recovery-sizing advice.
MINIMUM_FLOOR_BYTES = 1024**3
MINIMUM_FLOOR_MEM_BYTES = 512 * 1024**2
MAX_INPUT_INTEGER = 2**63 - 1
DECIMAL_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


class InputError(ValueError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InputError(message)


def _positive_integer(value: str, option: str, floor: int) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise InputError(option)
    number = int(value)
    if number > MAX_INPUT_INTEGER or number < floor:
        raise InputError(option)
    return number


def _pressure_limit(value: str, option: str) -> Decimal:
    if not DECIMAL_PATTERN.fullmatch(value):
        raise InputError(option)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise InputError(option) from exc
    if not number.is_finite() or number < 0 or number > 100:
        raise InputError(option)
    return number


def _read_mem_available(path: Path) -> int:
    matches = []
    with path.open("r", encoding="ascii") as sensor:
        for line in sensor:
            if line.startswith("MemAvailable:"):
                fields = line.split()
                if len(fields) == 3 and fields[2] == "kB" and fields[1].isdigit():
                    matches.append(int(fields[1]) * 1024)
                else:
                    raise InputError("meminfo")
    if len(matches) != 1 or matches[0] > MAX_INPUT_INTEGER:
        raise InputError("meminfo")
    return matches[0]


def _read_io_full_pressure(path: Path) -> tuple[Decimal, Decimal]:
    rows = []
    with path.open("r", encoding="ascii") as sensor:
        for line in sensor:
            fields = line.split()
            if fields and fields[0] == "full":
                rows.append(fields[1:])
    if len(rows) != 1:
        raise InputError("io_pressure")
    values: dict[str, Decimal] = {}
    seen: set[str] = set()
    for field in rows[0]:
        if "=" not in field:
            raise InputError("io_pressure")
        key, raw = field.split("=", 1)
        if key not in {"avg10", "avg60", "avg300", "total"} or key in seen:
            raise InputError("io_pressure")
        seen.add(key)
        if key == "total":
            if not raw.isdigit() or int(raw) > MAX_INPUT_INTEGER:
                raise InputError("io_pressure")
            continue
        if not DECIMAL_PATTERN.fullmatch(raw):
            raise InputError("io_pressure")
        value = Decimal(raw)
        if not value.is_finite() or value < 0 or value > 100:
            raise InputError("io_pressure")
        values[key] = value
    if seen != {"avg10", "avg60", "avg300", "total"}:
        raise InputError("io_pressure")
    return values["avg10"], values["avg60"]


def _mount_id(path: Path, mountinfo: Path) -> int:
    """Return the mount ID covering path using Linux's current mount table."""
    absolute = Path(os.path.abspath(path))
    matches: list[tuple[int, int]] = []
    with mountinfo.open("r", encoding="ascii") as source:
        for line in source:
            fields = line.split()
            if len(fields) < 6 or "-" not in fields:
                raise InputError("mountinfo")
            try:
                mount_id = int(fields[0])
            except ValueError as exc:
                raise InputError("mountinfo") from exc
            # mountinfo escapes spaces, tabs, newlines, and backslashes as octal.
            mountpoint = re.sub(
                r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), fields[4]
            )
            point = Path(mountpoint)
            if absolute == point or point in absolute.parents:
                matches.append((len(point.parts), mount_id))
    if not matches:
        raise InputError("mountinfo")
    deepest = max(depth for depth, _mount_id in matches)
    ids = {mount_id for depth, mount_id in matches if depth == deepest}
    if len(ids) != 1:
        raise InputError("mountinfo")
    return ids.pop()


def _open_directory_without_symlinks(path: Path) -> int:
    """Open each component with O_NOFOLLOW, pinning the final directory."""
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        raise InputError("target")
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise InputError("target")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise InputError("target")
        return descriptor
    except FileNotFoundError as exc:
        os.close(descriptor)
        raise InputError("target") from exc
    except BaseException:
        os.close(descriptor)
        raise


def measure(
    target: str,
    meminfo: Path,
    io_pressure: Path,
    disk_usage=None,
    mountinfo: Path = Path("/proc/self/mountinfo"),
) -> dict[str, int | Decimal]:
    """Read the destination filesystem and two Linux resource sensors only."""
    if not target:
        raise InputError("target")
    target_path = Path(target)
    if not target_path.is_absolute():
        target_path = Path(os.path.abspath(target_path))
    first_mount_id = _mount_id(target_path, mountinfo)
    descriptor = _open_directory_without_symlinks(target_path)
    try:
        first_stat = os.fstat(descriptor)
        if disk_usage is None:
            fs = os.fstatvfs(descriptor)
            free_bytes = fs.f_bavail * fs.f_frsize
        else:
            # Injection is used by unit tests; production measures the pinned FD.
            free_bytes = disk_usage(target_path).free
    finally:
        os.close(descriptor)
    second_mount_id = _mount_id(target_path, mountinfo)
    second_descriptor = _open_directory_without_symlinks(target_path)
    try:
        second_stat = os.fstat(second_descriptor)
    finally:
        os.close(second_descriptor)
    if (
        first_mount_id != second_mount_id
        or (first_stat.st_dev, first_stat.st_ino) != (second_stat.st_dev, second_stat.st_ino)
    ):
        raise InputError("target_changed")
    if not isinstance(free_bytes, int) or free_bytes < 0 or free_bytes > MAX_INPUT_INTEGER:
        raise InputError("filesystem")
    mem_available_bytes = _read_mem_available(meminfo)
    io_full_avg10, io_full_avg60 = _read_io_full_pressure(io_pressure)
    return {
        "free_bytes": free_bytes,
        "mem_available_bytes": mem_available_bytes,
        "io_full_avg10_percent": io_full_avg10,
        "io_full_avg60_percent": io_full_avg60,
    }


def _parser() -> JsonArgumentParser:
    parser = JsonArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--target", required=True, help="existing proposed destination directory")
    parser.add_argument("--min-free-bytes", required=True)
    parser.add_argument("--min-memavailable-bytes", required=True)
    parser.add_argument("--max-io-full-avg10", required=True, help="operator cap, percent")
    parser.add_argument("--max-io-full-avg60", required=True, help="operator cap, percent")
    return parser


def _json_number(value: Any) -> int | float:
    if isinstance(value, Decimal):
        return float(value)
    return value


def main(argv: list[str] | None = None) -> int:
    resources: dict[str, Any] = {
        "free_bytes": None,
        "mem_available_bytes": None,
        "io_full_avg10_percent": None,
        "io_full_avg60_percent": None,
    }
    code = "BLOCK_UNMEASURED"
    try:
        args = _parser().parse_args(argv)
        min_free = _positive_integer(args.min_free_bytes, "min_free", MINIMUM_FLOOR_BYTES)
        min_mem = _positive_integer(args.min_memavailable_bytes, "min_mem", MINIMUM_FLOOR_MEM_BYTES)
        max_avg10 = _pressure_limit(args.max_io_full_avg10, "max_avg10")
        max_avg60 = _pressure_limit(args.max_io_full_avg60, "max_avg60")
        observed = measure(args.target, Path("/proc/meminfo"), Path("/proc/pressure/io"))
        resources = {key: _json_number(value) for key, value in observed.items()}
        if (
            observed["free_bytes"] >= min_free
            and observed["mem_available_bytes"] >= min_mem
            and observed["io_full_avg10_percent"] <= max_avg10
            and observed["io_full_avg60_percent"] <= max_avg60
        ):
            code = "RESOURCE_CANDIDATE_ONLY"
        else:
            code = "BLOCK_RESOURCE"
    except (InputError, OSError, UnicodeError, ValueError, OverflowError):
        code = "BLOCK_UNMEASURED"
    print(json.dumps({"code": code, "resources": resources}, allow_nan=False, sort_keys=True))
    return 0 if code == "RESOURCE_CANDIDATE_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
