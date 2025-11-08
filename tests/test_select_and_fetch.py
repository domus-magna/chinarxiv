import json
from pathlib import Path


from src.select_and_fetch import process_records


def test_select_and_fetch_marks_seen(tmp_path, monkeypatch):
    # Prepare fake records
    records = [
        {
            "id": "R1",
            "oai_identifier": "oai:chinaxiv.org:R1",
            "pdf_url": "https://example.org/fake.pdf",
            "source_url": "https://example.org/abs/R1",
        },
        {
            "id": "R2",
            "oai_identifier": "oai:chinaxiv.org:R2",
            "pdf_url": None,
            "source_url": None,
        },
    ]
    # switch cwd to tmp
    monkeypatch.chdir(tmp_path)
    data_dir = Path("data")
    data_dir.mkdir()
    rec_path = data_dir / "recs.json"
    rec_path.write_text(json.dumps(records))

    # Monkeypatch http_get to avoid network; return simple HTML
    from src import select_and_fetch as saf

    def fake_http_get(url):
        class Resp:
            def __init__(self):
                self.content = b"%PDF-1.4"
                self.text = "<html><body><a href=\"/src.tar.gz\">TeX source</a></body></html>"
                self.headers = {"Content-Type": "application/pdf"}

        return Resp()

    monkeypatch.setattr(saf, "http_get", fake_http_get)

    out = process_records(str(rec_path))

    seen_path = Path("data/seen.json")
    assert seen_path.exists()
    seen = json.loads(seen_path.read_text())
    assert "R1" in seen.get("ids", []) and "R2" in seen.get("ids", [])
    # Check files paths captured
    assert out[0]["files"]["pdf_path"].endswith("R1.pdf")


def test_download_skips_non_pdf(tmp_path, monkeypatch):
    # Prepare one record with a fake pdf URL
    records = [
        {"id": "R3", "oai_identifier": "oai:x:R3", "pdf_url": "https://example.org/notpdf.pdf", "source_url": None}
    ]
    monkeypatch.chdir(tmp_path)
    from src import select_and_fetch as saf

    def fake_http_get(url):
        class Resp:
            def __init__(self):
                self.content = b"<html>error</html>"
                self.text = self.content.decode()
                self.headers = {"Content-Type": "text/html"}

        return Resp()

    monkeypatch.setattr(saf, "http_get", fake_http_get)
    import json

    tmp_path.joinpath("data").mkdir()
    p = tmp_path / "data" / "r.json"
    p.write_text(json.dumps(records))
    out = saf.process_records(str(p))
    assert out[0]["files"]["pdf_path"] is None
