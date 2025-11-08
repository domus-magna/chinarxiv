"""
Tests for math/LaTeX masking and unmasking functionality.
"""
from src.tex_guard import mask_math, unmask_math, verify_token_parity, MATH_TOKEN_FMT


class TestMathMasking:
    """Test math masking functionality."""
    
    def test_mask_inline_math(self):
        """Test masking inline math expressions."""
        text = "The equation $x = y + z$ is simple."
        masked, mappings = mask_math(text)
        
        assert len(mappings) == 1
        assert mappings[0].content == "$x = y + z$"
        assert mappings[0].token == MATH_TOKEN_FMT.format(1)
        assert masked == f"The equation {mappings[0].token} is simple."
    
    def test_mask_display_math(self):
        """Test masking display math expressions."""
        text = "The formula is:\n\n$$\\sum_{i=1}^{n} x_i$$\n\nThat's it."
        masked, mappings = mask_math(text)
        
        assert len(mappings) == 1
        assert mappings[0].content == "$$\\sum_{i=1}^{n} x_i$$"
        assert masked == f"The formula is:\n\n{mappings[0].token}\n\nThat's it."
    
    def test_mask_multiple_math_expressions(self):
        """Test masking multiple math expressions."""
        text = "First $x = y$, then $$z = w$$, and finally $a = b$."
        masked, mappings = mask_math(text)
        
        assert len(mappings) == 3
        # Order may vary due to regex processing, so check all mappings
        contents = [m.content for m in mappings]
        assert "$x = y$" in contents
        assert "$$z = w$$" in contents
        assert "$a = b$" in contents
        
        # Verify all tokens are in the masked text
        tokens = [m.token for m in mappings]
        for token in tokens:
            assert token in masked
    
    def test_mask_latex_environments(self):
        """Test masking LaTeX environments."""
        text = "\\begin{equation}\nx = y + z\n\\end{equation}"
        masked, mappings = mask_math(text)
        
        assert len(mappings) == 1
        assert mappings[0].content == "\\begin{equation}\nx = y + z\n\\end{equation}"
        assert masked == mappings[0].token
    
    def test_mask_no_math(self):
        """Test masking text with no math expressions."""
        text = "This is just plain text with no math."
        masked, mappings = mask_math(text)
        
        assert len(mappings) == 0
        assert masked == text
    
    def test_unmask_math(self):
        """Test unmasking math expressions."""
        text = "The equation $x = y$ is simple."
        masked, mappings = mask_math(text)
        unmasked = unmask_math(masked, mappings)
        
        assert unmasked == text
    
    def test_unmask_multiple_math(self):
        """Test unmasking multiple math expressions."""
        text = "First $x = y$, then $$z = w$$, and finally $a = b$."
        masked, mappings = mask_math(text)
        unmasked = unmask_math(masked, mappings)
        
        assert unmasked == text
    
    def test_verify_token_parity_success(self):
        """Test token parity verification with correct translation."""
        text = "The equation $x = y$ is simple."
        masked, mappings = mask_math(text)
        
        # Simulate correct translation (tokens preserved)
        translated = f"The equation {mappings[0].token} is simple."
        
        assert verify_token_parity(mappings, translated) is True
    
    def test_verify_token_parity_failure(self):
        """Test token parity verification with incorrect translation."""
        text = "The equation $x = y$ is simple."
        masked, mappings = mask_math(text)
        
        # Simulate incorrect translation (token missing)
        translated = "The equation is simple."
        
        assert verify_token_parity(mappings, translated) is False
    
    def test_verify_token_parity_duplicate(self):
        """Test token parity verification with duplicate tokens."""
        text = "The equation $x = y$ is simple."
        masked, mappings = mask_math(text)
        
        # Simulate incorrect translation (token duplicated)
        translated = f"The equation {mappings[0].token} {mappings[0].token} is simple."
        
        assert verify_token_parity(mappings, translated) is False
    
    def test_complex_math_expressions(self):
        """Test masking complex math expressions."""
        text = """
        The equation $\\frac{d}{dx}f(x) = \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}$ 
        is fundamental.
        
        $$\\int_{-\\infty}^{\\infty} e^{-x^2} dx = \\sqrt{\\pi}$$
        
        \\begin{align}
        x &= y + z \\\\
        a &= b \\cdot c
        \\end{align}
        """
        
        masked, mappings = mask_math(text)
        unmasked = unmask_math(masked, mappings)
        
        assert len(mappings) == 3
        assert unmasked == text
        assert verify_token_parity(mappings, masked) is True
    
    def test_escaped_dollar_signs(self):
        """Test that escaped dollar signs are not treated as math."""
        text = "The price is \\$100, not $x = y$."
        masked, mappings = mask_math(text)
        
        assert len(mappings) == 1
        assert mappings[0].content == "$x = y$"
        assert "\\$100" in masked  # Escaped dollar should remain
    
    def test_nested_math_expressions(self):
        """Test that nested math expressions are handled correctly."""
        text = "Outer: $x = \\text{inner: $y = z$}$"
        masked, mappings = mask_math(text)
        
        # The regex patterns match individual $...$ pairs, not nested ones
        # So we get multiple matches for nested expressions
        assert len(mappings) >= 1
        # Verify that the text is properly masked
        for mapping in mappings:
            assert mapping.token in masked