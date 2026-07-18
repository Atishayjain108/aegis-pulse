# AEGIS PULSE: PRODUCTION-READY EXECUTION FRAMEWORK
## One-Month Sprint to World-Class System (Phase Sequencing & Token-Optimized Audit)

---

## CORE EXECUTION MODEL

```
ARCHITECTURE:
1. Store comprehensive audit prompt in: /aegis_pulse/AUDIT_FRAMEWORK.md (reference only)
2. Create session-based execution tracker: /aegis_pulse/EXECUTION_STATE.json (Claude updates)
3. Run phases sequentially with state checkpoints
4. Claude makes PRODUCTION DECISIONS, not suggestions
5. Output: Live code fixes + implementation PRs
```

---

## EXECUTION SEQUENCE: ONE-MONTH SPRINT PLAN

### Timeline Overview:
```
WEEK 1: Foundation (Security + Architecture)
WEEK 2: Code Quality + Performance Baselines
WEEK 3: Production Hardening + Integration
WEEK 4: Scaling + Deployment Readiness + Documentation
```

---

## PHASE EXECUTION ORDER (PRODUCTION-PRIORITIZED)

This ordering ensures your system can stay operational while improvements are made:

### **PHASE 0: PRE-EXECUTION SETUP** (Day 1 - 4 hours)
**Why First:** Establish execution context, state tracking, and token optimization

**Actions:**
1. Initialize `/aegis_pulse/EXECUTION_STATE.json`:
   ```json
   {
     "audit_session_id": "AEGIS_PULSE_2026_Q2",
     "start_timestamp": "2026-05-24T00:00:00Z",
     "target_completion": "2026-06-24T23:59:59Z",
     "days_remaining": 31,
     "phases_completed": [],
     "phases_in_progress": [],
     "phases_pending": [],
     "blocking_issues": [],
     "production_hotfixes": [],
     "token_budget": 500000,
     "tokens_used": 0,
     "tokens_remaining": 500000,
     "decision_log": [],
     "code_changes_staged": [],
     "code_changes_committed": []
   }
   ```

2. Create `/aegis_pulse/PHASE_INSTRUCTIONS.md` (Claude's execution guide):
   ```
   ## Instructions to Claude for Running This Audit
   
   ### Session Model:
   - You have access to: AUDIT_FRAMEWORK.md, EXECUTION_STATE.json, project codebase
   - You will execute phases in specified order
   - After each phase: UPDATE EXECUTION_STATE.json with findings
   - NEVER SUGGEST — DECIDE AND IMPLEMENT
   - When uncertain: Ask for business context, then decide
   
   ### Token Optimization:
   - Read AUDIT_FRAMEWORK.md sections relevant to current phase only
   - Compress findings using JSON format (not verbose prose)
   - Reuse previous analysis (don't re-scan dependencies)
   - Batch small code fixes into single commits
   - Use code diffs, not full file rewrites
   
   ### Decision-Making Model:
   - RULE 1: Production Safety First (no breaking changes without approval)
   - RULE 2: Security > Performance > Code Quality
   - RULE 3: Implement fixes, don't explain them (vibe coding mode)
   - RULE 4: If ambiguous, choose the boring/safe solution
   - RULE 5: Track every decision in EXECUTION_STATE.json
   
   ### Output Format Per Phase:
   {
     "phase_id": "PHASE_N",
     "findings": [structured list],
     "blocking_issues": [issues preventing production],
     "code_changes": [git diffs],
     "estimated_impact": "HIGH/MEDIUM/LOW",
     "tokens_used_this_phase": number,
     "next_phase_recommendation": "PHASE_X"
   }
   ```

3. Structure project directory:
   ```bash
   /aegis_pulse/
   ├── AUDIT_FRAMEWORK.md (the god-tier prompt)
   ├── EXECUTION_STATE.json (session tracking)
   ├── PHASE_INSTRUCTIONS.md (this file)
   ├── src/
   ├── tests/
   ├── config/
   └── deployment/
   ```

---

### **PHASE 1: SECURITY AUDIT (CRITICAL)** (Day 1-2, 8 hours)
**Why Second:** Security vulnerabilities will block production deployment

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 2.1 Threat Model Analysis (STRIDE)
   - 2.2 Secret & Credential Management
   - 2.3 Input Validation
   - 2.4 Compliance & Audit Trail

2. Scan codebase for:
   - Hardcoded secrets/API keys
   - Unvalidated inputs (API endpoints, database queries, file uploads)
   - Missing auth checks
   - Unencrypted sensitive data
   - Audit logging gaps

3. **Decision Rules for This Phase:**
   ```
   IF secret found in code → REMOVE IMMEDIATELY (no ask)
   IF SQL injection vector → ADD parameterized query (no ask)
   IF missing HTTPS → ENFORCE TLS (no ask)
   IF unencrypted PII → ENCRYPT AT REST (no ask)
   
   IF unclear risk → ASK: "Is [service name] internet-facing?"
                    → ASK: "What's the business impact if compromised?"
   ```

4. **Output to EXECUTION_STATE.json:**
   ```json
   {
     "phase": "PHASE_1_SECURITY",
     "findings": {
       "critical": [
         {"type": "hardcoded_secret", "location": "src/api.py:42", "action": "REMOVED"},
         {"type": "sql_injection_vector", "location": "src/db.py:156", "action": "FIXED"}
       ],
       "high": [
         {"type": "missing_input_validation", "endpoint": "/api/trade", "action": "ADDED"}
       ],
       "medium": [...]
     },
     "code_changes": [
       "git diff src/api.py",
       "git diff src/db.py"
     ],
     "blocking_issues": [],
     "safe_to_continue": true
   }
   ```

5. **Ask Clarifying Questions (if needed):**
   - "Is AEGIS PULSE exposing APIs to internet? (If yes: stricter security)"
   - "Are you handling real money/market orders? (If yes: encryption + audit trails mandatory)"
   - "What's your compliance domain? (PCI? GDPR? FCA?)"

---

### **PHASE 2: ARCHITECTURE & DEPENDENCY AUDIT** (Day 2-3, 12 hours)
**Why Early:** Foundational understanding before code changes

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 1.1 Dependency Graph Analysis
   - 1.2 Phase Interconnection Mapping
   - 5.2 Microservices & Distributed Systems

2. Map complete dependency graph:
   ```
   - All imports (direct + transitive)
   - All external services (APIs, databases, message queues)
   - All configuration assumptions
   - All environment variable dependencies
   - All async/event dependencies
   ```

3. Create visual representation (Mermaid diagram):
   ```mermaid
   graph TD
     Phase1[Phase 1: Data Ingestion] --> Phase2[Phase 2: Signal Generation]
     Phase2 --> Phase3[Phase 3: Risk Analysis]
     Phase3 --> Phase4[Phase 4: Trade Decision]
     Phase1 -.-> Cache[(Redis Cache)]
     Phase2 -.-> DB[(PostgreSQL)]
     Phase4 -.-> Broker[Market Broker API]
   ```

4. **Decision Rules:**
   ```
   IF circular dependency found → REFACTOR to eliminate (specific steps)
   IF missing error handling → ADD try-except with recovery (specific steps)
   IF phase timeout undefined → SET conservative SLA (specific value)
   IF state management not explicit → DEFINE state machine (specific design)
   
   IF unclear phase contract → ASK: "What does Phase N expect as input?"
                             → ASK: "What does Phase N produce as output?"
                             → ASK: "What's the failure mode for Phase N?"
   ```

5. **Output:**
   ```json
   {
     "phase": "PHASE_2_ARCHITECTURE",
     "dependency_graph": {...},
     "phase_contracts": {
       "Phase_1": {"input": "...", "output": "...", "timeout_ms": 5000},
       "Phase_2": {...}
     },
     "orphaned_phases": [],
     "circular_dependencies": [],
     "missing_error_handling": [...],
     "code_changes": [...],
     "architecture_diagram": "mermaid-format"
   }
   ```

---

### **PHASE 3: PRODUCTION HOTFIX IDENTIFICATION** (Day 3, 4 hours)
**Why Critical Path:** Fix issues blocking current production operation

**Execution Steps:**
1. Identify and fix:
   - Memory leaks
   - Connection pooling exhaustion
   - Unhandled exceptions
   - Timeouts without recovery
   - Data loss risks
   - Race conditions

2. **Decision Rules:**
   ```
   IF memory growing unbounded → IMPLEMENT fix immediately (specify fix)
   IF connections not returned to pool → IMPLEMENT fix immediately
   IF exception swallowed without logging → ADD error handling immediately
   IF no timeout defined → SET reasonable timeout (specify value)
   IF data could be lost → ADD persistence (specify mechanism)
   IF race condition possible → ADD lock/mutex (specify protection)
   ```

3. **Output:**
   ```json
   {
     "phase": "PHASE_3_HOTFIXES",
     "production_issues_fixed": [
       {"severity": "CRITICAL", "issue": "...", "fix": "...", "tested": true}
     ],
     "code_changes": [...],
     "deployment_notes": "Can be deployed immediately without other changes"
   }
   ```

---

### **PHASE 4: CODE QUALITY AUDIT** (Day 4-5, 12 hours)
**Why After Security/Architecture:** Foundation is stable

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 3.1 Cyclomatic Complexity
   - 3.2 Code Smell Detection
   - 3.3 Testing Strategy
   - 3.4 Documentation

2. Scan for:
   - Duplicated code → Extract to functions
   - Long functions (>50 lines) → Break into smaller functions
   - Magic numbers/strings → Define constants
   - Dead code → Remove
   - Missing tests → Write tests
   - Missing documentation → Add docstrings

3. **Decision Rules:**
   ```
   IF duplicate code exists → EXTRACT to utility function (provide code)
   IF function > 50 lines → REFACTOR into smaller functions (provide code)
   IF magic number → EXTRACT as constant with semantic name (provide code)
   IF test coverage < 70% on critical paths → WRITE tests (provide code)
   IF documentation missing → ADD docstring (provide code)
   
   IF refactoring risky → ASK: "Should we add tests first?"
   IF unclear function purpose → ASK: "What is this function supposed to do?"
   ```

4. **Output:**
   ```json
   {
     "phase": "PHASE_4_CODE_QUALITY",
     "complexity_analysis": {
       "high_complexity_functions": [...],
       "refactoring_recommendations": [...]
     },
     "code_smells": [...],
     "test_coverage": {"current": 45, "target": 85},
     "code_changes": [...],
     "estimated_effort_hours": 16
   }
   ```

---

### **PHASE 5: PERFORMANCE AUDIT & OPTIMIZATION** (Day 6-7, 16 hours)
**Why After Code Quality:** Cleaner code optimizes faster

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 4.1 Bottleneck Identification
   - 4.2 Scalability Analysis
   - 9.1-9.3 Advanced Optimization

2. Profile critical paths:
   - Database queries (N+1? missing indexes?)
   - API calls (sequential? should be async?)
   - Data processing (algorithm complexity? caching?)
   - Memory usage (leaks? unnecessary allocations?)

3. **Decision Rules:**
   ```
   IF N+1 query pattern found → BATCH queries (provide SQL)
   IF sequential I/O → IMPLEMENT async/await (provide code)
   IF missing index on frequently-queried column → ADD index (provide SQL)
   IF expensive operation not cached → IMPLEMENT caching (specify mechanism)
   IF algorithm is O(n²) when O(n log n) is possible → OPTIMIZE (provide algorithm)
   
   IF optimization has tradeoffs → ASK: "Is latency or throughput the priority?"
   IF caching invalidation unclear → ASK: "How stale can this data be?"
   ```

4. **Output:**
   ```json
   {
     "phase": "PHASE_5_PERFORMANCE",
     "bottlenecks": [
       {"location": "src/db.py:query_trades", "current_time": "2500ms", "optimization": "...", "expected_time": "250ms"}
     ],
     "code_changes": [...],
     "expected_improvement": "3x throughput increase, 5x latency reduction"
   }
   ```

---

### **PHASE 6: OBSERVABILITY & MONITORING** (Day 7-8, 8 hours)
**Why Before Deployment:** Need visibility in production

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 4.3 Observability & Monitoring
   - 8.2 Incident Management

2. Implement:
   - Structured logging (JSON with trace IDs)
   - Metrics (RED: Request Rate, Error Rate, Duration)
   - Traces (distributed tracing for phase calls)
   - Alerts (for critical thresholds)
   - Dashboards (operational view)

3. **Decision Rules:**
   ```
   IF no structured logging → IMPLEMENT immediately (provide code)
   IF no trace ID propagation → ADD trace ID tracking (provide code)
   IF no metrics collection → ADD Prometheus metrics (provide code)
   IF no critical alerts → ADD alerts for: errors, latency, resource usage (specify thresholds)
   
   IF unclear metrics → ASK: "What's the SLA for Phase N?"
   IF alert thresholds unclear → ASK: "At what latency should we page on-call?"
   ```

4. **Output:**
   ```json
   {
     "phase": "PHASE_6_OBSERVABILITY",
     "logging": {"structured": true, "sample_format": {...}},
     "metrics": ["request_rate", "error_rate", "phase_duration"],
     "tracing": {"tool": "OpenTelemetry", "sampling_rate": 0.1},
     "alerts": [
       {"metric": "error_rate", "threshold": "> 1%", "severity": "CRITICAL"}
     ],
     "code_changes": [...]
   }
   ```

---

### **PHASE 7: RESILIENCE & ERROR HANDLING** (Day 8-9, 12 hours)
**Why Before Deployment:** Need graceful failure modes

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 6.1 Failure Mode Analysis
   - 6.2 Graceful Degradation
   - 6.3 Health Check & Self-Healing

2. Implement:
   - Timeout handling (per operation)
   - Retry logic (exponential backoff, max retries)
   - Circuit breaker (for external services)
   - Graceful degradation (reduced functionality)
   - Health checks (liveness, readiness)
   - Self-healing (auto-restart, reconnect)

3. **Decision Rules:**
   ```
   IF no timeout defined → SET reasonable timeout (specify value in ms)
   IF no retry logic → ADD exponential backoff (specify max retries)
   IF no circuit breaker → IMPLEMENT circuit breaker (specify open/close thresholds)
   IF failure causes cascade → ADD bulkhead isolation (specify partition strategy)
   IF no health checks → ADD liveness + readiness checks (specify logic)
   
   IF unclear failure mode → ASK: "What's the worst thing that can happen?"
                           → ASK: "Can we recover automatically?"
   ```

4. **Output:**
   ```json
   {
     "phase": "PHASE_7_RESILIENCE",
     "failure_modes": [
       {"scenario": "External API timeout", "current_handling": "crash", "new_handling": "retry with backoff + circuit breaker"}
     ],
     "timeouts": [
       {"operation": "query_trades", "timeout_ms": 5000, "reasoning": "DB max expected latency"}
     ],
     "code_changes": [...],
     "tested_failure_scenarios": [...]
   }
   ```

---

### **PHASE 8: PRODUCTION DEPLOYMENT SETUP** (Day 9-10, 8 hours)
**Why Before Integration:** Infrastructure must be ready

**Execution Steps:**
1. Read AUDIT_FRAMEWORK.md sections:
   - 8.1 Deployment Strategy
   - 8.3 Change Management

2. Set up:
   - Docker/container configuration
   - Kubernetes manifests (if applicable)
   - Environment configuration management
   - CI/CD pipeline
   - Rollback procedures
   - Change log

3. **Decision Rules:**
   ```
   IF no Docker image → CREATE Dockerfile (provide code)
   IF no K8s manifests → CREATE deployment YAML (provide code)
   IF no environment management → IMPLEMENT (specify tool: env vars, ConfigMap, etc.)
   IF no CI/CD → SETUP (GitHub Actions, GitLab CI, etc. - specify config)
   IF no rollback procedure → DOCUMENT procedure (specific steps)
   
   IF deployment strategy unclear → ASK: "Can we do rolling updates?"
                                  → ASK: "Do we need canary deployments?"
   ```

4. **Output:**
   ```json
   {
     "phase": "PHASE_8_DEPLOYMENT",
     "containerization": {"dockerfile": "provided", "image_size": "XXX MB"},
     "orchestration": {"kubernetes": true, "manifests_created": [...]},
     "ci_cd": {"platform": "GitHub Actions", "pipeline": "provided"},
     "deployment_procedure": "rolling update, 30s health check grace period",
     "rollback_procedure": "documented",
     "code_changes": [...]
   }
   ```

---

### **PHASE 9: INTEGRATION TESTING & VALIDATION** (Day 10-12, 16 hours)
**Why Before Full Release:** Catch issues in staging

**Execution Steps:**
1. Create integration tests:
   - Phase-to-phase contracts
   - External API mocks
   - Database integration
   - Error scenarios
   - Load scenarios

2. **Decision Rules:**
   ```
   IF integration test missing → WRITE integration test (provide code)
   IF test failure not understood → INVESTIGATE root cause (fix code)
   IF load test shows degradation → OPTIMIZE (identify bottleneck)
   
   IF test infrastructure missing → ASK: "Can we use existing test DB?"
   ```

3. **Output:**
   ```json
   {
     "phase": "PHASE_9_INTEGRATION",
     "test_coverage": {"integration": 85, "e2e": 60},
     "test_results": {"passing": 156, "failing": 0},
     "load_test": {"throughput": "XXX req/sec", "latency_p99": "YYY ms"},
     "code_changes": [...]
   }
   ```

---

### **PHASE 10: SECURITY HARDENING (FINAL)** (Day 12-13, 8 hours)
**Why Late: Confirms all previous changes don't introduce vulnerabilities**

**Execution Steps:**
1. Run security scans:
   - Dependency vulnerability scan (pip-audit)
   - SAST scan (bandit, semgrep)
   - Secrets scanning
   - Network security review
   - Access control verification

2. **Decision Rules:**
   ```
   IF CVE found in dependency → UPGRADE dependency or find alternative (provide code)
   IF security vulnerability in code → FIX immediately (provide code)
   IF secret found → REMOVE immediately (no ask)
   ```

3. **Output:**
   ```json
   {
     "phase": "PHASE_10_SECURITY_FINAL",
     "vulnerability_scan": {"high": 0, "medium": 0, "low": 2},
     "sast_results": {"issues_found": 0},
     "secrets_scan": {"secrets_found": 0},
     "code_changes": [...],
     "ready_for_production": true
   }
   ```

---

### **PHASE 11: DOCUMENTATION & KNOWLEDGE TRANSFER** (Day 13-14, 8 hours)
**Why Late: Document decisions made, not theoretical architecture**

**Execution Steps:**
1. Create:
   - Architecture decision records (ADRs)
   - Deployment runbook
   - Troubleshooting guide
   - API documentation
   - Phase documentation (inputs/outputs/contracts)
   - On-call runbook

2. **Decision Rules:**
   ```
   IF decision not documented → DOCUMENT with rationale
   IF runbook missing step → ADD step with screenshots/examples
   IF API not documented → ADD OpenAPI/swagger documentation
   ```

3. **Output:**
   ```json
   {
     "phase": "PHASE_11_DOCUMENTATION",
     "documentation_created": [
       "ARCHITECTURE.md",
       "DEPLOYMENT_RUNBOOK.md",
       "TROUBLESHOOTING.md",
       "API_DOCS.md"
     ],
     "code_changes": [...]
   }
   ```

---

### **PHASE 12: PRODUCTION RELEASE & MONITORING** (Day 14-31, ongoing)
**Why Last: Only after everything is proven**

**Execution Steps:**
1. Production deployment
2. Real-time monitoring
3. On-call alerting
4. Incident response
5. Optimization based on prod metrics

---

## PHASE DEPENDENCY GRAPH

```
PHASE 0 (Setup)
    ↓
PHASE 1 (Security) ← BLOCKING (must pass before others)
    ↓
PHASE 2 (Architecture) ← BLOCKING (must understand structure)
    ↓
PHASE 3 (Hotfixes) ← CAN RUN IN PARALLEL with Phases 4-5
    ├→ PHASE 4 (Code Quality)
    ├→ PHASE 5 (Performance)
    ├→ PHASE 6 (Observability)
    └→ PHASE 7 (Resilience)
    ↓
PHASE 8 (Deployment) ← DEPENDS ON all above
    ↓
PHASE 9 (Integration Testing) ← DEPENDS ON Phase 8
    ↓
PHASE 10 (Security Final) ← FINAL VALIDATION
    ↓
PHASE 11 (Documentation) ← DOCUMENT before release
    ↓
PHASE 12 (Production) ← LAUNCH
```

---

## TOKEN OPTIMIZATION STRATEGY

### Optimized Workflow:

```
INIT SESSION:
1. Load /AUDIT_FRAMEWORK.md once (entire file)
2. Create EXECUTION_STATE.json tracking
3. Set token budget: 500K tokens for entire month

PER PHASE:
1. Read only relevant sections of AUDIT_FRAMEWORK.md
2. Output findings as JSON (not prose)
3. Update EXECUTION_STATE.json
4. Provide code diffs only (not full files)
5. Batch similar fixes together

TOKEN BUDGET PER PHASE:
- Phase 0: 2K tokens (setup)
- Phase 1: 15K tokens (security scan + fixes)
- Phase 2: 20K tokens (architecture mapping)
- Phase 3: 10K tokens (hotfixes)
- Phase 4: 18K tokens (code quality)
- Phase 5: 25K tokens (performance optimization)
- Phase 6: 12K tokens (observability)
- Phase 7: 15K tokens (resilience)
- Phase 8: 10K tokens (deployment)
- Phase 9: 20K tokens (integration testing)
- Phase 10: 10K tokens (security final)
- Phase 11: 12K tokens (documentation)
- Phase 12: Unlimited (monitoring/optimization)
- Buffer: 31K tokens (unexpected issues)

TOTAL BUDGET: 220K tokens (leaves 280K for debugging/revisions)
```

### Token-Saving Techniques:

1. **Compress findings:**
   ```json
   // DON'T DO THIS (verbose):
   "findings": "We found that the database queries are not using indices..."
   
   // DO THIS (compact):
   "findings": [{"type": "missing_index", "table": "trades", "column": "user_id"}]
   ```

2. **Reuse previous analysis:**
   ```json
   "dependency_graph": "see EXECUTION_STATE.json for Phase 2 output - do not re-scan"
   ```

3. **Use diffs, not full files:**
   ```diff
   // Provide only changes
   - secret = "hardcoded_key_123"
   + secret = os.getenv("API_KEY")
   ```

4. **Batch similar changes:**
   ```
   // Instead of 5 separate commits:
   "batch_commit": "Add error handling to 12 functions"
   "files_changed": ["api.py", "db.py", "cache.py"]
   ```

---

## VIBE CODER DECISION-MAKING MODEL

### Core Principle:
**Claude does NOT suggest. Claude DECIDES and IMPLEMENTS.**

### Decision Framework:

```
WHEN ENCOUNTERING A PROBLEM:

Step 1: Classify Risk Level
├─ CRITICAL (production broken, security vuln, data loss)
├─ HIGH (degraded performance, architectural issue)
├─ MEDIUM (code quality, testing gap)
└─ LOW (documentation, nice-to-have optimization)

Step 2: Apply Decision Rules
├─ If CRITICAL → FIX IMMEDIATELY (no permission needed)
├─ If HIGH → DECIDE and IMPLEMENT (ask for context if ambiguous)
├─ If MEDIUM → DECIDE and IMPLEMENT (safe to refactor)
└─ If LOW → DECIDE and BATCH (group similar changes)

Step 3: When UNCERTAIN
├─ ASK 1-2 clarifying questions (not 10)
├─ ASSUME conservative/safe approach
├─ IMPLEMENT immediately after answer
├─ NO "here are 3 options" (vibe coding: make the call)

Step 4: NEVER Do This
├─ Don't ask "should I fix this?" (YES)
├─ Don't provide 3 code options (pick best one)
├─ Don't explain happy path only (handle errors)
├─ Don't leave TODOs (implement now)
```

### Vibe Coding Checklist (Before Submitting Code):

```
Every code change MUST have:
☑ Input validation
☑ Error handling (try/except or Result type)
☑ Logging (what happened, why, for debugging)
☑ Tests (unit + integration if possible)
☑ Backwards compatibility (or explicit migration guide)
☑ Documentation (docstring with example)
☑ No hardcoded values (constants/env vars)
☑ No TODOs (implement or create ticket)

If ANY of above missing → REVISIT code before submitting
```

---

## EXECUTION SESSION STRUCTURE

### How Claude Will Execute:

```
STEP 1: Read /AEGIS_PULSE_EXECUTION_FRAMEWORK.md (this file)
STEP 2: Load /AUDIT_FRAMEWORK.md (god-tier prompt)
STEP 3: Initialize EXECUTION_STATE.json with session tracking
STEP 4: Start with PHASE 0

FOR EACH PHASE:
  1. Read relevant sections of AUDIT_FRAMEWORK.md
  2. Scan codebase for issues
  3. Apply decision rules (DECIDE, don't suggest)
  4. Generate code fixes (diffs, not full files)
  5. Write tests for changes
  6. Update EXECUTION_STATE.json
  7. Log findings and decisions
  8. Move to next phase OR ask for context

AFTER EACH PHASE:
  - Verify code compiles/runs
  - Run tests
  - Update tracking file
  - Log tokens used
  - Calculate remaining time/tokens
  - Recommend next actions

EXCEPTION HANDLING:
  - If blocking issue found → PAUSE phase, escalate in EXECUTION_STATE.json
  - If code change causes test failure → INVESTIGATE root cause, FIX immediately
  - If tokens running low → SUMMARIZE findings, PRIORITIZE remaining work
  - If timeline at risk → COMPRESS remaining phases, FOCUS on critical path
```

---

## DAILY STANDUP CHECKLIST (For You)

Each morning, review:

```
□ What phase is Claude currently executing?
□ What's the token usage so far? (should be < daily_budget)
□ Are there any BLOCKING_ISSUES in EXECUTION_STATE.json?
□ How many code changes are staged? Are they ready to merge?
□ What's the status for next phase? (ready to start?)
□ Do I need to provide clarification for any questions?
□ Calculate: (days_remaining / phases_remaining) >= 0.5? (if no: compress)
```

---

## CRITICAL SUCCESS FACTORS

### For This to Work:

1. **File Organization:**
   - ✅ Store audit prompt in: `/aegis_pulse/AUDIT_FRAMEWORK.md`
   - ✅ Session tracking in: `/aegis_pulse/EXECUTION_STATE.json`
   - ✅ Execution guide in: `/aegis_pulse/PHASE_INSTRUCTIONS.md` (this file)

2. **Claude Instructions:**
   - ✅ Read execution file completely before starting
   - ✅ Execute phases in specified order
   - ✅ Make decisions (don't suggest)
   - ✅ Update session state after each phase
   - ✅ Provide code diffs (not prose explanations)

3. **Your Role (Minimal):**
   - ✅ Point Claude to `/aegis_pulse/PHASE_INSTRUCTIONS.md`
   - ✅ Answer clarifying questions (1-2 per phase)
   - ✅ Review staged code changes
   - ✅ Approve commits
   - ✅ Monitor EXECUTION_STATE.json for blockers
   - ✅ Provide business context when asked

4. **Weekly Reviews:**
   - ✅ Check phase progress
   - ✅ Validate token budget tracking
   - ✅ Approve production changes
   - ✅ Adjust timeline if needed

---

## WHAT TO SAY TO CLAUDE

When starting the audit session, say:

```
Hi Claude, I want you to execute the AEGIS PULSE codebase audit using this framework:

1. Read /aegis_pulse/PHASE_INSTRUCTIONS.md completely first
2. Read /aegis_pulse/AUDIT_FRAMEWORK.md for reference
3. Execute phases in order (1 → 2 → ... → 12)
4. For EACH phase:
   - Scan codebase for issues
   - Apply decision rules (DECIDE, don't suggest)
   - Generate code fixes with tests
   - Update /aegis_pulse/EXECUTION_STATE.json
   - Output findings as JSON (compact, not verbose)
5. Use diffs for code changes, not full file rewrites
6. When uncertain, ask 1-2 clarifying questions, then decide
7. Track token usage strictly

My context: [describe AEGIS PULSE briefly]
- Business purpose: [what it does]
- Current state: [where it is in development]
- Main constraints: [1 month timeline, production-critical, etc.]
- Key blockers: [if any]

Ready? Start with PHASE 0.
```

---

## FINAL CHECKLIST BEFORE STARTING

```
PRE-EXECUTION CHECKLIST:

Repository Setup:
☑ AEGIS PULSE code organized in /aegis_pulse/
☑ Git repository initialized (or will Claude create commits?)
☑ Python version documented (3.8+? 3.11?)
☑ Dependencies in requirements.txt or pyproject.toml
☑ Environment variables documented
☑ Can Claude write files to your project directory?

Infrastructure Access:
☑ Database accessible for testing?
☑ External APIs mockable/testable?
☑ CI/CD pipeline set up?
☑ Deployment target identified?

Business Context Ready:
☑ What's the #1 business outcome? (profit? speed? reliability?)
☑ What failure modes are unacceptable?
☑ What's your deployment frequency? (weekly? monthly?)
☑ Who are the stakeholders for each phase?

Timeline Reality Check:
☑ 31 days is tight. Are you prepared to focus fully?
☑ Can code changes go to production daily? (or batched weekly?)
☑ Who reviews/approves code changes?
☑ Who can answer clarifying questions quickly?

If ALL CHECKED: You're ready to start.
```

---

## EXPECTED OUTCOME (Day 31)

```
AEGIS_PULSE_DELIVERED:
├── Security
│   ├── ✅ Zero hardcoded secrets
│   ├── ✅ All inputs validated
│   ├── ✅ All data encrypted at rest
│   ├── ✅ Audit trail complete
│   └── ✅ Compliance verified
├── Architecture
│   ├── ✅ Dependency graph clean
│   ├── ✅ Phases well-integrated
│   ├── ✅ Async/await properly used
│   └── ✅ Error handling comprehensive
├── Code Quality
│   ├── ✅ Test coverage 85%+
│   ├── ✅ Cyclomatic complexity < 8
│   ├── ✅ Zero code smells
│   └── ✅ Full documentation
├── Performance
│   ├── ✅ Critical paths optimized
│   ├── ✅ Database queries optimized
│   ├── ✅ Caching implemented
│   └── ✅ Load tested
├── Resilience
│   ├── ✅ Timeout handling complete
│   ├── ✅ Retry logic with backoff
│   ├── ✅ Circuit breakers in place
│   └── ✅ Health checks running
├── Operations
│   ├── ✅ Containerized
│   ├── ✅ CI/CD working
│   ├── ✅ Monitoring active
│   ├── ✅ Alerts configured
│   └── ✅ Runbooks documented
└── Deployment
    ├── ✅ Rolling updates working
    ├── ✅ Rollback procedure tested
    ├── ✅ Change log maintained
    └── ✅ Ready for production

METRICS:
- Security Issues Fixed: N
- Code Quality Improvements: M
- Performance Gains: X%
- Test Coverage: 85%+
- Deployment Ready: YES
```

---

## TROUBLESHOOTING IF THINGS GO WRONG

### If Claude gets stuck on a phase:

```
Intervene with: "Claude, I'm unblocking Phase X. [context]. Continue."
Example: "Claude, the database is PostgreSQL 13.4. Continue Phase 5 optimization."
```

### If token budget running low:

```
Action: "Claude, compress remaining phases. Focus on: security + deployment readiness. Skip nice-to-haves."
```

### If timeline slipping:

```
Action: "Claude, reprioritize. Phases to complete: 0,1,2,3,8,10,12. Defer: 4,5,6,7,11."
```

### If code changes cause production issues:

```
Rollback: "Revert latest 3 commits. Identify root cause. Re-test before re-applying."
```

---

## SUCCESS NARRATIVE

By Day 31, you will have:

1. **Security Hardened:** All secrets managed, inputs validated, data encrypted
2. **Architecture Clean:** Dependencies mapped, phases well-integrated, errors handled
3. **Code Quality High:** Tests covering critical paths, complexity managed, documented
4. **Performance Optimized:** Bottlenecks removed, caching implemented, load-tested
5. **Operationally Ready:** Containerized, monitored, alerting, runbooks written
6. **Production Deployable:** Rolling updates, rollback procedures, change log, zero blocker issues

**Result: AEGIS PULSE becomes a world-class, production-ready autonomous trading intelligence system.**

---

**End of PHASE_INSTRUCTIONS.md**

Store this file at: `/aegis_pulse/PHASE_INSTRUCTIONS.md`

Then tell Claude: "Read /aegis_pulse/PHASE_INSTRUCTIONS.md and execute."