"""
Investigation state engine under adversarial conditions.

Covers event idempotency (sequential and concurrent duplicates), retry of
FAILED events, last-seen ordering by timestamp quality, route chain state
after rejection, and graph version bookkeeping. Needs PostgreSQL, see
conftest.py.
"""
import threading
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import (
    InvestigationTarget,
    Camera,
    VehicleTrack,
    VehicleObservation,
    ObservationStatus,
    TimestampSource,
    TimestampQuality,
    InvestigationEvent,
    EventProcessingStatus,
    InvestigationState,
    RouteChain,
    RouteChainStatus,
    EvidenceReviewEntityType,
    EvidenceReviewAction,
    Mode,
)
from conftest import requires_db
from app.investigation_service import (
    process_new_observation,
    process_verification_event,
    get_current_graph_version,
    bump_graph_version,
)


pytestmark = requires_db


def _setup_cameras(db, *camera_ids):
    cams = []
    for cid in camera_ids:
        c = Camera(camera_id=cid, department="police")
        db.add(c)
        cams.append(c)
    db.flush()
    return cams


def _setup_obs(db, camera_id, track_id, target_id, observed_at, **kwargs):
    defaults = dict(
        status=ObservationStatus.FINALIZED,
        timestamp_source=TimestampSource.SOURCE_PTS,
        evidence_completeness=0.9,
        plate="TEST123",
        color="red",
        mode=Mode.REAL,
    )
    defaults.update(kwargs)
    track = VehicleTrack(camera_id=camera_id, track_id=track_id, target_id=target_id)
    db.add(track)
    db.flush()
    obs = VehicleObservation(
        camera_id=camera_id, session_id="test-session", track_id=track_id,
        candidate_id=track.id, observed_at=observed_at, **defaults
    )
    db.add(obs)
    db.flush()
    return obs


# ═══════════════════════════════════════════════════════════
# Sequential duplicate idempotency
# ═══════════════════════════════════════════════════════════


class TestSequentialDuplicateIdempotency:
    def test_duplicate_new_observation_is_noop(self, db_session):
        """Processing the same observation twice produces exactly 1 event."""
        target = InvestigationTarget(plate_number="DUP_SEQ")
        db_session.add(target)
        _setup_cameras(db_session, "cam_dup1")

        obs = _setup_obs(db_session, "cam_dup1", 1, target.id, datetime.utcnow())
        db_session.commit()

        process_new_observation(db_session, str(obs.id))
        process_new_observation(db_session, str(obs.id))

        events = (
            db_session.query(InvestigationEvent)
            .filter_by(event_id=f"NEW_OBSERVATION:{obs.id}")
            .all()
        )
        assert len(events) == 1
        assert events[0].status == EventProcessingStatus.PROCESSED

    def test_duplicate_verification_is_noop(self, db_session):
        """Processing the same verification twice produces exactly 1 processed event."""
        target = InvestigationTarget(plate_number="DUP_VER")
        db_session.add(target)
        _setup_cameras(db_session, "cam_ver1")

        obs = _setup_obs(db_session, "cam_ver1", 1, target.id, datetime.utcnow())
        db_session.commit()
        process_new_observation(db_session, str(obs.id))

        process_verification_event(
            db_session,
            EvidenceReviewEntityType.VEHICLE_OBSERVATION,
            str(obs.id),
            EvidenceReviewAction.ACCEPT,
            "reviewer1",
        )
        process_verification_event(
            db_session,
            EvidenceReviewEntityType.VEHICLE_OBSERVATION,
            str(obs.id),
            EvidenceReviewAction.ACCEPT,
            "reviewer1",
        )

        event_id = f"VERIFICATION:vehicle_observation:{obs.id}:accept:reviewer1"
        events = (
            db_session.query(InvestigationEvent)
            .filter_by(event_id=event_id)
            .all()
        )
        assert len(events) == 1
        assert events[0].status == EventProcessingStatus.PROCESSED


# ═══════════════════════════════════════════════════════════
# Concurrent duplicate idempotency (actual PostgreSQL test)
# ═══════════════════════════════════════════════════════════


class TestConcurrentDuplicateIdempotency:
    def test_concurrent_new_observation(self, db_session):
        """
        Two threads process the same observation concurrently.
        Exactly one must succeed; the other must gracefully skip.
        """
        target = InvestigationTarget(plate_number="CONC1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_conc1")

        obs = _setup_obs(db_session, "cam_conc1", 1, target.id, datetime.utcnow())
        db_session.commit()

        results = {"success": 0, "skip": 0, "error": 0}
        lock = threading.Lock()

        obs_id_str = str(obs.id)
        
        def worker():
            session = SessionLocal()
            try:
                process_new_observation(session, obs_id_str)
                with lock:
                    results["success"] += 1
            except Exception:
                with lock:
                    results["error"] += 1
            finally:
                session.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Both threads should succeed (one processes, one skips as already claimed)
        assert results["error"] == 0, f"Concurrent test produced errors: {results}"

        # Exactly one event should exist
        events = (
            db_session.query(InvestigationEvent)
            .filter_by(event_id=f"NEW_OBSERVATION:{obs.id}")
            .all()
        )
        assert len(events) == 1
        assert events[0].status == EventProcessingStatus.PROCESSED


# ═══════════════════════════════════════════════════════════
# Transaction boundary - FAILED event retry
# ═══════════════════════════════════════════════════════════


class TestFailedEventRetry:
    def test_failed_event_can_be_retried(self, db_session):
        """
        An event that failed processing can be retried after next_retry_at.
        """
        target = InvestigationTarget(plate_number="RETRY1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_retry1")

        obs = _setup_obs(db_session, "cam_retry1", 1, target.id, datetime.utcnow())
        db_session.commit()

        # Create a FAILED event manually
        event = InvestigationEvent(
            event_id=f"NEW_OBSERVATION:{obs.id}",
            event_type="new_observation",
            entity_id=str(obs.id),
            status=EventProcessingStatus.FAILED,
            attempt_count=1,
            last_error="simulated failure",
            next_retry_at=datetime.utcnow() - timedelta(minutes=5),
        )
        db_session.add(event)
        db_session.commit()

        # Retry should pick it up since next_retry_at is in the past
        process_new_observation(db_session, str(obs.id))

        db_session.refresh(event)
        assert event.status == EventProcessingStatus.PROCESSED
        assert event.attempt_count == 2


# ═══════════════════════════════════════════════════════════
# Timestamp quality ordering
# ═══════════════════════════════════════════════════════════


class TestLastSeenOrdering:
    def test_valid_timestamp_preferred_over_unreliable(self, db_session):
        """
        last_seen must prefer VALID timestamp over UNRELIABLE,
        even if the UNRELIABLE observation has a later observed_at.
        """
        target = InvestigationTarget(plate_number="TSORD1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_ts1")

        now = datetime.utcnow()

        # Unreliable observation with LATER timestamp
        obs_unreliable = _setup_obs(
            db_session, "cam_ts1", 1, target.id,
            now + timedelta(minutes=10),
            timestamp_quality=TimestampQuality.UNRELIABLE,
        )
        # Valid observation with EARLIER timestamp
        obs_valid = _setup_obs(
            db_session, "cam_ts1", 2, target.id, now,
            timestamp_quality=TimestampQuality.VALID,
        )
        db_session.commit()

        process_new_observation(db_session, str(obs_unreliable.id))
        process_new_observation(db_session, str(obs_valid.id))

        state = db_session.query(InvestigationState).filter_by(
            target_id=target.id
        ).first()
        assert state is not None
        # Should prefer the VALID observation even though UNRELIABLE has later time
        assert state.last_seen_observation_id == obs_valid.id


# ═══════════════════════════════════════════════════════════
# Route state separation
# ═══════════════════════════════════════════════════════════


class TestRouteStateSeparation:
    def test_rejection_does_not_set_is_superseded(self, db_session):
        """
        Human rejection should set status=INVALIDATED but NOT is_superseded=1.
        is_superseded is only for chain extension.
        """
        target = InvestigationTarget(plate_number="RSEP1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_rs1")

        obs = _setup_obs(db_session, "cam_rs1", 1, target.id, datetime.utcnow())
        db_session.commit()
        process_new_observation(db_session, str(obs.id))

        chains = db_session.query(RouteChain).filter_by(target_id=target.id).all()
        assert len(chains) == 1
        assert chains[0].is_superseded == 0

        # Reject observation
        process_verification_event(
            db_session,
            EvidenceReviewEntityType.VEHICLE_OBSERVATION,
            str(obs.id),
            EvidenceReviewAction.REJECT,
            "reviewer",
        )

        db_session.refresh(chains[0])
        assert chains[0].status == RouteChainStatus.INVALIDATED
        # is_superseded must NOT be set by rejection
        assert chains[0].is_superseded == 0


# ═══════════════════════════════════════════════════════════
# Graph version tracking
# ═══════════════════════════════════════════════════════════


class TestGraphVersioning:
    def test_graph_version_initializes_to_1(self, db_session):
        """First call to get_current_graph_version bootstraps to 1."""
        gv = get_current_graph_version(db_session, Mode.REAL)
        db_session.commit()
        assert gv == 1

    def test_bump_increments_version(self, db_session):
        """bump_graph_version increments the global counter."""
        v1 = get_current_graph_version(db_session, Mode.REAL)
        db_session.commit()
        v2 = bump_graph_version(db_session, Mode.REAL, "test_bump")
        db_session.commit()
        assert v2 == v1 + 1
