# Stop Hooks Explained: Why They Don't Dispatch Agents (And How to Fix It)

## The Core Issue

**Stop hooks CANNOT directly dispatch agents.** They can only:
- ✅ Detect conditions (incomplete work, failed tests, etc.)
- ✅ Block Claude from stopping (exit code 2 or `"decision": "block"`)
- ✅ Provide continuation instructions via the `reason` field
- ❌ Execute tools or dispatch agents themselves

## Your Previous Hook Configuration (BROKEN)

Your global `~/.claude/settings.json` had Stop hooks using this **incorrect pattern**:

```json
{
  "decision": "approve",      // ← Allows Claude to STOP
  "continue": true,            // ← Not a real field
  "systemMessage": "Dispatch agent..."  // ← Never reaches Claude
}
```

**Why it failed:**
- `"decision": "approve"` lets Claude stop immediately
- The `systemMessage` never gets processed because the session ends
- Agents are never dispatched

## Correct Pattern: Block with Reason

```json
{
  "decision": "block",   // ← BLOCKS Claude from stopping
  "reason": "Code changes detected. Dispatch a Simplification subagent using the Task tool..."
}
```

The `reason` field becomes a system message that Claude receives and acts upon (though it still depends on Claude's interpretation).

## What I Fixed

### 1. Updated Global Stop Hooks (`~/.claude/settings.json`)

**Simplification Dispatcher (hook #2):**
- Changed from `"approve"` + `"systemMessage"` to `"block"` + `"reason"`
- Now correctly blocks stoppage when code changes are detected
- Instructs Claude to dispatch Simplification subagent

**Retrospective Dispatcher (hook #3):**
- Changed from `"approve"` + `"systemMessage"` to `"block"` + `"reason"`
- Now correctly blocks stoppage after substantial work sessions
- Instructs Claude to invoke retrospective skill

### 2. Created Project-Specific Stop Hook (`.claude/hooks/stop.sh`)

A comprehensive shell script that checks for:
- ✅ Pending todos in task list
- ✅ Ready bd (beads) issues
- ✅ Uncommitted git changes
- ✅ Failed CI checks on open PRs

**To use this hook:**

```bash
# Make it executable (required)
chmod +x .claude/hooks/stop.sh

# Test it manually
cat <<EOF | .claude/hooks/stop.sh
{"session_id": "test", "stop_hook_active": false}
EOF
```

The hook will:
- Exit 0 (allow stop) if all work is complete
- Exit 2 (block stop) with detailed continuation instructions if work remains

## How Stop Hooks Actually Work

### Execution Flow

1. Claude finishes responding and wants to stop
2. Stop hooks are invoked in order
3. Each hook receives session context via stdin (JSON)
4. Hook analyzes the situation and returns a decision

### Decision Types

| Exit Code | JSON Decision | Behavior |
|-----------|---------------|----------|
| `0` | `"approve"` | Allow Claude to stop |
| `2` | `"block"` | Prevent stop, continue with `reason` as instruction |

### Example: Incomplete Work Detection

```bash
#!/bin/bash
# Check for pending todos
PENDING=$(cat .claude/todos/*.json | jq '[.[] | select(.status != "completed")] | length')

if [ "$PENDING" -gt 0 ]; then
  # Block stoppage
  echo '{"decision": "block", "reason": "Continue working: You have '"$PENDING"' pending todos. Complete all tasks before stopping."}' >&2
  exit 2
else
  # Allow stoppage
  echo '{"decision": "approve"}' >&2
  exit 0
fi
```

## Important Limitations

### What Stop Hooks CAN Do

✅ **Detect conditions:** Read files, check git status, query APIs, analyze session state
✅ **Block stoppage:** Return exit 2 or `"decision": "block"`
✅ **Provide instructions:** The `reason` field tells Claude what to do next
✅ **Chain evaluations:** Multiple hooks can run in sequence

### What Stop Hooks CANNOT Do

❌ **Execute tools:** Cannot call Read, Write, Bash, Task, etc.
❌ **Dispatch agents:** Cannot invoke Task tool with subagent_type
❌ **Guarantee behavior:** Claude interprets the `reason` and decides how to proceed
❌ **Access tools directly:** Hooks run in a separate process from Claude's tool execution

### The Reality of Agent Dispatch

When a stop hook returns:
```json
{
  "decision": "block",
  "reason": "Dispatch a Simplification subagent using Task tool..."
}
```

What actually happens:
1. Claude receives the `reason` as a system message
2. Claude **interprets** it (not guaranteed to follow literally)
3. Claude **may** choose to dispatch the agent
4. Claude's decision depends on context, judgment, and understanding

**This is not a command—it's a suggestion.** Claude Code's LLM decides whether and how to act on it.

## Testing Your Updated Hooks

### Verify Global Hooks Work

1. Make some uncommitted changes:
   ```bash
   echo "test" > test_file.txt
   ```

2. Ask Claude to do a trivial task, then try to stop

3. The completion validator hook should block if work is incomplete

4. The simplification dispatcher should trigger after code changes

### Verify Project Hook Works

```bash
# Create a pending todo (simulate incomplete work)
mkdir -p .claude/todos
cat > .claude/todos/test.json <<EOF
[{"content": "Test task", "status": "pending", "activeForm": "Testing"}]
EOF

# Run the hook manually
cat <<'INPUT' | .claude/hooks/stop.sh
{"session_id": "test-session", "stop_hook_active": false, "transcript_path": "/tmp/test"}
INPUT
# Should output: {"decision": "block", ...} and exit 2

# Clean up
rm .claude/todos/test.json
```

## Best Practices

### 1. Be Explicit in `reason` Field

**Bad:**
```json
{"decision": "block", "reason": "Not done yet"}
```

**Good:**
```json
{
  "decision": "block",
  "reason": "Continue working: You have 3 pending todos (Fix tests, Update docs, Commit changes). Complete all tasks before stopping. Start with: Fix tests"
}
```

### 2. Respect `stop_hook_active` Flag

Always check `$STOP_HOOK_ACTIVE` to prevent infinite loops:

```bash
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  echo '{"decision": "approve"}' >&2
  exit 0
fi
```

### 3. Use Structured JSON Output

```bash
jq -n \
  --arg decision "block" \
  --arg reason "Detailed instructions here" \
  '{decision: $decision, reason: $reason}' >&2
```

### 4. Layer Your Hooks

- **Hook 1:** Validate completeness (scope, verification)
- **Hook 2:** Dispatch code review agents (simplification, testing)
- **Hook 3:** Trigger process analysis (retrospective)

Each hook has a single, focused responsibility.

### 5. Use Timeouts Appropriately

```json
{
  "type": "prompt",
  "prompt": "...",
  "timeout": 30  // 30 seconds for complex analysis
}
```

- Simple checks: 5-10 seconds
- LLM evaluation: 20-30 seconds
- Complex analysis: 30-60 seconds

## Alternatives for Agent Dispatch

Since stop hooks can't guarantee agent dispatch, consider:

### Option 1: SessionStart Hooks
Run agents when session **starts** instead of when it ends:

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "prompt",
        "prompt": "Check for incomplete work from previous session and dispatch agents to resume"
      }]
    }]
  }
}
```

### Option 2: Slash Commands
Create explicit commands for agent dispatch:

```bash
# .claude/commands/continue.md
Dispatch an Explore agent to check for incomplete work:
- Review bd ready issues
- Check pending todos
- Analyze uncommitted changes
- Continue the most important task
```

Usage: `/continue`

### Option 3: Skills
Auto-trigger based on conditions:

```yaml
# .claude/skills/auto-review/SKILL.md
---
trigger: on_commit
---
When code is committed, dispatch Simplification subagent to review changes.
```

### Option 4: Manual Workflow
Document when to dispatch agents in `AGENTS.md`:

```markdown
## Agent Dispatch Workflow

After completing a feature:
1. Run tests
2. Commit changes
3. Dispatch Simplification agent: `/reduce-complexity`
4. Dispatch Retrospective agent: `Skill(command='retrospective')`
5. Review and merge
```

## Summary

**The Fix:**
- ✅ Changed global Stop hooks from `"approve"` + `"systemMessage"` to `"block"` + `"reason"`
- ✅ Created project-specific `.claude/hooks/stop.sh` for incomplete work detection
- ✅ Hooks now correctly block stoppage and provide continuation instructions

**The Reality:**
- Stop hooks cannot dispatch agents directly
- They can only suggest Claude do so via the `reason` field
- Claude interprets and acts on the suggestion (not guaranteed)

**For Reliable Agent Dispatch:**
- Use SessionStart hooks (trigger on session start)
- Use slash commands (manual trigger)
- Use skills (condition-based trigger)
- Document workflow in AGENTS.md

**Next Steps:**
1. Test the updated hooks by making changes and asking Claude to stop
2. Observe if Claude now dispatches agents based on the `reason` field
3. If behavior is inconsistent, switch to SessionStart hooks or slash commands for more control
