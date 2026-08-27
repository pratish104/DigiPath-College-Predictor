# Graph Report - DigiPath  (2026-05-29)

## Corpus Check
- 22 files · ~14,775 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 414 nodes · 401 edges · 23 communities (20 shown, 3 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1949c336`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]

## God Nodes (most connected - your core abstractions)
1. `/graphify` - 15 edges
2. `What You Must Do When Invoked` - 14 edges
3. `predict()` - 4 edges
4. `Step 3 - Extract entities and relationships` - 4 edges
5. `normalize_text()` - 3 edges
6. `predict_job()` - 3 edges
7. `normalize_text()` - 3 edges
8. `apply_forecast_mode()` - 2 edges
9. `clean_city()` - 2 edges
10. `clean_branch()` - 2 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Communities (23 total, 3 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (335): A. P. Shah Institute of Technology, Thane (Un-Aided Religious Minority - Jain), Abhinav Education Societys College of Engineering and Technology (Degree), Wadwadi (Un-Aided), Adarsh Shikshan Prasarak Mandals K. T. Patil College of Engineering and Technology, Dharashiv (Un-Aided), Aditya Education Trusts Mitthulalji Sarada Polytechnic, Nalwandi Road, Beed (Un-Aided), Aditya Engineering College , Beed (Un-Aided), Adsuls Technical Campus, Chas Dist. Ahmednagar (Un-Aided), Agnel Charities FR. C. Rodrigues Institute of Technology, Vashi, Navi Mumbai (Un-Aided Religious Minority - Christian), Ahmednagar Jilha Maratha Vidya Prasarak Samajache, Shri. Chhatrapati Shivaji Maharaj College of Engineering, Nepti (Un-Aided) (+327 more)

### Community 1 - "Community 1"
Cohesion: 0.12
Nodes (17): Part A - Structural extraction for code files, Part B - Semantic extraction (parallel subagents), Part C - Merge AST + semantic into final extraction, Step 1 - Ensure graphify is installed, Step 2.5 - Transcribe video / audio files (only if video files detected), Step 2 - Detect files, Step 3 - Extract entities and relationships, Step 4 - Build graph, cluster, analyze, generate outputs (+9 more)

### Community 2 - "Community 2"
Cohesion: 0.12
Nodes (15): For --cluster-only, For git commit hook, For /graphify add, For /graphify explain, For /graphify path, For /graphify query, For native CLAUDE.md integration, For --update (incremental re-extraction) (+7 more)

### Community 3 - "Community 3"
Cohesion: 0.27
Nodes (7): apply_forecast_mode(), choose_best_category(), classify_college(), clean_branch(), clean_city(), normalize_text(), predict()

### Community 4 - "Community 4"
Cohesion: 0.60
Nodes (3): clean_text(), extract_resume_text(), predict_job()

### Community 5 - "Community 5"
Cohesion: 0.60
Nodes (3): clean_branch(), clean_city(), normalize_text()

## Knowledge Gaps
- **366 isolated node(s):** `PreToolUse`, `Government College of Engineering, Amravati (Government Autonomous)`, `Sant Gadge Baba Amravati University,Amravati (University Department)`, `Government College of Engineering,Yavatmal (Government)`, `Shri Sant Gajanan Maharaj College of Engineering,Shegaon (Un-Aided)` (+361 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `What You Must Do When Invoked` connect `Community 1` to `Community 2`?**
  _High betweenness centrality (0.004) - this node is a cross-community bridge._
- **Why does `/graphify` connect `Community 2` to `Community 1`?**
  _High betweenness centrality (0.004) - this node is a cross-community bridge._
- **What connects `PreToolUse`, `Government College of Engineering, Amravati (Government Autonomous)`, `Sant Gadge Baba Amravati University,Amravati (University Department)` to the rest of the system?**
  _366 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.005952380952380952 - nodes in this community are weakly interconnected._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.11764705882352941 - nodes in this community are weakly interconnected._
- **Should `Community 2` be split into smaller, more focused modules?**
  _Cohesion score 0.125 - nodes in this community are weakly interconnected._