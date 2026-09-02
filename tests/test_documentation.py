from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [ROOT / name for name in output.decode("utf-8").rstrip("\0").split("\0") if name]


# Patterns are assembled from fragments so this file does not itself trip the
# scan below.  Two things the previous singular-only, \b-delimited patterns let
# through, both of which really occurred:
#   * the plural -- \b<name>\b has no word boundary before a trailing "s", so
#     `part-of: <name>s` never matched;
#   * the identifier -- "_" is a word character, so \b<name>s\b never matched
#     `<NAME>S_NAMESPACE`.
# Hence explicit lookarounds that treat "_" as a separator instead of \b.
FORBIDDEN_APPLICATION_NAMES = (
    "ag" + "ent" + "s?",
    "sand" + "box" + "(?:es)?",
    "si" + "te" + "s?",
)


def first_party_name_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in FORBIDDEN_APPLICATION_NAMES:
        expression = rf"(?i)(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])"
        hits.extend(match.group(0) for match in re.finditer(expression, text))
    return hits


class DocumentationContractTests(unittest.TestCase):
    def test_readme_links_core_documents(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for relative in (
            "docs/PLUGIN_ARCHITECTURE.md",
            "docs/CONFIGURATION.md",
            "docs/NODE_POOLS.md",
            "docs/OBSERVABILITY.md",
            "docs/BENCHMARK_REPORT_TEMPLATE.md",
        ):
            self.assertIn(f"]({relative})", readme)
            self.assertTrue((ROOT / relative).is_file())

    def test_readme_names_and_links_the_composition_repository(self) -> None:
        """The pointer to the repository that describes the whole platform must survive.

        This repository describes itself and is neutral about the applications
        composed on top of it, so the pointer is the only thing connecting a
        reader here to the wider picture.  A rename on the other side, or an
        edit that drops the paragraph, would otherwise leave that reader with
        nothing and produce no signal here.

        IMPORTANT: this reads a literal in this repository's own README and
        nothing else.  Whether the link resolves, whether the target repository
        exists and whether it is public are not observable from inside this
        tree; a gate that implied otherwise would be believed and would be
        wrong.  Cross-repository link health needs a check that can reach the
        other side.

        The slug is asserted separately from the URL: a rename changes the
        first, and a reader clicks the second.
        """
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        # A rename changes the slug; a reader clicks the URL. Each is asserted
        # with its own terminator - backticks, and the closing parenthesis of
        # the Markdown link - because a bare substring stays green through the
        # rename that matters most: "hullwork/platform-composition-v2" contains
        # "hullwork/platform-composition". Same shape as an unanchored pattern
        # matching inside a longer word.
        self.assertIn("`hullwork/platform-composition`", readme)
        self.assertIn("](https://github.com/hullwork/platform-composition)", readme)

    def test_new_application_uses_declarative_package_contract(self) -> None:
        texts = [
            (ROOT / name).read_text(encoding="utf-8")
            for name in (
                "README.md",
                "docs/PACKAGE_AUTHORING.md",
                "docs/PLUGIN_ARCHITECTURE.md",
            )
        ]
        for text in texts:
            for concept in ("Package", "Stack", "VersionLock"):
                self.assertIn(concept, text)
        self.assertIn("Do not modify Infra source", texts[1])
        self.assertIn("no Infra code change", texts[0])

    def test_architecture_review_covers_boundaries_and_scorecard(self) -> None:
        review = (ROOT / "docs" / "ARCHITECTURE_REVIEW.md").read_text(encoding="utf-8")
        for heading in (
            "Core and extension boundary",
            "Independent package boundaries",
            "Robustness",
            "Decoupling",
            "i18n",
            "Code simplicity",
            "Newcomer onboarding",
            "Unfinished work",
        ):
            self.assertIn(heading, review)
        self.assertIn("Installs no application", review)
        self.assertIn("unscored", review)

    def test_pending_benchmarks_are_fail_closed_not_scores(self) -> None:
        benchmark = (ROOT / "docs" / "BENCHMARK_REPORT_TEMPLATE.md").read_text(encoding="utf-8")
        layers = benchmark.split("## Evidence layers for this snapshot", 1)[1].split(
            "## Pending benchmark matrix", 1
        )[0]
        for layer in ("Source", "CI", "Runtime", "Benchmark"):
            self.assertIn(f"| {layer} |", layers)
        self.assertEqual(4, layers.count("**NOT RUN**"))
        pending = benchmark.split("## Pending benchmark matrix", 1)[1].split(
            "## Scoring", 1
        )[0]
        rows = [line for line in pending.splitlines() if line.startswith("|")]
        scenario_rows = [line for line in rows if "**NOT RUN**" in line]
        self.assertGreaterEqual(len(scenario_rows), 9)
        self.assertFalse(any(re.search(r"\bPASS\b|\b100%\b", row) for row in scenario_rows))
        self.assertIn("mock", benchmark.lower())
        self.assertIn("fixture-only", benchmark.lower())
        self.assertIn("dry-run", benchmark.lower())

    def test_first_party_name_matcher_has_discriminating_power(self) -> None:
        # A neutrality gate that can never match is worse than no gate: it stays
        # green while the repository drifts.  Pin the matcher against samples it
        # must catch and samples it must not, so a narrowed pattern fails here.
        must_match = (
            "ag" + "ent",
            "ag" + "ents",
            "sand" + "box",
            "sand" + "boxes",
            "si" + "te",
            "si" + "tes",
            "    app.kubernetes.io/part-of: " + "si" + "tes",
            "SI" + "TES_NAMESPACE=x",
        )
        for sample in must_match:
            with self.subTest(sample=sample):
                self.assertTrue(
                    first_party_name_hits(sample),
                    f"neutrality matcher missed {sample!r}",
                )
        must_not_match = ("agentic", "website", "websites", "composite", "opposite")
        for sample in must_not_match:
            with self.subTest(sample=sample):
                self.assertEqual(
                    [], first_party_name_hits(sample),
                    f"neutrality matcher over-matched {sample!r}",
                )

    def test_the_scan_face_covers_every_tracked_area(self) -> None:
        # 🔴 The gate below used to end on `assertEqual(len(tracked_files()),
        # scanned)`, which re-derives both sides from the same list and is true
        # by construction. Measured: narrowing tracked_files() to *.md dropped
        # the face from 79 files to 16 and the suite stayed green -- the exact
        # suffix-allowlist regression the comment there warns about. The sibling
        # gate in test_project_naming.py pins this properly; these two
        # assertions are that pinning, ported.
        #
        # One witness per area a directory- or suffix-scoped filter would drop.
        # The extension-less ones are why there is no suffix allowlist.
        names = {path.relative_to(ROOT).as_posix() for path in tracked_files()}
        for witness in (
            "Makefile",
            "VERSION",
            "LICENSE",
            "bootstrap/git-server.Dockerfile",
            "catalog/packages/demo-web.yaml",
            "contracts/v1alpha1/package.schema.json",
            "scripts/providers/lima-kubeadm-nodepool.sh",
            ".github/workflows/security.yaml",
            "tests/test_documentation.py",
        ):
            with self.subTest(witness=witness):
                self.assertIn(witness, names)

    def test_tracked_repository_has_no_first_party_application_names(self) -> None:
        # Every tracked file is scanned, including extension-less ones such as
        # Makefile and Dockerfile: an allowlist of suffixes silently exempted
        # them before.
        scanned = 0
        for path in tracked_files():
            relative = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            scanned += 1
            for hit in first_party_name_hits(relative) + first_party_name_hits(text):
                self.fail(f"{relative} contains first-party application name {hit!r}")
        # A floor, not an identity: a filter that quietly drops most of the tree
        # also reports zero violations, and comparing the count to the same list
        # it came from cannot notice that.
        self.assertGreater(scanned, 60)


if __name__ == "__main__":
    unittest.main()
