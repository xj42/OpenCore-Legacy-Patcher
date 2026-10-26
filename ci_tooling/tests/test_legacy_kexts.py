"""Regression coverage for legacy kext relocation, without GUI dependencies."""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

from datetime import datetime
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[2] / "opencore_legacy_patcher/sys_patch/kernelcache/kernel_collection/legacy_kexts.py"
SPEC = importlib.util.spec_from_file_location("legacy_kexts", SOURCE)
legacy_kexts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy_kexts)


class LegacyKextRelocationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.volume = Path(self.temporary_directory.name)
        self.extensions = self.volume / "Library/Extensions"
        self.relocated = self.volume / "Library/Relocated Extensions"
        self.extensions.mkdir(parents=True)
        self.commands = []

    def extension(self, name="AppleMobileDevice.kext", date=datetime(2019, 8, 22)):
        bundle = self.extensions / name
        (bundle / "Contents").mkdir(parents=True)
        (bundle / "Contents/Info.plist").write_bytes(b"original bundle contents")
        timestamp = date.timestamp()
        os.utime(bundle, (timestamp, timestamp))
        return bundle

    def run_command(self, arguments):
        # Execute only filesystem operations inside the temporary target volume.
        self.commands.append(arguments)
        for argument in arguments[1:]:
            if argument.startswith("/"):
                self.assertTrue(Path(argument).is_relative_to(self.volume))
        if arguments[:2] == ["/bin/mkdir", "-p"]:
            Path(arguments[2]).mkdir(parents=True, exist_ok=True)
        elif arguments[0] == "/bin/mv":
            shutil.move(arguments[1], arguments[2])
        else:
            self.fail(f"Unexpected command: {arguments}")

    def relocate(self, runner=None):
        legacy_kexts.relocate_legacy_kexts(str(self.volume), runner or self.run_command)

    def test_empty_data_mount_uses_live_volume_absolute_path(self):
        scanned = []

        def glob(path, pattern):
            scanned.append((path, pattern))
            return iter(())

        with patch.object(Path, "glob", glob):
            legacy_kexts.relocate_legacy_kexts("", self.run_command)
        self.assertEqual(scanned, [(Path("/Library/Extensions"), "*.kext")])
        self.assertEqual(self.commands, [])

    def test_old_mobiledevice_is_removed_from_search_path_and_preserved(self):
        old = self.extension()
        self.relocate()
        self.assertFalse(old.exists())
        self.assertEqual((self.relocated / "AppleMobileDevice.kext/Contents/Info.plist").read_bytes(), b"original bundle contents")

    def test_existing_backup_is_preserved(self):
        self.relocated.mkdir(parents=True)
        backup = self.relocated / "AppleMobileDevice.kext"
        backup.mkdir()
        (backup / "previous-backup").write_text("keep me")
        self.extension()
        self.relocate()
        self.assertEqual((backup / "previous-backup").read_text(), "keep me")
        self.assertTrue((self.relocated / "AppleMobileDevice-1.kext/Contents/Info.plist").exists())

    def test_multiple_backup_collisions_are_preserved(self):
        self.relocated.mkdir(parents=True)
        (self.relocated / "AppleMobileDevice.kext").mkdir()
        (self.relocated / "AppleMobileDevice-1.kext").symlink_to(self.volume / "missing")
        self.extension()
        self.relocate()
        self.assertTrue((self.relocated / "AppleMobileDevice-1.kext").is_symlink())
        self.assertTrue((self.relocated / "AppleMobileDevice-2.kext/Contents/Info.plist").exists())

    def test_current_and_cutoff_date_extensions_are_untouched(self):
        cutoff = self.extension("Cutoff.kext", datetime(2021, 10, 1))
        current = self.extension("Current.kext", datetime(2026, 10, 26))
        self.relocate()
        self.assertTrue(cutoff.exists())
        self.assertTrue(current.exists())
        self.assertEqual(self.commands, [])

    def test_symlinks_are_not_followed_or_moved(self):
        outside = self.volume / "outside.kext"
        outside.mkdir()
        timestamp = datetime(2019, 8, 22).timestamp()
        os.utime(outside, (timestamp, timestamp))
        link = self.extensions / "Linked.kext"
        link.symlink_to(outside)
        broken = self.extensions / "Broken.kext"
        broken.symlink_to(self.volume / "missing")
        self.relocate()
        self.assertTrue(link.is_symlink())
        self.assertTrue(broken.is_symlink())
        self.assertTrue(outside.exists())
        self.assertEqual(self.commands, [])

    def test_failed_relocation_is_reported_to_caller(self):
        old = self.extension()
        self.relocated.mkdir(parents=True)

        def fail(arguments):
            raise subprocess.CalledProcessError(1, arguments)

        with self.assertRaises(subprocess.CalledProcessError):
            self.relocate(fail)
        self.assertTrue(old.exists())

    def test_unreadable_extension_is_logged_and_other_extensions_relocate(self):
        unreadable = self.extension("Unreadable.kext")
        old = self.extension()
        original_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path == unreadable:
                raise PermissionError("test permission error")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", stat), self.assertLogs(level="WARNING") as messages:
            self.relocate()
        self.assertIn("Unable to inspect", messages.output[0])
        self.assertTrue(unreadable.exists())
        self.assertFalse(old.exists())

    def test_repeated_cleanup_is_a_noop(self):
        self.extension()
        self.relocate()
        self.commands.clear()
        self.relocate()
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
