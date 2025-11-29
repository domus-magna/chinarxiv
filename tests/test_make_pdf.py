import sys
from src.make_pdf import run_cli


def test_make_pdf_no_pandoc(monkeypatch):
    # Force has_binary to return False to skip
    monkeypatch.setattr("src.make_pdf.has_binary", lambda name: False)
    # Should not raise
    old_argv = sys.argv
    try:
        sys.argv = [old_argv[0]]
        run_cli()
    finally:
        sys.argv = old_argv
