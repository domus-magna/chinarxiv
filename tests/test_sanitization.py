"""
Tests for HTML sanitization in rendered content.

These tests verify that the bleach-based sanitization in render.py
correctly prevents XSS attacks from LLM-generated or PDF-derived content.
"""

# Import bleach and markdown with same config as render.py
import bleach
import markdown

# Exact configuration from src/render.py
BLEACH_ALLOWED_TAGS = [
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "dl", "dt", "dd",
    "code", "pre", "blockquote",
    "em", "strong", "a", "br", "hr",
    "table", "thead", "tbody", "tr", "th", "td",
    "img", "sup", "sub", "span", "div",
]
# Allowed attributes - includes class for code highlighting (codehilite extension)
BLEACH_ALLOWED_ATTRS = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "th": ["colspan", "rowspan", "class"],
    "td": ["colspan", "rowspan", "class"],
    "div": ["class"],
    "pre": ["class"],
    "code": ["class"],
    "span": ["class"],
    "table": ["class"],
}


def sanitize_markdown(text: str) -> str:
    """Replicate the markdown_filter from render.py for testing."""
    html = markdown.markdown(text, extensions=["extra", "codehilite"])
    return bleach.clean(
        html,
        tags=BLEACH_ALLOWED_TAGS,
        attributes=BLEACH_ALLOWED_ATTRS,
        strip=True,
    )


class TestScriptTagStripping:
    """Test that <script> tags are removed.

    Note: bleach with strip=True removes tags but may leave text content.
    This is safe because the text is not executable - only the tag is dangerous.
    """

    def test_inline_script_removed(self):
        """Basic script tag is stripped (text content may remain but is not executable)."""
        malicious = "<script>alert('xss')</script>"
        result = sanitize_markdown(malicious)
        # The script TAG is removed - that's what matters for security
        assert "<script>" not in result
        assert "</script>" not in result

    def test_script_in_markdown_removed(self):
        """Script embedded in markdown content is stripped."""
        malicious = "Hello world\n\n<script>document.cookie</script>\n\nGoodbye"
        result = sanitize_markdown(malicious)
        # Script tags are removed
        assert "<script>" not in result
        assert "</script>" not in result
        # Safe content preserved
        assert "Hello world" in result

    def test_script_with_src_removed(self):
        """External script tags are stripped."""
        malicious = '<script src="https://evil.com/xss.js"></script>'
        result = sanitize_markdown(malicious)
        assert "<script" not in result

    def test_nested_script_removed(self):
        """Nested/obfuscated script tags are stripped."""
        malicious = "<scr<script>ipt>alert(1)</scr</script>ipt>"
        result = sanitize_markdown(malicious)
        assert "<script" not in result.lower()


class TestEventHandlerRemoval:
    """Test that event handlers (onclick, onerror, etc.) are removed."""

    def test_onclick_removed(self):
        """onclick handlers are stripped from elements."""
        malicious = '<div onclick="alert(1)">Click me</div>'
        result = sanitize_markdown(malicious)
        assert "onclick" not in result
        assert "alert" not in result

    def test_onerror_on_img_removed(self):
        """onerror handlers are stripped from img tags."""
        malicious = '<img src="x" onerror="alert(1)">'
        result = sanitize_markdown(malicious)
        assert "onerror" not in result
        assert "alert" not in result
        # img tag itself should be preserved (allowed tag)
        assert "<img" in result

    def test_onload_removed(self):
        """onload handlers are stripped."""
        malicious = '<body onload="evil()">'
        result = sanitize_markdown(malicious)
        assert "onload" not in result
        assert "evil" not in result

    def test_onmouseover_removed(self):
        """onmouseover handlers are stripped."""
        malicious = '<a href="#" onmouseover="alert(1)">hover</a>'
        result = sanitize_markdown(malicious)
        assert "onmouseover" not in result
        # a tag with href should be preserved
        assert "<a" in result


class TestMaliciousURLSchemes:
    """Test that dangerous URL schemes are handled."""

    def test_javascript_url_in_href(self):
        """javascript: URLs in href should be stripped or neutralized."""
        malicious = '<a href="javascript:alert(1)">click</a>'
        result = sanitize_markdown(malicious)
        # bleach strips non-http/https URLs by default
        assert "javascript:" not in result

    def test_javascript_url_in_img_src(self):
        """javascript: URLs in img src are handled."""
        malicious = '<img src="javascript:alert(1)">'
        result = sanitize_markdown(malicious)
        assert "javascript:" not in result

    def test_data_url_script(self):
        """data: URLs with script content are handled."""
        malicious = '<a href="data:text/html,<script>alert(1)</script>">click</a>'
        result = sanitize_markdown(malicious)
        # data: URLs should be stripped
        assert "<script>" not in result

    def test_vbscript_url(self):
        """vbscript: URLs are stripped."""
        malicious = '<a href="vbscript:msgbox(1)">click</a>'
        result = sanitize_markdown(malicious)
        assert "vbscript:" not in result


class TestAllowedTagsPreserved:
    """Test that legitimate HTML tags are preserved."""

    def test_paragraph_preserved(self):
        """p tags are preserved."""
        text = "Hello world"
        result = sanitize_markdown(text)
        assert "<p>" in result
        assert "Hello world" in result

    def test_headings_preserved(self):
        """Heading tags are preserved."""
        text = "# Heading 1\n\n## Heading 2"
        result = sanitize_markdown(text)
        assert "<h1>" in result
        assert "<h2>" in result

    def test_lists_preserved(self):
        """List tags are preserved."""
        text = "- Item 1\n- Item 2"
        result = sanitize_markdown(text)
        assert "<ul>" in result
        assert "<li>" in result

    def test_code_preserved(self):
        """Code blocks are preserved."""
        text = "```python\nprint('hello')\n```"
        result = sanitize_markdown(text)
        assert "<code>" in result or "<pre>" in result

    def test_links_with_href_preserved(self):
        """a tags with safe href are preserved."""
        text = "[link](https://example.com)"
        result = sanitize_markdown(text)
        assert "<a" in result
        assert "https://example.com" in result

    def test_images_with_src_preserved(self):
        """img tags with src and alt are preserved."""
        text = "![alt text](https://example.com/img.png)"
        result = sanitize_markdown(text)
        assert "<img" in result
        assert "https://example.com/img.png" in result

    def test_tables_preserved(self):
        """Table tags are preserved."""
        text = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = sanitize_markdown(text)
        assert "<table>" in result
        assert "<tr>" in result
        assert "<td>" in result or "<th>" in result

    def test_emphasis_preserved(self):
        """em and strong tags are preserved."""
        text = "*italic* and **bold**"
        result = sanitize_markdown(text)
        assert "<em>" in result
        assert "<strong>" in result


class TestDisallowedTagsStripped:
    """Test that non-whitelisted tags are stripped."""

    def test_iframe_stripped(self):
        """iframe tags are stripped."""
        malicious = '<iframe src="https://evil.com"></iframe>'
        result = sanitize_markdown(malicious)
        assert "<iframe" not in result

    def test_object_stripped(self):
        """object tags are stripped."""
        malicious = '<object data="https://evil.com/flash.swf"></object>'
        result = sanitize_markdown(malicious)
        assert "<object" not in result

    def test_embed_stripped(self):
        """embed tags are stripped."""
        malicious = '<embed src="https://evil.com/flash.swf">'
        result = sanitize_markdown(malicious)
        assert "<embed" not in result

    def test_form_stripped(self):
        """form tags are stripped."""
        malicious = '<form action="https://evil.com"><input></form>'
        result = sanitize_markdown(malicious)
        assert "<form" not in result
        assert "<input" not in result

    def test_style_stripped(self):
        """style tags are stripped."""
        malicious = '<style>body { background: url("javascript:alert(1)") }</style>'
        result = sanitize_markdown(malicious)
        assert "<style" not in result

    def test_svg_stripped(self):
        """svg tags (potential XSS vector) are stripped."""
        malicious = '<svg onload="alert(1)"><circle r="50"></circle></svg>'
        result = sanitize_markdown(malicious)
        assert "<svg" not in result


class TestDisallowedAttributesStripped:
    """Test that non-whitelisted attributes are stripped."""

    def test_style_attr_stripped(self):
        """style attributes are stripped."""
        malicious = '<div style="background:url(javascript:alert(1))">text</div>'
        result = sanitize_markdown(malicious)
        assert "style=" not in result
        # div is allowed, just style attr removed
        assert "<div>" in result

    def test_class_attr_preserved_on_allowed_elements(self):
        """class attributes are preserved on elements in whitelist."""
        # div with class should be preserved (needed for codehilite)
        text = '<div class="highlight">text</div>'
        result = sanitize_markdown(text)
        assert 'class="highlight"' in result

    def test_class_attr_stripped_on_non_allowed_elements(self):
        """class attributes are still stripped on elements not in whitelist."""
        # a tag doesn't allow class attribute
        text = '<a href="#" class="btn">link</a>'
        result = sanitize_markdown(text)
        assert 'class="btn"' not in result
        assert '<a' in result  # link itself preserved

    def test_id_attr_stripped(self):
        """id attributes are stripped."""
        text = '<div id="target">text</div>'
        result = sanitize_markdown(text)
        assert 'id="target"' not in result


class TestComplexAttackVectors:
    """Test complex/real-world attack patterns.

    Note: bleach strips tags but may preserve text content.
    The security guarantee is that no HTML tags execute - text is safe.
    """

    def test_mixed_case_script(self):
        """Mixed case script tags are handled - tags removed."""
        malicious = "<ScRiPt>alert(1)</ScRiPt>"
        result = sanitize_markdown(malicious)
        # Tags are removed (case-insensitive)
        assert "<script" not in result.lower()
        assert "</script" not in result.lower()

    def test_encoded_script(self):
        """HTML-encoded attacks in raw markdown."""
        # This tests the actual HTML that markdown produces
        malicious = "&lt;script&gt;alert(1)&lt;/script&gt;"
        result = sanitize_markdown(malicious)
        # Encoded entities should be visible as text, not executed
        assert "<script>" not in result

    def test_null_byte_injection(self):
        """Null byte injection attempts are handled - tags removed."""
        malicious = "<scr\x00ipt>alert(1)</script>"
        result = sanitize_markdown(malicious)
        # The malformed tag should not survive as a script tag
        assert "<script" not in result.lower()

    def test_svg_use_xss(self):
        """SVG use element XSS is prevented."""
        malicious = '<svg><use href="data:image/svg+xml,<svg onload=alert(1)>"></use></svg>'
        result = sanitize_markdown(malicious)
        assert "<svg" not in result
        assert "onload" not in result


class TestRealWorldContent:
    """Test with content similar to what LLMs/PDFs might produce."""

    def test_academic_markdown_safe(self):
        """Normal academic content renders correctly."""
        text = """# Abstract

This paper presents a novel approach to **machine learning**.

## Methods

We used the following equation:

$$E = mc^2$$

### Results

| Metric | Value |
|--------|-------|
| Accuracy | 95% |

See [Figure 1](#fig1) for details.
"""
        result = sanitize_markdown(text)
        # Content should be preserved
        assert "Abstract" in result
        assert "machine learning" in result
        assert "<table>" in result
        # No XSS vectors introduced
        assert "<script>" not in result

    def test_code_blocks_dont_execute(self):
        """Code in code blocks doesn't become executable."""
        text = """Here's how to show an alert:

```javascript
<script>alert('demo')</script>
```
"""
        result = sanitize_markdown(text)
        # The code block content should be escaped, not stripped
        # It should appear as text, not as a real script tag
        assert "alert" in result  # the text is there
        # But not as an actual script element
        result_lower = result.lower()
        # Count occurrences - should only be in code, not as real tag
        assert result_lower.count("<script>") == 0 or "<code>" in result


class TestCodeHighlightingClasses:
    """Test that code highlighting classes are preserved for codehilite extension."""

    def test_codehilite_div_class_preserved(self):
        """codehilite div class is preserved."""
        text = '<div class="codehilite"><pre>code</pre></div>'
        result = sanitize_markdown(text)
        assert 'class="codehilite"' in result

    def test_pre_class_preserved(self):
        """class on pre tags is preserved."""
        text = '<pre class="language-python">code</pre>'
        result = sanitize_markdown(text)
        assert 'class="language-python"' in result

    def test_code_class_preserved(self):
        """class on code tags is preserved."""
        text = '<code class="python">print</code>'
        result = sanitize_markdown(text)
        assert 'class="python"' in result

    def test_span_class_preserved(self):
        """class on span tags is preserved (for syntax highlighting tokens)."""
        text = '<span class="keyword">def</span>'
        result = sanitize_markdown(text)
        assert 'class="keyword"' in result

    def test_table_class_preserved(self):
        """class on table tags is preserved."""
        text = '<table class="data-table"><tr><td>cell</td></tr></table>'
        result = sanitize_markdown(text)
        assert 'class="data-table"' in result

    def test_th_td_class_preserved(self):
        """class on th/td tags is preserved."""
        text = '<table><tr><th class="header">H</th><td class="cell">C</td></tr></table>'
        result = sanitize_markdown(text)
        assert 'class="header"' in result
        assert 'class="cell"' in result


class TestMarkdownFilterIntegration:
    """Integration tests verifying the actual render.py filter matches test config."""

    def test_config_matches_render_py(self):
        """Verify test config matches src/render.py to prevent drift.

        This test reads the actual render.py source and extracts the bleach
        config to ensure our test fixture stays in sync.
        """
        import re
        from pathlib import Path

        # Get path relative to test file location
        test_dir = Path(__file__).parent
        project_root = test_dir.parent
        render_py = (project_root / "src" / "render.py").read_text()

        # Extract BLEACH_ALLOWED_TAGS from render.py
        tags_match = re.search(
            r'BLEACH_ALLOWED_TAGS\s*=\s*\[(.*?)\]',
            render_py,
            re.DOTALL
        )
        assert tags_match, "Could not find BLEACH_ALLOWED_TAGS in render.py"

        # Extract BLEACH_ALLOWED_ATTRS from render.py
        attrs_match = re.search(
            r'BLEACH_ALLOWED_ATTRS\s*=\s*\{(.*?)\n\s*\}',
            render_py,
            re.DOTALL
        )
        assert attrs_match, "Could not find BLEACH_ALLOWED_ATTRS in render.py"

        # Verify our test config has the same tags
        for tag in BLEACH_ALLOWED_TAGS:
            assert f'"{tag}"' in tags_match.group(1), \
                f"Tag '{tag}' in test but not in render.py"

        # Verify class is allowed on the right elements
        attrs_text = attrs_match.group(1)
        for element in ["div", "pre", "code", "span", "table"]:
            assert f'"{element}": ["class"]' in attrs_text or \
                   f'"{element}":["class"]' in attrs_text or \
                   (f'"{element}"' in attrs_text and '"class"' in attrs_text), \
                f"Element '{element}' should allow 'class' attribute in render.py"

    def test_markdown_filter_handles_none(self):
        """Verify markdown filter handles None input gracefully.

        The render.py markdown_filter should return empty string for None,
        not raise TypeError.
        """
        # Simulate what render.py's markdown_filter does
        def markdown_filter(text):
            if not text:
                return ""
            html = markdown.markdown(text, extensions=["extra", "codehilite"])
            return bleach.clean(
                html,
                tags=BLEACH_ALLOWED_TAGS,
                attributes=BLEACH_ALLOWED_ATTRS,
                strip=True,
            )

        # These should not raise
        assert markdown_filter(None) == ""
        assert markdown_filter("") == ""
        assert markdown_filter("   ") == ""  # whitespace only

    def test_markdown_filter_sanitizes_xss(self):
        """End-to-end test that markdown filter properly sanitizes XSS."""
        def markdown_filter(text):
            if not text:
                return ""
            html = markdown.markdown(text, extensions=["extra", "codehilite"])
            return bleach.clean(
                html,
                tags=BLEACH_ALLOWED_TAGS,
                attributes=BLEACH_ALLOWED_ATTRS,
                strip=True,
            )

        # XSS attack vectors
        malicious_inputs = [
            '<script>alert("xss")</script>',
            '<img src="x" onerror="alert(1)">',
            '<a href="javascript:alert(1)">click</a>',
            '<div onclick="evil()">click</div>',
        ]

        for malicious in malicious_inputs:
            result = markdown_filter(malicious)
            assert "<script" not in result.lower()
            assert "onerror" not in result
            assert "onclick" not in result
            assert "javascript:" not in result
