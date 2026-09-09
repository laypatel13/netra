# Timeline (Final) - Sentinel Gujarat CCTV Integration Hackathon

**Deadline: Sep 15. Today is Sep 9 — 6 days out.**

This is a two-phase competition: Phase 1 (Sandbox Round) is what this whole timeline targets — submit by Sep 15, shortlisting announced that evening. Only the top 6 teams across both categories (we're in Category 1, student track) advance to a Phase 2 live demo on Sep 22–23 against a production-scale feed set. Sep 15 is the deadline that matters here; the 22–23 dates are conditional on being shortlisted and shouldn't change how this week is prioritized. Plan accordingly: mandatory deliverables first, bonus items only if there's slack left on Day 5.

## Chosen Approach

- **Model 1 (mandatory): Centralised CCTV Registry & GIS Foundation** — metadata/registry only, no video.
- **Model 2: Unified Viewing & Selective Analytics** — direct RTSP/ONVIF/vendor-API integration, no middleware layer.

## One-Line Pitch

Turns 26 fragmented, department-owned CCTV systems into one searchable network — pull up a plate (or, when it's not legible, just a vehicle's type and color), see every camera it passed and when, and get auto-alerted if it matches a stolen/wanted/blacklisted watchlist, without touching any department's existing infrastructure.

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

## Day 1 (Sep 10) — ANPR Pipeline Rework — done early (Sep 9), plus a major finding

Hardened `anpr/pipeline.py` a day ahead of schedule: frame throttling (grab/retrieve so CPU-bound YOLO+EasyOCR doesn't fall behind live video), decoder warm-up frame skip, absolute HLS URLs, crop upscaling before OCR. Verified against 9+ real live cameras.

**Major finding, not a code bug**: zero legible plates across 150+ real vehicle detections on every camera tested (including the toll plaza and one of the three official test-case cameras). The sandbox streams generic wide-angle surveillance CCTV, not purpose-built ANPR hardware — confirmed by comparing against how Ahmedabad/Gandhinagar's real e-challan system actually works (see `PLAN.md` Section 0b). This led directly to Day 2's expanded scope below — don't be surprised the plan changed here.

## Day 2 (Sep 11) — Vehicle Attribute Tracking + Watchlist Alerts + Route-on-Map — core done early (Sep 9)

Expanded scope (see `PLAN.md` Section 0b for the full reasoning): plate ANPR alone isn't enough given the sandbox's camera quality, so cross-camera tracking now also works off vehicle type + color, complementing exact-plate matches rather than replacing them.

**Core — done and verified end-to-end** (real live pipeline run against `cam14`, real API calls, in-browser verification with no console errors — see `PLAN.md` Section 0c):
- ✅ Schema: nullable `plate_number` on `Detection`/`WatchlistEntry`; `vehicle_type`, `vehicle_color` on both; `thumbnail_path` on `Detection`.
- ✅ `anpr/color.py`: dominant-color extraction from a vehicle crop (masks out glare/shadow first, buckets into a small named palette). Its night/artificial-lighting limitation is documented plainly and was confirmed live (a real detection was labeled "orange" that's actually a silver/gray car under sodium streetlight — exactly why the thumbnail matters, and it made the true color obvious at a glance).
- ✅ `anpr/pipeline.py`: records **every** detected vehicle now (type + color always, plate when legible, thumbnail always) via multipart upload — no longer silently drops vehicles with no legible plate. Dedup-cooldown extended to key on `(camera_id, vehicle_type, vehicle_color)` when there's no plate.
- ✅ Backend: generalized watchlist matching (tiered: exact plate > attributes-only), `GET /detections/search` for candidate sightings by attributes, thumbnail storage + serving at `GET /detections/{id}/thumbnail`.
- ✅ **Real-time alerts**: `GET /watchlist/alerts/recent`, short-polled every 5s from the new Watchlist page, surfacing both plate and attribute matches with a clear tier badge.
- ✅ Frontend: new Watchlist page (entry management + live alert feed); Dashboard search generalized to plate-or-attributes with thumbnails; Registry GIS map draws the route as a polyline + numbered stop markers with popups (thumbnail included).
- ✅ Manually recreated the local dev DB tables for the new schema (no Alembic in this project) — same step will be needed once Supabase is provisioned.
- ⬜ Still open: seed a realistic representative watchlist dataset for the actual demo (only synthetic test entries exist right now).

**Stretch (only if there's still time before Day 3):**
- GIS-plausibility ranking for attribute-based candidate sightings — use camera lat/lng (already in the registry) to flag/reject matches that would require impossible travel speed between two cameras.
- Partial/low-confidence plate tier — keep near-miss OCR reads instead of today's all-or-nothing regex match, and combine them with type+color for a stronger signal than either alone.

Given core landed a day ahead of schedule, Day 3's integration test can start early if the team is ready — no need to wait for Sep 12 if Supabase provisioning and watchlist seeding are also done sooner.

## Day 3 (Sep 12) — End-to-End Integration Test

- Full test-case rehearsal: onboard feeds → live monitoring → vehicle detected (plate if legible, else type+color+thumbnail) → watchlist alert fires (exact or attribute-tiered) → route renders on the GIS map
- Bug fixes from whatever the rehearsal surfaces
- Stress-test reconnect/backoff by deliberately restarting a feed mid-test
- Confirm behavior across a scene discontinuity (loop point) — long-lived ANPR state must recover from the hard cut, not assume continuity
- If ANPR, attribute tracking, or route-on-map isn't reliable yet, this is the day to cut scope (e.g., ship the text-based route view if the polyline isn't stable, or drop the stretch items from Day 2) rather than carry risk into Day 4

## Day 4 (Sep 13) — Documentation

- **Solution Presentation (PPT/PDF)**: model chosen + justification, overview, architecture, workflow, analytics approach, alert methodology, tech stack, expected impact
- **HLD document**: architecture diagrams, integration approach for heterogeneous cameras, ANPR + cross-camera tracking approach, alert workflow, department-wise technical requirements
- **Scalability section** (written, not built): central/regional/edge compute, GPU/accelerator sizing, bandwidth planning, hot/warm/cold storage tiers, HA/DR, phased statewide rollout

## Day 5 (Sep 14) — Demo Recording + Hardening Buffer

- Own-feed demo (2–3 min): onboarding + live/recorded viewing + ANPR + watchlist match + alert — record cleanly. Worth showing the attribute-based (type+color) tracking too, since it's a genuine differentiator (bonus criteria: "advanced cross-camera tracking," "additional reliable analytics beyond mandatory ANPR")
- Government-feed live demo: onboarding + viewing + analytics output, screen-recorded
- Output report: detected plates (or type+color when no plate was legible) + timestamps (CSV/table export — `GapAnalysis.jsx` already has a JSON-export pattern to copy)
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
