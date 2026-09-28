"""
Investigation pipeline orchestrator.

Runs within a camera thread, intercepting tracked vehicles. Buffers evidence,
filters against active targets, selects best frames, enhances them, computes
OCR consensus, scores the candidate, and submits to the backend.

IMPORTANT: UNKNOWN observations continue collecting evidence.
The whole point of investigation mode is to resolve UNKNOWN observations
using later frames - never discard a track just because early frames
are inconclusive.
"""
import logging
import os
import time
import uuid
from typing import Optional

import cv2
import requests

from evidence_buffer import TrackBuffer, select_best_frames
from target_filter import filter_candidate
from enhance import enhance_crop, ENABLE_ENHANCEMENT
from ocr_consensus import compute_consensus, FrameOCRResult
from scoring import compute_evidence_score
from plate_reader import PlateReader


logger = logging.getLogger(__name__)

# Heartbeat interval in seconds
HEARTBEAT_INTERVAL = 5.0


class InvestigationPipeline:
    def __init__(self, backend_url: str, camera_id: str, plate_reader: PlateReader,
                 session_id: Optional[str] = None,
                 debug_counters: Optional[dict] = None):
        self.backend_url = backend_url.rstrip("/")
        self.camera_id = camera_id
        self.plate_reader = plate_reader
        self.targets = []
        self.last_targets_sync = 0.0
        self.last_heartbeat = 0.0
        self.debug_counters = debug_counters or {}
        # Tracker IDs are meaningful only within one camera-process session.
        # Persist a fresh session ID so an RTSP reconnect/restart cannot merge
        # a new local ``track_id=7`` with a previous physical vehicle.
        self.session_id = session_id or str(uuid.uuid4())
        
        # Evidence crops go straight into the backend's data dir so its
        # /data/evidence static mount can serve them. Resolved from this file,
        # not the working directory, so the pipeline can be launched from anywhere.
        self.db_evidence_dir = os.path.join("data", "evidence", self.camera_id)
        self.fs_evidence_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "backend", "data", "evidence", self.camera_id
        )
        os.makedirs(self.fs_evidence_dir, exist_ok=True)

    def sync_targets(self):
        """Fetch active investigation targets from the backend."""
        now = time.time()
        if now - self.last_targets_sync < 10.0:  # Sync every 10s
            return
            
        try:
            resp = requests.get(f"{self.backend_url}/investigations/targets?status=active", timeout=5.0)
            if resp.ok:
                self.targets = resp.json()
                self.debug_counters["active_targets"] = len(self.targets)
            self.last_targets_sync = now
        except Exception as e:
            logger.warning(f"Failed to sync targets: {e}")

    def send_heartbeat(self):
        """Send periodic heartbeat to backend to prove pipeline is alive."""
        now = time.time()
        if now - self.last_heartbeat < HEARTBEAT_INTERVAL:
            return
        
        try:
            payload = {
                "pipeline_id": "main",
                "cameras_configured": self.debug_counters.get("cameras_configured", self.debug_counters.get("cameras_active", 1)),
                "cameras_connected": self.debug_counters.get("cameras_connected", self.debug_counters.get("cameras_active", 1)),
                "cameras_active": self.debug_counters.get("cameras_active", 1),
                "vehicles_detected": self.debug_counters.get("vehicles_detected", 0),
                "tracks_created": self.debug_counters.get("tracks_created", 0),
                "active_targets": self.debug_counters.get("active_targets", 0),
                "candidates_created": self.debug_counters.get("candidates_created", 0),
                "ocr_attempts": self.debug_counters.get("ocr_attempts", 0),
                "ocr_success": self.debug_counters.get("ocr_success", 0),
                "api_failures": self.debug_counters.get("api_failures", 0),
                "per_camera": self.debug_counters.get("per_camera"),
            }
            requests.post(
                f"{self.backend_url}/investigations/heartbeat",
                json=payload,
                timeout=3.0,
            )
            self.last_heartbeat = now
        except Exception as e:
            logger.debug(f"Heartbeat failed: {e}")

    def _ingest_observation(
        self,
        buffer: TrackBuffer,
        plate_text: Optional[str] = None,
        plate_conf: float = 0.0,
        candidate_id: Optional[str] = None,
        evidence_completeness: Optional[float] = None,
        overall_confidence: Optional[float] = None,
    ):
        """Persist a finalized, camera-local observation with explicit provenance.

        ``candidate_id`` is supplied only when this observation was produced
        from a persisted candidate track.  That direct foreign-key link is the
        sole supported way for the workstation to retrieve its evidence.
        """
        dom_type = buffer.dominant_type
        dom_color_consensus = buffer.dominant_color
        
        first_obs = buffer.observations[0] if buffer.observations else None
        last_obs = buffer.observations[-1] if buffer.observations else None
        
        pts_start = first_obs.pts_ms if first_obs else 0.0
        pts_end = last_obs.pts_ms if last_obs else 0.0
        # PTS is kept for within-stream provenance, but it is not a shared clock
        # across independent camera connections (each starts near zero), so it
        # can't order sightings on different cameras. The wall-clock time we last
        # saw the vehicle is - the same reasoning as detections' created_at, see
        # backend/app/routers/detections.py.
        timestamp_source = "absolute_timestamp"
        observed_at = buffer.last_seen_at

        obs_payload = {
            "camera_id": self.camera_id,
            "session_id": self.session_id,
            "track_id": buffer.track_id,
            "status": "finalized",
            "timestamp_source": timestamp_source,
            "observed_at": observed_at,
            "ingested_at": buffer.created_at,
            "is_playback_repetition": buffer.loop_count > 0,
            "candidate_id": candidate_id,
            "source_pts_start": pts_start,
            "source_pts_end": pts_end,
            "vehicle_type": dom_type or None,
            "color": dom_color_consensus.dominant_color,
            "color_confidence": dom_color_consensus.confidence,
            "plate": plate_text,
            "plate_confidence": plate_conf,
            "evidence_completeness": evidence_completeness,
            "overall_confidence": overall_confidence,
            "is_simulated": False
        }
        try:
            requests.post(f"{self.backend_url}/investigations/observations", json=obs_payload, timeout=5.0)
        except Exception as e:
            logger.debug(f"Failed to ingest observation: {e}")

    def process_expired_buffer(self, buffer: TrackBuffer):
        """Process a track buffer that has expired or reached capacity."""
        if not buffer.observations:
            return

        dom_type = buffer.dominant_type
        dom_color_consensus = buffer.dominant_color
        dom_color = dom_color_consensus.dominant_color
        
        if not dom_type:
            return

        if not self.targets:
            self._ingest_observation(buffer)
            return  # No active investigations

        # Fast filter against targets - UNKNOWN continues collecting
        matched_targets = []
        for target in self.targets:
            res = filter_candidate(
                target_plate=target.get("plate_number"),
                target_type=target.get("vehicle_type"),
                target_color=target.get("vehicle_color"),
                candidate_type=dom_type,
                candidate_color=dom_color,
                candidate_plate=None,  # We don't have OCR yet
                type_required=bool(target.get("type_required")),
                color_required=bool(target.get("color_required")),
            )
            self.debug_counters["target_evaluations"] = self.debug_counters.get("target_evaluations", 0) + 1
            if res.should_investigate:
                matched_targets.append(target)
                logger.info(
                    "[INVESTIGATION] Target ID: %s | Camera: %s | Track: %d | "
                    "Type: %s | Color: %s | Decision: %s | Detail: %s",
                    target.get("id", "?")[:8], self.camera_id, buffer.track_id,
                    dom_type, dom_color, "INVESTIGATE", res.detail,
                )
                self.debug_counters["matches"] = self.debug_counters.get("matches", 0) + 1
                
        if not matched_targets:
            self._ingest_observation(buffer)
            return  # Not a candidate for any active target
            
        # Select best evidence frames
        evidence_frames = select_best_frames(buffer)
        if not evidence_frames:
            self._ingest_observation(buffer)
            return
            
        # Process OCR and Enhancement
        ocr_results = []
        evidence_payloads = []
        
        # Collect attribute readings for temporal consistency
        type_readings = [o.vehicle_type for o in buffer.observations if o.vehicle_type]
        color_readings = [o.vehicle_color for o in buffer.observations if o.vehicle_color]
        
        for obs in evidence_frames:
            # Save raw crop
            raw_filename = f"track_{buffer.track_id}_f{obs.frame_index}_raw.jpg"
            raw_fs_path = os.path.join(self.fs_evidence_dir, raw_filename)
            raw_db_path = os.path.join(self.db_evidence_dir, raw_filename).replace("\\", "/")
            cv2.imwrite(raw_fs_path, obs.crop)
            
            enhanced_db_path = None
            crop_to_read = obs.crop
            
            if ENABLE_ENHANCEMENT:
                enh_res = enhance_crop(obs.crop)
                if enh_res.enhanced_crop is not None:
                    crop_to_read = enh_res.enhanced_crop
                    enh_filename = f"track_{buffer.track_id}_f{obs.frame_index}_enh.jpg"
                    enh_fs_path = os.path.join(self.fs_evidence_dir, enh_filename)
                    enhanced_db_path = os.path.join(self.db_evidence_dir, enh_filename).replace("\\", "/")
                    cv2.imwrite(enh_fs_path, enh_res.enhanced_crop)
                    
            # OCR using region proposals if available, else full crop
            self.debug_counters["ocr_attempts"] = self.debug_counters.get("ocr_attempts", 0) + 1
            candidates = self.plate_reader.read_with_regions(crop_to_read)
            best_read = max(candidates, key=lambda c: c.confidence) if candidates else None
            
            plate_text = best_read.text if best_read else None
            plate_conf = best_read.confidence if best_read else 0.0
            
            if plate_text:
                self.debug_counters["ocr_success"] = self.debug_counters.get("ocr_success", 0) + 1
            
            ocr_results.append(FrameOCRResult(
                frame_index=obs.frame_index,
                plate=plate_text,
                confidence=plate_conf
            ))
            
            evidence_payloads.append({
                "frame_index": obs.frame_index,
                "quality_score": obs.quality.overall,
                "raw_path": raw_db_path,
                "enhanced_path": enhanced_db_path,
                "ocr_candidate": plate_text,
                "ocr_confidence": plate_conf,
                "vehicle_type": obs.vehicle_type,
                "vehicle_color": obs.vehicle_color,
                "source_pts": obs.pts_ms,
                "evidence_state": "unknown" if obs.vehicle_color is None else "match",
                "unknown_reason": obs.color_unknown_reason,
                "extraction_method": "plate_region_ocr_v1+color_consensus_v1",
            })
            
        # Compute consensus
        consensus = compute_consensus(ocr_results)
        best_plate = consensus.best_plate
        ocr_readings = [r.plate for r in ocr_results]
        
        # Re-filter targets with OCR results - but UNKNOWN still passes
        final_targets = []
        for target in matched_targets:
            res = filter_candidate(
                target_plate=target.get("plate_number"),
                target_type=target.get("vehicle_type"),
                target_color=target.get("vehicle_color"),
                candidate_type=dom_type,
                candidate_color=dom_color,
                candidate_plate=best_plate,
                type_required=bool(target.get("type_required")),
                color_required=bool(target.get("color_required")),
            )
            if res.should_investigate:
                final_targets.append(target)
                
        if not final_targets:
            self._ingest_observation(buffer, best_plate, consensus.consensus_score)
            return  # Disqualified after OCR

        # Take the highest-priority target match, or just the first one
        target = final_targets[0]
        
        # Compute final score with conditional weighting
        score = compute_evidence_score(
            target_plate=target.get("plate_number"),
            target_type=target.get("vehicle_type"),
            target_color=target.get("vehicle_color"),
            ocr_best_plate=best_plate,
            ocr_consensus_score=consensus.consensus_score,
            detected_type=dom_type,
            detected_color=dom_color,
            image_quality=sum(e["quality_score"] for e in evidence_payloads) / len(evidence_payloads),
            evidence_frame_count=len(evidence_frames),
            ocr_candidates_found=consensus.frames_with_plate,
            type_readings=type_readings,
            color_readings=color_readings,
            ocr_readings=ocr_readings,
            color_unknown_reason=dom_color_consensus.unknown_reason,
            type_required=bool(target.get("type_required")),
            color_required=bool(target.get("color_required")),
        )
        
        if score.tier == "no_match":
            self._ingest_observation(buffer, best_plate, consensus.consensus_score)
            return
        
        logger.info(
            "[INVESTIGATION] Track %d finalized | Evidence frames: %d | "
            "Consensus: %s | Match score: %.2f | Completeness: %.2f | Tier: %s",
            buffer.track_id, len(evidence_frames),
            consensus.best_plate or "none", score.final_score,
            score.evidence_completeness, score.tier.value,
        )
            
        # Submit to backend
        payload = {
            "camera_id": self.camera_id,
            "track_id": buffer.track_id,
            "target_id": target.get("id"),
            "started_at": buffer.created_at,
            "ended_at": buffer.last_seen_at,
            "final_score": score.final_score,
            "tier": score.tier.value,
            "score_breakdown": score.signals.to_dict(),
            "ocr_consensus": {
                "best_plate": consensus.best_plate,
                "consensus_score": consensus.consensus_score,
                "supporting_frames": consensus.supporting_frames,
                "total_frames": consensus.total_frames,
            },
            "evidence_completeness": score.evidence_completeness,
            "evidence": evidence_payloads
        }
        
        candidate_id = None
        try:
            self.debug_counters["api_posts"] = self.debug_counters.get("api_posts", 0) + 1
            resp = requests.post(
                f"{self.backend_url}/investigations/ingest",
                json=payload,
                timeout=10.0
            )
            if resp.ok:
                candidate_id = resp.json().get("track_id")
                self.debug_counters["candidates_created"] = self.debug_counters.get("candidates_created", 0) + 1
                logger.info(f"Ingested candidate track {buffer.track_id} for target {target.get('id')}")
            else:
                self.debug_counters["api_failures"] = self.debug_counters.get("api_failures", 0) + 1
                logger.error(f"Failed to ingest candidate: {resp.text}")
        except Exception as e:
            self.debug_counters["api_failures"] = self.debug_counters.get("api_failures", 0) + 1
            logger.error(f"Failed to ingest candidate: {e}")

        self._ingest_observation(
            buffer,
            best_plate,
            consensus.consensus_score,
            candidate_id=candidate_id,
            evidence_completeness=score.evidence_completeness,
            overall_confidence=score.final_score,
        )
