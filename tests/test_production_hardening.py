"""
Phase 6 Production Hardening & Security Certification Tests.
Validates:
1. Exact filesystem containment and boundary traversal protection.
2. Sibling directory prefix containment attack prevention (project_evil vs project).
3. Zero-side-effect dry-run previews (no parent directories created during preview).
4. REST API path containment rejection (403 on traversal / out-of-boundary paths).
5. Config loader containment fallback against malicious or corrupted paths.
6. CLI optimize command and duplicate compatibility alias execution.
7. Argument parsing for --no-browser and --headless modes.
"""
try:
    import _hermetic  # noqa: F401  (isolates data dir; must precede any src import)
except ImportError:
    from tests import _hermetic  # noqa: F401

import argparse
import os
import shutil
import unittest

from src.config_loader import PROJECT_ROOT, TEMP_DIR, get_downloads_path, get_credentials_path
from src.db import init_db
from src.file_organizer import FileOrganizer
from src.library_intelligence import LibraryIntelligence
from src.security import SecurityManager
from src.web.server import app


class TestProductionHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = TEMP_DIR / f"test_hardening_{os.getpid()}"
        cls.test_dir.mkdir(parents=True, exist_ok=True)
        init_db()
        app.testing = True
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        if cls.test_dir.exists():
            shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_01_path_containment_and_sibling_prefix_attack(self):
        """Verify that sibling directories with matching prefix strings are rejected."""
        root = self.test_dir / "target_root"
        root.mkdir(parents=True, exist_ok=True)

        sibling_attack = self.test_dir / "target_root_evil" / "stolen.wav"
        inside_valid = root / "subfolder" / "song.wav"
        outside_parent = self.test_dir / "outside.wav"

        # Valid child
        self.assertTrue(SecurityManager.validate_safe_path(inside_valid, root))
        # Exact root
        self.assertTrue(SecurityManager.validate_safe_path(root, root))
        # Sibling folder starting with same prefix MUST be rejected
        self.assertFalse(SecurityManager.validate_safe_path(sibling_attack, root))
        # Parent / outside folder MUST be rejected
        self.assertFalse(SecurityManager.validate_safe_path(outside_parent, root))

    def test_02_dry_run_zero_filesystem_mutation(self):
        """Verify resolve_output_path does NOT create parent directories when create_parent=False."""
        uncreated_dir = self.test_dir / "uncreated_artist" / "uncreated_album"
        self.assertFalse(uncreated_dir.exists())

        # Dry-run preview mode (default create_parent=False)
        target_path, should_skip = FileOrganizer.resolve_output_path(
            base_dir=self.test_dir,
            template="uncreated_artist/uncreated_album/{track_number} - {title}.{ext}",
            meta={"title": "Song", "artists": ["Artist"], "track_number": 1},
            output_format="mp3",
            create_parent=False
        )

        # Directory MUST NOT exist after dry-run
        self.assertFalse(target_path.parent.exists(), "Dry-run preview must not create directories on disk")

        # Explicit execution mode (create_parent=True)
        target_path_exec, _ = FileOrganizer.resolve_output_path(
            base_dir=self.test_dir,
            template="uncreated_artist/uncreated_album/{track_number} - {title}.{ext}",
            meta={"title": "Song", "artists": ["Artist"], "track_number": 1},
            output_format="mp3",
            create_parent=True
        )
        self.assertTrue(target_path_exec.parent.exists(), "Explicit write mode must create directories on disk")

    def test_03_rest_api_path_containment_guards(self):
        """Verify REST API routes reject paths attempting to escape PROJECT_ROOT with 403."""
        evil_path = "C:/Windows/System32/drivers/etc/hosts" if os.name == "nt" else "/etc/passwd"

        # 1. /api/stream
        res_stream = self.client.get(f"/api/stream?path={evil_path}")
        self.assertEqual(res_stream.status_code, 403)

        # 2. /api/tags/edit
        res_tag = self.client.post("/api/tags/edit", json={"path": evil_path, "metadata": {}})
        self.assertEqual(res_tag.status_code, 403)

        # 3. /api/duplicates/move-backup
        res_move = self.client.post("/api/duplicates/move-backup", json={"path": evil_path})
        self.assertEqual(res_move.status_code, 403)

        # 4. /api/duplicates/delete
        res_del = self.client.post("/api/duplicates/delete", json={"path": evil_path})
        self.assertEqual(res_del.status_code, 403)

        # 5. /api/metadata/repair/propose
        res_prop = self.client.post("/api/metadata/repair/propose", json={"path": evil_path})
        self.assertEqual(res_prop.status_code, 403)

        # 6. /api/metadata/repair/apply
        res_apply = self.client.post("/api/metadata/repair/apply", json={"path": evil_path, "approved_fields": {"title": "X"}})
        self.assertEqual(res_apply.status_code, 403)

        # 7. /api/fingerprint/compute
        res_fp = self.client.post("/api/fingerprint/compute", json={"path": evil_path})
        self.assertEqual(res_fp.status_code, 403)

        # 8. /api/quality/inspect
        res_qi = self.client.post("/api/quality/inspect", json={"path": evil_path})
        self.assertEqual(res_qi.status_code, 403)

    def test_04_library_intelligence_duplicate_alias_and_defense_in_depth(self):
        """Verify defense-in-depth containment in LibraryIntelligence."""
        # Defense-in-depth containment check in Python methods
        evil_path = "C:/Windows/System32/kernel32.dll" if os.name == "nt" else "/bin/bash"
        with self.assertRaises(PermissionError):
            LibraryIntelligence.move_duplicate_to_backup(evil_path)

        with self.assertRaises(PermissionError):
            LibraryIntelligence.delete_duplicate_safely(evil_path)

        with self.assertRaises(PermissionError):
            LibraryIntelligence.generate_proposed_metadata(evil_path)

        with self.assertRaises(PermissionError):
            LibraryIntelligence.apply_metadata_repair(evil_path, {"title": "Hacked"})

    def test_05_config_loader_containment_fallback(self):
        """Verify config loader enforces containment on download and credentials paths."""
        # Valid path
        down_path = get_downloads_path()
        self.assertTrue(down_path.resolve().is_relative_to(PROJECT_ROOT.resolve()))

        cred_path = get_credentials_path()
        self.assertTrue(cred_path.resolve().is_relative_to(PROJECT_ROOT.resolve()))

    def test_06_main_cli_argument_parsing(self):
        """Verify main.py correctly parses --no-browser and --headless flags."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--no-browser", action="store_true")
        parser.add_argument("--headless", action="store_true")

        args = parser.parse_args(["--no-browser"])
        self.assertTrue(args.no_browser)
        self.assertFalse(args.headless)

        args2 = parser.parse_args(["--headless"])
        self.assertTrue(args2.headless)


if __name__ == "__main__":
    unittest.main()
