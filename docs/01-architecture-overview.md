# 01. Architecture Overview: Hermes Engine & 3-Tier Memory

## 1. Executive Summary & Design Philosophy

Modern autonomous AI coding agents face three critical architectural pitfalls:
1. **Context Pollution & Prefix Cache Invalidation**: Naive runtime memory injections mutate prompts mid-turn, destroying key-value (KV) cache reuse and blowing up token costs.
2. **Memory Drift & Hallucinatory Overwriting**: Unrestricted agent self-updates frequently overwrite or dilute foundational user preferences and system constraints.
3. **Runaway Latency on Session Termination**: Complex post-session summarization and reflection pipelines can hang terminal processes, blocking developer workflows.

**Hermes Engine** solves these structural flaws through a **decoupled, provenance-guarded 3-tier memory and reflection architecture**. It separates high-speed interactive user loops from asynchronous out-of-band distillation while enforcing strict character budgets, atomic locks, and monotonic deadlines.

---

## 2. 3-Tier Memory Architecture

```mermaid
flowchart TD
    %% Styling
    classDef hook fill:#e1f5fe,stroke:#0288d1,stroke-width:2px,color:#01579b;
    classDef store fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px,color:#4a148c;
    classDef runtime fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#1b5e20;
    classDef guard fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100;

    %% 1. Interactive Runtime
    subgraph RUNTIME["1. INTERACTIVE RUNTIME (Prefix-Cache Friendly)"]
        direction TB
        USER_REQ["User Request"] --> PRE_HOOK["PreInvocation Hook\n(pre_invocation.py)"]:::hook
        
        PRE_HOOK -->|"Step 0 (invocationNum == 0)"| INJECT["Inject Context Snapshot\n(Frozen for entire turn)"]:::runtime
        PRE_HOOK -->|"Step > 0 (Tool Turn)"| SKIP["Skip Injection\n(Zero Token Overhead)"]:::runtime
        
        INJECT --> AGENT["AI Coding Agent\n(Active Reasoning Loop)"]:::runtime
        SKIP --> AGENT
        
        AGENT <-->|"FastMCP Protocol\n(memory, session_search, skill_manage)"| MCP["Hermes MCP Server\n(server.py)"]:::guard
    end

    %% 2. Curated Mid-Term Memory
    subgraph TIER2["2. TIER 2: CURATED DUAL MEMORY (Mid-Term)"]
        direction TB
        MEM_STORE["MemoryStore (memory.py)\nAdvisory fcntl Locks + Atomic Replacement"]:::store
        
        subgraph DUAL_STORES["Dual Markdown Stores (Delimiter: \\n§\\n)"]
            USER_FILE["USER.md (max 1,375 chars)\nUser Profile & Preferences"]:::store
            MEM_FILE["MEMORY.md (max 2,200 chars)\nWorkspace Rules & Tools"]:::store
        end

        subgraph TWO_ZONE["Two-Zone Protection Model"]
            ZONE_0["Anchor Zone (Index 0)\n- Immutable Core Identity\n- Protected against batch/remove"]:::guard
            ZONE_1["Dynamic Zone (Index >= 1)\n- FIFO Overflow Eviction\n- Transparent Eviction Telemetry"]:::guard
        end

        MEM_STORE --> DUAL_STORES
        DUAL_STORES -.-> TWO_ZONE
    end

    %% 3. Episodic Recall & Autonomous Reflection
    subgraph TIER3["3. TIER 3: EPISODIC RECALL & AUTONOMOUS REFLECTION"]
        direction TB
        STOP_HOOK["Stop Hook (stop_hook.py)\nCooperative Monotonic Deadline <= 1.5s"]:::hook
        
        subgraph EPISODIC["Episodic Storage & Search"]
            FTS5[("SQLite FTS5 (state.db)\n- BM25 Relevance Search\n- Sanitized System Metadata")]:::store
        end

        subgraph EVOLUTION["Out-of-Band Self-Evolution"]
            REFLECTOR["SessionReflector (reflector.py)\n- Exact + Jaccard >= 0.85 Dedup\n- Bilingual Negation Guard"]:::guard
            SKILL_MGR["SkillManager (skills.py)\n- Provenance: 'background_review'\n- User & Pinned Skills Protected"]:::guard
            SKILLS_DIR["Procedural Skills\n(~/.agents/skills/)"]:::store
        end

        STOP_HOOK -->|"1. Ingest Sanitized Transcript"| FTS5
        STOP_HOOK -->|"2. Trigger Reflection"| REFLECTOR
        REFLECTOR -->|"Distill Procedural Knowledge"| SKILL_MGR
        SKILL_MGR --> SKILLS_DIR
        REFLECTOR -.->|"Distill Discovered Preferences"| USER_FILE
    end

    %% Key Inter-Tier Flows
    MEM_STORE ==>|"Load Snapshot at Turn Start"| INJECT
    MCP <-->|"Direct Curated Operations"| MEM_STORE
    MCP <-->|"On-Demand Lexical Search"| FTS5
    AGENT -.->|"Session Exit"| STOP_HOOK
```

### Tier 1: Working Context Snapshot (Short-Term)
- **Lifecycle Injection**: Hooked into runtime via `PreInvocation` (`pre_invocation.py`).
- **Gating**: Injected *exclusively* on `invocationNum == 0` (the first step of a turn). Intra-turn tool executions receive `{"injectSteps": []}`, preserving token budgets.
- **Prefix Cache Stability**: Read-only during active reasoning turns. Zero mid-turn prompt mutation.

### Tier 2: Mid-Term Curated Dual Memory
- **Partitioned Stores**:
  - `USER.md`: User persona, coding style, preferences ($\le 1,375$ characters).
  - `MEMORY.md`: Workspace rules, architecture invariants, environment quirks ($\le 2,200$ characters).
- **Delimiter**: Normalized entries separated strictly by `\n§\n`.
- **Two-Zone Protection Model**:
  - **Zone 0 (Anchor Zone)**: Always preserved at index 0. Immutable against automated deletion or batch eviction.
  - **Zone 1+ (Dynamic Zone)**: Managed via deterministic FIFO eviction when total length exceeds budget.
- **Concurrency & Atomicity**: Atomic file replacement via PID-stamped temporary files (`.tmp.<pid>`) protected by advisory `fcntl.flock(LOCK_EX)` locks.

### Tier 3: Long-Term Episodic Recall & Self-Evolution
- **SQLite FTS5 Storage**: Complete historical transcripts indexed with BM25 relevance ranking.
- **Progressive Retrieval**: Search queries return compact snippets and bounded previews ($\le 1,200$ characters); full messages are fetched on-demand with pagination via `session_get_message`.
- **Out-of-Band Reflection**: Triggered via `Stop` hook on session exit (`stop_hook.py`).
- **Monotonic Deadline Guard**: Reflection processes execute under a strict cooperative deadline (`time.monotonic() + timeout`, default 1.5s) ensuring clean, non-hanging exits.

---

## 3. FastMCP Tool Interface

The Hermes Engine exposes four core tools to AI coding agents via Model Context Protocol (FastMCP):

| Tool | Parameters | Purpose |
| :--- | :--- | :--- |
| `memory` | `action`, `target`, `content`, `old_text`, `operations` | Query or update Tier 2 dual stores with Two-Zone eviction and atomic batch transactions. |
| `skill_manage` | `action`, `name`, `content`, `file_path`, `old_string`, `origin` | Manage procedural skills (`agentskills.io` standard) with write-origin provenance checks. |
| `session_search` | `query`, `limit` | Full-text lexical search across past session history using SQLite FTS5 BM25 ranking. |
| `session_get_message` | `message_id`, `offset`, `limit` | Retrieve paginated message content from past sessions with zero context overflow risk. |

---

## 4. Architectural Invariants & Safety Guarantees

1. **Write-Origin Provenance**:
   - `origin="foreground"`: Granted when users directly command changes. Allows creating, editing, and deleting skills.
   - `origin="background_review"`: Enforced during autonomous reflection. Strictly prohibited from modifying or deleting user-made skills or pinned skills (`pinned: true`).
2. **Metadata Sanitization**:
   - Transcripts ingested into SQLite FTS5 are automatically stripped of system-internal envelopes (`<USER_SETTINGS_CHANGE>`, `<ADDITIONAL_METADATA>`, `<CONTEXT_SUMMARY>`) to prevent search poisoning.
3. **Intra-Turn Tolerance & Rewind Pruning**:
   - The ingestion parser handles async, out-of-order log writes within turns while properly pruning abandoned branches during conversational user rewinds.
