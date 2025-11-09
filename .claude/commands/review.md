Please perform a pragmatic code review of all changes in the current branch that differ from main.

## Core Principles

**Pragmatic over Defensive**: Focus on real problems in the actual threat model, not theoretical security theater.

**Simplicity over Comprehensiveness**: Prefer simple solutions that work over complex "best practice" implementations.

**Proportional Recommendations**: Ensure suggestions match the actual risk and scope of the code. Don't recommend enterprise patterns for localhost tools.

## Review Process

1. **Analyze Branch Changes**
   - Run `git diff main...HEAD --stat` to see the scope of changes
   - Run `git diff main...HEAD` to see the full diff
   - Run `git log main..HEAD --oneline` to see commit history
   - Identify which files were added, modified, or deleted

2. **Verify Original Goals**
   - Examine commit messages and PR descriptions to understand the intended purpose
   - Check if the stated goals were successfully achieved
   - Identify any incomplete or missing functionality
   - If goals weren't fully met, create a concrete plan to remediate

3. **Identify Overengineering** (PRIMARY FOCUS)
   - Look for unnecessary abstractions, patterns, or layers
   - Identify code built "just in case" rather than for actual requirements
   - Find opportunities to delete code or reduce complexity while maintaining functionality
   - Question whether frameworks, libraries, or patterns are needed or if simpler approaches work
   - Check for premature optimization or overly defensive error handling
   - Look for "enterprise patterns" applied to simple problems

4. **Code Quality Assessment**
   - **Correctness**: Check for actual logic errors and bugs (not theoretical edge cases)
   - **Simplicity**: Can this be done with less code? Fewer dependencies? Less abstraction?
   - **Consistency**: Does it follow existing project patterns?
   - **Readability**: Is the code clear and obvious?
   - **Performance**: Are there obvious bottlenecks or inefficiencies?

5. **Testing & Reliability**
   - Does critical path functionality have tests?
   - Are the tests simple and maintainable, or overengineered themselves?
   - Don't require tests for trivial code or obvious functionality
   - Focus on tests that prevent real regressions, not theoretical coverage metrics

6. **Security (Threat Model Aware)**
   - **Assess actual threat model**: localhost-only? Internal tool? Public API?
   - Flag real vulnerabilities: hardcoded secrets, actual injection risks, exposed credentials
   - **Avoid security theater**: Don't recommend CSRF tokens for localhost tools, rate limiting for internal dashboards, or JWT tokens when Basic Auth suffices
   - Focus on proportional security: simple auth for simple tools, strong auth for public APIs
   - Only flag issues that matter for the actual deployment and use case

7. **Documentation & Dependencies**
   - Is complex logic explained? (But don't require docs for obvious code)
   - Are new dependencies actually needed, or could stdlib/existing deps work?
   - Check for dependency bloat or version conflicts

8. **Git Hygiene**
   - Check for accidentally committed files (.env, credentials, debug artifacts)
   - Verify commit messages are descriptive
   - Identify debug code or commented-out code that should be removed

## Output Format

Provide a structured summary with:

- **Summary**: Brief overview of what changed and why
- **Strengths**: What was done well (especially simplicity and pragmatism)
- **Overengineering Found**: Code that could be simplified or removed while maintaining functionality
- **Real Issues**: Actual problems organized by severity (Critical, High, Medium, Low)
  - Only flag issues that matter for the actual threat model and use case
  - Avoid theoretical problems, edge cases, or "best practices" that don't apply
- **Pragmatic Recommendations**: Concrete, proportional action items
  - Match recommendations to actual risk and scope
  - Prefer simple fixes over complex "proper" solutions
  - Include file paths and line numbers
  - Mark which recommendations are truly blockers vs. nice-to-haves
- **Approval Status**: Ready to merge, needs minor fixes, or requires significant rework

## Anti-Patterns to Avoid in Your Review

❌ Don't recommend CSRF protection for localhost-only tools
❌ Don't require rate limiting for internal dashboards
❌ Don't suggest JWT/OAuth when Basic Auth is sufficient
❌ Don't require comprehensive test coverage for simple utilities
❌ Don't flag theoretical security issues that don't apply to the threat model
❌ Don't suggest enterprise patterns (DI containers, event buses, etc.) for simple scripts
❌ Don't recommend adding logging, monitoring, alerting to every function
❌ Don't require type hints, linting, or formatting as blockers unless project standards demand it
