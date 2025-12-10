"""
Screenshot test for visual verification.
Run with: pytest tests/test_screenshot.py -v
Or directly: python tests/test_screenshot.py
"""
from playwright.sync_api import sync_playwright
from pathlib import Path
import time

def test_homepage_screenshot():
    """Capture homepage screenshot for visual verification."""
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 1024})

        # Navigate to localhost
        page.goto('http://localhost:5001/')

        # Wait for page to fully load
        page.wait_for_load_state('networkidle')
        time.sleep(1)  # Extra time for CSS/fonts

        # Take screenshot
        screenshot_path = Path('screenshot-homepage.png')
        page.screenshot(path=str(screenshot_path), full_page=True)

        browser.close()

        print(f"✅ Screenshot saved to: {screenshot_path.absolute()}")
        assert screenshot_path.exists()

def test_detail_page_screenshot():
    """Capture paper detail page screenshot."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 1024})

        # Navigate to a paper detail page
        page.goto('http://localhost:5001/items/chinaxiv-202201.00007')
        page.wait_for_load_state('networkidle')
        time.sleep(1)

        # Take screenshot
        screenshot_path = Path('screenshot-detail.png')
        page.screenshot(path=str(screenshot_path), full_page=True)

        browser.close()

        print(f"✅ Screenshot saved to: {screenshot_path.absolute()}")
        assert screenshot_path.exists()

if __name__ == '__main__':
    """Run directly without pytest."""
    print("Taking screenshots...")
    test_homepage_screenshot()
    test_detail_page_screenshot()
    print("\n✅ All screenshots captured!")
