import psycopg2


def test_index_abstract_preview_strips_para_like_tags(
    client, test_database
) -> None:
    """
    Regression test: Some translations can contain XML-ish markers like
    `<PARA id="1">` in abstract fields. The homepage preview should not show
    raw tag text.
    """
    conn = psycopg2.connect(test_database)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO papers (
            id,
            title_en,
            abstract_en,
            creators_en,
            date,
            qa_status,
            text_status,
            has_full_text
        ) VALUES (
            'chinaxiv-202507.00003',
            'Test Title',
            %s,
            '["A", "B"]',
            '2025-07-01T00:00:00+00:00',
            'pass',
            'complete',
            TRUE
        )
        """,
        ('<PARA id="1">This should not render as a tag.</PARA>',),
    )
    conn.commit()
    conn.close()

    res = client.get("/")
    assert res.status_code == 200
    body = res.get_data(as_text=True)

    # We should not leak raw tag text into the preview.
    assert "<PARA" not in body
    assert "&lt;PARA" not in body

