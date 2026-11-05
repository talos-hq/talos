"""write_unsigned_list: the signing action resolves each catalog entry relative to the
catalog file's directory, so entries must be relative to it (run 41497874698 doubled
D:\\a\\_temp\\hs-work\\ onto absolute entries)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_bundle as bb  # noqa: E402


class UnsignedListTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name).resolve() / "hs-work"
        self.bundle = self.work / "talos"
        self.pyd = self.bundle / "python" / "Lib" / "site-packages" / "_cffi_backend.cp314-win_amd64.pyd"
        self.pyd.parent.mkdir(parents=True)
        self.pyd.write_bytes(b"MZ".ljust(64, b"\0"))  # decoy stub: no PE header, so scan() lists it unsigned
        (self.bundle / "README.txt").write_text("not a binary")

    def entries(self):
        return (self.work / "unsigned.txt").read_text().splitlines()

    def test_absolute_bundle_yields_entries_relative_to_catalog_dir(self):
        bb.write_unsigned_list(self.bundle, self.work)
        entries = self.entries()
        self.assertEqual(entries, [os.path.join("talos", "python", "Lib", "site-packages",
                                                "_cffi_backend.cp314-win_amd64.pyd")])
        for e in entries:
            self.assertFalse(os.path.isabs(e), e)
            self.assertNotIn(str(self.work), e)  # the doubled-prefix bug
            self.assertTrue((self.work / e).is_file(), e)  # how the signing action joins

    def test_bundle_outside_catalog_dir_still_resolves(self):
        other = self.work.parent / "elsewhere"
        self.work.rename(other)
        self.work.mkdir()
        bb.write_unsigned_list(other / "talos", self.work)
        for e in self.entries():
            self.assertFalse(os.path.isabs(e), e)
            self.assertEqual((self.work / e).resolve(), (other / "talos" / self.pyd.relative_to(self.bundle)).resolve())


if __name__ == "__main__":
    unittest.main()
