# Daily Health Module — Design Spec

**Date:** 2026-04-10
**Approach:** Pure Skill (Approach A) — skill + scripts + cron jobs
**Scope:** MVP (Phase 1 from brief)

---

## Context

Aryan needs a daily health coach via Telegram that pulls WHOOP data, sends scheduled check-ins, logs habits conversationally, and catches disengagement early ("never miss twice"). The module is a Hermes skill — no custom tool registration, no standalone daemon.

**Key behavioral insight:** Aryan builds systems during moments of clarity, follows intensely, then collapses on single disruption. The agent must be low-friction (explicit trigger, not always-on), zero-judgment on slips, and operationalize "never miss twice" rather than just noting it.

---

## File Structure

```
skills/health/daily-health/
├── SKILL.md                    # Skill definition, trigger keywords, agent instructions
├── scripts/
│   └── health_log.py          # SQLite-backed health event logging + queries
└── references/
    └── aryan-health-profile.md # Condensed health profile for system prompt context

~/.hermes/health/
└── health.db                  # SQLite database for health events
```

---

## SKILL.md

**Frontmatter:**
```yaml
name: daily-health
description: Daily health coach — WHOOP check-ins, habit logging, melatonin reminders, never-miss-twice detection
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [Health, WHOOP, Habits, Telegram]
    related_skills: []
triggers:
  - /health
  - health check
  - log health
```

**Body sections:**
1. **Context** — Aryan's health conditions (hypothyroidism, lichen planus, asthma, recently quit smoking, deconditioned cardio), current phase (Recovery → Phase 1: fix sleep, walk daily, supplements, don't smoke), behavioral patterns (video game mindset, identity-based framing)
2. **Morning Check-In Instructions** — How to call WHOOP MCP tools (`mcp_whoop_get_recoveries`, `mcp_whoop_get_sleeps`), format the summary (recovery, HRV, RHR, SpO2, sleep duration/stages), suggest 3 daily tasks from current phase, ask "did you smoke?"
3. **Evening Reminder Instructions** — Melatonin message format, mood 1-5 rating request
4. **Habit Parsing Guide** — Natural language patterns: walks ("walked 25 min"), meals ("had chicken rice"), supplements ("took supplements"), smoking ("smoked a cigarette"), nebulizing, weight, symptoms. How to call `health_log.py` for each.
5. **Tone Rules** — Direct not preachy. Zero judgment on slips (respond: "Noted. One slip doesn't undo your progress. Don't buy a pack. Tomorrow is a new day."). Brief Telegram-scannable messages. Identity-based framing. Data-driven (reference actual WHOOP numbers).
6. **Never Miss Twice** — When script reports >48h since last interaction, append nudge: "Hey — haven't heard from you in 2 days. Everything okay? Remember: missing once is fine. Missing twice starts a new habit. Even just saying 'alive' counts as checking in."

---

## Data Storage: SQLite

**File:** `~/.hermes/health/health.db`

**Schema:**
```sql
CREATE TABLE health_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,          -- ISO 8601 timestamp (timezone-aware)
    type TEXT NOT NULL,        -- checkin, response, habit, evening, symptom
    subtype TEXT,              -- walk, meal, supplement, smoke, nebulize, weight
    value REAL,                -- 25 (minutes), 4 (mood), 71.2 (kg)
    unit TEXT,                 -- min, kg, scale
    data TEXT,                 -- JSON blob for structured data (WHOOP metrics, meal details)
    note TEXT                  -- free text (symptom notes, meal descriptions)
);
CREATE INDEX idx_ts ON health_events(ts);
CREATE INDEX idx_type ON health_events(type);
CREATE INDEX idx_type_ts ON health_events(type, ts);
```

**Why SQLite over JSONL:**
- Concurrent-safe (cron jobs + user interaction won't corrupt)
- Atomic writes (no half-written records)
- Instant indexed queries (last interaction, today's events, 7-day range)
- Hermes already uses SQLite (hermes_state.py) — known pattern

**Event types:**
| type | subtype | value | data | note |
|------|---------|-------|------|------|
| checkin | — | — | `{"recovery":95,"hrv":94.8,...}` | — |
| response | — | — | `{"smoked":false,"supplements":true}` | — |
| habit | walk | 25 | — | "evening walk, cold but manageable" |
| habit | meal | — | `{"protein_est":45}` | "chicken rice eggs" |
| habit | supplement | — | — | — |
| habit | smoke | — | — | — |
| habit | nebulize | — | — | — |
| habit | weight | 71.2 | — | — |
| habit | sighing | 5 | min | "cyclic sighing" |
| evening | — | 4 | — | "feeling better than yesterday" |
| symptom | — | — | — | "wheeze less on forced exhale" |

---

## health_log.py CLI

**Location:** `skills/health/daily-health/scripts/health_log.py`

**Commands:**

```bash
# Logging
health_log.py log --type habit --subtype walk --value 25 --unit min
health_log.py log --type habit --subtype meal --note "chicken rice eggs" --data '{"protein_est":45}'
health_log.py log --type response --data '{"smoked":false,"supplements":true}'
health_log.py log --type checkin --data '{"recovery":95,"hrv":94.8,"rhr":54,"spo2":94.5,"sleep_hours":10.18,"deep_min":166,"rem_min":164}'
health_log.py log --type symptom --note "wheeze less on forced exhale"
health_log.py log --type evening --value 4 --note "good day"

# Queries
health_log.py query --last-interaction          # ISO timestamp of most recent event
health_log.py query --today                      # All events for today (JSON array)
health_log.py query --check-morning-response     # true/false: did today's checkin get a response?
health_log.py query --range 7d                   # All events from last 7 days (for weekly review)
```

**Implementation details:**
- Uses Python `sqlite3` (stdlib, zero deps)
- DB path: `~/.hermes/health/health.db` (auto-creates directory + tables on first run)
- All timestamps stored as ISO 8601 with timezone
- JSON output for queries (agent parses it)
- Exit code 0 on success, 1 on error

---

## Cron Jobs

Three jobs in `~/.hermes/cron/jobs.json`:

### Job 1: Morning Check-In
```json
{
  "name": "health-morning-checkin",
  "schedule": "30 10 * * *",
  "skills": ["daily-health"],
  "prompt": "Run the morning health check-in. Pull last night's WHOOP data via mcp_whoop_get_recoveries and mcp_whoop_get_sleeps. Format as a brief Telegram summary with recovery, HRV, RHR, SpO2, sleep duration and stages. Add 3 daily tasks from current phase priorities. Ask 'Did you smoke yesterday? (yes/no)'. Log the WHOOP data via health_log.py. If the script output shows last interaction was >48h ago, append the never-miss-twice nudge.",
  "deliver": "telegram",
  "script": "skills/health/daily-health/scripts/health_log.py query --last-interaction"
}
```

### Job 2: Evening Reminder
```json
{
  "name": "health-evening-reminder",
  "schedule": "30 19 * * *",
  "skills": ["daily-health"],
  "prompt": "Send the evening melatonin reminder: 'Melatonin time. Take half a tab now. Blue light glasses on in an hour. How was today? (1-5 scale or a few words)'",
  "deliver": "telegram"
}
```

### Job 3: Noon Supplement Nudge (Conditional)
```json
{
  "name": "health-noon-nudge",
  "schedule": "0 12 * * *",
  "skills": ["daily-health"],
  "prompt": "Check if the morning check-in was responded to today. The script output tells you. If it was responded to, output exactly '[SILENT]' to suppress delivery. If no response yet, send: 'Hey — did you take your supplements? Just checking.'",
  "deliver": "telegram",
  "script": "skills/health/daily-health/scripts/health_log.py query --check-morning-response"
}
```

**Timezone note:** Cron expressions are in system local time (ET for Aryan). If system timezone changes, job times shift.

---

## WHOOP MCP Configuration

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  whoop:
    command: /Users/aryanbhatia/Documents/0DevProjects/mcp-hub/.venv/bin/mcp-hub
    args: ["serve", "-s", "whoop", "--transport", "stdio"]
    timeout: 120
    connect_timeout: 60
```

**Tools available to agent:**
- `mcp_whoop_get_recoveries` — recovery score, HRV, RHR, SpO2, skin temp
- `mcp_whoop_get_sleeps` — sleep stages, duration, efficiency, respiratory rate
- `mcp_whoop_get_cycles` — daily strain, calories
- `mcp_whoop_get_workouts` — workout details (future use)
- `mcp_whoop_get_profile` — user profile

**Auth:** mcp-hub reads tokens from `~/.config/mcp-hub/whoop_tokens.json`, auto-refreshes OAuth. No agent-side auth needed.

---

## references/aryan-health-profile.md

Condensed from the full health research. Contains:
- Age, height, weight, location
- Active conditions (hypothyroidism, lichen planus, asthma, post-smoking recovery)
- Current phase (Recovery → Phase 1) and daily priorities
- Supplement stack (condensed list, not full research)
- Medications (Oracort 0.1%, Budecort nebulizer)
- Behavioral notes (video game mindset, identity framing, never miss twice)

**Not included:** Full research files, session logs, predictive models — those stay in the aryan-health project. The skill only carries what the agent needs for daily interactions.

---

## Verification Plan

1. **health_log.py unit test** — Create temp SQLite DB, test all log/query commands, verify concurrent writes via threading
2. **Skill load test** — Run `hermes` with skill loaded, send test messages ("walked 20 min", "smoked", "weight 71.2"), verify events persist in DB
3. **WHOOP MCP test** — Verify mcp-hub spawns from Hermes config, agent can call `mcp_whoop_get_recoveries` and get real data
4. **Cron dry run** — Create morning check-in job, trigger manually (`hermes cron run <job-id>`), verify Telegram message arrives with WHOOP data
5. **Evening reminder** — Manual trigger, verify melatonin message arrives
6. **Noon nudge conditional** — Trigger without morning response → nudge sent. Trigger after morning response → "SKIP" (no message)
7. **Never miss twice** — Insert stale last-interaction timestamp (>48h), trigger morning cron, verify nudge appended to message
8. **End-to-end** — Let it run for a full day cycle: morning check-in → respond → log habits → evening reminder → verify DB has all events
