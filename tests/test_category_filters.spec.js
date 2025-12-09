/**
 * Category Filter Tests
 *
 * Tests for category tab filtering functionality on the Recent Translations page.
 * These are Playwright-style tests that should be run against a deployed site.
 *
 * Run with: npx playwright test tests/test_category_filters.spec.js
 */

const { test, expect } = require('@playwright/test');

test.describe('Category Filters', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to home page before each test
    await page.goto('/');
    // Wait for page to be fully loaded
    await page.waitForLoadState('networkidle');
  });

  test('should filter papers by AI & Computer Science category', async ({ page }) => {
    // Click AI & Computer Science tab
    await page.click('[data-category="ai_computing"]');

    // Verify URL updated
    expect(page.url()).toContain('?category=ai_computing');

    // Verify tab is active
    const aiTab = await page.locator('[data-category="ai_computing"]');
    await expect(aiTab).toHaveClass(/active/);
    await expect(aiTab).toHaveAttribute('aria-selected', 'true');

    // Verify other tabs are not active
    const allRecentTab = await page.locator('[data-category=""]');
    await expect(allRecentTab).not.toHaveClass(/active/);
  });

  test('should filter papers by Physics category', async ({ page }) => {
    // Click Physics & Nuclear Science tab
    await page.click('[data-category="physics"]');

    // Verify URL updated
    expect(page.url()).toContain('?category=physics');

    // Verify tab is active
    const physicsTab = await page.locator('[data-category="physics"]');
    await expect(physicsTab).toHaveClass(/active/);
    await expect(physicsTab).toHaveAttribute('aria-selected', 'true');
  });

  test('should update paper count when filtering', async ({ page }) => {
    // Get initial count
    const initialCount = await page.textContent('#paperCount');
    expect(initialCount).toMatch(/Showing \d+ papers?/);

    // Click a category tab
    await page.click('[data-category="physics"]');

    // Get filtered count
    const filteredCount = await page.textContent('#paperCount');

    // Should NOT contain "of" - format is "Showing X papers"
    expect(filteredCount).not.toContain('of');
    expect(filteredCount).toMatch(/Showing \d+ papers?/);

    // Count should have changed (unless all papers are physics, which is unlikely)
    expect(filteredCount).not.toEqual(initialCount);
  });

  test('should show all papers when clicking "All Recent"', async ({ page }) => {
    // First, filter by a category
    await page.click('[data-category="ai_computing"]');
    expect(page.url()).toContain('?category=ai_computing');

    // Then click "All Recent"
    await page.click('[data-category=""]');

    // Verify URL no longer has category param
    expect(page.url()).not.toContain('?category=');

    // Verify "All Recent" tab is active
    const allRecentTab = await page.locator('[data-category=""]');
    await expect(allRecentTab).toHaveClass(/active/);
  });

  test('should restore category from URL on page load', async ({ page }) => {
    // Navigate directly to a URL with category filter
    await page.goto('/?category=psychology');

    // Verify the correct tab is active
    const psychologyTab = await page.locator('[data-category="psychology"]');
    await expect(psychologyTab).toHaveClass(/active/);
    await expect(psychologyTab).toHaveAttribute('aria-selected', 'true');

    // Verify other tabs are not active
    const allRecentTab = await page.locator('[data-category=""]');
    await expect(allRecentTab).not.toHaveClass(/active/);
  });

  test('should handle browser back button', async ({ page }) => {
    // Click AI tab
    await page.click('[data-category="ai_computing"]');
    expect(page.url()).toContain('?category=ai_computing');

    // Click Physics tab
    await page.click('[data-category="physics"]');
    expect(page.url()).toContain('?category=physics');

    // Go back
    await page.goBack();

    // Should be back to AI category
    expect(page.url()).toContain('?category=ai_computing');
    const aiTab = await page.locator('[data-category="ai_computing"]');
    await expect(aiTab).toHaveClass(/active/);
  });

  test('should handle browser forward button', async ({ page }) => {
    // Click AI tab
    await page.click('[data-category="ai_computing"]');

    // Click Physics tab
    await page.click('[data-category="physics"]');

    // Go back
    await page.goBack();

    // Go forward
    await page.goForward();

    // Should be back to Physics category
    expect(page.url()).toContain('?category=physics');
    const physicsTab = await page.locator('[data-category="physics"]');
    await expect(physicsTab).toHaveClass(/active/);
  });

  test('should clear search input when switching categories', async ({ page }) => {
    // Enter a search term
    const searchInput = await page.locator('#search-input');
    await searchInput.fill('quantum');

    // Click a category tab
    await page.click('[data-category="physics"]');

    // Search input should be cleared
    await expect(searchInput).toHaveValue('');
  });

  test('should show paper count as "Showing X papers" format', async ({ page }) => {
    // Check default count format
    const count1 = await page.textContent('#paperCount');
    expect(count1).toMatch(/^Showing \d+ papers?$/);
    expect(count1).not.toContain(' of ');

    // Filter and check count format again
    await page.click('[data-category="ai_computing"]');
    const count2 = await page.textContent('#paperCount');
    expect(count2).toMatch(/^Showing \d+ papers?$/);
    expect(count2).not.toContain(' of ');
  });

  test('should handle invalid category in URL gracefully', async ({ page }) => {
    // Navigate to URL with invalid category
    await page.goto('/?category=invalid_category');

    // Should show "All Recent" as active (graceful degradation)
    // Note: This depends on implementation - may show no results or all papers
    const paperCount = await page.textContent('#paperCount');
    expect(paperCount).toMatch(/Showing \d+ papers?/);
  });
});

test.describe('Category Filter Edge Cases', () => {
  test('should maintain singular/plural paper count', async ({ page }) => {
    await page.goto('/');

    const count = await page.textContent('#paperCount');

    // Check for correct singular/plural usage
    if (count.includes('Showing 1 paper')) {
      expect(count).not.toContain('papers');
    } else {
      expect(count).toContain('papers');
    }
  });

  test('should handle rapid tab switching', async ({ page }) => {
    await page.goto('/');

    // Rapidly click different tabs
    await page.click('[data-category="ai_computing"]');
    await page.click('[data-category="physics"]');
    await page.click('[data-category="psychology"]');
    await page.click('[data-category=""]');

    // Should end up on "All Recent" with no errors
    const allRecentTab = await page.locator('[data-category=""]');
    await expect(allRecentTab).toHaveClass(/active/);
    expect(page.url()).not.toContain('?category=');
  });
});
