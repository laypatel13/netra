
import pytest
from datetime import datetime, timedelta
import uuid

import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.models import (
    VehicleObservation, 
    CrossCameraLinkCandidate, 
    CameraGraphEdge,
    VehicleTrack,
    InvestigationTarget,
    VerificationAction,
    TargetStatus,
    TargetPriority,
    TargetCategory,
    ObservationStatus,
    VehicleType,
    Mode,
    MachineAssessment
)
from app.route_engine import reconstruct_routes

from app.database import SessionLocal
from app.models import Camera

@pytest.fixture
def db_session():
    session = SessionLocal()
    # we can start a transaction and roll back
    session.begin_nested()
    yield session
    session.rollback()
    session.close()

def _create_cameras(session, cam_ids):
    for cid in cam_ids:
        # insert if not exists
        if not session.query(Camera).filter_by(camera_id=cid).first():
            c = Camera(camera_id=cid, department="test", camera_type="ip")
            session.add(c)
    session.flush()

def test_reconstruct_basic_route(db_session):
    # Setup
    _create_cameras(db_session, ["cam_A", "cam_B"])
    target_id = uuid.uuid4()
    target = InvestigationTarget(id=target_id, plate_number="ABC1234")
    db_session.add(target)
    
    # Obs A
    obs_a = VehicleObservation(
        camera_id="cam_A", track_id=1, status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow(), mode=Mode.REAL
    )
    db_session.add(obs_a)
    db_session.flush()
    
    track_a = VehicleTrack(camera_id="cam_A", track_id=1, target_id=target_id)
    db_session.add(track_a)
    
    # Obs B
    obs_b = VehicleObservation(
        camera_id="cam_B", track_id=2, status=ObservationStatus.FINALIZED,
        observed_at=datetime.utcnow() + timedelta(minutes=5), mode=Mode.REAL
    )
    db_session.add(obs_b)
    db_session.flush()
    
    track_b = VehicleTrack(camera_id="cam_B", track_id=2, target_id=target_id)
    db_session.add(track_b)
    
    # Edge A -> B
    edge = CameraGraphEdge(source_camera_id="cam_A", destination_camera_id="cam_B", min_travel_time=60, max_travel_time=600)
    db_session.add(edge)
    db_session.flush()
    
    # Link A -> B
    link = CrossCameraLinkCandidate(
        source_observation_id=obs_a.id,
        destination_observation_id=obs_b.id,
        camera_edge_id=edge.id,
        temporal_feasibility="valid",
        attribute_comparisons={},
        evidence_completeness=1.0,
        link_score=0.9,
        machine_assessment=MachineAssessment.POSSIBLE,
        mode=Mode.REAL
    )
    db_session.add(link)
    db_session.flush()
    
    # Run
    chains = reconstruct_routes(db_session, target_id)
    assert len(chains) == 1
    assert len(chains[0]["observations"]) == 2
    assert chains[0]["cameras_visited"] == ["cam_A", "cam_B"]
    assert chains[0]["status"] == "active"
    assert chains[0]["is_simulated"] == False

def test_simulation_isolation(db_session):
    _create_cameras(db_session, ["cam_A", "cam_B"])
    target_id = uuid.uuid4()
    target = InvestigationTarget(id=target_id)
    db_session.add(target)
    
    # REAL Obs A
    obs_a = VehicleObservation(camera_id="cam_A", track_id=101, mode=Mode.REAL)
    db_session.add(obs_a)
    db_session.add(VehicleTrack(camera_id="cam_A", track_id=101, target_id=target_id))
    db_session.flush()
    
    # SIM Obs B
    obs_b = VehicleObservation(camera_id="cam_B", track_id=102, mode=Mode.SIMULATED)
    db_session.add(obs_b)
    db_session.add(VehicleTrack(camera_id="cam_B", track_id=102, target_id=target_id))
    db_session.flush()
    
    edge = CameraGraphEdge(source_camera_id="cam_A", destination_camera_id="cam_B", min_travel_time=60, max_travel_time=600)
    db_session.add(edge)
    db_session.flush()
    
    # Mixed mode link
    link = CrossCameraLinkCandidate(
        source_observation_id=obs_a.id,
        destination_observation_id=obs_b.id,
        camera_edge_id=edge.id,
        temporal_feasibility="valid",
        attribute_comparisons={},
        evidence_completeness=1.0,
        link_score=0.9,
        machine_assessment=MachineAssessment.POSSIBLE,
        mode=Mode.SIMULATED # SIM link from REAL -> SIM
    )
    db_session.add(link)
    db_session.flush()
    
    # Should not produce A -> B chain, should just produce two separate length-1 chains because they can't be linked
    chains = reconstruct_routes(db_session, target_id)
    # The DFS should reject the link, so we get two single-node chains
    assert len(chains) == 2
    for c in chains:
        assert len(c["observations"]) == 1

def test_branching_routes(db_session):
    _create_cameras(db_session, ["cam_A", "cam_B", "cam_C", "cam_D"])
    target_id = uuid.uuid4()
    target = InvestigationTarget(id=target_id)
    db_session.add(target)
    
    obs_a = VehicleObservation(camera_id="cam_A", track_id=201, mode=Mode.REAL)
    obs_b = VehicleObservation(camera_id="cam_B", track_id=202, mode=Mode.REAL)
    obs_c = VehicleObservation(camera_id="cam_C", track_id=203, mode=Mode.REAL)
    obs_d = VehicleObservation(camera_id="cam_D", track_id=204, mode=Mode.REAL)
    
    db_session.add_all([obs_a, obs_b, obs_c, obs_d])
    db_session.flush()
    
    db_session.add(VehicleTrack(camera_id="cam_A", track_id=201, target_id=target_id))
    db_session.add(VehicleTrack(camera_id="cam_B", track_id=202, target_id=target_id))
    db_session.add(VehicleTrack(camera_id="cam_C", track_id=203, target_id=target_id))
    db_session.add(VehicleTrack(camera_id="cam_D", track_id=204, target_id=target_id))
    
    edge1 = CameraGraphEdge(source_camera_id="cam_A", destination_camera_id="cam_B", min_travel_time=60, max_travel_time=600)
    edge2 = CameraGraphEdge(source_camera_id="cam_B", destination_camera_id="cam_D", min_travel_time=60, max_travel_time=600)
    edge3 = CameraGraphEdge(source_camera_id="cam_A", destination_camera_id="cam_C", min_travel_time=60, max_travel_time=600)
    edge4 = CameraGraphEdge(source_camera_id="cam_C", destination_camera_id="cam_D", min_travel_time=60, max_travel_time=600)
    
    for e in [edge1, edge2, edge3, edge4]:
        db_session.add(e)
    db_session.flush()
    
    def add_link(src, dst, edge, mode=Mode.REAL):
        l = CrossCameraLinkCandidate(
            source_observation_id=src.id, destination_observation_id=dst.id,
            camera_edge_id=edge.id, temporal_feasibility="valid",
            attribute_comparisons={}, evidence_completeness=1.0, link_score=0.9,
            machine_assessment=MachineAssessment.POSSIBLE, mode=mode
        )
        db_session.add(l)
        
    add_link(obs_a, obs_b, edge1, mode=Mode.REAL)
    add_link(obs_b, obs_d, edge2, mode=Mode.REAL)
    add_link(obs_a, obs_c, edge3, mode=Mode.REAL)
    add_link(obs_c, obs_d, edge4, mode=Mode.REAL)
    db_session.flush()
    
    chains = reconstruct_routes(db_session, target_id)
    assert len(chains) == 2
    cams = [c["cameras_visited"] for c in chains]
    assert ["cam_A", "cam_B", "cam_D"] in cams
    assert ["cam_A", "cam_C", "cam_D"] in cams
