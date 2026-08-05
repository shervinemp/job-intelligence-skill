# Self-Adaptation Meta-Flow Graph

The flow that builds and adapts the flow itself — probe-cascade learning,
answer learning, drift demotion, and the human/orchestrator loops that extend
static vocabulary. Source in `apply/common/observations.py`, `resolve.py`,
`corpus.py`, `registry_cli.py`, and the SKILL.md change protocol.

Render with: `npx -y @mermaid-js/mermaid-cli -i META_FLOW_GRAPH.md -o META_FLOW_GRAPH.png`

Legend: ◈ loop · ◆ decision · ◎ observation · purple = orchestrator contract

```mermaid
flowchart LR
    subgraph PROBE["LOOP 1 — widget-strategy learning"]
        direction TB
        M1["◈ probe cascade runs"]
        M2["◎ capability profile computed"]
        M3["◆ strategy won / failed?"]
        M4["record_success<br/>same win N× → confirmed"]
        M5["record_failure<br/>0 fields → fail_count++"]
        M6["◆ fail_count ≥ 2?"]
        M7["demote: confirmed=False<br/>reset, re-confirm from scratch"]
    end
    M1 --> M2 --> M3
    M3 -- won --> M4
    M3 -- failed --> M5 --> M6
    M6 -- yes --> M7
    M6 -- no --> M1
    M4 --> M1
    M7 --> M1

    subgraph CORPUS["LOOP 3 — drift detection"]
        direction TB
        D1["◎ corpus snapshot captured"]
        D2["registry drift re-probe"]
        D3["◆ silent ATS redesign?"]
        D4["auto-demote stale observation"]
    end
    D1 --> D2 --> D3
    D3 -- yes --> D4
    D4 --> M7
    D3 -- no --> D1

    subgraph RESOLVE["LOOP 2 — answer learning"]
        direction TB
        R1["resolver"]
        R2["◆ no_match?"]
        R3["orchestrator --answers<br/>(purple: C2)"]
        R4["learn_mapping<br/>domain-scoped"]
        R5["resolves autonomously next run"]
        R6["◆ answers_override contradicts?"]
        R7["_invalidate_learned"]
    end
    R1 --> R2
    R2 -- yes --> R3 --> R4 --> R5
    R2 -- no --> R5
    R4 --> R6
    R6 -- yes --> R7

    subgraph STATIC["LOOP 4 — static-vocab extension (human process)"]
        direction TB
        H1["repeated no_match label<br/>or demoted observation"]
        H2["orchestrator fleet decision<br/>(purple: C7)"]
        H3["edit aliases / countries /<br/>keywords / categories"]
        H4["lint.py → pytest → shadow →<br/>verify → sync → commit"]
    end
    H1 --> H2 --> H3 --> H4
    H2 -. proposed, not wired .-> H3

    %% cross-links
    M1 -. feeds .-> H1
    R2 -. feeds .-> H1
    H4 -. updates .-> R1
    H4 -. updates .-> M2

    classDef loop fill:#d5f5e3,stroke:#1e8449,color:#000;
    classDef decision fill:#d4efdf,stroke:#1e8449,color:#000;
    classDef observe fill:#d6eaf8,stroke:#2874a6,color:#000;
    classDef human fill:#fdebd0,stroke:#b9770e,color:#000;
    class M1,M4,M5 loop;
    class M3,M6,R2,R6,D3,H2 decision;
    class M2,D1 observe;
    class H1,H3,H4 human;
```
