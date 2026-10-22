#!/usr/bin/env python3
"""ExpenseLens backup agent: scheduled encrypted backups, rotation, upload, verification."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

VERSION = "1.0.0"
TS_RE = re.compile(r"^\d{8}T\d{6}Z$")
TS_FMT = "%Y%m%dT%H%M%SZ"
AGE_HEADER = b"age-encryption.org/v1"
STALE_HOURS = 36
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

MSG = {
    "it": {
        "backup_ok": "ExpenseLens: backup completato ({dir}, {size}).",
        "backup_fail": "ExpenseLens: backup FALLITO: {err}",
        "upload_fail": "ExpenseLens: backup {dir} creato in locale ma upload remoto FALLITO: {err}",
        "verify_ok": "ExpenseLens: verifica backup OK ({dir}).",
        "verify_partial": "ExpenseLens: verifica backup PARZIALE ({dir}): {err}",
        "verify_fail": "ExpenseLens: verifica backup FALLITA ({dir}): {err}",
    },
    "en": {
        "backup_ok": "ExpenseLens: backup completed ({dir}, {size}).",
        "backup_fail": "ExpenseLens: backup FAILED: {err}",
        "upload_fail": "ExpenseLens: backup {dir} created locally but remote upload FAILED: {err}",
        "verify_ok": "ExpenseLens: backup verification OK ({dir}).",
        "verify_partial": "ExpenseLens: backup verification PARTIAL ({dir}): {err}",
        "verify_fail": "ExpenseLens: backup verification FAILED ({dir}): {err}",
    },
}


class BackupError(Exception):
    pass


class UploadRefused(BackupError):
    pass


# ---------------------------------------------------------------- config
def env(name, default=""):
    return os.environ.get(name, default).strip()


def env_bool(name, default=False):
    v = env(name, "true" if default else "false").lower()
    return v in ("1", "true", "yes", "on")


def env_int(name, default):
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def backup_dir():
    return env("BACKUP_DIR", "/backups")


def documents_dir():
    return env("BACKUP_DOCUMENTS_DIR", "/data/documents")


def tzinfo():
    try:
        return ZoneInfo(env("TZ", "Europe/Rome"))
    except Exception:
        return ZoneInfo("UTC")


def parse_hhmm(value, default):
    try:
        h, m = value.split(":")
        return dt.time(int(h), int(m))
    except Exception:
        return default


def recipients():
    return [r.strip() for r in env("BACKUP_AGE_RECIPIENT").split(",") if r.strip()]


def secrets():
    names = [
        "POSTGRES_PASSWORD", "BACKUP_S3_SECRET_KEY", "BACKUP_S3_ACCESS_KEY",
        "BACKUP_WEBDAV_PASSWORD", "TELEGRAM_BOT_TOKEN",
    ]
    return [env(n) for n in names if len(env(n)) >= 4]


def redact(text):
    for s in secrets():
        text = text.replace(s, "***")
    return text


def log(msg):
    ts = dt.datetime.now(tzinfo()).strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} [backup] {redact(str(msg))}", flush=True)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def human(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


# ---------------------------------------------------------------- status
def status_path():
    return os.path.join(backup_dir(), "status.json")


def read_status():
    try:
        with open(status_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def write_status(update):
    st = read_status()
    st.update(update)
    st["warnings"] = current_warnings()
    st["next_backup"] = iso(next_backup_time()) if env_bool("BACKUP_ENABLED", True) else None
    st["updated"] = iso(now_utc())
    fd, tmp = tempfile.mkstemp(prefix=".status.", dir=backup_dir())
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(st, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, status_path())
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return st


def current_warnings():
    w = []
    if not recipients():
        w.append("Backups are NOT encrypted (BACKUP_AGE_RECIPIENT is empty)")
    if env("BACKUP_REMOTE") and not recipients():
        if env_bool("BACKUP_ALLOW_UNENCRYPTED_REMOTE"):
            w.append("Unencrypted backups are uploaded to the remote (BACKUP_ALLOW_UNENCRYPTED_REMOTE=true)")
        else:
            w.append("Remote upload disabled: no BACKUP_AGE_RECIPIENT")
    if recipients() and not env("BACKUP_AGE_IDENTITY_FILE"):
        w.append("No BACKUP_AGE_IDENTITY_FILE: restore verification is partial")
    return w


# ---------------------------------------------------------------- scheduling
def next_backup_time():
    tz = tzinfo()
    now = dt.datetime.now(tz)
    t = parse_hhmm(env("BACKUP_TIME", "03:30"), dt.time(3, 30))
    cand = dt.datetime.combine(now.date(), t, tz)
    if cand <= now:
        cand += dt.timedelta(days=1)
    return cand


def verify_schedule():
    day = env("BACKUP_VERIFY_DAY", "sun").lower()
    if not day:
        return None
    if day != "daily" and day[:3] not in DAYS:
        return None
    return day, parse_hhmm(env("BACKUP_VERIFY_TIME", "05:00"), dt.time(5, 0))


def due(schedule_time, last_attempt_iso, now_local):
    """True if today's scheduled time has passed and no attempt happened since."""
    sched = dt.datetime.combine(now_local.date(), schedule_time, now_local.tzinfo)
    if now_local < sched:
        return False
    if not last_attempt_iso:
        return True
    return parse_iso(last_attempt_iso) < sched.astimezone(dt.timezone.utc)


# ---------------------------------------------------------------- subprocesses
def child_env(extra=None):
    e = dict(os.environ)
    e["PGHOST"] = env("BACKUP_PGHOST", "db")
    e["PGPORT"] = env("BACKUP_PGPORT", "5432")
    e["PGUSER"] = env("POSTGRES_USER", "expenselens")
    e["PGPASSWORD"] = os.environ.get("POSTGRES_PASSWORD", "")
    e["PGDATABASE"] = env("POSTGRES_DB", "expenselens")
    e["PGCONNECT_TIMEOUT"] = "15"
    if extra:
        e.update(extra)
    return e


def run_pipeline(cmds, stdout_path=None, stdin_path=None, environ=None, ok_codes=None, capture=False):
    """Run cmds connected by pipes. Returns captured stdout (if capture) or None.
    ok_codes: optional dict index -> allowed return codes."""
    ok_codes = ok_codes or {}
    procs, errs, files = [], [], []
    out = None
    try:
        prev = None
        if stdin_path:
            prev = open(stdin_path, "rb")
            files.append(prev)
        for i, cmd in enumerate(cmds):
            last = i == len(cmds) - 1
            if last and stdout_path:
                dest = open(stdout_path, "wb")
                files.append(dest)
            elif last and capture:
                dest = subprocess.PIPE
            elif last:
                dest = subprocess.DEVNULL
            else:
                dest = subprocess.PIPE
            err = tempfile.TemporaryFile()
            errs.append(err)
            p = subprocess.Popen(cmd, stdin=prev, stdout=dest, stderr=err, env=environ)
            if prev is not None and prev not in files:
                prev.close()
            prev = p.stdout
            procs.append(p)
        if capture:
            out = procs[-1].stdout.read()
            procs[-1].stdout.close()
        for p in procs:
            p.wait()
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait()
        for f in files:
            f.close()
    for i, p in enumerate(procs):
        if p.returncode not in ok_codes.get(i, (0,)):
            errs[i].seek(0)
            msg = errs[i].read().decode(errors="replace").strip()[-500:]
            raise BackupError(f"{os.path.basename(cmds[i][0])} exited with {p.returncode}: {redact(msg)}")
    return out


def psql(sql, dbname=None, environ=None):
    environ = environ or child_env()
    cmd = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"]
    if dbname:
        cmd += ["-d", dbname]
    cmd += ["-c", sql]
    out = run_pipeline([cmd], environ=environ, capture=True)
    return out.decode().strip()


# ---------------------------------------------------------------- lock
class Lock:
    def __init__(self):
        self.fd = None

    def __enter__(self):
        try:
            self.fd = open(os.path.join(backup_dir(), ".agent.lock"), "w")
        except OSError as e:
            raise BackupError(f"cannot write to {backup_dir()} ({e.strerror}): check ownership vs BACKUP_UID/BACKUP_GID")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fd.close()
            raise BackupError("another backup/verify/prune operation is already running")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        self.fd.close()


# ---------------------------------------------------------------- alerts
def alert(kind, **kw):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = env("BACKUP_ALERT_TELEGRAM_CHAT_ID")
    if not token or not chat:
        return
    if kind.endswith("_ok") and not env_bool("BACKUP_NOTIFY_SUCCESS"):
        return
    lang = env("BACKUP_LANG", "it").lower()
    text = MSG.get(lang, MSG["it"])[kind].format(**kw)
    text = redact(text)[:1000]
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:  # never log the URL: it contains the token
        log(f"Telegram alert not sent: {type(e).__name__} {getattr(e, 'code', '')}")


# ---------------------------------------------------------------- helpers
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def list_backups(base=None):
    """Directories created by this agent (timestamp name + MANIFEST.json), oldest first."""
    base = base or backup_dir()
    res = []
    for name in os.listdir(base):
        p = os.path.join(base, name)
        if TS_RE.match(name) and os.path.isdir(p) and os.path.isfile(os.path.join(p, "MANIFEST.json")):
            res.append(name)
    return sorted(res)


def ts_to_dt(name):
    return dt.datetime.strptime(name, TS_FMT).replace(tzinfo=dt.timezone.utc)


def pg_version():
    try:
        return psql("show server_version")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- remote
def remote_enabled():
    return env("BACKUP_REMOTE").lower() in ("s3", "webdav")


def remote_env_and_base():
    kind = env("BACKUP_REMOTE").lower()
    e = {"RCLONE_CONFIG": "/tmp/rclone-unused.conf"}
    if kind == "s3":
        bucket = env("BACKUP_S3_BUCKET")
        if not bucket or not env("BACKUP_S3_ACCESS_KEY") or not env("BACKUP_S3_SECRET_KEY"):
            raise BackupError("incomplete S3 configuration (bucket/access key/secret key)")
        endpoint = env("BACKUP_S3_ENDPOINT")
        e.update({
            "RCLONE_CONFIG_REMOTE_TYPE": "s3",
            "RCLONE_CONFIG_REMOTE_PROVIDER": env("BACKUP_S3_PROVIDER", "Other" if endpoint else "AWS"),
            "RCLONE_CONFIG_REMOTE_ENV_AUTH": "false",
            "RCLONE_CONFIG_REMOTE_ACCESS_KEY_ID": env("BACKUP_S3_ACCESS_KEY"),
            "RCLONE_CONFIG_REMOTE_SECRET_ACCESS_KEY": env("BACKUP_S3_SECRET_KEY"),
            "RCLONE_CONFIG_REMOTE_NO_CHECK_BUCKET": "true",
        })
        if endpoint:
            e["RCLONE_CONFIG_REMOTE_ENDPOINT"] = endpoint
        if env("BACKUP_S3_REGION"):
            e["RCLONE_CONFIG_REMOTE_REGION"] = env("BACKUP_S3_REGION")
        base = bucket + "/" + env("BACKUP_S3_PREFIX", "expenselens").strip("/")
    elif kind == "webdav":
        if not env("BACKUP_WEBDAV_URL"):
            raise BackupError("BACKUP_WEBDAV_URL is not set")
        pw = os.environ.get("BACKUP_WEBDAV_PASSWORD", "")
        obscured = ""
        if pw:
            obscured = subprocess.run(
                ["rclone", "obscure", "-"], input=pw.encode(), capture_output=True, check=True
            ).stdout.decode().strip()
        e.update({
            "RCLONE_CONFIG_REMOTE_TYPE": "webdav",
            "RCLONE_CONFIG_REMOTE_URL": env("BACKUP_WEBDAV_URL"),
            "RCLONE_CONFIG_REMOTE_USER": env("BACKUP_WEBDAV_USER"),
            "RCLONE_CONFIG_REMOTE_PASS": obscured,
        })
        if env("BACKUP_WEBDAV_VENDOR"):
            e["RCLONE_CONFIG_REMOTE_VENDOR"] = env("BACKUP_WEBDAV_VENDOR")
        base = env("BACKUP_WEBDAV_PATH", "expenselens").strip("/")
    else:
        raise BackupError("BACKUP_REMOTE must be empty, s3 or webdav")
    return dict(os.environ, **e), base


def rclone(args, renv, capture=False):
    return run_pipeline([["rclone", "--retries", "3", "--low-level-retries", "5"] + args],
                        environ=renv, capture=capture)


def upload(name, dirpath):
    if not recipients() and not env_bool("BACKUP_ALLOW_UNENCRYPTED_REMOTE"):
        raise UploadRefused("upload refused: BACKUP_AGE_RECIPIENT is empty "
                          "(set it, or BACKUP_ALLOW_UNENCRYPTED_REMOTE=true)")
    renv, base = remote_env_and_base()
    log(f"Uploading {name} to remote ({env('BACKUP_REMOTE')})")
    rclone(["copy", dirpath, f"remote:{base}/{name}"], renv)
    log("Upload completed")


def remote_list_dirs(renv, base):
    out = rclone(["lsf", "--dirs-only", f"remote:{base}"], renv, capture=True).decode()
    return sorted(d.strip("/") for d in out.split() if TS_RE.match(d.strip("/")))


def prune_remote():
    days = env_int("BACKUP_REMOTE_KEEP_DAYS", 30)
    if not remote_enabled() or days <= 0:
        return
    renv, base = remote_env_and_base()
    names = remote_list_dirs(renv, base)
    if len(names) <= 1:
        return
    cutoff = now_utc() - dt.timedelta(days=days)
    for n in names[:-1]:  # the newest remote copy is always kept
        if ts_to_dt(n) < cutoff:
            log(f"Removing remote backup {n}")
            rclone(["purge", f"remote:{base}/{n}"], renv)


def remote_check(name, dirpath):
    renv, base = remote_env_and_base()
    out = rclone(["lsjson", f"remote:{base}/{name}"], renv, capture=True)
    remote = {e["Name"]: e["Size"] for e in json.loads(out) if not e.get("IsDir")}
    local = {f: os.path.getsize(os.path.join(dirpath, f)) for f in os.listdir(dirpath)
             if os.path.isfile(os.path.join(dirpath, f))}
    for f, size in local.items():
        if remote.get(f) != size:
            raise BackupError(f"remote copy mismatch for {f}")


# ---------------------------------------------------------------- rotation
def gfs_keep(names):
    """Names to keep given timestamp-named backups (grandfather-father-son)."""
    if not names:
        return set()
    names = sorted(names, reverse=True)
    keep = {names[0]}
    tz = tzinfo()
    rules = [
        (env_int("BACKUP_KEEP_DAILY", 7), lambda d: d.date()),
        (env_int("BACKUP_KEEP_WEEKLY", 4), lambda d: tuple(d.isocalendar()[:2])),
        (env_int("BACKUP_KEEP_MONTHLY", 6), lambda d: (d.year, d.month)),
    ]
    for count, keyf in rules:
        seen = []
        for n in names:  # newest first: first of each bucket is the bucket's newest backup
            k = keyf(ts_to_dt(n).astimezone(tz))
            if k in seen:
                continue
            if len(seen) >= count:
                break
            seen.append(k)
            keep.add(n)
    return keep


def prune_local():
    names = list_backups()
    if len(names) <= 1:
        return []
    keep = gfs_keep(names)
    removed = []
    for n in names:
        if n not in keep:
            log(f"Rotation: removing {n}")
            shutil.rmtree(os.path.join(backup_dir(), n))
            removed.append(n)
    # stale partial dirs from crashed runs (agent pattern only)
    for n in os.listdir(backup_dir()):
        if n.endswith(".partial") and TS_RE.match(n[:-8]):
            p = os.path.join(backup_dir(), n)
            if os.path.isdir(p) and time.time() - os.path.getmtime(p) > 6 * 3600:
                shutil.rmtree(p, ignore_errors=True)
    return removed


# ---------------------------------------------------------------- backup
def do_backup():
    """Create the backup. Returns (info dict). Raises BackupError."""
    os.umask(0o077)
    base = backup_dir()
    ts = now_utc().strftime(TS_FMT)
    partial = os.path.join(base, ts + ".partial")
    final = os.path.join(base, ts)
    rcp = recipients()
    enc = bool(rcp)
    if not enc:
        log("WARNING: BACKUP_AGE_RECIPIENT is not set: backup stored in PLAINTEXT")
    penv = child_env()
    age = ["age"] + [x for r in rcp for x in ("-r", r)]
    os.mkdir(partial, 0o700)
    try:
        suffix = ".age" if enc else ""
        db_file = "database.dump" + suffix
        doc_file = "documents.tar.gz" + suffix
        log(f"Dumping database to {db_file}")
        dump = ["pg_dump", "-Fc"]
        run_pipeline([dump, age] if enc else [dump], stdout_path=os.path.join(partial, db_file), environ=penv)
        log(f"Archiving documents to {doc_file}")
        tar = ["tar", "-czf", "-", "-C", documents_dir(), "."]
        # tar exit 1 = files changed while reading; the archive is still usable
        run_pipeline([tar, age] if enc else [tar], stdout_path=os.path.join(partial, doc_file),
                     ok_codes={0: (0, 1)})
        files = [db_file, doc_file]
        sizes = {f: os.path.getsize(os.path.join(partial, f)) for f in files}
        with open(os.path.join(partial, "SHA256SUMS"), "w") as f:
            for name in files:
                f.write(f"{sha256_file(os.path.join(partial, name))}  {name}\n")
        manifest = {
            "timestamp": iso(dt.datetime.strptime(ts, TS_FMT).replace(tzinfo=dt.timezone.utc)),
            "name": ts,
            "encrypted": enc,
            "files": sizes,
            "pg_server_version": pg_version(),
            "agent_version": VERSION,
        }
        with open(os.path.join(partial, "MANIFEST.json"), "w") as f:
            json.dump(manifest, f, indent=2)
        os.rename(partial, final)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    size = sum(sizes.values())
    log(f"Backup {ts} completed ({human(size)}, encrypted={enc})")
    return {"name": ts, "dir": final, "size_bytes": size, "encrypted": enc}


def cmd_backup():
    os.makedirs(backup_dir(), exist_ok=True)
    attempt = iso(now_utc())
    try:
        lock = Lock().__enter__()
    except BackupError as e:
        log(f"ERROR: {e}")
        return 1
    try:
        try:
            info = do_backup()
        except Exception as e:
            err = redact(str(e))
            log(f"ERROR: backup failed: {err}")
            write_status({"last_attempt_backup": attempt, "last_backup": {
                "time": iso(now_utc()), "ok": False, "status": "failed", "error": err}})
            alert("backup_fail", err=err)
            return 1
        remote_ok, remote_err, refused = None, None, False
        if remote_enabled():
            try:
                upload(info["name"], info["dir"])
                remote_ok = True
            except Exception as e:
                remote_ok, remote_err, refused = False, redact(str(e)), isinstance(e, UploadRefused)
                log(f"ERROR: remote upload failed: {remote_err}")
        try:
            prune_local()
            if remote_ok:
                prune_remote()
        except Exception as e:
            log(f"WARNING: rotation error: {redact(str(e))}")
        st = {
            "time": iso(now_utc()), "ok": not refused,
            "status": "failed" if refused else ("ok" if remote_ok is not False else "degraded"),
            "dir": info["dir"], "size_bytes": info["size_bytes"], "encrypted": info["encrypted"],
            "remote_ok": remote_ok,
        }
        if remote_err:
            st["error"] = remote_err
        write_status({"last_attempt_backup": attempt, "last_backup": st,
                      **({} if refused else {"last_success_time": st["time"]})})
        if remote_ok is False:
            alert("upload_fail", dir=info["name"], err=remote_err)
            return 1
        alert("backup_ok", dir=info["name"], size=human(info["size_bytes"]))
        return 0
    finally:
        lock.__exit__()


# ---------------------------------------------------------------- verify
def identity_file():
    p = env("BACKUP_AGE_IDENTITY_FILE")
    if p and os.path.isfile(p) and os.access(p, os.R_OK):
        return p
    return None


def read_sums(d):
    sums = {}
    with open(os.path.join(d, "SHA256SUMS")) as f:
        for line in f:
            line = line.strip()
            if line:
                digest, name = line.split(None, 1)
                sums[name.lstrip("*")] = digest
    return sums


def full_restore_test(d, enc, ident):
    dump = os.path.join(d, "database.dump" + (".age" if enc else ""))
    docs = os.path.join(d, "documents.tar.gz" + (".age" if enc else ""))
    dec = ["age", "-d", "-i", ident] if enc else None
    ts = os.path.basename(d.rstrip("/")).lower()
    scratch = "expenselens_verify_" + re.sub(r"[^a-z0-9]", "", ts)
    penv = child_env()
    details = {}
    psql(f'CREATE DATABASE "{scratch}"', dbname="postgres", environ=penv)
    try:
        restore = ["pg_restore", "--no-owner", "--no-acl", "-d", scratch]
        penv_r = child_env()
        if enc:
            run_pipeline([dec, restore], stdin_path=dump, environ=penv_r)
        else:
            run_pipeline([restore + [dump]], environ=penv_r)
        tables = int(psql("select count(*) from information_schema.tables where table_schema='public'",
                          dbname=scratch, environ=penv))
        if tables <= 0:
            raise BackupError("restored database has no tables in schema public")
        users = int(psql("select count(*) from users", dbname=scratch, environ=penv))
        psql("select count(*) from alembic_version", dbname=scratch, environ=penv)
        details.update({"tables": tables, "users": users, "alembic_version": True})
    finally:
        try:
            psql(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)', dbname="postgres", environ=penv)
        except Exception as e:
            log(f"WARNING: could not drop scratch database {scratch}: {redact(str(e))}")
    listing = run_pipeline([dec, ["tar", "-tzf", "-"]] if enc else [["tar", "-tzf", docs]],
                           stdin_path=docs if enc else None, capture=True)
    details["document_entries"] = len([x for x in listing.decode(errors="replace").splitlines() if x])
    return details


def do_verify(name_or_dir=None):
    """Returns (result, details) where result in ok/partial/failed."""
    base = backup_dir()
    if name_or_dir:
        d = name_or_dir if os.path.isabs(name_or_dir) or os.sep in name_or_dir else os.path.join(base, name_or_dir)
    else:
        names = list_backups()
        if not names:
            return "failed", {"error": "no backups found"}
        d = os.path.join(base, names[-1])
    d = os.path.abspath(d)
    details = {"dir": d}
    if not os.path.isdir(d):
        return "failed", dict(details, error="backup directory not found")
    try:
        sums = read_sums(d)
        if not sums:
            raise BackupError("SHA256SUMS is empty")
        for name, digest in sums.items():
            p = os.path.join(d, name)
            if not os.path.isfile(p):
                raise BackupError(f"missing file {name}")
            if sha256_file(p) != digest:
                raise BackupError(f"checksum mismatch for {name}")
        details["checksums"] = "ok"
        enc = os.path.exists(os.path.join(d, "database.dump.age"))
        details["encrypted"] = enc
        result = "ok"
        if enc:
            for f in ("database.dump.age", "documents.tar.gz.age"):
                with open(os.path.join(d, f), "rb") as fh:
                    if not fh.read(len(AGE_HEADER)) == AGE_HEADER:
                        raise BackupError(f"{f} does not have an age header")
            details["age_header"] = "ok"
        ident = identity_file() if enc else None
        if enc and not ident:
            result = "partial"
            details["note"] = "partial verification (no identity key): checksums and age header only"
        else:
            details.update(full_restore_test(d, enc, ident))
            details["restore_test"] = "ok"
        if remote_enabled():
            try:
                remote_check(os.path.basename(d), d)
                details["remote"] = "ok"
            except Exception as e:
                details["remote"] = "failed"
                raise BackupError(f"remote check failed: {redact(str(e))}")
        return result, details
    except Exception as e:
        details["error"] = redact(str(e))
        return "failed", details


def cmd_verify(target=None):
    os.makedirs(backup_dir(), exist_ok=True)
    attempt = iso(now_utc())
    try:
        lock = Lock().__enter__()
    except BackupError as e:
        log(f"ERROR: {e}")
        return 1
    try:
        log("Starting backup verification")
        result, details = do_verify(target)
        write_status({"last_attempt_verify": attempt,
                      "last_verify": {"time": iso(now_utc()), "result": result, "details": details}})
        log(f"Verification result: {result} {json.dumps(details)}")
        name = os.path.basename(details.get("dir", "?"))
        if result == "ok":
            alert("verify_ok", dir=name)
        elif result == "partial":
            alert("verify_partial", dir=name, err=details.get("note", ""))
        else:
            alert("verify_fail", dir=name, err=details.get("error", ""))
        return 0 if result in ("ok", "partial") else 1
    finally:
        lock.__exit__()


def cmd_prune():
    os.makedirs(backup_dir(), exist_ok=True)
    try:
        lock = Lock().__enter__()
    except BackupError as e:
        log(f"ERROR: {e}")
        return 1
    try:
        removed = prune_local()
        log(f"Rotation done, removed: {removed or 'nothing'}")
        if remote_enabled():
            prune_remote()
        return 0
    except Exception as e:
        log(f"ERROR: prune failed: {redact(str(e))}")
        return 1
    finally:
        lock.__exit__()


# ---------------------------------------------------------------- status / health
def cmd_status():
    st = read_status()
    st["warnings"] = current_warnings()
    print(json.dumps(st, indent=2))
    return 0


def cmd_health():
    if not env_bool("BACKUP_ENABLED", True):
        return 0
    st = read_status()
    lb = st.get("last_backup") or {}
    if lb.get("status") == "failed" or lb.get("ok") is False:
        print(f"unhealthy: last backup failed: {lb.get('error', '')}")
        return 1
    limit = dt.timedelta(hours=STALE_HOURS)
    ok_time = st.get("last_success_time")
    if ok_time:
        if now_utc() - parse_iso(ok_time) < limit:
            return 0
        print(f"unhealthy: last successful backup older than {STALE_HOURS}h ({ok_time})")
        return 1
    started = st.get("agent_started")
    if started and now_utc() - parse_iso(started) < limit:
        return 0
    print("unhealthy: no successful backup recorded")
    return 1


# ---------------------------------------------------------------- scheduler
def cmd_run():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    os.makedirs(backup_dir(), exist_ok=True)
    if not env_bool("BACKUP_ENABLED", True):
        log("BACKUP_ENABLED is false: agent idle")
        while True:
            time.sleep(3600)
    started = now_utc()
    st0 = read_status()
    # keep the original start time when restarting before the first backup ever succeeded
    write_status({"agent_started": st0.get("agent_started") if not st0.get("last_success_time")
                  and st0.get("agent_started") else iso(started)})
    log(f"Agent v{VERSION} started; daily backup at {env('BACKUP_TIME', '03:30')} ({env('TZ', 'Europe/Rome')}), "
        f"encrypted={bool(recipients())}, remote={env('BACKUP_REMOTE') or 'none'}")
    for w in current_warnings():
        log(f"WARNING: {w}")
    startup_check_at = time.time() + 60
    startup_done = False
    try:
        while True:
            now_local = dt.datetime.now(tzinfo())
            st = read_status()
            if not startup_done and time.time() >= startup_check_at:
                startup_done = True
                latest = list_backups()
                recent = bool(latest) and now_utc() - ts_to_dt(latest[-1]) < dt.timedelta(hours=24)
                if not recent:
                    log("No backup newer than 24h: running catch-up backup")
                    cmd_backup()
                    continue
            bt = parse_hhmm(env("BACKUP_TIME", "03:30"), dt.time(3, 30))
            last = st.get("last_attempt_backup")
            if due(bt, last, now_local):
                sched = dt.datetime.combine(now_local.date(), bt, now_local.tzinfo)
                names = list_backups()
                recent = bool(names) and now_utc() - ts_to_dt(names[-1]) < dt.timedelta(hours=24)
                if now_local - sched > dt.timedelta(minutes=10) and recent:
                    # missed slot (e.g. agent was down) but a backup younger than 24h exists
                    write_status({"last_attempt_backup": iso(now_utc())})
                else:
                    cmd_backup()
                    continue
            vs = verify_schedule()
            if vs and (vs[0] == "daily" or DAYS[now_local.weekday()] == vs[0][:3]) \
                    and due(vs[1], st.get("last_attempt_verify"), now_local) and list_backups():
                cmd_verify()
                continue
            time.sleep(30)
    except SystemExit:
        log("SIGTERM received, shutting down")
        raise


def main():
    ap = argparse.ArgumentParser(prog="agent.py")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("backup")
    v = sub.add_parser("verify")
    v.add_argument("--dir")
    sub.add_parser("prune")
    sub.add_parser("status")
    sub.add_parser("health")
    args = ap.parse_args()
    cmd = args.cmd or "run"
    if cmd == "run":
        return cmd_run()
    if cmd == "backup":
        return cmd_backup()
    if cmd == "verify":
        return cmd_verify(args.dir)
    if cmd == "prune":
        return cmd_prune()
    if cmd == "status":
        return cmd_status()
    if cmd == "health":
        try:
            code = cmd_health()
        except Exception as e:
            print(f"unhealthy: {e}")
            code = 1
        return code
    return 2


if __name__ == "__main__":
    sys.exit(main())
