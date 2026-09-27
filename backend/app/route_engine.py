"""
Phase 5 - Historical Route Reconstruction Engine.
Builds RouteChains from VehicleObservations and CrossCameraLinkCandidates.
"""
import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.models import (
    VehicleObservation, 
    CrossCameraLinkCandidate, 
    CameraGraphEdge,
    RouteChain,
    RouteChainObservation,
    RouteChainLink,
    RouteChainStatus,
    InvestigationTarget,
    VerificationAction,
    TargetStatus,
    ObservationStatus,
    Mode,
    MachineAssessment
)

def reconstruct_routes(
    db: Session,
    target_id: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    max_depth: int = 10,
    persist: bool = False
) -> List[Dict[str, Any]]:
    """
    Reconstruct valid historical route chains for an investigation target.
    We find all observations for this target, and then follow CrossCameraLinkCandidates.
    """
    # 1. Fetch observations that could match the target.
    # For Phase 5, we assume the Investigation Pipeline has already tagged tracks 
    # with this target_id in VehicleTrack, OR we search by attribute.
    # To keep it simple and bounded, we'll fetch observations where target matches
    # or just fetch all observations in the time window if target_id is None.
    # Wait, VehicleObservation doesn't have target_id. VehicleTrack does.
    # The requirement: "Input: investigation target, starting observation... "
    # Let's get observations that are linked to this target's tracks.
    
    from app.models import VehicleTrack
    tracks = db.query(VehicleTrack).filter(VehicleTrack.target_id == target_id).all()
    track_map = {t.track_id: t.camera_id for t in tracks}
    
    if not tracks:
        return []

    # Get the actual observations for these tracks
    obs_query = db.query(VehicleObservation)
    
    # We filter by track_id and camera_id matching the target's tracks
    # (Since track_id is local to camera)
    obs_list = []
    for t in tracks:
        o = db.query(VehicleObservation).filter(
            VehicleObservation.camera_id == t.camera_id,
            VehicleObservation.track_id == t.track_id
        ).first()
        if o:
            obs_list.append(o)
            
    if not obs_list:
        return []
        
    obs_list.sort(key=lambda x: x.observed_at)
    
    # Get all link candidates involving these observations
    obs_ids = [o.id for o in obs_list]
    obs_ids = [o.id for o in obs_list]
    links = db.query(CrossCameraLinkCandidate).filter(
        CrossCameraLinkCandidate.source_observation_id.in_(obs_ids),
        CrossCameraLinkCandidate.destination_observation_id.in_(obs_ids),
        CrossCameraLinkCandidate.temporal_feasibility != 'impossible'
    ).all()
    
    # Build adjacency list
    adj = {o.id: [] for o in obs_list}
    link_map = {}
    
    obs_by_id = {o.id: o for o in obs_list}
    
    for link in links:
        src = obs_by_id.get(link.source_observation_id)
        dst = obs_by_id.get(link.destination_observation_id)
        if not src or not dst:
            continue
            
        # Isolation check: mode must match across src, link, dst
        mode = src.mode
        if dst.mode != mode or link.mode != mode:
            continue
            
        adj[link.source_observation_id].append(link.destination_observation_id)
        link_map[(link.source_observation_id, link.destination_observation_id)] = link
        
    # Find start nodes (indegree 0)
    indegree = {o.id: 0 for o in obs_list}
    for u in adj:
        for v in adj[u]:
            indegree[v] += 1
            
    start_nodes = [o.id for o in obs_list if indegree[o.id] == 0]
    if not start_nodes and obs_list:
        # if cycle or all connected, just pick the earliest
        start_nodes = [obs_list[0].id]
        
    obs_by_id = {o.id: o for o in obs_list}
    
    chains = []
    
    def dfs(current_id, current_chain_obs, current_chain_links):
        # We must enforce REAL vs SIMULATION isolation
        current_obs = obs_by_id[current_id]
        mode = current_obs.mode
        
        # Check if we can extend
        extended = False
        for nxt_id in adj[current_id]:
            nxt_obs = obs_by_id[nxt_id]
            link = link_map[(current_id, nxt_id)]
            
            # Isolation check
            if nxt_obs.mode != mode or link.mode != mode:
                continue # mixed mode rejected
                
            extended = True
            dfs(nxt_id, current_chain_obs + [nxt_obs], current_chain_links + [link])
            
        if not extended and len(current_chain_obs) > 0:
            chains.append({
                "observations": current_chain_obs,
                "links": current_chain_links
            })
            
    for sn in start_nodes:
        dfs(sn, [obs_by_id[sn]], [])
        
    result = []
    
    for idx, c in enumerate(chains):
        chain_obs = c["observations"]
        chain_links = c["links"]
        
        # Determine chain status
        status = RouteChainStatus.ACTIVE
        if any(l.machine_assessment == MachineAssessment.WEAK for l in chain_links):
            status = RouteChainStatus.ACTIVE
        if any(l.machine_assessment == MachineAssessment.CONFLICTING for l in chain_links):
            status = RouteChainStatus.ACTIVE
            
        mode_val = chain_obs[0].mode if chain_obs else Mode.REAL
        
        # Check for gaps (UNOBSERVED_SEGMENT)
        # We could infer this if the CameraGraphEdge has multiple hops, but Phase 4 edges 
        # are direct. If we wanted to show gaps, we'd look at graph distance > 1. 
        # For now, we rely on the link candidate's provenance/explanation.
        
        chain_data = {
            "route_chain_id": str(uuid.uuid4()),
            "status": status.value if hasattr(status, 'value') else status,
            "mode": mode_val.value if hasattr(mode_val, 'value') else mode_val,
            "is_simulated": mode_val == Mode.SIMULATED,
            "cameras_visited": [o.camera_id for o in chain_obs],
            "timestamps": [o.observed_at.isoformat() if o.observed_at else None for o in chain_obs],
            "observations": [
                {
                    "id": str(o.id),
                    "camera_id": o.camera_id,
                    "observed_at": o.observed_at.isoformat() if o.observed_at else None,
                    "status": o.status.value if hasattr(o.status, 'value') else o.status,
                    "vehicle_type": o.vehicle_type.value if hasattr(o.vehicle_type, 'value') else o.vehicle_type,
                    "color": o.color,
                    "plate": o.plate,
                    "timestamp_source": getattr(o.timestamp_source, 'value', o.timestamp_source) if o.timestamp_source else None,
                    "verified_action": getattr(o.human_review_state, 'value', str(o.human_review_state)) if hasattr(o, 'human_review_state') and o.human_review_state else "pending"
                } for o in chain_obs
            ],
            "links": [
                {
                    "id": str(l.id),
                    "source_id": str(l.source_observation_id),
                    "destination_id": str(l.destination_observation_id),
                    "temporal_feasibility": l.temporal_feasibility,
                    "machine_assessment": l.machine_assessment.value if hasattr(l.machine_assessment, 'value') else l.machine_assessment,
                    "link_score": l.link_score,
                    "explanation": l.explanation,
                    "verified_action": getattr(l, 'verified_action', None) # F3 might have verified_action
                } for l in chain_links
            ]
        }
        result.append(chain_data)
        
    return result
