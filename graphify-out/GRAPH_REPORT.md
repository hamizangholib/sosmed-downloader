# Graph Report - sosmed downloader  (2026-08-24)

## Corpus Check
- 3 files · ~6,891 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 111 nodes · 155 edges · 6 communities
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `65209c40`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- script.js
- _self_check
- 📋 Daftar Isi (Indonesian)
- main.py
- 📋 Table of Contents (English)
- README.md

## God Nodes (most connected - your core abstractions)
1. `_self_check()` - 12 edges
2. `📋 Daftar Isi (Indonesian)` - 10 edges
3. `📋 Table of Contents (English)` - 10 edges
4. `extract_with_session()` - 9 edges
5. `extract()` - 8 edges
6. `download()` - 8 edges
7. `is_sensitive_x_error()` - 7 edges
8. `build_formats()` - 7 edges
9. `run_extraction()` - 6 edges
10. `pick_raw_format()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `extract_with_session()` --calls--> `is_sensitive_x_error()`  [EXTRACTED]
  backend/main.py → backend/main.py  _Bridges community 1 → community 3_

## Import Cycles
- None detected.

## Communities (6 total, 0 thin omitted)

### Community 0 - "script.js"
Cohesion: 0.10
Nodes (19): API_ROOT, errorAlert, errorClose, errorText, escapeHtml(), form, formatDuration(), header (+11 more)

### Community 1 - "_self_check"
Cohesion: 0.13
Nodes (18): build_formats(), classify(), content_disposition(), human_size(), is_sensitive_x_error(), is_x_url(), pick_raw_format(), Bytes to a short human string. Returns None when the size is unknown. (+10 more)

### Community 2 - "📋 Daftar Isi (Indonesian)"
Cohesion: 0.11
Nodes (19): 1. `GET /`, 1. Menggunakan Python venv, 2. Menggunakan Docker, 2. `POST /api/extract`, 3. `GET /api/download`, Batasan Sistem, Cara Menjalankan di Lokal, 📋 Daftar Isi (Indonesian) (+11 more)

### Community 3 - "main.py"
Cohesion: 0.13
Nodes (26): add_x_auth_cookies(), build_item(), download(), entries_of(), extract(), extract_info(), extract_with_session(), ExtractRequest (+18 more)

### Community 4 - "📋 Table of Contents (English)"
Cohesion: 0.11
Nodes (19): 1. `GET /`, 1. Using Python venv, 2. `POST /api/extract`, 2. Using Docker, 3. `GET /api/download`, API Reference, Backend Deployment Guide, Enabling sensitive X (Twitter) videos (+11 more)

### Community 5 - "README.md"
Cohesion: 0.40
Nodes (4): 🇮🇩 Bahasa Indonesia, 🇬🇧 English, 🌐 Language / Bahasa, 🌊 Saveflow — Social Media Media Downloader

## Knowledge Gaps
- **45 isolated node(s):** `API_ROOT`, `form`, `urlInput`, `pasteBtn`, `submitBtn` (+40 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `📋 Daftar Isi (Indonesian)` connect `📋 Daftar Isi (Indonesian)` to `README.md`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `📋 Table of Contents (English)` connect `📋 Table of Contents (English)` to `README.md`?**
  _High betweenness centrality (0.094) - this node is a cross-community bridge._
- **Why does `🇮🇩 Bahasa Indonesia` connect `README.md` to `📋 Daftar Isi (Indonesian)`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **What connects `API_ROOT`, `form`, `urlInput` to the rest of the system?**
  _45 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `script.js` be split into smaller, more focused modules?**
  _Cohesion score 0.10276679841897234 - nodes in this community are weakly interconnected._
- **Should `_self_check` be split into smaller, more focused modules?**
  _Cohesion score 0.13071895424836602 - nodes in this community are weakly interconnected._
- **Should `📋 Daftar Isi (Indonesian)` be split into smaller, more focused modules?**
  _Cohesion score 0.10526315789473684 - nodes in this community are weakly interconnected._