from __future__ import annotations

import csv
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tools.build_ecdict_bundle import build_bundle, normalize_term


FIELDS = (
    "word",
    "phonetic",
    "definition",
    "translation",
    "pos",
    "collins",
    "oxford",
    "tag",
    "bnc",
    "frq",
    "exchange",
    "detail",
    "audio",
)


class DictionaryBundleBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.source = Path(self.temp.name) / "ecdict.csv"
        with self.source.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(
                (
                    {
                        "word": "Resilience",
                        "phonetic": "rɪˈzɪliəns",
                        "definition": "the ability to recover",
                        "translation": "n. 恢复力；韧性\\n恢复能力",
                        "pos": "n.",
                        "collins": "3",
                        "oxford": "1",
                        "tag": "cet4 cet6 ky",
                        "bnc": "4321",
                        "frq": "5000",
                        "exchange": "s:resiliences",
                    },
                    {
                        "word": "practice",
                        "phonetic": "ˈpræktɪs",
                        "definition": "repeat to improve",
                        "translation": "n. 练习\\nv. 练习",
                        "pos": "n:v",
                        "tag": "cet4 ky",
                        "bnc": "900",
                        "exchange": "p:practiced/d:practiced/i:practicing/3:practices",
                    },
                    {
                        "word": "obscure-specialist-term",
                        "translation": "专业词",
                        "tag": "toefl gre",
                    },
                )
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_builds_filtered_searchable_bundle_with_forms(self) -> None:
        output = Path(self.temp.name) / "dictionary.dbpkg"
        result = build_bundle(self.source, output)
        self.assertEqual(result["entries"], 2)
        self.assertEqual(result["cet4"], 2)
        self.assertEqual(result["cet6"], 1)
        self.assertEqual(result["ky"], 2)
        self.assertGreater(result["forms"], 0)

        connection = sqlite3.connect(output)
        try:
            entry = connection.execute(
                """
                SELECT lemma, translation, frequency_rank
                FROM dictionary_entries WHERE dictionary_key = 'resilience'
                """
            ).fetchone()
            self.assertEqual(entry, ("Resilience", "n. 恢复力；韧性\n恢复能力", 4321))
            lemma = connection.execute(
                """
                SELECT e.lemma
                FROM dictionary_forms AS f
                JOIN dictionary_entries AS e ON e.id = f.entry_id
                WHERE f.normalized_form = 'practicing'
                """
            ).fetchone()[0]
            self.assertEqual(lemma, "practice")
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_rejects_wrong_source_checksum_and_unsafe_output_suffix(self) -> None:
        with self.assertRaisesRegex(ValueError, ".dbpkg"):
            build_bundle(self.source, Path(self.temp.name) / "dictionary.sqlite3")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            build_bundle(
                self.source,
                Path(self.temp.name) / "dictionary.dbpkg",
                expected_sha256="0" * 64,
            )

    def test_normalize_term_uses_nfkc_casefold_and_space_collapse(self) -> None:
        self.assertEqual(normalize_term("  Ｃｏｌｏｕｒ   Test  "), "colour test")


if __name__ == "__main__":
    unittest.main()
