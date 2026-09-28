"""Seed demo investigation data for the hackathon presentation."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal, engine
from app.models import (
    Base, InvestigationTarget, VehicleTrack, TrackEvidence,
    TargetCategory, TargetPriority, TargetStatus, TrackStatus, VehicleType
)
from datetime import datetime, timedelta
import uuid

Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Clear old demo data
db.query(TrackEvidence).delete()
db.query(VehicleTrack).delete()
db.query(InvestigationTarget).delete()
db.commit()

# --- Target 1: Stolen car with known plate ---
t1 = InvestigationTarget(
    id=uuid.uuid4(),
    plate_number="GJ01AB1234",
    vehicle_type=VehicleType.car,
    vehicle_color="white",
    description="Stolen Maruti Swift reported near Gandhinagar",
    category=TargetCategory.stolen,
    priority=TargetPriority.critical,
    status=TargetStatus.active,
)
db.add(t1)
db.flush()

# Candidate 1 for Target 1 — strong match
now = datetime.utcnow()
vt1 = VehicleTrack(
    id=uuid.uuid4(),
    camera_id="cam03",
    track_id=42,
    target_id=t1.id,
    started_at=now - timedelta(minutes=15),
    ended_at=now - timedelta(minutes=14),
    final_score=0.91,
    tier="exact_plate",
    score_breakdown={
        "plate_similarity": 1.0,
        "type_match": 1.0,
        "color_match": 1.0,
        "ocr_consensus": 0.88,
        "image_quality": 0.72,
    },
    ocr_consensus={
        "best_plate": "GJ01AB1234",
        "consensus_score": 0.88,
        "supporting_frames": 4,
        "total_frames": 5,
    },
    status=TrackStatus.completed,
)
db.add(vt1)
db.flush()

# Evidence frames for candidate 1
for i in range(5):
    ev = TrackEvidence(
        track_id=vt1.id,
        frame_index=i * 15 + 10,
        quality_score=round(0.6 + i * 0.08, 2),
        raw_path=f"data/evidence/cam03/track_42_f{i}_raw.jpg",
        enhanced_path=f"data/evidence/cam03/track_42_f{i}_enh.jpg" if i < 3 else None,
        ocr_candidate="GJ01AB1234" if i != 2 else "GJ01A81234",
        ocr_confidence=round(0.7 + i * 0.05, 2),
        vehicle_type=VehicleType.car,
        vehicle_color="white",
    )
    db.add(ev)

# Candidate 2 for Target 1 — weaker attribute match from another camera
vt2 = VehicleTrack(
    id=uuid.uuid4(),
    camera_id="cam11",
    track_id=78,
    target_id=t1.id,
    started_at=now - timedelta(minutes=8),
    ended_at=now - timedelta(minutes=7),
    final_score=0.64,
    tier="strong_candidate",
    score_breakdown={
        "plate_similarity": 0.7,
        "type_match": 1.0,
        "color_match": 1.0,
        "ocr_consensus": 0.55,
        "image_quality": 0.58,
    },
    ocr_consensus={
        "best_plate": "GJ01AB12?4",
        "consensus_score": 0.55,
        "supporting_frames": 2,
        "total_frames": 4,
    },
    status=TrackStatus.completed,
)
db.add(vt2)
db.flush()

for i in range(4):
    ev = TrackEvidence(
        track_id=vt2.id,
        frame_index=i * 20 + 5,
        quality_score=round(0.45 + i * 0.1, 2),
        raw_path=f"data/evidence/cam11/track_78_f{i}_raw.jpg",
        enhanced_path=f"data/evidence/cam11/track_78_f{i}_enh.jpg" if i < 2 else None,
        ocr_candidate="GJ01AB1234" if i == 0 else ("GJ01AB12X4" if i == 1 else None),
        ocr_confidence=round(0.5 + i * 0.1, 2) if i < 2 else None,
        vehicle_type=VehicleType.car,
        vehicle_color="white",
    )
    db.add(ev)

# --- Target 2: Suspect truck by attributes ---
t2 = InvestigationTarget(
    id=uuid.uuid4(),
    plate_number=None,
    vehicle_type=VehicleType.truck,
    vehicle_color="blue",
    description="Blue truck seen fleeing hit-and-run, SG Highway",
    category=TargetCategory.suspect,
    priority=TargetPriority.high,
    status=TargetStatus.active,
)
db.add(t2)
db.flush()

vt3 = VehicleTrack(
    id=uuid.uuid4(),
    camera_id="cam07",
    track_id=19,
    target_id=t2.id,
    started_at=now - timedelta(minutes=22),
    ended_at=now - timedelta(minutes=21),
    final_score=0.52,
    tier="attribute_candidate",
    score_breakdown={
        "plate_similarity": 0.0,
        "type_match": 1.0,
        "color_match": 0.85,
        "ocr_consensus": 0.0,
        "image_quality": 0.61,
    },
    ocr_consensus={
        "best_plate": None,
        "consensus_score": 0.0,
        "supporting_frames": 0,
        "total_frames": 3,
    },
    status=TrackStatus.completed,
)
db.add(vt3)
db.flush()

for i in range(3):
    ev = TrackEvidence(
        track_id=vt3.id,
        frame_index=i * 25,
        quality_score=round(0.5 + i * 0.12, 2),
        raw_path=f"data/evidence/cam07/track_19_f{i}_raw.jpg",
        enhanced_path=None,
        ocr_candidate=None,
        ocr_confidence=None,
        vehicle_type=VehicleType.truck,
        vehicle_color="blue",
    )
    db.add(ev)

db.commit()
db.close()

print("✅ Seeded 2 targets, 3 candidates, 12 evidence frames")
