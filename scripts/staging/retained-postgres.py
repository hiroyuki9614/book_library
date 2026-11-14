#!/usr/bin/env python3
"""Concrete retained PostgreSQL port for the independently admitted executor.

No CLI or approval decision lives here. ``for_retained`` binds the one existing
staging DB; its caller must already own the project lock and stop application
containers before backup. The constructor is also used by disposable tests.
No production database, volume or role name is accepted as an argument.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import resource
import selectors
import stat
import subprocess
import time
from pathlib import Path

IMAGE = "postgres:16.14-bookworm@sha256:714b08dd70c05160db032e4321033ac4e5b6328935b2d03ed419c3a898aa8219"
RETAINED_ID = "9ab9d141599e8b33f51a1e4e9bcd16d66e92b9d134889014d9d9bd618b42f73d"
PROJECT = "belib-isolated-i5"
DATABASE = "belib_staging"
BOOTSTRAP = "belib_staging_bootstrap"
APP = "belib_staging_app"
MIGRATE = "belib_staging_migrate"
MAX_DUMP = 64 * 1024**3
MAX_REPORT = 1024 * 1024
IDENTIFIER = re.compile(r"[a-z_][a-z_0-9]{0,62}\Z")


class PostgresBlocked(RuntimeError):
    pass


def _private_directory(path):
    info = path.lstat()
    if (path.resolve() != path or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise PostgresBlocked("Backup directory must be private and symlink-free")


def _atomic(path, raw):
    _private_directory(path.parent)
    pending = path.with_suffix(".pending")
    if pending.exists() or pending.is_symlink():
        info = pending.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise PostgresBlocked("Pending operation record is unsafe")
        pending.unlink()
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        pending.unlink(missing_ok=True)


class RetainedPostgres:
    def __init__(self, container_id: str, directory: Path, operation_id: str):
        if not re.fullmatch(r"[0-9a-f]{64}", container_id) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise PostgresBlocked("Immutable database/operation identity required")
        _private_directory(directory)
        self.container = container_id
        self.directory = directory
        self.operation_id = operation_id
        self.dump_path = directory / "backup.dump"
        self.restore_name = "belib-upgrade-restore-" + operation_id
        self.restore_id = None
        self.baseline = None
        self.prefix = ["/usr/bin/docker", "--host", f"unix:///run/user/{os.getuid()}/docker.sock"]
        self.environment = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "DOCKER_CONFIG": "/nonexistent"}

    @classmethod
    def for_retained(cls, directory: Path, operation_id: str):
        if os.getuid() != 1000:
            raise PostgresBlocked("Retained PostgreSQL requires staging UID 1000")
        port = cls(RETAINED_ID, directory, operation_id)
        source = port.inspect(RETAINED_ID)
        labels = source.get("Config", {}).get("Labels") or {}
        mounts = [m for m in source.get("Mounts", []) if m.get("Destination") == "/var/lib/postgresql/data"]
        if (source.get("Id") != RETAINED_ID or source.get("Config", {}).get("Image") != IMAGE
                or labels.get("com.docker.compose.project") != PROJECT
                or labels.get("com.docker.compose.service") != "db"
                or len(mounts) != 1 or mounts[0].get("Type") != "volume"
                or mounts[0].get("Name") != PROJECT + "_staging-postgres-data"):
            raise PostgresBlocked("Retained database identity or volume changed")
        return port

    def docker(self, *args, input=None, stdout=subprocess.PIPE, timeout=120):
        result = subprocess.run([*self.prefix, *args], input=input, stdout=stdout, stderr=subprocess.PIPE,
            env=self.environment, timeout=timeout, check=False)
        if result.returncode:
            # stderr can contain database row values; never copy it into receipts.
            raise PostgresBlocked("Fixed PostgreSQL Docker command failed")
        return result.stdout

    def inspect(self, container):
        return json.loads(self.docker("inspect", container))[0]

    def sql(self, sql, *, container=None):
        output = self.docker("exec", "-i", container or self.container, "psql", "-X", "-q", "-A", "-t",
            "-v", "ON_ERROR_STOP=1", "-U", BOOTSTRAP, "-d", DATABASE, input=sql.encode())
        if len(output) > MAX_REPORT:
            raise PostgresBlocked("Database query report exceeds its bound")
        return output.decode("utf-8", "strict").strip()

    def quiesce_database(self):
        # Caller stops backend/ingress first. Block reconnects and terminate any
        # old app sessions before the dump. This does not alter dump table ACLs.
        self.sql("ALTER ROLE belib_staging_app NOLOGIN; SELECT pg_terminate_backend(pid) "
                 "FROM pg_stat_activity WHERE usename='belib_staging_app' AND pid<>pg_backend_pid();")
        if self.sql("SELECT count(*) FROM pg_stat_activity WHERE datname='belib_staging' "
                    "AND pid<>pg_backend_pid() AND backend_type='client backend'") != "0":
            raise PostgresBlocked("Database has another client while quiescing")

    def _open_dump(self, digest=None):
        fd = os.open(self.dump_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        stream = os.fdopen(fd, "rb")
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or not 1 <= info.st_size <= MAX_DUMP):
            stream.close()
            raise PostgresBlocked("Backup artifact is unsafe")
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(fd)
        if ((info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or digest is not None and digest != actual):
            stream.close()
            raise PostgresBlocked("Backup artifact digest or bytes changed")
        stream.seek(0)
        return stream, actual

    def backup(self):
        if self.dump_path.exists() or self.dump_path.is_symlink():
            stream, digest = self._open_dump()
            stream.close()
            return digest
        pending = self.directory / "backup.incomplete"
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                def limit_dump():
                    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_DUMP, MAX_DUMP))
                result = subprocess.run([*self.prefix, "exec", self.container, "pg_dump", "-w", "-U", BOOTSTRAP,
                    "-Fc", "--dbname", DATABASE], stdout=stream, stderr=subprocess.PIPE,
                    env=self.environment, timeout=1800, check=False, preexec_fn=limit_dump)
                if result.returncode:
                    raise PostgresBlocked("Bounded backup failed; incomplete bytes preserved")
                stream.flush()
                os.fsync(stream.fileno())
            # Link is exclusive: a concurrent caller cannot replace an approved dump.
            os.link(pending, self.dump_path, follow_symlinks=False)
            pending.unlink()
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            # Keep incomplete dump for diagnosis; never silently reuse or replace it.
            raise
        stream, digest = self._open_dump()
        stream.close()
        return digest

    def _start_restore(self):
        if self.restore_id is not None:
            return
        # Reconcile an interruption between creation and the caller's durable
        # phase record. Only this exact labelled disposable identity is removed.
        existing = self.docker("ps", "--all", "--no-trunc", "--filter", "name=^/" + self.restore_name + "$", "--format", "{{.ID}}").decode().splitlines()
        if existing:
            if len(existing) != 1 or not re.fullmatch(r"[0-9a-f]{64}", existing[0]):
                raise PostgresBlocked("Restore namespace has an ambiguous resource")
            self.restore_id = existing[0]
            self.cleanup_restore()
        # The exclusive name still prevents a concurrent creator from replacing it.
        created = self.docker("run", "--detach", "--pull=never", "--name", self.restore_name,
            "--network", "none", "--label", "belib.retained-restore=" + self.operation_id,
            "--label", "belib.source-container=" + self.container,
            "--tmpfs", "/var/lib/postgresql/data:rw,size=2147483648", "--env", "POSTGRES_HOST_AUTH_METHOD=trust",
            "--env", "POSTGRES_USER=" + BOOTSTRAP, "--env", "POSTGRES_DB=" + DATABASE, IMAGE).decode().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", created):
            raise PostgresBlocked("Restore container identity is malformed")
        self.restore_id = created
        for attempt in range(60):
            try:
                self.docker("exec", created, "pg_isready", "-h", "127.0.0.1", "-U", BOOTSTRAP, timeout=5)
                break
            except PostgresBlocked:
                time.sleep(.25)
        else:
            raise PostgresBlocked("Isolated restore database did not start")
        self.sql("CREATE ROLE belib_staging_app LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; "
                 "CREATE ROLE belib_staging_migrate LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;",
                 container=created)

    def _restore(self, container, digest):
        stream, _ = self._open_dump(digest)
        try:
            # This restores the existing database from its exact archive. No DB or
            # retained volume is dropped, removed, recreated or seeded.
            self.sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public;", container=container)
            result = subprocess.run([*self.prefix, "exec", "-i", container, "pg_restore", "--exit-on-error",
                "--no-owner", "-U", BOOTSTRAP, "--dbname", DATABASE], stdin=stream,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=self.environment, timeout=1800, check=False)
            if result.returncode:
                raise PostgresBlocked("Exact backup restoration failed")
            # --no-owner avoids dependence on local OIDs; restore the fixed owner.
            self.sql("ALTER SCHEMA public OWNER TO belib_staging_migrate; "
                "DO $q$ DECLARE r record; BEGIN FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP "
                "EXECUTE format('ALTER TABLE public.%I OWNER TO belib_staging_migrate',r.tablename); END LOOP; END $q$;",
                container=container)
        finally:
            stream.close()

    def _data_digest(self, target, query):
        command = [*self.prefix, "exec", target, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1",
                   "-U", BOOTSTRAP, "-d", DATABASE, "-c", "COPY (" + query + ") TO STDOUT"]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=self.environment)
        digest, size, deadline = hashlib.sha256(), 0, time.monotonic() + 300
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise PostgresBlocked("Database digest exceeded its time bound")
                if not selector.select(.5):
                    continue
                chunk = os.read(process.stdout.fileno(), 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DUMP:
                    raise PostgresBlocked("Database digest exceeded its size bound")
                digest.update(chunk)
            if process.wait(timeout=5):
                raise PostgresBlocked("Database digest query failed")
            return digest.hexdigest()
        finally:
            selector.close()
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()

    def fingerprint(self, *, container=None, columns=None):
        target = container or self.container
        metadata = json.loads(self.sql("SELECT coalesce(json_agg(x ORDER BY table_name,ordinal_position),'[]') FROM "
            "(SELECT table_name,column_name,data_type,ordinal_position FROM information_schema.columns "
            "WHERE table_schema='public') x", container=target))
        observed = {}
        for row in metadata:
            table, column = row["table_name"], row["column_name"]
            if not IDENTIFIER.fullmatch(table) or not IDENTIFIER.fullmatch(column):
                raise PostgresBlocked("Unexpected schema identifier")
            observed.setdefault(table, []).append(column)
        if columns is not None:
            if set(columns) != set(observed) or any(not set(value) <= set(observed[name]) for name, value in columns.items()):
                raise PostgresBlocked("Migration removed or introduced an unreviewed table/column")
            selected = columns
        else:
            selected = observed
        data = {}
        for table, names in sorted(selected.items()):
            # SQL quotes only validated catalog identifiers; row bytes are reduced
            # to digests in-process and never appear in receipts or stdout.
            projection = ",".join('"' + name + '"' for name in names)
            query = f'SELECT row_to_json(t)::text FROM (SELECT {projection} FROM public."{table}") t ORDER BY row_to_json(t)::text'
            data[table] = {"sha256": self._data_digest(target, query),
                           "count": int(self.sql(f'SELECT count(*) FROM public."{table}"', container=target))}
        return {"columns": selected, "schema": metadata, "data": data}

    def verify_restore(self, digest):
        self._start_restore()
        self._restore(self.restore_id, digest)
        baseline = self.fingerprint(container=self.restore_id)
        if self.fingerprint() != baseline:
            raise PostgresBlocked("Restored backup does not match the quiesced current database")
        self.baseline = baseline
        _atomic(self.directory / "restore-report.json", json.dumps({"schema": "belib-actual-restore/v1",
            "backup_sha256": digest, "source_container_id": self.container, "restore_container_id": self.restore_id,
            "data": baseline["data"], "schema_sha256": hashlib.sha256(json.dumps(baseline["schema"],sort_keys=True).encode()).hexdigest()},
            sort_keys=True,separators=(",",":")).encode())

    def verify_preserved(self, digest, *, upgraded):
        if self.baseline is None:
            self._start_restore()
            self._restore(self.restore_id, digest)
            self.baseline = self.fingerprint(container=self.restore_id)
        current = self.fingerprint(columns=self.baseline["columns"] if upgraded else None)
        # Prisma's migration ledger necessarily changes; application rows do not.
        before = {k:v for k,v in self.baseline["data"].items() if k != "_prisma_migrations"}
        after = {k:v for k,v in current["data"].items() if k != "_prisma_migrations"}
        if before != after or not upgraded and current["schema"] != self.baseline["schema"]:
            raise PostgresBlocked("Retained rows/progress/authorization data changed")
        if upgraded:
            required = {("reading_infos", "current_position", "text"),
                        ("reading_infos", "progress_percentage", "integer"),
                        ("reading_infos", "save_sequence", "bigint")}
            observed = {(x["table_name"],x["column_name"],x["data_type"]) for x in current["schema"]}
            if not required <= observed:
                raise PostgresBlocked("Required reading-progress schema is missing")

    def read_only_application(self):
        self.sql("REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA public FROM belib_staging_app; "
                 "REVOKE USAGE,UPDATE ON ALL SEQUENCES IN SCHEMA public FROM belib_staging_app; "
                 "ALTER ROLE belib_staging_app LOGIN;")
        self.verify_write_barrier()

    def verify_write_barrier(self):
        if self.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND "
                    "(has_table_privilege('belib_staging_app',format('%I.%I',schemaname,tablename),'INSERT,UPDATE,DELETE,TRUNCATE') "
                    "OR tableowner='belib_staging_app')") != "0":
            raise PostgresBlocked("Application write barrier is not effective")
        if self.sql("SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='belib_staging_app')") != "0":
            raise PostgresBlocked("Application role has unexpected role membership")
        if self.sql("SELECT count(*) FROM pg_roles WHERE rolname='belib_staging_app' AND "
                    "(rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolinherit)") != "0":
            raise PostgresBlocked("Application role has unexpected authority")

    def release_writes(self):
        # Caller may invoke ONLY after the engine's durable commit/abort decision.
        self.sql("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO belib_staging_app; "
                 "GRANT USAGE,SELECT,UPDATE ON ALL SEQUENCES IN SCHEMA public TO belib_staging_app; "
                 "ALTER ROLE belib_staging_app LOGIN;")

    def restore_backup(self, digest):
        self.quiesce_database()
        self._restore(self.container, digest)
        self.verify_preserved(digest, upgraded=False)

    def verify_disposable_identity(self):
        if self.restore_id is None:
            raise PostgresBlocked("Restore identity is missing")
        info = self.inspect(self.restore_id)
        if (info.get("Id") != self.restore_id or info.get("Config",{}).get("Image") != IMAGE
                or info.get("Name") != "/" + self.restore_name
                or self.restore_id == self.container
                or (info.get("Config",{}).get("Labels") or {}).get("belib.retained-restore") != self.operation_id
                or (info.get("Config",{}).get("Labels") or {}).get("belib.source-container") != self.container
                or info.get("Config",{}).get("Entrypoint") != ["docker-entrypoint.sh"]
                or info.get("Config",{}).get("Cmd") != ["postgres"]
                or info.get("HostConfig",{}).get("Privileged") is not False
                or info.get("HostConfig",{}).get("NetworkMode") != "none"
                or any(m.get("Type") in {"volume", "bind"} for m in info.get("Mounts",[]))):
            raise PostgresBlocked("Disposable restore identity changed")

    def cleanup_restore(self, *, missing_ok=False):
        if self.restore_id:
            if missing_ok:
                # Only the caller's durable unpublished-discard phase may treat
                # absence as success. Never adopt a name/replacement container.
                present = self.docker("ps", "--all", "--no-trunc", "--filter", "id=" + self.restore_id,
                                      "--format", "{{.ID}}").decode().splitlines()
                if not present:
                    self.restore_id = None
                    return
                if present != [self.restore_id]:
                    raise PostgresBlocked("Disposable discard identity is ambiguous")
            self.verify_disposable_identity()
            self.docker("rm", "-f", self.restore_id)
            self.restore_id = None
