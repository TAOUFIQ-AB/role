import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from database import DatabaseManager


class DatabaseTests(unittest.TestCase):
    def test_processed_pending_and_run_history_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "history.db"
            db = DatabaseManager(str(db_path))
            db.initialize()
            try:
                self.assertFalse(db.is_processed("abc"))
                db.mark_processed("abc", "https://example/abc", "skipped", 123, 45, "test")
                self.assertTrue(db.is_processed("abc"))

                video = Path(td) / "video.mp4"
                video.write_bytes(b"fake")
                db.add_pending_upload("abc", "https://example/abc", video, 123, 45)
                pending = db.get_pending_uploads(3)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["reel_id"], "abc")
                db.increment_upload_attempt("abc")
                self.assertEqual(db.get_pending_uploads(3)[0]["attempts"], 1)
                db.remove_pending_upload("abc")
                self.assertEqual(db.get_pending_uploads(3), [])

                run_id = db.start_run()
                db.end_run(run_id, scanned=4, sent=1)
                row = db.conn.execute("SELECT status, reels_scanned, reels_sent FROM run_history WHERE id=?", (run_id,)).fetchone()
                self.assertEqual(tuple(row), ("completed", 4, 1))
            finally:
                db.close()

            self.assertTrue(db_path.exists())
            self.assertFalse((Path(str(db_path) + "-wal")).exists())


if __name__ == "__main__":
    unittest.main()
