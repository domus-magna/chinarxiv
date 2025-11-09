---
description: Review open PRs to determine merge readiness
---

# Check Open PRs for Merge Readiness

Reviews open pull requests in numerical order, analyzing comments and reviews to determine if each PR is ready to merge or needs fixes.

## Process

Follow these steps for each PR review session:

### 1. List All Open PRs

Run the following command to get all open PRs in numerical order:
```bash
gh pr list --state open --json number,title,author,createdAt --limit 100
```

### 2. Select Target PR

- Process PRs in numerical order (lowest number first)
- Focus on ONLY the first/earliest PR in the list
- Note the PR number for the session

### 3. Gather PR Information

For the selected PR, collect comprehensive data:

```bash
# Get PR details
gh pr view [PR_NUMBER] --json number,title,author,body,reviews,comments,reviewDecision,statusCheckRollup

# Get review comments specifically
gh api repos/:owner/:repo/pulls/[PR_NUMBER]/comments

# Get conversation comments
gh api repos/:owner/:repo/issues/[PR_NUMBER]/comments

# Get reviews with full details
gh api repos/:owner/:repo/pulls/[PR_NUMBER]/reviews
```

### 4. Analyze Feedback

Review all collected information and categorize:

**Blocking Issues**:
- Explicit "Request Changes" reviews
- Unresolved conversations about bugs or critical issues
- Failed required status checks
- Security concerns or vulnerabilities
- Breaking changes without approval

**Non-Blocking Concerns**:
- Suggestions or recommendations
- "Comment" type reviews (not blocking)
- Nitpicks or style preferences
- Questions that have been answered
- Optional improvements

**Approval Signals**:
- Explicit "Approve" reviews
- "LGTM" or similar approval comments
- All status checks passing
- No unresolved blocking conversations

### 5. Determine Merge Readiness

Make a clear recommendation:

**READY TO MERGE** if:
- At least one approval review exists
- No "Request Changes" reviews are outstanding
- All blocking conversations are resolved
- Required status checks are passing
- No security or breaking change concerns

**NEEDS FIXES** if:
- Any "Request Changes" reviews exist
- Blocking conversations are unresolved
- Required status checks are failing
- Security or breaking change concerns raised

**NEEDS ATTENTION** if:
- No reviews yet (waiting for review)
- Ambiguous feedback
- Mixed signals from reviewers

### 6. Report Results

Provide a clear, structured report:

```
## PR #[NUMBER]: [Title]

**Author**: [Author Name]
**Status**: [READY TO MERGE / NEEDS FIXES / NEEDS ATTENTION]

### Summary
[Brief description of the PR's purpose]

### Review Analysis
- Total Reviews: [count]
- Approvals: [count]
- Change Requests: [count]
- Comments: [count]

### Blocking Issues
[List any blocking issues, or "None" if clear]

### Recommendation
[Clear recommendation with reasoning]

### Next Steps
[If NEEDS FIXES: list specific actions required]
[If READY TO MERGE: suggest merge command or next steps]
```

### 7. Check for Additional PRs

After completing the report:
- Count remaining open PRs
- If more exist, inform the user:
  - "✓ Review complete. [N] more open PR(s) remaining. Run /CheckOpenPRs again to review the next one."
- If this was the last PR:
  - "✓ Review complete. No more open PRs to review."

## Important Notes

- **One PR at a time**: Never analyze multiple PRs in a single session
- **Numerical order**: Always process lowest PR number first
- **Fresh data**: Always fetch current data from GitHub, don't rely on cached information
- **Context matters**: Consider the repository's standards and conventions
- **Be thorough**: Read all comments and reviews, don't skip any feedback
- **Be practical**: Distinguish between blocking issues and nice-to-haves

## Error Handling

If errors occur:
- No open PRs: Report "No open PRs found in this repository"
- GitHub API failures: Show the error and suggest checking `gh auth status`
- Missing permissions: Inform user to check repository access rights
