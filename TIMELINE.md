# Timeline (Final) - Sentinel Gujarat CCTV Integration Hackathon

**Deadline: Sep 15. Today is Sep 9 — 6 days out.**

This is a two-phase competition: Phase 1 (Sandbox Round) is what this whole timeline targets — submit by Sep 15, shortlisting announced that evening. Only the top 6 teams across both categories (we're in Category 1, student track) advance to a Phase 2 live demo on Sep 22–23 against a production-scale feed set. Sep 15 is the deadline that matters here; the 22–23 dates are conditional on being shortlisted and shouldn't change how this week is prioritized. Plan accordingly: mandatory deliverables first, bonus items only if there's slack left on Day 5.

## Chosen Approach

- **Model 1 (mandatory): Centralised CCTV Registry & GIS Foundation** — metadata/registry only, no video.
- **Model 2: Unified Viewing & Selective Analytics** — direct RTSP/ONVIF/vendor-API integration, no middleware layer.

## One-Line Pitch

Turns 26 fragmented, department-owned CCTV systems into one searchable network — pull up a plate, see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching any department's existing infrastructure.

## Explicitly Out of Scope

- Model 3 (VMS federation/middleware) and Model 4 (Central VMS, face recognition, crowd/vehicle counting, statewide infra)
- Real integration with VAHAN/SARTHI/eGujCop/AFIS/NAFIS — a representative watchlist DB is explicitly permitted
- Full 80,000-camera build — this is a written scalability plan, not something you build
- Bonus features beyond mandatory scope unless Day 5 has spare time

---

## Done (Sep 9 or earlier) — Registry, GIS, Viewer

Model 1 is essentially complete and Model 2's viewing half is working:

- Camera onboarding: manual entry + bulk CSV + bulk JSON API, sourced from the catalogue, never hardcoded
- RBAC (admin/department/viewer roles) + audit trail on registry actions
- Interactive GIS map (Leaflet) with department/status filters
- Gap-analysis report (per-department coverage, stale cameras, missing departments) — its own page, plus a summary strip on the Dashboard
- Unified viewer connected to cctv.corp8.cloud via authenticated HLS proxy, with real reconnect-with-backoff
- Unified control room Dashboard (coverage summary + live preview grid + plate/route search)
- Database wired for both Supabase (prod) and local Docker Postgres/PostGIS (dev) via one env var — see `PLAN.md` Section 5a for the remaining provisioning step

Fixed today: the bug where only 1–2 of 8 opened camera tiles ever showed video (broken/unauthenticated MP4 fallback treated as permanent failure instead of retrying HLS — see `PLAN.md` Section 0a), plus enum-validation gaps that let bad onboarding data 500 instead of failing cleanly.

**If you only do one thing before moving on:** actually create the Supabase project and drop the pooled connection string into `.env` (10 minutes, `PLAN.md` Section 5a) — everything else here assumes a real deployed DB exists well before Day 5.

---

## Day 1 (Sep 10) — ANPR Pipeline Rework

The existing pipeline (`anpr/pipeline.py`) isn't solid enough to trust for the test case yet. Rebuild/harden it:

- Vehicle detection (YOLO) + plate cropping — verify against live cctv.corp8.cloud feeds, not just the bundled `yolov8n.pt` on stock footage
- OCR (PaddleOCR/EasyOCR) + confidence scoring
- Store detections via `POST /detections`: plate, **PTS-derived** timestamp, camera ID, confidence — never wall-clock/arrival time (`PLAN.md` Section 8)
- Handle mixed H.264/H.265 and mixed resolutions per-camera — no fixed-shape batching across cameras
- Reconnect-with-backoff on the RTSP side, same discipline as the HLS viewer already has
- Sanity-check output against 2–3 cameras first before scaling to all 30

## Day 2 (Sep 11) — Watchlist Alerts + Route-on-Map

Two independent, parallelizable gaps, both already have backend groundwork:

- **Real-time alerts** (required, not optional — the official own-feed demo checklist explicitly calls for "watchlist matching, alert generation"): watchlist match-on-detection logic already exists (`watchlist.py`, wired into `detections.py`) but only logs server-side. Add a `GET /watchlist/alerts/recent` endpoint and short-poll it from the Dashboard — good enough to demo, no websocket layer needed at this scale.
- **Route on the GIS map**: `/detections/route/{plate}` already returns the right data (used by the Dashboard's text-based route search). Add a `Polyline` + numbered stop markers to the Leaflet map in `Registry.jsx` (or a dedicated tracking view) so the official test case — plate seen on cam01 → cam13 → cam15 — renders visually, not just as a list.
- Seed the representative watchlist dataset for the demo plates you'll use.

## Day 3 (Sep 12) — End-to-End Integration Test

- Full test-case rehearsal: onboard feeds → live monitoring → ANPR on a real plate → watchlist alert fires → route renders on the GIS map
- Bug fixes from whatever the rehearsal surfaces
- Stress-test reconnect/backoff by deliberately restarting a feed mid-test
- Confirm behavior across a scene discontinuity (loop point) — long-lived ANPR state must recover from the hard cut, not assume continuity
- If ANPR or route-on-map isn't reliable yet, this is the day to cut scope (e.g., ship the text-based route view if the polyline isn't stable) rather than carry risk into Day 4

## Day 4 (Sep 13) — Documentation

- **Solution Presentation (PPT/PDF)**: model chosen + justification, overview, architecture, workflow, analytics approach, alert methodology, tech stack, expected impact
- **HLD document**: architecture diagrams, integration approach for heterogeneous cameras, ANPR + cross-camera tracking approach, alert workflow, department-wise technical requirements
- **Scalability section** (written, not built): central/regional/edge compute, GPU/accelerator sizing, bandwidth planning, hot/warm/cold storage tiers, HA/DR, phased statewide rollout

## Day 5 (Sep 14) — Demo Recording + Hardening Buffer

- Own-feed demo (2–3 min): onboarding + live/recorded viewing + ANPR — record cleanly
- Government-feed live demo: onboarding + viewing + analytics output, screen-recorded
- Output report: detected plates + timestamps (CSV/table export — `GapAnalysis.jsx` already has a JSON-export pattern to copy)
- Re-record if anything looks like a mockup or scripted fake — evaluators explicitly reject non-functional demos
- Any remaining slack today only: RBAC/audit hardening, edge-case passes, additional analytics — never at the expense of the mandatory items above

## Day 6 (Sep 15) — Final Submission

- Final polish and bug buffer
- Upload demo videos as unlisted YouTube (or Drive/OneDrive with viewer access)
- Push source to GitHub/GitLab
- If Supabase is deployed: include the hosted platform URL with test credentials
- Compile and submit all links well before the deadline — don't let submission-portal friction be the last thing that goes wrong

---

## Evaluation Areas to Keep in Mind Throughout

1. Successful test case (onboarding + analytics on gov feed)
2. Solution presentation clarity
3. Solution architecture soundness (HLD)
4. Working platform maturity — must be real software, not mockups
5. Video analytics output quality (ANPR/detection/reports)
6. Scalability & PoC readiness (written plan)
7. Submission completeness

See `PLAN.md` for full technical detail, endpoints, schemas, current status, and things not to do.
