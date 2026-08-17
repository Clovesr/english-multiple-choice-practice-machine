from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.services.dictionary import (
    DictionaryUnavailableError,
    dictionary_summary,
    lookup_dictionary,
    tagged_entries,
)
from tools.build_ecdict_bundle import build_bundle


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


class DictionaryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(ignore_cleanup_errors=True)
        source = Path(self.temp.name) / "ecdict.csv"
        with source.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(
                (
                    {
                        "word": "resilience",
                        "phonetic": "rɪˈzɪliəns",
                        "definition": "the ability to recover",
                        "translation": "n. 恢复力；韧性\\n恢复能力",
                        "pos": "n.",
                        "tag": "cet4 cet6 ky",
                        "bnc": "4321",
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
                        "exchange": "p:practiced/i:practicing/3:practices",
                    },
                    {
                        "word": "serendipity",
                        "phonetic": "ˌserənˈdɪpəti",
                        "definition": "a fortunate accidental discovery",
                        "translation": "n. 意外发现珍宝的好运",
                        "pos": "n.",
                        "tag": "ielts gre",
                        "bnc": "14063",
                        "frq": "10578",
                    },
                )
            )
        self.bundle = Path(self.temp.name) / "dictionary.dbpkg"
        build_bundle(source, self.bundle)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_direct_and_inflected_lookup(self) -> None:
        direct = lookup_dictionary("  ＲＥＳＩＬＩＥＮＣＥ ", path=self.bundle)
        self.assertTrue(direct["found"])
        self.assertEqual(direct["entry"]["lemma"], "resilience")
        self.assertEqual(direct["entry"]["pos_senses"][0]["pos"], "n.")
        self.assertEqual(direct["entry"]["pos_senses"][0]["gloss_zh"], "恢复力；韧性")
        self.assertIn("cet6", direct["entry"]["tags"])

        inflected = lookup_dictionary("practicing", path=self.bundle)
        self.assertTrue(inflected["found"])
        self.assertEqual(inflected["entry"]["lemma"], "practice")
        self.assertEqual(inflected["entry"]["matched_form"], "practicing")
        self.assertFalse(lookup_dictionary("not-in-dictionary", path=self.bundle)["found"])

        ranked = lookup_dictionary("serendipity", path=self.bundle)
        self.assertTrue(ranked["found"])
        self.assertEqual(ranked["entry"]["tags"], [])

    def test_tag_listing_and_summary(self) -> None:
        self.assertEqual(len(tagged_entries("cet6", path=self.bundle)), 1)
        self.assertEqual(len(tagged_entries("ky", path=self.bundle)), 2)
        summary = dictionary_summary(path=self.bundle)
        self.assertEqual(summary["counts"], {"cet4": 2, "cet6": 1, "ky": 2})
        self.assertEqual(summary["metadata"]["source_commit"], "bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b")
        self.assertEqual(
            summary["metadata"]["selection"],
            "target-tags-or-frequency-ranked",
        )

    def test_missing_bundle_has_readable_error(self) -> None:
        with self.assertRaisesRegex(DictionaryUnavailableError, "离线词典资源不存在"):
            lookup_dictionary("resilience", path=Path(self.temp.name) / "missing.dbpkg")


if __name__ == "__main__":
    unittest.main()
