from pathlib import Path


def test_pipeline_workflow_installs_lmodern_for_pdf_builds() -> None:
    """
    Regression test: CI PDF generation can fail with:
      LaTeX Error: File `lmodern.sty' not found.

    Ensure the Universal Pipeline workflow installs the missing package.
    """
    workflow_path = Path(".github/workflows/pipeline.yml")
    text = workflow_path.read_text(encoding="utf-8")

    assert "lmodern" in text

