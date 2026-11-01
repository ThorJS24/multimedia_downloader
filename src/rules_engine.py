import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config_loader import PROJECT_ROOT, get_downloads_path, get_data_dir
from src.db import get_db_connection
from src.security import SecurityManager

logger = logging.getLogger("RulesEngine")


class RulesEngine:
    """
    Declarative Rules Engine for Safe Music Organization & Automation.
    Evaluates rule conditions against library files, provides dry-run previews,
    executes atomic moves with collision policies, and records execution logs
    with full rollback support.
    """

    SUPPORTED_CONDITIONS = {
        "genre_contains",
        "artist_contains",
        "album_contains",
        "bitrate_below",
        "format_equals",
        "missing_tag",
        "all"
    }

    SUPPORTED_ACTIONS = {
        "move_to_template",
        "rename_template",
        "flag_issue"
    }

    @staticmethod
    def _sanitize_path_segment(val: Optional[str], default: str = "Unknown") -> str:
        if not val or not str(val).strip():
            return default
        # Strip illegal characters on Windows
        sanitized = re.sub(r'[\\/*?:"<>|]', "", str(val)).strip()
        sanitized = sanitized.rstrip(". ")
        return sanitized if sanitized else default

    @classmethod
    def get_rules(cls) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM organization_rules ORDER BY priority ASC, created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def save_rule(cls, rule_data: Dict[str, Any]) -> Dict[str, Any]:
        rule_id = rule_data.get("id") or f"rule_{uuid.uuid4().hex[:8]}"
        name = str(rule_data.get("name", "Untitled Rule")).strip()
        priority = int(rule_data.get("priority", 10))
        condition_type = str(rule_data.get("condition_type", "all")).strip()
        condition_value = str(rule_data.get("condition_value", "")).strip()
        action_type = str(rule_data.get("action_type", "move_to_template")).strip()
        action_value = str(rule_data.get("action_value", "{artist}/{album}/{title}.{ext}")).strip()
        is_enabled = 1 if rule_data.get("is_enabled", True) else 0

        conn = get_db_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO organization_rules (id, name, priority, condition_type, condition_value, action_type, action_value, is_enabled, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    priority = excluded.priority,
                    condition_type = excluded.condition_type,
                    condition_value = excluded.condition_value,
                    action_type = excluded.action_type,
                    action_value = excluded.action_value,
                    is_enabled = excluded.is_enabled
                """,
                (rule_id, name, priority, condition_type, condition_value, action_type, action_value, is_enabled, time.time())
            )
        return {
            "id": rule_id,
            "name": name,
            "priority": priority,
            "condition_type": condition_type,
            "condition_value": condition_value,
            "action_type": action_type,
            "action_value": action_value,
            "is_enabled": is_enabled
        }

    @classmethod
    def delete_rule(cls, rule_id: str) -> bool:
        conn = get_db_connection()
        with conn:
            cur = conn.execute("DELETE FROM organization_rules WHERE id = ?", (rule_id,))
            return cur.rowcount > 0

    @classmethod
    def evaluate_condition(cls, condition_type: str, condition_value: str, file_meta: Dict[str, Any]) -> bool:
        c_type = condition_type.lower()
        val = condition_value.strip().lower()

        if c_type == "all":
            return True

        if c_type == "genre_contains":
            genre = str(file_meta.get("genre") or "").lower()
            return val in genre if val else False

        if c_type == "artist_contains":
            artist = str(file_meta.get("artist") or "").lower()
            return val in artist if val else False

        if c_type == "album_contains":
            album = str(file_meta.get("album") or "").lower()
            return val in album if val else False

        if c_type == "bitrate_below":
            try:
                target_kbps = int(condition_value)
                actual_kbps = int(file_meta.get("bitrate_kbps") or 0)
                return actual_kbps > 0 and actual_kbps < target_kbps
            except (ValueError, TypeError):
                return False

        if c_type == "format_equals":
            fmt = str(file_meta.get("format") or "").lower().lstrip(".")
            return fmt == val.lstrip(".")

        if c_type == "missing_tag":
            tag_field = val
            if tag_field in ("artist", "author"):
                art = str(file_meta.get("artist") or "").strip()
                return not art or art.lower() == "unknown artist"
            if tag_field == "album":
                alb = str(file_meta.get("album") or "").strip()
                return not alb or alb.lower() == "unknown album"
            if tag_field == "year":
                yr = str(file_meta.get("year") or "").strip()
                return not yr
            if tag_field == "genre":
                gnr = str(file_meta.get("genre") or "").strip()
                return not gnr
            if tag_field in ("artwork", "cover"):
                return not bool(file_meta.get("has_artwork"))

        return False

    @classmethod
    def build_destination_path(cls, template: str, file_meta: Dict[str, Any], base_dir: Path) -> Path:
        artist = cls._sanitize_path_segment(file_meta.get("artist"), "Unknown Artist")
        album = cls._sanitize_path_segment(file_meta.get("album"), "Unknown Album")
        title = cls._sanitize_path_segment(file_meta.get("title"), Path(file_meta.get("path", "")).stem or "Track")
        track_num = file_meta.get("track_number") or 0
        track_str = f"{track_num:02d}" if track_num else "00"
        year = cls._sanitize_path_segment(str(file_meta.get("year") or ""), "Unknown Year")
        genre = cls._sanitize_path_segment(str(file_meta.get("genre") or ""), "General")

        orig_p = Path(file_meta.get("path", ""))
        ext = orig_p.suffix.lstrip(".").lower() or "mp3"

        # Expand template variables
        rel = template.replace("{artist}", artist)\
                      .replace("{album}", album)\
                      .replace("{title}", title)\
                      .replace("{track_number}", track_str)\
                      .replace("{year}", year)\
                      .replace("{genre}", genre)\
                      .replace("{ext}", ext)

        # Normalize relative path
        rel_p = Path(rel.replace("\\", "/"))
        return base_dir / rel_p

    @classmethod
    def preview(
        cls,
        rules: Optional[List[Dict[str, Any]]] = None,
        file_paths: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Calculates dry-run transformations without moving or altering any files on disk.
        """
        conn = get_db_connection()
        if rules is None:
            rules = cls.get_rules()

        active_rules = [r for r in rules if r.get("is_enabled", 1)]
        if not active_rules:
            # Default fallback rule if none configured
            active_rules = [{
                "id": "default",
                "name": "Standard Artist/Album Structure",
                "priority": 10,
                "condition_type": "all",
                "condition_value": "",
                "action_type": "move_to_template",
                "action_value": "{artist}/{album}/{title}.{ext}",
                "is_enabled": 1
            }]

        base_dir = get_downloads_path()

        # Fetch files
        if file_paths:
            placeholders = ",".join("?" for _ in file_paths)
            rows = conn.execute(
                f"SELECT * FROM library_files WHERE path IN ({placeholders})",
                file_paths
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM library_files LIMIT 2000").fetchall()

        files = [dict(r) for r in rows]

        planned_moves = []
        collisions = 0
        will_move_count = 0
        unchanged_count = 0

        for f in files:
            orig_path = Path(f["path"])
            matched_rule = None

            for rule in active_rules:
                if cls.evaluate_condition(rule["condition_type"], rule["condition_value"], f):
                    matched_rule = rule
                    break

            if not matched_rule:
                unchanged_count += 1
                continue

            action_type = matched_rule["action_type"]
            action_val = matched_rule["action_value"]

            if action_type == "move_to_template":
                dest_path = cls.build_destination_path(action_val, f, base_dir)
            elif action_type == "rename_template":
                dest_path = cls.build_destination_path(action_val, f, orig_path.parent)
            else:
                unchanged_count += 1
                continue

            orig_norm = orig_path.resolve()
            dest_norm = dest_path.resolve()

            will_move = (orig_norm != dest_norm)
            is_collision = will_move and dest_norm.exists()

            if is_collision:
                collisions += 1

            if will_move:
                will_move_count += 1
            else:
                unchanged_count += 1

            planned_moves.append({
                "rule_id": matched_rule.get("id"),
                "rule_name": matched_rule.get("name"),
                "source_path": str(orig_norm),
                "target_path": str(dest_norm),
                "filename": orig_path.name,
                "target_filename": dest_path.name,
                "will_move": will_move,
                "collision": is_collision,
                "action": action_type
            })

        return {
            "total_evaluated": len(files),
            "will_move": will_move_count,
            "unchanged": unchanged_count,
            "collisions": collisions,
            "planned_actions": planned_moves
        }

    @staticmethod
    def calculate_file_sha256(filepath: Path) -> str:
        """Computes SHA-256 checksum in 64KB blocks."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def get_rules_recovery_dir(cls) -> Path:
        """Returns the persistent recovery buffer directory on D: drive."""
        rec_dir = get_data_dir() / "recovery" / "rules_recovery"
        rec_dir.mkdir(parents=True, exist_ok=True)
        return rec_dir

    @classmethod
    def _create_recovery_copy(cls, source_dest: Path, batch_id: str) -> Tuple[str, str]:
        """
        Creates an atomic, flushed, fsync'd, and checksum-verified backup of source_dest
        in the persistent recovery directory prior to any destructive overwrite.
        """
        batch_dir = cls.get_rules_recovery_dir() / batch_id
        batch_dir.mkdir(parents=True, exist_ok=True)

        target_sha256 = cls.calculate_file_sha256(source_dest)
        tmp_backup = batch_dir / f".tmp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        final_backup = batch_dir / f"bak_{int(time.time() * 1000)}_{source_dest.name}"

        with open(source_dest, "rb") as f_in, open(tmp_backup, "wb") as f_out:
            while chunk := f_in.read(65536):
                f_out.write(chunk)
            f_out.flush()
            os.fsync(f_out.fileno())

        tmp_sha = cls.calculate_file_sha256(tmp_backup)
        if tmp_sha != target_sha256:
            tmp_backup.unlink(missing_ok=True)
            raise IOError(f"Recovery buffer checksum verification failed for {source_dest}")

        os.replace(str(tmp_backup), str(final_backup))
        return str(final_backup), target_sha256

    @classmethod
    def execute(
        cls,
        planned_actions: List[Dict[str, Any]],
        collision_policy: str = "skip"
    ) -> Dict[str, Any]:
        """
        Executes verified planned moves with atomic copy/move and rollback tracking.
        Collision policy options: 'skip', 'rename', 'overwrite'
        """
        batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        conn = get_db_connection()
        executed_count = 0
        skipped_count = 0
        error_count = 0
        records = []

        for item in planned_actions:
            if not item.get("will_move"):
                continue

            src = Path(item["source_path"]).resolve()
            dst = Path(item["target_path"]).resolve()

            # Security verification
            if not SecurityManager.validate_safe_path(src, PROJECT_ROOT) or \
               not SecurityManager.validate_safe_path(dst, PROJECT_ROOT):
                error_count += 1
                continue

            if not src.exists():
                error_count += 1
                continue

            actual_dst = dst
            backup_path_str = None
            target_sha256 = None
            source_sha256 = cls.calculate_file_sha256(src)

            if actual_dst.exists() and actual_dst != src:
                if collision_policy == "skip":
                    skipped_count += 1
                    continue
                elif collision_policy == "rename":
                    stem = actual_dst.stem
                    suffix = actual_dst.suffix
                    idx = 1
                    while actual_dst.exists():
                        actual_dst = actual_dst.parent / f"{stem}_{idx}{suffix}"
                        idx += 1
                elif collision_policy == "overwrite":
                    # Mandatory safety invariant: Never implement overwrite as an irreversible replacement.
                    # Preserve existing destination in verified recovery buffer before replacing.
                    try:
                        backup_path_str, target_sha256 = cls._create_recovery_copy(actual_dst, batch_id)
                    except Exception as e:
                        logger.error(f"Failed to create safe recovery buffer for {actual_dst}: {e}")
                        error_count += 1
                        continue

            try:
                actual_dst.parent.mkdir(parents=True, exist_ok=True)
                # Atomic move
                shutil.move(str(src), str(actual_dst))

                # Update database index record with atomic rollback protection
                try:
                    with conn:
                        conn.execute(
                            """
                            UPDATE library_files
                            SET path = ?, filename = ?, folder = ?
                            WHERE path = ?
                            """,
                            (str(actual_dst), actual_dst.name, str(actual_dst.parent), str(src))
                        )
                        conn.execute(
                            """
                            INSERT INTO rules_execution_log (id, batch_id, timestamp, rule_id, source_path, target_path, action_taken, status, details)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'SUCCESS', ?)
                            """,
                            (
                                f"log_{uuid.uuid4().hex[:8]}",
                                batch_id,
                                time.time(),
                                item.get("rule_id", "default"),
                                str(src),
                                str(actual_dst),
                                item.get("action", "move"),
                                json.dumps({
                                    "collision_policy": collision_policy,
                                    "backup_path": backup_path_str,
                                    "source_sha256": source_sha256,
                                    "target_sha256": target_sha256
                                })
                            )
                        )
                except Exception as db_err:
                    # Database failure after filesystem was mutated:
                    # Immediately reverse filesystem mutation to preserve DB-filesystem parity!
                    logger.error(f"Database commit failed after move {src} -> {actual_dst}: {db_err}. Reverting filesystem...")
                    try:
                        if actual_dst.exists():
                            shutil.move(str(actual_dst), str(src))
                        if backup_path_str and Path(backup_path_str).exists():
                            shutil.copy2(backup_path_str, str(actual_dst))
                    except Exception as rev_err:
                        logger.critical(f"Failed to reverse move after DB failure: {rev_err}")
                    raise db_err

                executed_count += 1
                records.append({"source": str(src), "target": str(actual_dst), "status": "SUCCESS"})
            except Exception as e:
                logger.error(f"Failed to move {src} -> {actual_dst}: {e}")
                error_count += 1
                with conn:
                    conn.execute(
                        """
                        INSERT INTO rules_execution_log (id, batch_id, timestamp, rule_id, source_path, target_path, action_taken, status, details)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'FAILED', ?)
                        """,
                        (
                            f"log_{uuid.uuid4().hex[:8]}",
                            batch_id,
                            time.time(),
                            item.get("rule_id", "default"),
                            str(src),
                            str(actual_dst),
                            item.get("action", "move"),
                            str(e)
                        )
                    )

        return {
            "success": error_count == 0,
            "batch_id": batch_id,
            "executed_count": executed_count,
            "skipped_count": skipped_count,
            "error_count": error_count,
            "records": records
        }

    @classmethod
    def rollback(cls, batch_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Reverses all file moves recorded under the specified batch (or latest batch).
        Restores any overwritten destination files from their verified recovery buffers.
        Detects destination modifications, corrupted backups, and preserves all user data.
        """
        conn = get_db_connection()
        if not batch_id:
            row = conn.execute(
                "SELECT batch_id FROM rules_execution_log WHERE status = 'SUCCESS' ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if not row:
                return {"success": False, "message": "No executed batches found to rollback"}
            batch_id = row["batch_id"]

        rows = conn.execute(
            "SELECT * FROM rules_execution_log WHERE batch_id = ? AND status = 'SUCCESS' ORDER BY timestamp DESC",
            (batch_id,)
        ).fetchall()

        if not rows:
            return {
                "success": False,
                "status": "already_rolled_back",
                "batch_id": batch_id,
                "reverted_count": 0,
                "message": f"No active un-reverted entries found for batch {batch_id} (already rolled back or non-existent)"
            }

        reverted_count = 0
        errors = []

        for r in rows:
            src = Path(r["source_path"]).resolve()
            dst = Path(r["target_path"]).resolve()

            if not dst.exists():
                errors.append(f"Destination file missing: {dst}")
                continue

            details = {}
            if r["details"]:
                try:
                    details = json.loads(r["details"])
                except Exception:
                    details = {}
            backup_path = details.get("backup_path")
            expected_source_sha256 = details.get("source_sha256")
            expected_target_sha256 = details.get("target_sha256")

            # Check if destination file was modified after the overwrite
            current_dst_sha256 = cls.calculate_file_sha256(dst)
            if expected_source_sha256 and current_dst_sha256 != expected_source_sha256:
                # Ambiguous / newer user data: Preserve all copies and report conflict!
                conflict_name = f"{dst.stem}_conflict_{int(time.time())}{dst.suffix}"
                conflict_path = dst.parent / conflict_name
                try:
                    shutil.copy2(str(dst), str(conflict_path))
                    kept = f"Preserved conflict copy '{conflict_name}'"
                except Exception:
                    logger.error("Could not save a conflict copy of %s", dst, exc_info=True)
                    kept = "Could NOT save a conflict copy (see the log)"
                msg = f"Conflict: Destination '{dst.name}' was modified after operation. {kept} and aborted rollback for manual resolution."
                logger.warning(msg)
                errors.append(msg)
                continue

            # Verify recovery copy integrity if an overwrite took place
            if backup_path:
                bak_p = Path(backup_path)
                if not bak_p.exists():
                    errors.append(f"Recovery copy missing for {dst}: {backup_path}")
                    continue
                if expected_target_sha256:
                    bak_sha = cls.calculate_file_sha256(bak_p)
                    if bak_sha != expected_target_sha256:
                        errors.append(f"Recovery copy corrupted (SHA-256 mismatch) for {dst}: expected {expected_target_sha256}, got {bak_sha}")
                        continue

            try:
                src.parent.mkdir(parents=True, exist_ok=True)
                # Revert moved file back to original location
                shutil.move(str(dst), str(src))

                # If an existing file was overwritten, restore it from recovery buffer
                if backup_path and Path(backup_path).exists():
                    shutil.copy2(backup_path, str(dst))
                    try:
                        Path(backup_path).unlink()
                    except OSError:
                        pass

                with conn:
                    conn.execute(
                        "UPDATE library_files SET path = ?, filename = ?, folder = ? WHERE path = ?",
                        (str(src), src.name, str(src.parent), str(dst))
                    )
                    conn.execute(
                        "UPDATE rules_execution_log SET status = 'ROLLED_BACK' WHERE id = ?",
                        (r["id"],)
                    )
                reverted_count += 1
            except Exception as e:
                errors.append(str(e))

        return {
            "success": len(errors) == 0,
            "batch_id": batch_id,
            "reverted_count": reverted_count,
            "errors": errors
        }

    @classmethod
    def get_batches(cls, limit: int = 20) -> List[Dict[str, Any]]:
        """One row per executed batch (newest first) so a whole batch can be undone in one step."""
        conn = get_db_connection()
        rows = conn.execute(
            """SELECT batch_id, MIN(timestamp) AS started_at, COUNT(*) AS total,
                      SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS moved,
                      SUM(CASE WHEN status = 'ROLLED_BACK' THEN 1 ELSE 0 END) AS rolled_back
               FROM rules_execution_log GROUP BY batch_id ORDER BY started_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    @classmethod
    def get_history(cls, limit: int = 50) -> List[Dict[str, Any]]:
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM rules_execution_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
