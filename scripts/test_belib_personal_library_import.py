from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("belib_personal_library_import.py")
SPEC = importlib.util.spec_from_file_location("belib_personal_library_import", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load bulk importer")
IMPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMPORTER)


def inventory_row(**overrides):
    row = {
        "logical_book_id": "lb_0123456789abcdef",
        "title": "テスト書籍",
        "author": "テスト著者",
        "category": "技術・プログラミング",
        "subcategory": "フロントエンド",
        "identity_state": "existing_logical_book",
        "assignment_state": "proposed",
        "source_kind": "existing_534",
        "source_locator": r"F:\電子書籍\技術・プログラミング\フロントエンド\テスト著者_〈テスト書籍〉",
        "source_observed_at": "2026-08-12",
    }
    row.update(overrides)
    return row


class PersonalLibraryImportTests(unittest.TestCase):
    def test_cli_is_dry_run_by_default(self) -> None:
        parser = IMPORTER.build_parser()
        args = parser.parse_args(
            [
                "--inventory",
                "/tmp/inventory.jsonl",
                "--library-root",
                "/srv/pv3-hdd/電子書籍",
            ]
        )
        self.assertFalse(args.execute)
        self.assertEqual(args.publication_scope, "admin_only")

    def test_map_source_directory_maps_only_expected_windows_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "電子書籍"
            target = root / "技術・プログラミング" / "フロントエンド" / "本"
            target.mkdir(parents=True)

            mapped = IMPORTER.map_source_directory(
                r"F:\電子書籍\技術・プログラミング\フロントエンド\本",
                root,
            )
            self.assertEqual(mapped, target.resolve())

            with self.assertRaises(IMPORTER.PlanError):
                IMPORTER.map_source_directory(r"D:\電子書籍\本", root)
            with self.assertRaises(IMPORTER.PlanError):
                IMPORTER.map_source_directory(r"F:\電子書籍\..\秘密", root)

    @unittest.skipIf(os.name == "nt", "symlink escape test targets Linux runner semantics")
    def test_map_source_directory_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "library"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)

            with self.assertRaises(IMPORTER.PlanError):
                IMPORTER.map_source_directory(r"F:\電子書籍\escape", root)

    def test_select_book_file_prefers_single_epub_over_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book_dir = Path(directory)
            epub = book_dir / "book.epub"
            pdf = book_dir / "book.pdf"
            epub.write_bytes(b"epub")
            pdf.write_bytes(b"%PDF-test")

            selected, reason = IMPORTER.select_book_file(book_dir)
            self.assertEqual(selected, epub)
            self.assertIsNone(reason)

    def test_select_book_file_falls_back_to_single_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book_dir = Path(directory)
            pdf = book_dir / "book.pdf"
            pdf.write_bytes(b"%PDF-test")

            selected, reason = IMPORTER.select_book_file(book_dir)
            self.assertEqual(selected, pdf)
            self.assertIsNone(reason)

    def test_select_book_file_refuses_ambiguous_epub(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book_dir = Path(directory)
            (book_dir / "a.epub").write_bytes(b"a")
            (book_dir / "b.epub").write_bytes(b"b")
            (book_dir / "book.pdf").write_bytes(b"%PDF-test")

            selected, reason = IMPORTER.select_book_file(book_dir)
            self.assertIsNone(selected)
            self.assertEqual(reason, "ambiguous_epub")

    def test_select_book_file_refuses_oversized_file_before_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            book_dir = Path(directory)
            pdf = book_dir / "book.pdf"
            with pdf.open("wb") as handle:
                handle.truncate(IMPORTER.MAX_BOOK_FILE_SIZE + 1)

            selected, reason = IMPORTER.select_book_file(book_dir)
            self.assertIsNone(selected)
            self.assertEqual(reason, "file_too_large")

    def test_author_placeholders_are_not_imported_as_real_authors(self) -> None:
        self.assertEqual(IMPORTER.normalize_author(None), "")
        self.assertEqual(IMPORTER.normalize_author(""), "")
        self.assertEqual(IMPORTER.normalize_author("1"), "")
        self.assertEqual(IMPORTER.normalize_author("著者要確認"), "")
        self.assertEqual(IMPORTER.normalize_author(" 山田 太郎 "), "山田 太郎")

    def test_manga_uses_rtl_and_other_categories_use_ltr(self) -> None:
        self.assertEqual(IMPORTER.page_turn_direction("漫画"), "rtl")
        self.assertEqual(IMPORTER.page_turn_direction("技術・プログラミング"), "ltr")
        self.assertEqual(IMPORTER.page_turn_direction(None), "ltr")

    def test_inventory_rejects_duplicate_logical_book_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.jsonl"
            row = inventory_row()
            path.write_text(
                json.dumps(row, ensure_ascii=False) + "\n" + json.dumps(row, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(IMPORTER.InventoryError):
                IMPORTER.load_inventory(path)

    def test_plan_entry_skips_missing_or_ambiguous_files_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "電子書籍"
            book_dir = root / "技術・プログラミング" / "フロントエンド" / "テスト著者_〈テスト書籍〉"
            book_dir.mkdir(parents=True)
            (book_dir / "a.pdf").write_bytes(b"%PDF-a")
            (book_dir / "b.pdf").write_bytes(b"%PDF-b")

            plan = IMPORTER.plan_entry(inventory_row(), root)
            self.assertEqual(plan.status, "skip")
            self.assertEqual(plan.reason, "ambiguous_pdf")
            self.assertIsNone(plan.file_path)

    def test_completed_ledger_entry_does_not_touch_the_hdd_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "電子書籍"
            root.mkdir()
            row = inventory_row()
            ledger = {
                "schema_version": 1,
                "entries": {
                    row["logical_book_id"]: {"status": "registered"},
                },
            }

            with mock.patch.object(
                IMPORTER,
                "select_book_file",
                side_effect=AssertionError("completed item touched the HDD"),
            ):
                plans = IMPORTER.plan_inventory([row], root, ledger, limit=None)

            self.assertEqual(len(plans), 1)
            self.assertEqual(plans[0].status, "skip")
            self.assertEqual(plans[0].reason, "ledger_completed")

    def test_plain_http_is_rejected_for_non_loopback_api_hosts(self) -> None:
        with self.assertRaises(IMPORTER.ExecuteError):
            IMPORTER.BelibClient("http://belib.example.com")

        loopback = IMPORTER.BelibClient("http://127.0.0.1:3000")
        self.assertEqual(loopback.scheme, "http")

        secure = IMPORTER.BelibClient("https://belib.example.com")
        self.assertEqual(secure.scheme, "https")

    def test_success_and_duplicate_ledger_entries_are_resumable(self) -> None:
        ledger = {
            "lb_registered": {"status": "registered"},
            "lb_duplicate": {"status": "duplicate"},
            "lb_failed": {"status": "failed"},
        }
        self.assertTrue(IMPORTER.is_completed_in_ledger(ledger, "lb_registered"))
        self.assertTrue(IMPORTER.is_completed_in_ledger(ledger, "lb_duplicate"))
        self.assertFalse(IMPORTER.is_completed_in_ledger(ledger, "lb_failed"))
        self.assertFalse(IMPORTER.is_completed_in_ledger(ledger, "lb_new"))

    def test_default_dry_run_never_constructs_network_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "電子書籍"
            book_dir = root / "技術・プログラミング" / "フロントエンド" / "テスト著者_〈テスト書籍〉"
            book_dir.mkdir(parents=True)
            (book_dir / "book.epub").write_bytes(b"not-validated-until-server")

            inventory = base / "inventory.jsonl"
            inventory.write_text(json.dumps(inventory_row(), ensure_ascii=False) + "\n", encoding="utf-8")

            with mock.patch.object(IMPORTER, "BelibClient", side_effect=AssertionError("network client constructed")):
                output = io.StringIO()
                with redirect_stdout(output):
                    status = IMPORTER.main(
                        [
                            "--inventory",
                            str(inventory),
                            "--library-root",
                            str(root),
                        ]
                    )

            self.assertEqual(status, 0)
            summary = json.loads(output.getvalue())
            self.assertFalse(summary["execute"])
            self.assertEqual(summary["ready"], 1)
            self.assertEqual(summary["network_requests"], 0)


if __name__ == "__main__":
    unittest.main()
