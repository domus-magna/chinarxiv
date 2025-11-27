# Stop Hooks Quick Reference

## ❌ BROKEN Pattern (Your Old Hooks)

```json
{
  "decision": "approve",      // Lets Claude stop!
  "continue": true,            // Not a real field
  "systemMessage": "Dispatch agent..."  // Never reaches Claude
}
```

## ✅ CORRECT Pattern (Fixed)

```json
{
  "decision": "block",   // Blocks Claude from stopping
  "reason": "Code changes detected. Dispatch a Simplification subagent using Task tool to review all changes for complexity reduction."
}
```

## Stop Hook Decisions

| Return | Effect |
|--------|--------|
| `{"decision": "approve"}` | ✅ Allow Claude to stop |
| `{"decision": "block", "reason": "..."}` | 🚫 Block stop, continue with instructions |

## Critical Understanding

### What Hooks CAN Do
- ✅ Detect conditions (read files, check git, query APIs)
- ✅ Block Claude from stopping
- ✅ Suggest what Claude should do next (`reason` field)

### What Hooks CANNOT Do
- ❌ Execute tools directly
- ❌ Dispatch agents directly
- ❌ Guarantee Claude will follow the `reason` instruction

## Your Updated Hooks

### Global (`~/.claude/settings.json`)
1. **Completion Validator** - Blocks if work is incomplete
2. **Simplification Dispatcher** - Suggests dispatching review agent after code changes
3. **Retrospective Dispatcher** - Suggests running process analysis after work

### Project (`.claude/hooks/stop.sh`)
- Checks: pending todos, bd issues, uncommitted changes, failed CI
- Blocks if ANY incomplete work detected
- **Needs to be made executable:** `chmod +x .claude/hooks/stop.sh`

## The Harsh Reality

When hook returns:
```json
{"decision": "block", "reason": "Dispatch Simplification agent"}
```

What happens:
1. Claude receives `reason` as system message
2. Claude **interprets** it (may or may not follow)
3. Claude **decides** whether to dispatch agent
4. **No guarantee** Claude will do exactly what you asked

**Bottom line:** Stop hooks suggest behavior; they don't command it.

## Better Alternatives for Guaranteed Agent Dispatch

| Method | Control | Use Case |
|--------|---------|----------|
| **SessionStart hooks** | Moderate | Auto-resume work on session start |
| **Slash commands** | Full | Manual agent dispatch (`/continue`) |
| **Skills** | High | Condition-triggered agents |
| **Documented workflow** | Full | Manual process in AGENTS.md |

## Testing Checklist

- [ ] Make `.claude/hooks/stop.sh` executable
- [ ] Create uncommitted changes and ask Claude to stop
- [ ] Verify completion validator blocks stoppage
- [ ] Make code changes and observe simplification dispatcher
- [ ] Check if Claude actually dispatches agents as suggested

## Manual Setup Required

```bash
# From project root
cd /Users/alexanderhuth/chinaxiv-english

# Make hook executable
chmod +x .claude/hooks/stop.sh

# Test manually
cat <<'EOF' | .claude/hooks/stop.sh
{"session_id": "test", "stop_hook_active": false}
EOF
# Should exit 0 if no incomplete work

# Create test todo
mkdir -p .claude/todos
echo '[{"content": "Test", "status": "pending", "activeForm": "Testing"}]' > .claude/todos/test.json

# Test again
cat <<'EOF' | .claude/hooks/stop.sh
{"session_id": "test", "stop_hook_active": false}
EOF
# Should exit 2 (block) with reason

# Cleanup
rm .claude/todos/test.json
```

## Key Files

- **Global config:** `~/.claude/settings.json` (updated ✅)
- **Project hook:** `.claude/hooks/stop.sh` (created ✅, needs chmod)
- **Full explanation:** `.claude/STOP_HOOKS_EXPLAINED.md`
- **This reference:** `.claude/STOP_HOOKS_QUICK_REF.md`

## Summary

**Problem:** Hooks used `"approve"` + `"systemMessage"` pattern (broken)
**Solution:** Changed to `"block"` + `"reason"` pattern (correct)
**Limitation:** Hooks can only *suggest* agent dispatch, not *command* it
**For reliability:** Use SessionStart hooks, slash commands, or skills instead
