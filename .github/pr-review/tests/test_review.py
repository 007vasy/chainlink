from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import review  # noqa: E402


SAMPLE_DIFF = """\
diff --git a/foo.go b/foo.go
index 1111111..2222222 100644
--- a/foo.go
+++ b/foo.go
@@ -1,5 +1,6 @@
 package foo
 
-func old() {}
+func new() {}
+func added() {}
 
 func keep() {}
"""


def finding(**overrides):
    value = {
        "path": "foo.go",
        "line": 3,
        "severity": "high",
        "title": "test finding",
        "body": "a concrete failure scenario",
        "standard": "Does the code do what the PR claims?",
        "fix": "change the code",
    }
    value.update(overrides)
    return value


class ParseDiffTests(unittest.TestCase):
    def test_added_context_and_deleted_lines(self):
        diff = review.parse_diff(SAMPLE_DIFF)
        self.assertIn("foo.go", diff)
        foo = diff["foo.go"]
        self.assertEqual(foo.added, {3, 4})
        self.assertEqual(foo.context, {1, 2, 5, 6})
        self.assertEqual(foo.deleted, {3})
        self.assertEqual(foo.right, {1, 2, 3, 4, 5, 6})
        self.assertEqual(foo.left, {1, 2, 3, 4, 5})

    def test_anchor_added_context_and_nearest(self):
        diff = review.parse_diff(SAMPLE_DIFF)
        self.assertEqual(review.anchor(diff, "foo.go", 3), (3, "RIGHT"))
        self.assertEqual(review.anchor(diff, "foo.go", 5), (5, "RIGHT"))
        self.assertEqual(review.anchor(diff, "foo.go", 100), (6, "RIGHT"))
        self.assertIsNone(review.anchor(diff, "bar.go", 1))

    def test_anchor_left(self):
        diff = review.parse_diff(SAMPLE_DIFF)
        self.assertEqual(review.anchor(diff, "foo.go", 3, "LEFT"), (3, "LEFT"))


class ExtractJsonTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(review.extract_json('{"findings": []}'), {"findings": []})

    def test_fenced_json_with_preamble(self):
        text = 'Here you go:\n```json\n{"findings": []}\n```\nDone.'
        self.assertEqual(review.extract_json(text), {"findings": []})

    def test_preamble_and_suffix(self):
        text = 'review:\n{"findings": [], "general": []}\nthanks'
        self.assertEqual(
            review.extract_json(text), {"findings": [], "general": []}
        )

    def test_invalid(self):
        self.assertIsNone(review.extract_json("not json"))


class NormalizeTests(unittest.TestCase):
    def test_valid_finding(self):
        output = review.normalize_output({"findings": [finding()], "general": []})
        self.assertEqual(len(output["findings"]), 1)
        self.assertEqual(output["findings"][0]["path"], "foo.go")

    def test_invalid_finding_is_dropped(self):
        output = review.normalize_output(
            {
                "findings": [
                    finding(severity="critical"),
                    finding(line="not-a-line"),
                    finding(path=""),
                ]
            }
        )
        self.assertEqual(output["findings"], [])

    def test_general_requires_location(self):
        output = review.normalize_output(
            {
                "general": [
                    {
                        "location": "foo.go:12",
                        "severity": "medium",
                        "body": "body",
                        "standard": "standard",
                        "fix": "fix",
                    },
                    {
                        "location": "foo.go",
                        "severity": "medium",
                        "body": "body",
                        "standard": "standard",
                        "fix": "fix",
                    },
                ]
            }
        )
        self.assertEqual(len(output["general"]), 1)
        self.assertEqual(output["general"][0]["location"], "foo.go:12")


class PayloadTests(unittest.TestCase):
    def test_inline_comment_is_anchored(self):
        payload = review.build_payload(
            {"findings": [finding()], "general": []},
            review.parse_diff(SAMPLE_DIFF),
            repo="owner/repo",
            pr=1,
            head_sha="a" * 40,
            base_sha="b" * 40,
            min_severity="medium",
            max_findings=8,
        )
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload["comments"]), 1)
        comment = payload["comments"][0]
        self.assertEqual(comment["path"], "foo.go")
        self.assertEqual(comment["line"], 3)
        self.assertEqual(comment["side"], "RIGHT")
        self.assertIn(review.MARKER, comment["body"])

    def test_outside_diff_becomes_general(self):
        payload = review.build_payload(
            {"findings": [finding(path="bar.go")], "general": []},
            review.parse_diff(SAMPLE_DIFF),
            repo="owner/repo",
            pr=1,
            head_sha="a" * 40,
            base_sha="b" * 40,
            min_severity="medium",
            max_findings=8,
        )
        self.assertIsNotNone(payload)
        self.assertEqual(payload["comments"], [])
        self.assertIn("Findings outside the diff", payload["body"])
        self.assertIn("bar.go:3", payload["body"])

    def test_min_severity_filters(self):
        payload = review.build_payload(
            {"findings": [finding(severity="low")], "general": []},
            review.parse_diff(SAMPLE_DIFF),
            repo="owner/repo",
            pr=1,
            head_sha="a" * 40,
            base_sha="b" * 40,
            min_severity="medium",
            max_findings=8,
        )
        self.assertIsNone(payload)

    def test_clean_is_silent_by_default(self):
        payload = review.build_payload(
            {"findings": [], "general": []},
            review.parse_diff(SAMPLE_DIFF),
            repo="owner/repo",
            pr=1,
            head_sha="a" * 40,
            base_sha="b" * 40,
            min_severity="medium",
            max_findings=8,
        )
        self.assertIsNone(payload)

    def test_existing_comment_is_deduplicated_by_location(self):
        item = finding()
        payload = review.build_payload(
            {"findings": [item], "general": []},
            review.parse_diff(SAMPLE_DIFF),
            repo="owner/repo",
            pr=1,
            head_sha="a" * 40,
            base_sha="b" * 40,
            min_severity="medium",
            max_findings=8,
            existing=[
                {
                    "path": "foo.go",
                    "line": 3,
                    "body": "a rephrased finding on the same line " + review.MARKER,
                }
            ],
        )
        self.assertIsNone(payload)

    def test_suggestion_block_is_well_formed(self):
        item = finding(suggestion="func new() {}")
        body = review.format_comment(item)
        self.assertIn("```suggestion\nfunc new() {}\n```", body)
        self.assertNotIn("\\\\n", body)


if __name__ == "__main__":
    unittest.main()
