import io
import tarfile
from pathlib import Path

from src.body_extract import extract_body_paragraphs, extract_from_latex


def make_tex_tar(tmp_path: Path, name: str = "paper.tar.gz") -> str:
    tex_content = r"""
\documentclass{article}
\begin{document}
这是正文内容。这里有数学 $x+y$。

第二段内容。
\end{document}
""".strip()
    tar_path = tmp_path / name
    with tarfile.open(tar_path, "w:gz") as tf:
        info = tarfile.TarInfo("main.tex")
        data = tex_content.encode("utf-8")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return str(tar_path)


def test_extract_from_latex_paragraphs(tmp_path):
    tar_path = make_tex_tar(tmp_path)
    paras = extract_from_latex(tar_path)
    assert paras and any("数学" in p for p in paras)


def test_body_extract_prefers_latex(tmp_path, monkeypatch):
    tar_path = make_tex_tar(tmp_path)
    rec = {"files": {"latex_source_path": tar_path, "pdf_path": None}}
    paras = extract_body_paragraphs(rec)
    assert len(paras) >= 1

