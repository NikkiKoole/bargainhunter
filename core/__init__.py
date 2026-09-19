"""Shared bargainhunter infrastructure.

Phase 0 extracts HTTP cache, SQLite storage, the listing model, and the
export/serve path so a later adapter (ok_bulgaria, akiyaportal, …) can plug
in without rewriting them. Franimo stays the only live source in this PR;
`python3 -m franimo.scrape` / `export` / `serve` keep working.
"""
