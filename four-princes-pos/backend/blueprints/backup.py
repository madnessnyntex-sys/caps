"""Admin-only database backup and restore.

For SQLite (the zero-setup default), a "backup" is just a timestamped copy
of the .db file -- SQLite is a single file, so copying it IS a full backup.
"Restore" is the reverse: copy a chosen .db file back over the live one.

For MySQL (the thesis's documented database), a backup is a .sql dump
produced by shelling out to `mysqldump`. Restore pipes a chosen .sql file
into the `mysql` client against the live database.

There are two ways to restore, sharing the same validated logic:
  - POST /restore            -- upload a file from your computer
  - POST /<filename>/restore -- pick one already sitting in the list below

Restore is the riskier of the two operations, so every restore -- either
way -- is preceded by an automatic, non-skippable safety backup of whatever
is live right now. If the wrong file gets picked, the previous state is one
more restore away, not gone.

Backups are written to backend/backups/, created on first use.
"""
import os
import shutil
import subprocess
from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, current_app, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

from auth_utils import role_required, token_required
from extensions import db

backup_bp = Blueprint("backup", __name__)

SQLITE_HEADER = b"SQLite format 3\x00"


def _backup_dir():
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(backend_dir, "backups")
    os.makedirs(path, exist_ok=True)
    return path


def _sqlite_path(uri):
    # Strip the 3-slash "sqlite:///" prefix, not 2. Getting this wrong leaves
    # a stray leading slash that Linux quietly tolerates but Windows can't
    # resolve (turns C:\Users\... into \C:\Users\...).
    return uri[len("sqlite:///"):]


def _mysql_connection_parts(uri):
    parsed = urlparse(uri)
    return {
        "dbname": parsed.path.lstrip("/"),
        "hostname": parsed.hostname or "localhost",
        "port": parsed.port,
        "username": parsed.username or "root",
        "password": parsed.password,
    }


# Parent-to-child order (respects foreign keys for CREATE/INSERT); reversed
# gives child-to-parent order for DROP. Kept in sync with models.py's 6 tables.
_TABLE_ORDER = ["users", "categories", "products", "stock_movements", "sales", "sale_items"]


def _raw_pymysql_connection(conn, multi_statements=False):
    import pymysql
    kwargs = dict(
        host=conn["hostname"], user=conn["username"], password=conn["password"] or "",
        database=conn["dbname"], charset="utf8mb4",
    )
    if conn["port"]:
        kwargs["port"] = conn["port"]
    if multi_statements:
        from pymysql.constants import CLIENT
        kwargs["client_flag"] = CLIENT.MULTI_STATEMENTS
    return pymysql.connect(**kwargs)


def _mysql_dump_pure_python(conn, dest_path):
    """A `mysqldump`-equivalent dump using only PyMySQL (no CLI binary) --
    the fallback used when `mysqldump` isn't installed, e.g. on a serverless
    host like Vercel. Produces plain, portable SQL any MySQL client can run."""
    cx = _raw_pymysql_connection(conn)
    try:
        with cx.cursor() as cur, open(dest_path, "w", encoding="utf-8") as out:
            out.write("-- Four Princes pure-Python backup (mysqldump not available)\n")
            out.write("SET FOREIGN_KEY_CHECKS=0;\n\n")

            for table in reversed(_TABLE_ORDER):
                out.write(f"DROP TABLE IF EXISTS `{table}`;\n")
            out.write("\n")

            create_sql = {}
            for table in _TABLE_ORDER:
                cur.execute(f"SHOW CREATE TABLE `{table}`")
                create_sql[table] = cur.fetchone()[1]
                out.write(create_sql[table] + ";\n\n")

            for table in _TABLE_ORDER:
                cur.execute(f"SELECT * FROM `{table}`")
                columns = [d[0] for d in cur.description]
                col_list = ", ".join(f"`{c}`" for c in columns)
                rows = cur.fetchall()
                if not rows:
                    continue
                out.write(f"-- Data for `{table}` ({len(rows)} rows)\n")
                for row in rows:
                    values = ", ".join(cx.escape(v) for v in row)
                    out.write(f"INSERT INTO `{table}` ({col_list}) VALUES ({values});\n")
                out.write("\n")

            out.write("SET FOREIGN_KEY_CHECKS=1;\n")
    finally:
        cx.close()


def _mysql_restore_pure_python(conn, src_path):
    """Runs a .sql script (ours or a real mysqldump's) via PyMySQL's
    multi-statement execution -- no `mysql` CLI binary required."""
    with open(src_path, "r", encoding="utf-8", errors="replace") as f:
        sql_text = f.read()
    cx = _raw_pymysql_connection(conn, multi_statements=True)
    try:
        with cx.cursor() as cur:
            cur.execute(sql_text)
            while cur.nextset():
                pass
        cx.commit()
    finally:
        cx.close()


def _create_backup_internal(uri, folder):
    """Returns {"filename", "size_bytes", "created_at"} or {"error"}."""
    # Microsecond precision matters here: a restore's mandatory safety backup
    # can happen within the same second as the backup being restored from,
    # and a filename collision would silently overwrite one with the other.
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")

    if uri.startswith("sqlite:"):
        src_path = _sqlite_path(uri)
        if not os.path.exists(src_path):
            return {"error": f"Database file not found at: {src_path}"}

        filename = f"four_princes_backup_{timestamp}.db"
        dest_path = os.path.join(folder, filename)
        try:
            shutil.copyfile(src_path, dest_path)
        except OSError as e:
            return {"error": f"Could not copy database file: {e}"}

    elif uri.startswith("mysql"):
        conn = _mysql_connection_parts(uri)
        filename = f"four_princes_backup_{timestamp}.sql"
        dest_path = os.path.join(folder, filename)

        cmd = ["mysqldump", "-h", conn["hostname"]]
        if conn["port"]:
            cmd += ["-P", str(conn["port"])]
        cmd += ["-u", conn["username"]]
        if conn["password"]:
            cmd += [f"-p{conn['password']}"]
        cmd += [conn["dbname"]]

        try:
            with open(dest_path, "wb") as out_file:
                result = subprocess.run(cmd, stdout=out_file, stderr=subprocess.PIPE, timeout=120)
            if result.returncode != 0:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                return {"error": f"mysqldump failed: {result.stderr.decode(errors='ignore')[:300]}"}
        except FileNotFoundError:
            # No mysqldump binary on this machine (e.g. a serverless host like
            # Vercel) -- fall back to a pure-Python/SQL equivalent dump.
            try:
                _mysql_dump_pure_python(conn, dest_path)
            except Exception as e:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                return {"error": f"Backup failed (pure-Python fallback): {e}"}
        except subprocess.TimeoutExpired:
            if os.path.exists(dest_path):
                os.remove(dest_path)
            return {"error": "Backup timed out after 2 minutes"}
    else:
        return {"error": "Unrecognized database type for backup"}

    return {
        "filename": filename,
        "size_bytes": os.path.getsize(dest_path),
        "created_at": datetime.utcnow().isoformat() + "Z",
    }


def _restore_from_path(uri, folder, src_path, original_filename):
    """Validates src_path as a backup for this DB type, creates a mandatory
    safety backup of the current live data, then restores from src_path.

    Returns {"safety_backup": filename} on success, or
    {"error": ..., "status": <400 or 500>} on failure. Never deletes
    src_path -- that's the caller's responsibility.
    """
    is_db_file = original_filename.lower().endswith(".db")
    is_sql_file = original_filename.lower().endswith(".sql")

    if uri.startswith("sqlite:"):
        if not is_db_file:
            return {"error": "This system runs on SQLite - restore needs a .db backup file", "status": 400}
        with open(src_path, "rb") as f:
            header = f.read(len(SQLITE_HEADER))
        if header != SQLITE_HEADER:
            return {"error": "That file doesn't look like a valid SQLite database", "status": 400}

        safety = _create_backup_internal(uri, folder)
        if "error" in safety:
            return {
                "error": f"Restore was cancelled - couldn't create a safety backup first: {safety['error']}",
                "status": 500,
            }

        db.session.close()
        db.engine.dispose()  # release pooled connections before swapping the file out
        try:
            shutil.copyfile(src_path, _sqlite_path(uri))
        except OSError as e:
            return {
                "error": f"Restore failed while copying the file: {e}. Your data was not changed; "
                         f"the safety backup '{safety['filename']}' was created but is not needed.",
                "status": 500,
            }

    elif uri.startswith("mysql"):
        if not is_sql_file:
            return {"error": "This system runs on MySQL - restore needs a .sql backup file", "status": 400}

        safety = _create_backup_internal(uri, folder)
        if "error" in safety:
            return {
                "error": f"Restore was cancelled - couldn't create a safety backup first: {safety['error']}",
                "status": 500,
            }

        conn = _mysql_connection_parts(uri)
        cmd = ["mysql", "-h", conn["hostname"]]
        if conn["port"]:
            cmd += ["-P", str(conn["port"])]
        cmd += ["-u", conn["username"]]
        if conn["password"]:
            cmd += [f"-p{conn['password']}"]
        cmd += [conn["dbname"]]

        db.session.close()
        db.engine.dispose()
        try:
            with open(src_path, "rb") as in_file:
                result = subprocess.run(cmd, stdin=in_file, stderr=subprocess.PIPE, timeout=120)
            if result.returncode != 0:
                stderr_text = result.stderr.decode(errors="ignore")[:300]
                return {
                    "error": f"Restore failed: {stderr_text}. Your safety backup "
                             f"'{safety['filename']}' is intact if you need to check your prior state.",
                    "status": 500,
                }
        except FileNotFoundError:
            # No `mysql` CLI client on this machine (e.g. Vercel) -- fall back
            # to a pure-Python restore via PyMySQL's multi-statement execution.
            try:
                _mysql_restore_pure_python(conn, src_path)
            except Exception as e:
                return {
                    "error": f"Restore failed (pure-Python fallback): {e}. Your safety backup "
                             f"'{safety['filename']}' is intact if you need to check your prior state.",
                    "status": 500,
                }
        except subprocess.TimeoutExpired:
            return {"error": "Restore timed out after 2 minutes", "status": 500}
    else:
        return {"error": "Unrecognized database type for restore", "status": 400}

    return {"safety_backup": safety["filename"]}


@backup_bp.route("", methods=["GET"])
@token_required
@role_required("admin")
def list_backups():
    folder = _backup_dir()
    files = []
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        if os.path.isfile(full):
            stat = os.stat(full)
            files.append({
                "filename": name,
                "size_bytes": stat.st_size,
                "created_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
            })
    files.sort(key=lambda f: f["created_at"], reverse=True)
    return jsonify(files)


@backup_bp.route("", methods=["POST"])
@token_required
@role_required("admin")
def create_backup():
    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    result = _create_backup_internal(uri, _backup_dir())
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result), 201


@backup_bp.route("/<path:filename>/download", methods=["GET"])
@token_required
@role_required("admin")
def download_backup(filename):
    safe_name = secure_filename(filename)
    folder = _backup_dir()
    if not safe_name or not os.path.isfile(os.path.join(folder, safe_name)):
        return jsonify({"error": "Backup not found"}), 404
    return send_from_directory(folder, safe_name, as_attachment=True)


@backup_bp.route("/<path:filename>", methods=["DELETE"])
@token_required
@role_required("admin")
def delete_backup(filename):
    safe_name = secure_filename(filename)
    folder = _backup_dir()
    full_path = os.path.join(folder, safe_name)
    if not safe_name or not os.path.isfile(full_path):
        return jsonify({"error": "Backup not found"}), 404
    os.remove(full_path)
    return jsonify({"message": "Backup deleted"})


@backup_bp.route("/restore", methods=["POST"])
@token_required
@role_required("admin")
def restore_from_upload():
    """Restore from a file uploaded straight from the admin's computer
    (the drag-and-drop / browse flow)."""
    if "file" not in request.files or request.files["file"].filename == "":
        return jsonify({"error": "No file was uploaded"}), 400

    uploaded = request.files["file"]
    original_name = uploaded.filename
    if not original_name.lower().endswith((".db", ".sql")):
        return jsonify({"error": "Please choose a .db or .sql backup file"}), 400

    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    folder = _backup_dir()
    temp_name = f"_incoming_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}_{secure_filename(original_name)}"
    temp_path = os.path.join(folder, temp_name)
    uploaded.save(temp_path)

    try:
        result = _restore_from_path(uri, folder, temp_path, original_name)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    if "error" in result:
        return jsonify({"error": result["error"]}), result.get("status", 500)
    return jsonify({"message": "Restore complete", "safety_backup": result["safety_backup"]})


@backup_bp.route("/<path:filename>/restore", methods=["POST"])
@token_required
@role_required("admin")
def restore_from_existing(filename):
    """Restore from a backup that's already sitting on the server -- pick
    one straight from the list instead of downloading it first just to
    drag it back in."""
    safe_name = secure_filename(filename)
    folder = _backup_dir()
    src_path = os.path.join(folder, safe_name)
    if not safe_name or not os.path.isfile(src_path):
        return jsonify({"error": "Backup not found"}), 404

    uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    result = _restore_from_path(uri, folder, src_path, safe_name)
    if "error" in result:
        return jsonify({"error": result["error"]}), result.get("status", 500)
    return jsonify({"message": "Restore complete", "safety_backup": result["safety_backup"]})
