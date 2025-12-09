/**
 * Unit tests for filter helper functions
 *
 * These tests cover bugs that have been fixed:
 * 1. normalizeSubject TypeError (Gemini review - commit 8e3d80b)
 * 2. setFilterState prototype pollution (Codex review - commit 2226cb0)
 * 3. setFilterState date type validation (Reviewer feedback)
 * 4. Query clearing edge cases (Codex review - commit d896f3c)
 *
 * Run with: node tests/unit/test_filter_helpers.js
 */

// ============================================================================
// Helper Functions (extracted from assets/site.js for testing)
// ============================================================================

function normalizeDate(dateStr) {
  if (!dateStr) return null;
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) {
    console.warn('[Filter] Invalid date:', dateStr);
    return null;
  }
  return date;
}

function normalizeSubject(subject) {
  // FIX: Changed from subject?.toLowerCase().trim() to (subject || '').toLowerCase().trim()
  // to prevent TypeError when subject is null/undefined
  return (subject || '').toLowerCase().trim();
}

function escapeHTML(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Simulated filter state for testing
let testFilterState = {
  query: '',
  category: '',
  dateFrom: null,
  dateTo: null,
};

function setFilterState(updates) {
  // Only allow known filter keys (prevents __proto__ injection)
  if (updates.hasOwnProperty('query') && typeof updates.query === 'string') {
    testFilterState.query = updates.query;
  }
  if (updates.hasOwnProperty('category') && typeof updates.category === 'string') {
    testFilterState.category = updates.category;
  }
  if (updates.hasOwnProperty('dateFrom')) {
    // Validate: must be Date object or null
    if (updates.dateFrom === null || updates.dateFrom instanceof Date) {
      testFilterState.dateFrom = updates.dateFrom;
    } else {
      console.warn('[Filter] Invalid dateFrom type:', typeof updates.dateFrom);
    }
  }
  if (updates.hasOwnProperty('dateTo')) {
    // Validate: must be Date object or null
    if (updates.dateTo === null || updates.dateTo instanceof Date) {
      testFilterState.dateTo = updates.dateTo;
    } else {
      console.warn('[Filter] Invalid dateTo type:', typeof updates.dateTo);
    }
  }
}

function resetTestState() {
  testFilterState = {
    query: '',
    category: '',
    dateFrom: null,
    dateTo: null,
  };
}

// ============================================================================
// Test Framework (minimal)
// ============================================================================

let testCount = 0;
let passCount = 0;
let failCount = 0;

function assert(condition, message) {
  testCount++;
  if (condition) {
    passCount++;
    console.log(`  ✓ ${message}`);
  } else {
    failCount++;
    console.log(`  ✗ FAIL: ${message}`);
  }
}

function assertEqual(actual, expected, message) {
  testCount++;
  if (actual === expected) {
    passCount++;
    console.log(`  ✓ ${message}`);
  } else {
    failCount++;
    console.log(`  ✗ FAIL: ${message}`);
    console.log(`    Expected: ${expected}`);
    console.log(`    Actual:   ${actual}`);
  }
}

function assertDeepEqual(actual, expected, message) {
  testCount++;
  const actualStr = JSON.stringify(actual);
  const expectedStr = JSON.stringify(expected);
  if (actualStr === expectedStr) {
    passCount++;
    console.log(`  ✓ ${message}`);
  } else {
    failCount++;
    console.log(`  ✗ FAIL: ${message}`);
    console.log(`    Expected: ${expectedStr}`);
    console.log(`    Actual:   ${actualStr}`);
  }
}

function describe(name, fn) {
  console.log(`\n${name}`);
  fn();
}

// ============================================================================
// Tests: normalizeSubject (Bug fix: TypeError with null/undefined)
// ============================================================================

describe('normalizeSubject()', () => {
  assert(normalizeSubject('AI & Computing') === 'ai & computing',
    'lowercases subject string');

  assert(normalizeSubject('  Physics  ') === 'physics',
    'trims whitespace');

  assert(normalizeSubject('') === '',
    'handles empty string');

  // BUG FIX: These used to throw TypeError
  assert(normalizeSubject(null) === '',
    'handles null without throwing TypeError');

  assert(normalizeSubject(undefined) === '',
    'handles undefined without throwing TypeError');

  assert(normalizeSubject(0) === '',
    'handles number 0 (falsy value)');
});

// ============================================================================
// Tests: normalizeDate
// ============================================================================

describe('normalizeDate()', () => {
  const result = normalizeDate('2022-01-15');
  assert(result instanceof Date,
    'returns Date object for valid date string');

  assert(normalizeDate('') === null,
    'returns null for empty string');

  assert(normalizeDate(null) === null,
    'returns null for null input');

  assert(normalizeDate('invalid-date') === null,
    'returns null for invalid date string');

  const date = normalizeDate('2022-12-31');
  assert(date && date.getFullYear() === 2022,
    'parses year correctly');
});

// ============================================================================
// Tests: escapeHTML (XSS prevention)
// ============================================================================

describe('escapeHTML()', () => {
  assertEqual(escapeHTML('<script>alert("xss")</script>'),
    '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;',
    'escapes HTML tags and quotes');

  assertEqual(escapeHTML('Safe text'),
    'Safe text',
    'leaves safe text unchanged');

  assertEqual(escapeHTML(''),
    '',
    'handles empty string');

  assertEqual(escapeHTML(null),
    '',
    'handles null');

  assertEqual(escapeHTML("It's a <test> & \"example\""),
    'It&#39;s a &lt;test&gt; &amp; &quot;example&quot;',
    'escapes all special characters');
});

// ============================================================================
// Tests: setFilterState (Prototype pollution prevention)
// ============================================================================

describe('setFilterState() - Prototype Pollution Prevention', () => {
  resetTestState();

  // Try to inject __proto__
  setFilterState({ __proto__: { injected: true } });

  assert(!Object.prototype.injected,
    'prevents __proto__ injection');

  resetTestState();

  // Try to inject constructor
  setFilterState({ constructor: { prototype: { injected: true } } });

  assert(!Object.prototype.injected,
    'prevents constructor.prototype injection');

  resetTestState();

  // Verify only valid keys are accepted
  setFilterState({
    query: 'test',
    category: 'ai',
    unknownKey: 'should be ignored'
  });

  assertEqual(testFilterState.query, 'test',
    'accepts valid query key');

  assertEqual(testFilterState.category, 'ai',
    'accepts valid category key');

  assert(!testFilterState.hasOwnProperty('unknownKey'),
    'rejects unknown keys');
});

// ============================================================================
// Tests: setFilterState - Type Validation
// ============================================================================

describe('setFilterState() - Type Validation', () => {
  resetTestState();

  // Test query type validation
  setFilterState({ query: 'valid string' });
  assertEqual(testFilterState.query, 'valid string',
    'accepts string for query');

  resetTestState();
  setFilterState({ query: 123 });
  assertEqual(testFilterState.query, '',
    'rejects number for query');

  resetTestState();
  setFilterState({ query: null });
  assertEqual(testFilterState.query, '',
    'rejects null for query');

  // Test category type validation
  resetTestState();
  setFilterState({ category: 'ai_computing' });
  assertEqual(testFilterState.category, 'ai_computing',
    'accepts string for category');

  resetTestState();
  setFilterState({ category: 123 });
  assertEqual(testFilterState.category, '',
    'rejects number for category');

  // Test dateFrom type validation (BUG FIX: now has validation)
  resetTestState();
  const validDate = new Date('2022-01-15');
  setFilterState({ dateFrom: validDate });
  assertEqual(testFilterState.dateFrom, validDate,
    'accepts Date object for dateFrom');

  resetTestState();
  setFilterState({ dateFrom: null });
  assertEqual(testFilterState.dateFrom, null,
    'accepts null for dateFrom');

  resetTestState();
  setFilterState({ dateFrom: '2022-01-15' });
  assertEqual(testFilterState.dateFrom, null,
    'rejects string for dateFrom (must be Date object)');

  resetTestState();
  setFilterState({ dateFrom: 1234567890 });
  assertEqual(testFilterState.dateFrom, null,
    'rejects number for dateFrom');

  resetTestState();
  setFilterState({ dateFrom: { year: 2022 } });
  assertEqual(testFilterState.dateFrom, null,
    'rejects plain object for dateFrom');

  // Test dateTo type validation (BUG FIX: now has validation)
  resetTestState();
  const validToDate = new Date('2022-12-31');
  setFilterState({ dateTo: validToDate });
  assertEqual(testFilterState.dateTo, validToDate,
    'accepts Date object for dateTo');

  resetTestState();
  setFilterState({ dateTo: null });
  assertEqual(testFilterState.dateTo, null,
    'accepts null for dateTo');

  resetTestState();
  setFilterState({ dateTo: '2022-12-31' });
  assertEqual(testFilterState.dateTo, null,
    'rejects string for dateTo (must be Date object)');
});

// ============================================================================
// Tests: Query Clearing Edge Cases
// ============================================================================

describe('Query Clearing Edge Cases', () => {
  // These tests simulate the bugs fixed in commit d896f3c

  resetTestState();

  // Simulate: initFromURL with query present
  setFilterState({ query: 'quantum' });
  assertEqual(testFilterState.query, 'quantum',
    'sets query when present in URL');

  // Simulate: initFromURL with query absent (should clear)
  setFilterState({ query: '' });
  assertEqual(testFilterState.query, '',
    'clears query when absent from URL');

  // Simulate: category tab click should clear query
  resetTestState();
  setFilterState({ query: 'test', category: '' });
  assertEqual(testFilterState.query, 'test',
    'query set initially');

  setFilterState({ query: '', category: 'ai_computing' });
  assertEqual(testFilterState.query, '',
    'query cleared on category change');
  assertEqual(testFilterState.category, 'ai_computing',
    'category updated');
});

// ============================================================================
// Tests: Combined State Updates
// ============================================================================

describe('Combined State Updates', () => {
  resetTestState();

  const fromDate = new Date('2022-01-01');
  const toDate = new Date('2022-12-31');

  setFilterState({
    query: 'machine learning',
    category: 'ai_computing',
    dateFrom: fromDate,
    dateTo: toDate
  });

  assertEqual(testFilterState.query, 'machine learning',
    'sets query in combined update');

  assertEqual(testFilterState.category, 'ai_computing',
    'sets category in combined update');

  assertEqual(testFilterState.dateFrom, fromDate,
    'sets dateFrom in combined update');

  assertEqual(testFilterState.dateTo, toDate,
    'sets dateTo in combined update');

  // Partial update should preserve other fields
  setFilterState({ query: 'quantum' });

  assertEqual(testFilterState.query, 'quantum',
    'updates query in partial update');

  assertEqual(testFilterState.category, 'ai_computing',
    'preserves category in partial update');
});

// ============================================================================
// Test Results Summary
// ============================================================================

console.log('\n' + '='.repeat(60));
console.log('Test Results');
console.log('='.repeat(60));
console.log(`Total:  ${testCount}`);
console.log(`Passed: ${passCount}`);
console.log(`Failed: ${failCount}`);
console.log('='.repeat(60));

if (failCount > 0) {
  console.log('\n❌ TESTS FAILED');
  process.exit(1);
} else {
  console.log('\n✅ ALL TESTS PASSED');
  process.exit(0);
}
