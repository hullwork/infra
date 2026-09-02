from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"

# A "|" that is neither half of a "||" list operator.
PIPELINE = re.compile(r"(?<!\|)\|(?!\|)")


def run_steps() -> list[tuple[str, str, dict]]:
    steps = []
    for workflow in sorted(WORKFLOW_DIR.glob("*.y*ml")):
        document = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        for job_name, job in document["jobs"].items():
            for index, step in enumerate(job.get("steps", [])):
                if "run" not in step:
                    continue
                label = f"{workflow.name}:{job_name}[{index}] {step.get('name', step['run'].splitlines()[0])}"
                steps.append((label, step["run"], step))
    return steps


def uses_a_pipeline(script: str) -> bool:
    return bool(PIPELINE.search(script))


class WorkflowShellSafetyTests(unittest.TestCase):
    def test_there_are_steps_to_check(self) -> None:
        # Guards against a glob or parser change turning this file into a no-op.
        steps = run_steps()
        self.assertGreaterEqual(len(steps), 8)
        self.assertTrue(any(uses_a_pipeline(script) for _, script, _ in steps))

    def test_piped_steps_declare_bash_and_pipefail(self) -> None:
        # GitHub's implicit shell for `run:` is `bash -e`, which has no pipefail:
        # `producer | tee file` then reports tee's 0 even when the producer died,
        # so a gate that greps the file passes on an empty file.  Declaring
        # `shell: bash` switches the runner to `bash --noprofile --norc -eo
        # pipefail`; `set -euo pipefail` in the body makes that visible in review.
        for label, script, step in run_steps():
            if not uses_a_pipeline(script):
                continue
            with self.subTest(step=label):
                self.assertEqual(
                    "bash", step.get("shell"),
                    f"{label} pipes without declaring `shell: bash`, so the "
                    f"pipeline exit status is the last command's only",
                )
                body = [line.strip() for line in script.splitlines() if line.strip()]
                self.assertEqual(
                    "set -euo pipefail", body[0],
                    f"{label} pipes but does not start with `set -euo pipefail`",
                )


if __name__ == "__main__":
    unittest.main()
