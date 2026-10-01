#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import secrets
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, NamedTuple
from urllib.parse import urlparse


MAX_BOOK_FILE_SIZE = 200 * 1024 * 1024
LOGICAL_BOOK_ID_PATTERN = re.compile(r"^(?:lb_[0-9a-f]{16}|stg_[0-9a-f]{20})$")
WINDOWS_LIBRARY_DRIVE = "F:"
WINDOWS_LIBRARY_DIRECTORY = "電子書籍"
UNCATEGORIZED_NAME = "未分類"
AUTHOR_PLACEHOLDERS = {"1", "著者要確認"}


class InventoryError(ValueError):
    pass


class PlanError(ValueError):
    pass


class ExecuteError(RuntimeError):
    pass


class ImportPlan(NamedTuple):
    logical_book_id: str
    title: str
    author_name: str
    category_name: str
    page_turn_direction: str
    file_path: Path | None
    status: str
    reason: str | None


def normalize_author(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if not normalized or normalized in AUTHOR_PLACEHOLDERS:
        return ""
    return normalized


def normalize_category(value: Any) -> str:
    if not isinstance(value, str):
        return UNCATEGORIZED_NAME
    normalized = value.strip()
    if not normalized or normalized == "要確認":
        return UNCATEGORIZED_NAME
    return normalized


def page_turn_direction(category: Any) -> str:
    return "rtl" if isinstance(category, str) and category.strip() == "漫画" else "ltr"


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath([str(path), str(root)]) == str(root)
    except ValueError:
        return False


def map_source_directory(source_locator: str, library_root: Path) -> Path:
    if not isinstance(source_locator, str) or not source_locator.strip():
        raise PlanError("source locator is required")

    windows_path = PureWindowsPath(source_locator.strip())
    if windows_path.drive.upper() != WINDOWS_LIBRARY_DRIVE or windows_path.root != "\\":
        raise PlanError("source locator is outside the expected Windows library drive")

    parts = windows_path.parts
    if len(parts) < 2 or parts[1] != WINDOWS_LIBRARY_DIRECTORY:
        raise PlanError("source locator is outside the expected Windows library root")

    relative_parts = parts[2:]
    if not relative_parts:
        raise PlanError("source locator must identify a logical-book directory")
    if any(part in {"", ".", ".."} for part in relative_parts):
        raise PlanError("source locator contains an unsafe path segment")
    if any("\x00" in part for part in relative_parts):
        raise PlanError("source locator contains NUL")

    root = Path(library_root).expanduser().resolve()
    candidate = root.joinpath(*relative_parts).resolve(strict=False)
    if not _is_within(candidate, root):
        raise PlanError("source locator escapes the Linux library root")
    return candidate


def select_book_file(book_directory: Path) -> tuple[Path | None, str | None]:
    try:
        if not book_directory.exists():
            return None, "missing_book_directory"
        if not book_directory.is_dir():
            return None, "source_not_directory"
        entries = [
            entry
            for entry in book_directory.iterdir()
            if not entry.is_symlink() and entry.is_file()
        ]
    except OSError:
        return None, "source_read_error"

    epubs = sorted((entry for entry in entries if entry.suffix.lower() == ".epub"), key=lambda p: p.name)
    pdfs = sorted((entry for entry in entries if entry.suffix.lower() == ".pdf"), key=lambda p: p.name)

    if len(epubs) > 1:
        return None, "ambiguous_epub"
    if len(epubs) == 1:
        selected = epubs[0]
    elif len(pdfs) > 1:
        return None, "ambiguous_pdf"
    elif len(pdfs) == 1:
        selected = pdfs[0]
    else:
        return None, "missing_book_file"

    try:
        size = selected.stat().st_size
    except OSError:
        return None, "source_read_error"
    if size < 1:
        return None, "invalid_file_size"
    if size > MAX_BOOK_FILE_SIZE:
        return None, "file_too_large"
    if len(selected.name) > 255:
        return None, "file_name_too_long"
    return selected, None


def load_inventory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        handle = Path(path).open("r", encoding="utf-8")
    except OSError as exc:
        raise InventoryError(f"cannot read inventory: {path}") from exc

    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise InventoryError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(row, dict):
                raise InventoryError(f"inventory line {line_number} must be an object")

            logical_book_id = row.get("logical_book_id")
            if not isinstance(logical_book_id, str) or not LOGICAL_BOOK_ID_PATTERN.fullmatch(logical_book_id):
                raise InventoryError(f"invalid logical_book_id at line {line_number}")
            if logical_book_id in seen:
                raise InventoryError(f"duplicate logical_book_id: {logical_book_id}")
            seen.add(logical_book_id)

            title = row.get("title")
            if not isinstance(title, str) or not title.strip():
                raise InventoryError(f"title is required for {logical_book_id}")
            if len(title.strip()) > 255:
                raise InventoryError(f"title is too long for {logical_book_id}")

            rows.append(row)

    rows.sort(key=lambda row: row["logical_book_id"])
    return rows


def plan_entry(row: dict[str, Any], library_root: Path) -> ImportPlan:
    logical_book_id = str(row["logical_book_id"])
    title = str(row["title"]).strip()
    author_name = normalize_author(row.get("author"))
    category_name = normalize_category(row.get("category"))
    direction = page_turn_direction(row.get("category"))

    if len(author_name) > 255:
        return ImportPlan(
            logical_book_id, title, author_name, category_name, direction, None, "skip", "author_too_long"
        )

    source_locator = row.get("source_locator")
    if not isinstance(source_locator, str) or not source_locator.strip():
        return ImportPlan(
            logical_book_id, title, author_name, category_name, direction, None, "skip", "missing_source_locator"
        )

    try:
        book_directory = map_source_directory(source_locator, library_root)
    except PlanError:
        return ImportPlan(
            logical_book_id, title, author_name, category_name, direction, None, "skip", "invalid_source_locator"
        )

    file_path, reason = select_book_file(book_directory)
    if file_path is None:
        return ImportPlan(
            logical_book_id, title, author_name, category_name, direction, None, "skip", reason
        )
    return ImportPlan(
        logical_book_id, title, author_name, category_name, direction, file_path, "ready", None
    )


def _ledger_entries(ledger: dict[str, Any]) -> dict[str, Any]:
    if ledger.get("schema_version") == 1 and isinstance(ledger.get("entries"), dict):
        return ledger["entries"]
    return ledger


def is_completed_in_ledger(ledger: dict[str, Any], logical_book_id: str) -> bool:
    entry = _ledger_entries(ledger).get(logical_book_id)
    return isinstance(entry, dict) and entry.get("status") in {"registered", "duplicate"}


def default_ledger_path() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return base / "belib" / "personal-library-import-ledger.json"


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecuteError(f"cannot read ledger: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("entries"), dict):
        raise ExecuteError("unsupported or invalid import ledger")
    return payload


def write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            temporary_path = Path(handle.name)
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        os.replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def record_ledger(
    ledger: dict[str, Any],
    logical_book_id: str,
    status: str,
    *,
    book_id: int | None = None,
    server_code: str | None = None,
) -> None:
    entry: dict[str, Any] = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if book_id is not None:
        entry["book_id"] = book_id
    if server_code:
        entry["server_code"] = server_code
    ledger["entries"][logical_book_id] = entry


def plan_inventory(
    rows: list[dict[str, Any]],
    library_root: Path,
    ledger: dict[str, Any],
    *,
    limit: int | None,
) -> list[ImportPlan]:
    plans: list[ImportPlan] = []
    ready_count = 0
    for row in rows:
        logical_book_id = row["logical_book_id"]
        if is_completed_in_ledger(ledger, logical_book_id):
            base = plan_entry(row, library_root)
            plans.append(base._replace(status="skip", reason="ledger_completed", file_path=None))
            continue

        plan = plan_entry(row, library_root)
        plans.append(plan)
        if plan.status == "ready":
            ready_count += 1
            if limit is not None and ready_count >= limit:
                break
    return plans


class BelibClient:
    def __init__(self, base_url: str, *, timeout: float = 300.0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ExecuteError("BELIB_API_BASE_URL must be an http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ExecuteError("BELIB_API_BASE_URL must not contain credentials, query, or fragment")

        self.scheme = parsed.scheme
        self.hostname = parsed.hostname
        self.port = parsed.port
        self.path_prefix = parsed.path.rstrip("/")
        self.timeout = timeout
        self.cookies: dict[str, str] = {}
        self.network_requests = 0

    def _connection(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(
                self.hostname,
                self.port,
                timeout=self.timeout,
                context=ssl.create_default_context(),
            )
        return http.client.HTTPConnection(self.hostname, self.port, timeout=self.timeout)

    def _target(self, endpoint: str) -> str:
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{self.path_prefix}{endpoint}" or "/"

    def _cookie_header(self) -> str:
        return "; ".join(f"{key}={value}" for key, value in self.cookies.items())

    def _capture_cookies(self, headers: list[tuple[str, str]]) -> None:
        for key, value in headers:
            if key.lower() != "set-cookie":
                continue
            pair = value.split(";", 1)[0]
            if "=" not in pair:
                continue
            name, cookie_value = pair.split("=", 1)
            name = name.strip()
            if name:
                self.cookies[name] = cookie_value.strip()

    @staticmethod
    def _decode_response(data: bytes) -> Any:
        if not data:
            return {}
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"message": "non-JSON response"}

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        request_headers = dict(headers or {})
        if self.cookies:
            request_headers["Cookie"] = self._cookie_header()

        connection = self._connection()
        self.network_requests += 1
        try:
            connection.request(method, self._target(endpoint), body=body, headers=request_headers)
            response = connection.getresponse()
            response_headers = response.getheaders()
            self._capture_cookies(response_headers)
            data = response.read()
            return response.status, self._decode_response(data)
        except (OSError, http.client.HTTPException) as exc:
            raise ExecuteError(f"HTTP request failed for {endpoint}") from exc
        finally:
            connection.close()

    def login(self, email: str, password: str) -> None:
        body = json.dumps({"email": email, "password": password}).encode("utf-8")
        status, payload = self._request(
            "POST",
            "/api/auth/sign-in/email",
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        if status != 200:
            code = payload.get("code") if isinstance(payload, dict) else None
            suffix = f" ({code})" if code else ""
            raise ExecuteError(f"admin login failed with HTTP {status}{suffix}")
        if not self.cookies:
            raise ExecuteError("admin login did not return a session cookie")

    def get_categories(self) -> list[dict[str, Any]]:
        status, payload = self._request("GET", "/api/v1/admin/categories")
        if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("categories"), list):
            raise ExecuteError(f"cannot load admin categories (HTTP {status})")
        return [category for category in payload["categories"] if isinstance(category, dict)]

    @staticmethod
    def _multipart_field(boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n'
            "\r\n"
            f"{value}\r\n"
        ).encode("utf-8")

    @staticmethod
    def _safe_multipart_filename(name: str) -> str:
        return name.replace("\\", "_").replace('"', "'").replace("\r", "_").replace("\n", "_")

    def register_book(
        self,
        plan: ImportPlan,
        *,
        category_id: int,
        publication_scope: str,
    ) -> tuple[int, Any]:
        if plan.file_path is None:
            raise ExecuteError("cannot upload a plan without a file")

        suffix = plan.file_path.suffix.lower()
        mime_type = "application/epub+zip" if suffix == ".epub" else "application/pdf"
        boundary = f"----belib-import-{secrets.token_hex(16)}"
        fields = [
            ("title", plan.title),
            ("authorName", plan.author_name),
            ("publisher", ""),
            ("publishedAt", ""),
            ("categoryId", str(category_id)),
            ("pageTurnDirection", plan.page_turn_direction),
            ("description", ""),
            ("publicationScope", publication_scope),
        ]
        field_chunks = [self._multipart_field(boundary, name, value) for name, value in fields]
        filename = self._safe_multipart_filename(plan.file_path.name)
        file_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n"
            "\r\n"
        ).encode("utf-8")
        closing = f"\r\n--{boundary}--\r\n".encode("ascii")

        try:
            file_size = plan.file_path.stat().st_size
        except OSError as exc:
            raise ExecuteError("source file cannot be stat'ed before upload") from exc
        if file_size < 1 or file_size > MAX_BOOK_FILE_SIZE:
            raise ExecuteError("source file size changed after planning")

        content_length = sum(len(chunk) for chunk in field_chunks) + len(file_header) + file_size + len(closing)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(content_length),
        }
        if self.cookies:
            headers["Cookie"] = self._cookie_header()

        connection = self._connection()
        self.network_requests += 1
        try:
            connection.putrequest("POST", self._target("/api/v1/admin/book-registrations"))
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()

            for chunk in field_chunks:
                connection.send(chunk)
            connection.send(file_header)
            with plan.file_path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    connection.send(chunk)
            connection.send(closing)

            response = connection.getresponse()
            self._capture_cookies(response.getheaders())
            data = response.read()
            return response.status, self._decode_response(data)
        except (OSError, http.client.HTTPException) as exc:
            raise ExecuteError(f"book upload failed for {plan.logical_book_id}") from exc
        finally:
            connection.close()


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely plan or execute BeLib bulk registration from a caller-supplied logical inventory."
    )
    parser.add_argument("--inventory", type=Path, required=True, help="v3 owned-inventory JSONL payload")
    parser.add_argument(
        "--library-root",
        type=Path,
        required=True,
        help=r"Linux directory corresponding exactly to F:\電子書籍",
    )
    parser.add_argument("--execute", action="store_true", help="perform Admin API registrations; default is dry-run")
    parser.add_argument("--limit", type=_positive_int, help="maximum number of ready books to upload in this run")
    parser.add_argument(
        "--publication-scope",
        choices=("admin_only", "all_users"),
        default="admin_only",
        help="publication scope for imported books",
    )
    parser.add_argument("--api-base", help="BeLib base URL; otherwise BELIB_API_BASE_URL")
    parser.add_argument("--ledger", type=Path, default=default_ledger_path())
    parser.add_argument("--verbose", action="store_true")
    return parser


def _summary(plans: list[ImportPlan], *, execute: bool, network_requests: int) -> dict[str, Any]:
    reasons: dict[str, int] = {}
    ready = 0
    for plan in plans:
        if plan.status == "ready":
            ready += 1
        elif plan.reason:
            reasons[plan.reason] = reasons.get(plan.reason, 0) + 1
    return {
        "execute": execute,
        "planned": len(plans),
        "ready": ready,
        "skipped": len(plans) - ready,
        "skip_reasons": dict(sorted(reasons.items())),
        "network_requests": network_requests,
    }


def _verbose_plan(plan: ImportPlan) -> None:
    payload = {
        "logical_book_id": plan.logical_book_id,
        "status": plan.status,
        "reason": plan.reason,
        "file_type": plan.file_path.suffix.lower().lstrip(".") if plan.file_path else None,
        "category": plan.category_name,
        "page_turn_direction": plan.page_turn_direction,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)


def _execute_plans(
    plans: list[ImportPlan],
    args: argparse.Namespace,
    ledger: dict[str, Any],
) -> dict[str, Any]:
    api_base = args.api_base or os.environ.get("BELIB_API_BASE_URL")
    email = os.environ.get("BELIB_ADMIN_EMAIL")
    password = os.environ.get("BELIB_ADMIN_PASSWORD")
    missing = [
        name
        for name, value in (
            ("BELIB_API_BASE_URL/--api-base", api_base),
            ("BELIB_ADMIN_EMAIL", email),
            ("BELIB_ADMIN_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ExecuteError("missing execute configuration: " + ", ".join(missing))

    client = BelibClient(str(api_base))
    client.login(str(email), str(password))
    categories = client.get_categories()
    category_ids = {
        category.get("name"): category.get("id")
        for category in categories
        if isinstance(category.get("name"), str) and isinstance(category.get("id"), int)
    }

    result = _summary(plans, execute=True, network_requests=client.network_requests)
    result.update({"registered": 0, "duplicates": 0, "failed": 0})
    ledger_path = Path(args.ledger).expanduser()

    for plan in plans:
        if args.verbose:
            _verbose_plan(plan)
        if plan.status != "ready":
            continue

        category_id = category_ids.get(plan.category_name)
        if not isinstance(category_id, int):
            category_id = category_ids.get(UNCATEGORIZED_NAME)
        if not isinstance(category_id, int):
            raise ExecuteError(
                f"category '{plan.category_name}' is unavailable and fallback '{UNCATEGORIZED_NAME}' does not exist"
            )

        try:
            status, payload = client.register_book(
                plan,
                category_id=category_id,
                publication_scope=args.publication_scope,
            )
        except ExecuteError:
            record_ledger(ledger, plan.logical_book_id, "failed", server_code="NETWORK_OR_SOURCE_ERROR")
            write_ledger(ledger_path, ledger)
            raise

        code = payload.get("code") if isinstance(payload, dict) and isinstance(payload.get("code"), str) else None
        if status == 201:
            book_id = payload.get("id") if isinstance(payload, dict) and isinstance(payload.get("id"), int) else None
            record_ledger(ledger, plan.logical_book_id, "registered", book_id=book_id)
            result["registered"] += 1
        elif status == 409 and code == "DUPLICATE_FILE":
            record_ledger(ledger, plan.logical_book_id, "duplicate", server_code=code)
            result["duplicates"] += 1
        elif status in {400, 409, 413}:
            record_ledger(ledger, plan.logical_book_id, "failed", server_code=code or f"HTTP_{status}")
            result["failed"] += 1
        elif status in {401, 403}:
            record_ledger(ledger, plan.logical_book_id, "failed", server_code=code or f"HTTP_{status}")
            write_ledger(ledger_path, ledger)
            raise ExecuteError(f"authorization failed during import (HTTP {status})")
        else:
            record_ledger(ledger, plan.logical_book_id, "failed", server_code=code or f"HTTP_{status}")
            write_ledger(ledger_path, ledger)
            raise ExecuteError(f"server returned HTTP {status} during import")

        write_ledger(ledger_path, ledger)

    result["network_requests"] = client.network_requests
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inventory = load_inventory(args.inventory)
        ledger = load_ledger(Path(args.ledger).expanduser())
        plans = plan_inventory(
            inventory,
            args.library_root,
            ledger,
            limit=args.limit,
        )

        if args.verbose and not args.execute:
            for plan in plans:
                _verbose_plan(plan)

        if not args.execute:
            print(json.dumps(_summary(plans, execute=False, network_requests=0), ensure_ascii=False, sort_keys=True))
            return 0

        result = _execute_plans(plans, args, ledger)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["failed"] == 0 else 1
    except (InventoryError, ExecuteError, OSError) as exc:
        print(
            json.dumps(
                {
                    "execute": bool(args.execute),
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
