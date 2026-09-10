"""
Small shared serialization helpers - kept separate from the routers so
detections.py and watchlist.py can both use them without an import cycle
(detections.py already depends on watchlist.py for match-checking).
"""
from app import models, schemas


def detection_to_read(d: models.Detection) -> schemas.DetectionRead:
    return schemas.DetectionRead(
        id=d.id,
        plate_number=d.plate_number,
        vehicle_type=d.vehicle_type.value if d.vehicle_type else None,
        vehicle_color=d.vehicle_color,
        thumbnail_url=f"/detections/{d.id}/thumbnail" if d.thumbnail_path else None,
        timestamp_ms=d.timestamp_ms,
        camera_id=d.camera_id,
        confidence=d.confidence,
    )
