"""
Phase 13.1 — Dedicated Overwrite Recovery Stress Test & Failure Injection Suite.
Validates:
1. Successful overwrite followed by rollback with SHA-256 preservation on both ends.
2. Failure injection while creating the recovery copy (source/dest untouched).
3. Failure injection after recovery copy creation but before replacement completes.
4. Failure injection after replacement completes but before DB commit (automatic filesystem reversal).
5. Missing recovery copy during rollback (graceful error handling, no crash).
6. Corrupted recovery copy during rollback (checksum mismatch detection, refuses corrupted overwrite).
7. Destination modified after original overwrite (conflict preservation of newer user data).
8. Repeated rollback attempts (idempotency, no state corruption).
9. Multiple operations in a batch with intermediate operation failure.
10. Collision policies: 'skip', 'rename' (with extension preservation), and 'overwrite'.
11. Cross-volume move fallback verification.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401

import hashlib
import json
import os
import shutil
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config_loader import get_temp_path
from src.db import init_db, get_db_connection
from src.rules_engine import RulesEngine


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class TestRecoveryStress(unittest.TestCase):
    def setUp(self):
        init_db()
        self.test_dir = get_temp_path() / f"test_stress_{os.getpid()}_{int(time.time() * 1000)}"
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.conn = get_db_connection()

    def tearDown(self):
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_file(self, path: Path, content: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return sha256_of_file(path)

    def test_01_successful_overwrite_and_rollback(self):
        """Test successful overwrite followed by rollback with bit-for-bit SHA-256 verification."""
        src_path = self.test_dir / "src_track.mp3"
        dst_path = self.test_dir / "dst_track.mp3"

        src_content = b"NEW_AUDIO_CONTENT_SRC_12345"
        dst_content = b"EXISTING_AUDIO_CONTENT_DST_67890"

        src_hash = self._create_file(src_path, src_content)
        dst_hash = self._create_file(dst_path, dst_content)

        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO library_files (path, filename, folder) VALUES (?, ?, ?)",
                (str(src_path), src_path.name, str(src_path.parent))
            )

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_test_1"
        }]

        # 1. Execute overwrite
        result = RulesEngine.execute(planned, collision_policy="overwrite")
        self.assertTrue(result["success"])
        self.assertEqual(result["executed_count"], 1)

        batch_id = result["batch_id"]

        # Destination must now hold source content
        self.assertFalse(src_path.exists())
        self.assertTrue(dst_path.exists())
        self.assertEqual(sha256_of_file(dst_path), src_hash)

        # Database must be updated
        row = self.conn.execute("SELECT path FROM library_files WHERE path = ?", (str(dst_path),)).fetchone()
        self.assertIsNotNone(row)

        # Execution log must record backup path and hashes
        log_row = self.conn.execute("SELECT * FROM rules_execution_log WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(log_row["status"], "SUCCESS")
        details = json.loads(log_row["details"])
        self.assertEqual(details["source_sha256"], src_hash)
        self.assertEqual(details["target_sha256"], dst_hash)
        self.assertTrue(Path(details["backup_path"]).exists())
        self.assertEqual(sha256_of_file(Path(details["backup_path"])), dst_hash)

        # 2. Rollback
        rb_res = RulesEngine.rollback(batch_id)
        self.assertTrue(rb_res["success"])
        self.assertEqual(rb_res["reverted_count"], 1)

        # Both files must be restored to original paths and original hashes
        self.assertTrue(src_path.exists())
        self.assertTrue(dst_path.exists())
        self.assertEqual(sha256_of_file(src_path), src_hash)
        self.assertEqual(sha256_of_file(dst_path), dst_hash)

    def test_02_failure_during_recovery_copy_creation(self):
        """Failure while creating recovery copy leaves source and destination 100% untouched."""
        src_path = self.test_dir / "src_fail.mp3"
        dst_path = self.test_dir / "dst_fail.mp3"

        src_hash = self._create_file(src_path, b"SRC_DATA_UNTOUCHED")
        dst_hash = self._create_file(dst_path, b"DST_DATA_UNTOUCHED")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_fail_1"
        }]

        # Inject failure in _create_recovery_copy
        with patch.object(RulesEngine, "_create_recovery_copy", side_effect=IOError("Simulated disk full")):
            result = RulesEngine.execute(planned, collision_policy="overwrite")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["executed_count"], 0)

        # Invariant: Neither file was modified
        self.assertTrue(src_path.exists())
        self.assertTrue(dst_path.exists())
        self.assertEqual(sha256_of_file(src_path), src_hash)
        self.assertEqual(sha256_of_file(dst_path), dst_hash)

    def test_03_failure_before_replacement_completes(self):
        """Failure after recovery copy created but before replacement completes leaves files untouched."""
        src_path = self.test_dir / "src_rep.mp3"
        dst_path = self.test_dir / "dst_rep.mp3"

        src_hash = self._create_file(src_path, b"SRC_DATA_SAFE")
        dst_hash = self._create_file(dst_path, b"DST_DATA_SAFE")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_rep_1"
        }]

        # Inject failure in shutil.move
        with patch("shutil.move", side_effect=PermissionError("Simulated lock on destination")):
            result = RulesEngine.execute(planned, collision_policy="overwrite")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_count"], 1)

        # Source and destination are intact
        self.assertTrue(src_path.exists())
        self.assertTrue(dst_path.exists())
        self.assertEqual(sha256_of_file(src_path), src_hash)
        self.assertEqual(sha256_of_file(dst_path), dst_hash)

    def test_04_failure_after_replacement_before_db_commit(self):
        """Failure in DB commit after replacement triggers automatic filesystem rollback to pre-move state."""
        src_path = self.test_dir / "src_db_fail.mp3"
        dst_path = self.test_dir / "dst_db_fail.mp3"

        src_hash = self._create_file(src_path, b"SRC_DB_FAIL_DATA")
        dst_hash = self._create_file(dst_path, b"DST_DB_FAIL_DATA")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_db_fail"
        }]

        orig_conn = self.conn

        class FailingConnProxy:
            def __init__(self, target):
                self._target = target

            def execute(self, sql, *args, **kwargs):
                if "INSERT INTO rules_execution_log" in sql and "SUCCESS" in sql:
                    raise RuntimeError("Simulated DB lock/crash on commit")
                return self._target.execute(sql, *args, **kwargs)

            def __enter__(self):
                self._target.__enter__()
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                return self._target.__exit__(exc_type, exc_val, exc_tb)

            def __getattr__(self, name):
                return getattr(self._target, name)

        with patch("src.rules_engine.get_db_connection", return_value=FailingConnProxy(orig_conn)):
            result = RulesEngine.execute(planned, collision_policy="overwrite")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_count"], 1)

        # Filesystem must have been automatically reversed!
        self.assertTrue(src_path.exists())
        self.assertTrue(dst_path.exists())
        self.assertEqual(sha256_of_file(src_path), src_hash)
        self.assertEqual(sha256_of_file(dst_path), dst_hash)

    def test_05_missing_recovery_copy_during_rollback(self):
        """Rollback detects missing recovery copy and reports error without crashing."""
        src_path = self.test_dir / "src_missing_rec.mp3"
        dst_path = self.test_dir / "dst_missing_rec.mp3"

        self._create_file(src_path, b"SRC_FOR_MISSING_REC")
        self._create_file(dst_path, b"DST_FOR_MISSING_REC")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_mrec"
        }]

        result = RulesEngine.execute(planned, collision_policy="overwrite")
        batch_id = result["batch_id"]

        # Delete the backup copy externally
        log_row = self.conn.execute("SELECT details FROM rules_execution_log WHERE batch_id = ?", (batch_id,)).fetchone()
        details = json.loads(log_row["details"])
        Path(details["backup_path"]).unlink()

        # Execute rollback
        rb_res = RulesEngine.rollback(batch_id)
        self.assertFalse(rb_res["success"])
        self.assertIn("Recovery copy missing", rb_res["errors"][0])

    def test_06_corrupted_recovery_copy_during_rollback(self):
        """Rollback detects corrupted recovery copy (SHA-256 mismatch) and refuses to overwrite destination."""
        src_path = self.test_dir / "src_corrupt_rec.mp3"
        dst_path = self.test_dir / "dst_corrupt_rec.mp3"

        self._create_file(src_path, b"SRC_FOR_CORRUPT_REC")
        self._create_file(dst_path, b"DST_FOR_CORRUPT_REC")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_crec"
        }]

        result = RulesEngine.execute(planned, collision_policy="overwrite")
        batch_id = result["batch_id"]

        # Corrupt the backup copy externally
        log_row = self.conn.execute("SELECT details FROM rules_execution_log WHERE batch_id = ?", (batch_id,)).fetchone()
        details = json.loads(log_row["details"])
        Path(details["backup_path"]).write_bytes(b"CORRUPTED_BYTES_IN_BACKUP")

        # Execute rollback
        rb_res = RulesEngine.rollback(batch_id)
        self.assertFalse(rb_res["success"])
        self.assertIn("Recovery copy corrupted (SHA-256 mismatch)", rb_res["errors"][0])

    def test_07_destination_modified_after_overwrite_conflict_preservation(self):
        """Rollback detects destination modified after overwrite, preserves all copies and reports conflict."""
        src_path = self.test_dir / "src_conflict.mp3"
        dst_path = self.test_dir / "dst_conflict.mp3"

        self._create_file(src_path, b"SRC_ORIGINAL_CONTENT")
        self._create_file(dst_path, b"DST_ORIGINAL_CONTENT")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_conflict"
        }]

        result = RulesEngine.execute(planned, collision_policy="overwrite")
        batch_id = result["batch_id"]

        # User modifies destination file after the overwrite
        newer_content = b"NEWER_USER_DATA_EDITED_BY_USER_AFTER_OPERATION"
        newer_hash = self._create_file(dst_path, newer_content)

        # Rollback
        rb_res = RulesEngine.rollback(batch_id)
        self.assertFalse(rb_res["success"])
        self.assertTrue(any("Conflict: Destination" in err for err in rb_res["errors"]))

        # Verify newer data was NOT silently destroyed!
        conflict_files = list(self.test_dir.glob("dst_conflict_conflict_*.mp3"))
        self.assertTrue(len(conflict_files) > 0)
        self.assertEqual(sha256_of_file(conflict_files[0]), newer_hash)

    def test_08_repeated_rollback_idempotency(self):
        """Repeated rollback calls return already_rolled_back status without crashing or erroring."""
        src_path = self.test_dir / "src_idem.mp3"
        dst_path = self.test_dir / "dst_idem.mp3"

        self._create_file(src_path, b"IDEMPOTENCY_SRC")
        self._create_file(dst_path, b"IDEMPOTENCY_DST")

        planned = [{
            "source_path": str(src_path),
            "target_path": str(dst_path),
            "will_move": True,
            "rule_id": "rule_idem"
        }]

        res = RulesEngine.execute(planned, collision_policy="overwrite")
        batch_id = res["batch_id"]

        # Run 1: Successful rollback
        rb1 = RulesEngine.rollback(batch_id)
        self.assertTrue(rb1["success"])
        self.assertEqual(rb1["reverted_count"], 1)

        # Run 2: Idempotent repeat
        rb2 = RulesEngine.rollback(batch_id)
        self.assertFalse(rb2["success"])
        self.assertEqual(rb2["status"], "already_rolled_back")
        self.assertEqual(rb2["reverted_count"], 0)

        # Run 3: Idempotent repeat again
        rb3 = RulesEngine.rollback(batch_id)
        self.assertFalse(rb3["success"])
        self.assertEqual(rb3["status"], "already_rolled_back")

    def test_09_batch_intermediate_operation_failure(self):
        """In a multi-file batch where an intermediate item fails, valid items succeed and rollback safely."""
        f1_src = self.test_dir / "b_f1_src.mp3"
        f1_dst = self.test_dir / "b_f1_dst.mp3"
        f2_src = self.test_dir / "b_f2_ghost.mp3"  # missing
        f2_dst = self.test_dir / "b_f2_dst.mp3"
        f3_src = self.test_dir / "b_f3_src.mp3"
        f3_dst = self.test_dir / "b_f3_dst.mp3"

        f1_hash = self._create_file(f1_src, b"BATCH_F1")
        f3_hash = self._create_file(f3_src, b"BATCH_F3")

        planned = [
            {"source_path": str(f1_src), "target_path": str(f1_dst), "will_move": True},
            {"source_path": str(f2_src), "target_path": str(f2_dst), "will_move": True},
            {"source_path": str(f3_src), "target_path": str(f3_dst), "will_move": True},
        ]

        result = RulesEngine.execute(planned, collision_policy="skip")
        self.assertFalse(result["success"])  # partial failure due to f2
        self.assertEqual(result["executed_count"], 2)
        self.assertEqual(result["error_count"], 1)

        batch_id = result["batch_id"]

        # Rollback should revert f1 and f3 cleanly
        rb_res = RulesEngine.rollback(batch_id)
        self.assertTrue(rb_res["success"])
        self.assertEqual(rb_res["reverted_count"], 2)

        self.assertTrue(f1_src.exists())
        self.assertTrue(f3_src.exists())
        self.assertEqual(sha256_of_file(f1_src), f1_hash)
        self.assertEqual(sha256_of_file(f3_src), f3_hash)

    def test_10_collision_policies_skip_rename_overwrite(self):
        """Verify collision policies: skip, rename (with index and extension preservation), and overwrite."""
        # 1. Policy: SKIP
        src1 = self.test_dir / "p_skip_src.mp3"
        dst1 = self.test_dir / "p_skip_dst.mp3"
        self._create_file(src1, b"DATA_SRC1")
        h_dst1 = self._create_file(dst1, b"DATA_DST1")

        res_skip = RulesEngine.execute(
            [{"source_path": str(src1), "target_path": str(dst1), "will_move": True}],
            collision_policy="skip"
        )
        self.assertEqual(res_skip["skipped_count"], 1)
        self.assertEqual(res_skip["executed_count"], 0)
        self.assertTrue(src1.exists())
        self.assertEqual(sha256_of_file(dst1), h_dst1)

        # 2. Policy: RENAME
        src2 = self.test_dir / "p_ren_src.mp3"
        dst2 = self.test_dir / "p_ren_dst.mp3"
        h_src2 = self._create_file(src2, b"DATA_SRC2")
        h_dst2 = self._create_file(dst2, b"DATA_DST2")

        res_ren = RulesEngine.execute(
            [{"source_path": str(src2), "target_path": str(dst2), "will_move": True}],
            collision_policy="rename"
        )
        self.assertTrue(res_ren["success"])
        self.assertEqual(res_ren["executed_count"], 1)

        # Original dst2 remains unchanged
        self.assertEqual(sha256_of_file(dst2), h_dst2)
        # Renamed target should be p_ren_dst_1.mp3
        renamed_target = self.test_dir / "p_ren_dst_1.mp3"
        self.assertTrue(renamed_target.exists())
        self.assertEqual(sha256_of_file(renamed_target), h_src2)

        # 3. Policy: OVERWRITE
        src3 = self.test_dir / "p_ovw_src.mp3"
        dst3 = self.test_dir / "p_ovw_dst.mp3"
        h_src3 = self._create_file(src3, b"DATA_SRC3")
        h_dst3 = self._create_file(dst3, b"DATA_DST3")

        res_ovw = RulesEngine.execute(
            [{"source_path": str(src3), "target_path": str(dst3), "will_move": True}],
            collision_policy="overwrite"
        )
        self.assertTrue(res_ovw["success"])
        self.assertEqual(res_ovw["executed_count"], 1)
        self.assertEqual(sha256_of_file(dst3), h_src3)

        # Rollback restores h_dst3
        RulesEngine.rollback(res_ovw["batch_id"])
        self.assertEqual(sha256_of_file(dst3), h_dst3)
        self.assertEqual(sha256_of_file(src3), h_src3)

    def test_11_cross_volume_fallback(self):
        """Cross-volume moves (where os.replace raises OSError 17) cleanly fall back to copy2 + unlink."""
        src = self.test_dir / "cross_src.mp3"
        dst = self.test_dir / "cross_dst.mp3"
        h_src = self._create_file(src, b"CROSS_VOLUME_AUDIO_STREAM")


        def cross_volume_replace(src_p, dst_p):
            # Simulate EXDEV / WinError 17 across volumes on first replace call
            raise OSError(17, "Cross-device link")

        # Patch os.replace during shutil.move
        with patch("os.replace", side_effect=cross_volume_replace):
            res = RulesEngine.execute(
                [{"source_path": str(src), "target_path": str(dst), "will_move": True}],
                collision_policy="skip"
            )

        self.assertTrue(res["success"])
        self.assertEqual(res["executed_count"], 1)
        self.assertFalse(src.exists())
        self.assertTrue(dst.exists())
        self.assertEqual(sha256_of_file(dst), h_src)


if __name__ == "__main__":
    unittest.main()
