from src.harvest_oai import normalize_record
from lxml import etree


def test_normalize_record_dc_minimal():
    xml = """
    <record xmlns:oai="http://www.openarchives.org/OAI/2.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <oai:header>
        <oai:identifier>oai:chinaxiv.org:2025-12345</oai:identifier>
        <oai:datestamp>2025-10-02</oai:datestamp>
      </oai:header>
      <oai:metadata>
        <dc:title>示例标题</dc:title>
        <dc:creator>Li, Hua</dc:creator>
        <dc:subject>cs.AI</dc:subject>
        <dc:description>这是摘要。</dc:description>
        <dc:date>2025-10-02</dc:date>
        <dc:identifier>https://example.org/abs/2025-12345</dc:identifier>
        <dc:identifier>https://example.org/pdf/2025-12345.pdf</dc:identifier>
        <dc:rights>CC-BY 4.0</dc:rights>
      </oai:metadata>
    </record>
    """.strip()
    el = etree.fromstring(xml.encode("utf-8"))
    rec = normalize_record(el)
    assert rec is not None
    assert rec["id"] == "2025-12345"
    assert rec["pdf_url"].endswith(".pdf")
    assert rec["title"] == "示例标题"
    assert rec["license"]["raw"].startswith("CC-BY") or rec["license"][
        "raw"
    ].startswith("CC BY")
