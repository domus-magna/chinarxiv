---
description: Dispatch a Simplification subagent to review all code changes for complexity reduction opportunities
---

Dispatch a Simplification subagent using the Task tool to systematically review all recent code changes for opportunities to reduce complexity while retaining full capability.

The subagent will:

1. **Analyze all modified files** using `git diff --cached` to identify changes
2. **Identify complexity issues**:
   - DRY violations (duplicated code)
   - Premature optimizations
   - Unnecessary abstractions
   - Code smells (long functions, deep nesting, complex conditionals)
   - Over-engineering

3. **Auto-execute simplifications** with detailed justification:
   - Extract duplicate logic to functions
   - Remove premature optimizations
   - Flatten nested conditionals
   - Simplify over-abstracted code
   - Delete dead code

4. **Provide detailed metrics**:
   - Lines of code reduced
   - Files simplified
   - Functions extracted
   - Complexity metrics (before → after)

**Requirements**:
- If simplifications are found: Auto-execute with justification
- If no simplifications found: Provide detailed analysis of why code is already optimal
- Always include quantitative metrics
- Preserve 100% of functionality

Dispatch the subagent now and report back when it completes its analysis.
