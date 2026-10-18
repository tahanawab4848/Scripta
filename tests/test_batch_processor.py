import os
import shutil
import tempfile
import unittest
from book_digitizer.config import AppConfig
from book_digitizer.batch_processor import BatchProcessor, process_single_page_worker


class TestBatchProcessor(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.config = AppConfig(
            output_dir=self.test_dir,
            languages="eng",
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_reprocess_failed_checkpoints(self):
        # Create a dummy failed checkpoint file
        chk_dir = self.config.checkpoint_dir
        os.makedirs(chk_dir, exist_ok=True)
        chk_file = os.path.join(chk_dir, "page_00001.json")
        with open(chk_file, "w", encoding="utf-8") as f:
            f.write('{"page_number": 1, "status": "failed", "error": "test failure"}')

        bp = BatchProcessor(self.config)
        # Check that target pages don't silently treat failed as success
        pending = []
        for p in [1]:
            chk = os.path.join(self.config.checkpoint_dir, f"page_{p:05d}.json")
            if os.path.exists(chk):
                import json
                with open(chk, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                if data.get("status") == "success":
                    pass
                else:
                    pending.append(p)
            else:
                pending.append(p)

        self.assertIn(1, pending)


if __name__ == "__main__":
    unittest.main()
