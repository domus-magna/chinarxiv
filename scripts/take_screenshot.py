#!/usr/bin/env python3
"""Quick screenshot capture script."""
from playwright.sync_api import sync_playwright
import sys

def take_screenshot(url='http://localhost:5001/', output='screenshot.png'):
    """Take a screenshot of the specified URL."""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1280, 'height': 1024})

        print(f"📸 Loading {url}...")
        page.goto(url)
        page.wait_for_load_state('networkidle')

        print(f"💾 Saving screenshot to {output}...")
        page.screenshot(path=output, full_page=True)

        browser.close()
        print(f"✅ Done! Screenshot saved to: {output}")

if __name__ == '__main__':
    url = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:5001/'
    output = sys.argv[2] if len(sys.argv) > 2 else 'screenshot.png'
    take_screenshot(url, output)
