from __future__ import annotations

import sys
import unittest
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from lib.test_selector import detect_file_type, compute_changeset


class TestDetectFileType(unittest.TestCase):
    """detect_file_type()のテスト（v4.2.4例外パターン）"""
    
    def test_package_lock_is_lock_not_config(self):
        """package-lock.jsonはlockであってconfigではない"""
        result = detect_file_type("package-lock.json")
        self.assertEqual(result, "lock")
    
    def test_yarn_lock_is_lock(self):
        """yarn.lockはlock"""
        result = detect_file_type("yarn.lock")
        self.assertEqual(result, "lock")
    
    def test_pipfile_lock_is_lock(self):
        """Pipfile.lockはlock"""
        result = detect_file_type("Pipfile.lock")
        self.assertEqual(result, "lock")
    
    def test_poetry_lock_is_lock(self):
        """poetry.lockはlock"""
        result = detect_file_type("poetry.lock")
        self.assertEqual(result, "lock")
    
    def test_normal_json_is_config(self):
        """通常の.jsonはconfig"""
        result = detect_file_type("config.json")
        self.assertEqual(result, "config")
    
    def test_tsconfig_is_still_config(self):
        """tsconfig.jsonはconfig（例外ではない）"""
        result = detect_file_type("tsconfig.json")
        self.assertEqual(result, "config")
    
    def test_python_file_is_code(self):
        """Pythonファイルはcode"""
        result = detect_file_type("lib/module.py")
        self.assertEqual(result, "code")
    
    def test_markdown_is_doc(self):
        """Markdownはdoc"""
        result = detect_file_type("README.md")
        self.assertEqual(result, "doc")


class TestComputeChangeset(unittest.TestCase):
    """compute_changeset()のテスト"""
    
    def test_added_files(self):
        """A ステータスはadded_filesに入る"""
        diff_output = "A\tnew_file.py"
        changeset = compute_changeset(diff_output)
        self.assertIn("new_file.py", changeset.added_files)
    
    def test_deleted_files(self):
        """D ステータスはdeleted_filesに入る"""
        diff_output = "D\told_file.py"
        changeset = compute_changeset(diff_output)
        self.assertIn("old_file.py", changeset.deleted_files)
    
    def test_renamed_files(self):
        """R100 ステータスはrenamed_filesに入る"""
        diff_output = "R100\told_name.py\tnew_name.py"
        changeset = compute_changeset(diff_output)
        self.assertIn(("old_name.py", "new_name.py"), changeset.renamed_files)
        # new_pathはchanged_filesにも入る
        self.assertIn("new_name.py", changeset.changed_files)
    
    def test_copied_files(self):
        """C100 ステータスはadded_filesに入る（copyはaddと同様）"""
        diff_output = "C100\toriginal.py\tcopy.py"
        changeset = compute_changeset(diff_output)
        self.assertIn("copy.py", changeset.added_files)
    
    def test_modified_files(self):
        """M ステータスはchanged_filesに入る"""
        diff_output = "M\tmodified.py"
        changeset = compute_changeset(diff_output)
        self.assertIn("modified.py", changeset.changed_files)


if __name__ == "__main__":
    unittest.main()
