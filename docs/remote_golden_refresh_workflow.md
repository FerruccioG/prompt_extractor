# Remote Job Opportunities Golden Master List Refresh

Primary entry point: `tools/remote_golden_refresh.py`

## Current status after corrective safety pass

The intake/checkpoint foundation is hardened before the final orchestration work:

- Gmail UID is the operational checkpoint authority.
- Any email fetch error makes the run non-committable and blocks watermark advancement.
- A run with no new `Remote` emails is a valid successful no-op.
- Email `Date:` remains freshness/evidence metadata and cannot move the checkpoint backwards.
- Golden row counts are computed dynamically; no hard-coded `46` remains in finalization.
- Candidate-level unresolved evidence can be persisted as `Check Later` instead of blocking the entire run.
- Persistent disposition files are staged before live replacement; state is committed last.
- Canonical source identity is shared through `tools/remote_source_identity.py`.
- Regional domains remain distinct (`example.com`, `example.ie`, `example.co.uk`) unless live redirect evidence proves equivalence.
- `remote_refresh_finalize.py` accepts `--run-dir` so the future orchestrator can pass the exact active run rather than depend on "latest directory" discovery.

`remote_golden_refresh.py` is still the intake-stage entry point today. The next development phase wires the existing downstream tasks into this one command.

## Target one-command workflow

```text
remote_golden_refresh.py
    |
    +-- 1. Load state / create exact run directory
    |
    +-- 2. Gmail intake: subject contains Remote
    |       - preserve email evidence
    |       - extract URLs
    |       - classify actionable/context/noise
    |       - fetch error => STOP, do not advance UID
    |       - no new email => successful no-op finalization
    |
    +-- 3. Harvest router
    |       `tools/remote_harvest_router.py`
    |       +-- Instagram queue
    |       +-- TikTok queue
    |       +-- LinkedIn queue
    |       `-- Generic/unclassified queue (generic harvester still to complete)
    |
    +-- 4A. Instagram harvest/extraction
    |       `tools/remote_instagram_harvester.py`
    |       `tools/remote_instagram_ocr.py`
    |       `tools/remote_instagram_reel_frame_harvester.py`
    |       `tools/remote_instagram_reel_frame_ocr.py`
    |       `tools/remote_instagram_evidence_builder.py`
    |       `tools/remote_instagram_name_resolver.py`
    |       `tools/remote_incremental_candidate_dedupe.py`
    |
    +-- 4B. TikTok harvest/extraction
    |       `tools/remote_tiktok_harvester.py`
    |       `tools/remote_tiktok_ocr.py`
    |       `tools/remote_tiktok_evidence_builder.py`
    |       `tools/remote_tiktok_candidate_dedupe.py`
    |
    +-- 4C. LinkedIn harvest/extraction
    |       `tools/remote_linkedin_harvester.py`
    |       `tools/remote_linkedin_ocr.py`
    |       `tools/remote_linkedin_evidence_builder.py`
    |       `tools/remote_linkedin_name_resolver.py`
    |       `tools/remote_linkedin_community_resolver.py`
    |       `tools/remote_linkedin_candidate_dedupe.py`
    |
    +-- 5. Validate genuinely new candidate sources only
    |       Generic/Instagram:
    |         `tools/remote_incremental_source_validator.py`
    |         `tools/remote_incremental_validation_assessor.py`
    |       TikTok:
    |         `tools/remote_tiktok_source_validator.py`
    |         `tools/remote_tiktok_validation_assessor.py`
    |       LinkedIn:
    |         `tools/remote_linkedin_source_validator.py`
    |         `tools/remote_linkedin_validation_assessor.py`
    |
    +-- 6. Profile + score
    |       Generic/Instagram:
    |         `tools/remote_incremental_candidate_profiler.py`
    |         `tools/remote_incremental_relationship_scorer.py`
    |         `tools/remote_incremental_final_scorer.py`
    |       TikTok:
    |         `tools/remote_tiktok_candidate_profiler.py`
    |         `tools/remote_tiktok_relationship_scorer.py`
    |         `tools/remote_tiktok_final_scorer.py`
    |       LinkedIn:
    |         `tools/remote_linkedin_candidate_profiler.py`
    |         `tools/remote_linkedin_relationship_scorer.py`
    |         `tools/remote_linkedin_final_scorer.py`
    |
    +-- 7. Whole-run decision reconciliation
    |       `tools/remote_refresh_reconcile.py --run-dir <exact-run>`
    |       +-- Promote
    |       +-- Hold / Review
    |       +-- Exclude
    |       `-- Check Later / unresolved-preserved-for-retry
    |
    +-- 8. Publication/finalization
    |       Promote = 0:
    |         `tools/remote_refresh_finalize.py --run-dir <exact-run>`
    |
    |       Promote > 0 (next implementation gap):
    |         merge promoted rows into Golden JSONL
    |         backup Excel
    |         `tools/storage/excel_source_loader.py`
    |         `tools/storage/excel_source_integrity_validator.py`
    |         persist dispositions
    |         advance Gmail UID checkpoint LAST
    |
    `-- 9. Print one concise run summary
```

## Hard invariants

1. One command is the eventual operator interface: `python -u tools/remote_golden_refresh.py`.
2. The historical cutoff remains 6 October 2026.
3. Gmail UID, not email `Date:`, controls progression.
4. A fetch/infrastructure failure never advances the UID checkpoint.
5. An unresolved candidate is preserved for retry; it is not silently excluded.
6. Canonical domain is the source identity. Regional domains stay distinct unless redirect evidence proves they collapse.
7. Known sources do not receive full expensive reprocessing; new evidence is attached to the known source.
8. Excel is written only for actual Golden promotions.
9. Excel integrity must pass before state is committed.
10. State/watermark is always the final commit step.
