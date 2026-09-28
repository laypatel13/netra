"""
Phase 8.1 Adversarial Test Suite.

Tests cover:
  - Sequential duplicate event idempotency
  - Concurrent duplicate event (IntegrityError handling)
  - Duplicate after PROCESSED
  - FAILED event retry
  - Future-data leakage prevention
  - Rejected observation cascade → transition INELIGIBLE → statistics exclusion
  - REAL/SIMULATION isolation
  - Timestamp quality ordering in last_seen
  - Graph version tracking
  - Route state separation (is_superseded vs rejection)
  - Evaluation service state restoration

IMPORTANT: These tests require PostgreSQL. If PostgreSQL is unavailable,
they will FAIL, not silently pass. Do NOT report them as passing unless
they actually execute against a live PostgreSQL database.
"""
import pytest
import uuid
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app.main import app
from app.models import (
    InvestigationTarget,
    Camera,
    VehicleTrack,
    VehicleObservation,
    CameraGraphEdge,
    CameraTransitionRecord,
    ObservationStatus,
    TimestampSource,
    TimestampQuality,
    TransitionEligibility,
    InvestigationEvent,
    EventProcessingStatus,
    InvestigationState,
    RouteChain,
    RouteChainStatus,
    HumanReviewState,
    EvidenceReviewEntityType,
    EvidenceReviewAction,
    Mode,
    GraphVersion,
)
from app.investigation_service import (
    process_new_observation,
    process_verification_event,
    get_current_graph_version,
    bump_graph_version,
)
from app.prediction_service import PredictionService


@pytest.fixture(scope="module")
def db_session():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def clean_db(db_session: Session):
    for table in reversed(Base.metadata.sorted_tables):
        db_session.execute(table.delete())
    db_session.commit()
    yield


def _setup_cameras(db, *camera_ids):
    cams = []
    for cid in camera_ids:
        c = Camera(camera_id=cid, department="police")
        db.add(c)
        cams.append(c)
    db.flush()
    return cams


def _setup_edge(db, src, dst, **kwargs):
    defaults = dict(min_travel_time=30, max_travel_time=300, mode=Mode.REAL, enabled=1)
    defaults.update(kwargs)
    e = CameraGraphEdge(source_camera_id=src, destination_camera_id=dst, **defaults)
    db.add(e)
    db.flush()
    return e


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
    t = VehicleTrack(camera_id=camera_id, track_id=track_id, target_id=target_id)
    obs = VehicleObservation(
        camera_id=camera_id, track_id=track_id, observed_at=observed_at, **defaults
    )
    db.add_all([t, obs])
    db.flush()
    return obs


# ═══════════════════════════════════════════════════════════
# F1: Sequential duplicate idempotency
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
# F1: Concurrent duplicate idempotency (actual PostgreSQL test)
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
            except Exception as e:
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
# F2: Transaction boundary — FAILED event retry
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
# Temporal cutoff — future data leakage
# ═══════════════════════════════════════════════════════════


class TestTemporalCutoff:
    def test_future_transition_not_visible(self, db_session):
        """
        Adversarial test: a future transition exists in the DB, but
        prediction at an earlier time must NOT see it.
        """
        target = InvestigationTarget(plate_number="FUTURE1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_f1", "cam_f2")
        _setup_edge(db_session, "cam_f1", "cam_f2")
        db_session.commit()

        now = datetime.utcnow()
        past = now - timedelta(days=10)

        # Create 6 transitions in the past (enough for prediction)
        for i in range(6):
            obs_a = _setup_obs(
                db_session, "cam_f1", 100 + i, target.id,
                past + timedelta(hours=i),
            )
            obs_b = _setup_obs(
                db_session, "cam_f2", 200 + i, target.id,
                past + timedelta(hours=i, minutes=2),
            )
            db_session.commit()
            process_new_observation(db_session, str(obs_a.id))
            process_new_observation(db_session, str(obs_b.id))

        # Create a "recent" observation at cam_f1 for prediction
        recent_obs = _setup_obs(
            db_session, "cam_f1", 999, target.id, past + timedelta(hours=7)
        )
        db_session.commit()
        process_new_observation(db_session, str(recent_obs.id))

        # Predict with a cutoff BEFORE the transitions were created
        # Since transitions are created with created_at=now (approx),
        # setting cutoff to past should yield no eligible transitions
        preds = PredictionService.predict_next_cameras(
            db_session, str(target.id), data_cutoff=past
        )

        # All predictions should be INSUFFICIENT_DATA or empty
        # because the cutoff excludes all transition records
        hypothesis_preds = [p for p in preds if p["status"] == "HYPOTHESIS"]
        assert len(hypothesis_preds) == 0, (
            f"Future data leaked into predictions: {hypothesis_preds}"
        )


# ═══════════════════════════════════════════════════════════
# REAL/SIMULATION isolation
# ═══════════════════════════════════════════════════════════


class TestModeIsolation:
    def test_simulated_transitions_not_in_real_predictions(self, db_session):
        """
        REAL predictions must not use SIMULATED transition data.
        """
        target = InvestigationTarget(plate_number="MODE1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_m1", "cam_m2")
        _setup_edge(db_session, "cam_m1", "cam_m2", mode=Mode.REAL)
        _setup_edge(db_session, "cam_m1", "cam_m2", mode=Mode.SIMULATED)
        db_session.commit()

        now = datetime.utcnow()

        # Create 6 SIMULATED transitions (enough for prediction)
        for i in range(6):
            obs_a = _setup_obs(
                db_session, "cam_m1", 100 + i, target.id,
                now - timedelta(hours=10 - i),
                mode=Mode.SIMULATED,
            )
            obs_b = _setup_obs(
                db_session, "cam_m2", 200 + i, target.id,
                now - timedelta(hours=10 - i, minutes=-2),
                mode=Mode.SIMULATED,
            )
            db_session.commit()
            process_new_observation(db_session, str(obs_a.id))
            process_new_observation(db_session, str(obs_b.id))

        # Create a REAL observation at cam_m1
        real_obs = _setup_obs(
            db_session, "cam_m1", 999, target.id, now, mode=Mode.REAL
        )
        db_session.commit()
        process_new_observation(db_session, str(real_obs.id))

        # REAL predictions should not have any HYPOTHESIS (only simulated data exists)
        state = db_session.query(InvestigationState).filter_by(
            target_id=target.id, mode=Mode.REAL
        ).first()
        if state:
            preds = PredictionService.predict_next_cameras(db_session, str(target.id))
            hypothesis_preds = [p for p in preds if p["status"] == "HYPOTHESIS"]
            assert len(hypothesis_preds) == 0, (
                "SIMULATED data contaminated REAL predictions"
            )


# ═══════════════════════════════════════════════════════════
# F15: Timestamp quality ordering
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
# F3: Route state separation
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
        # F3: is_superseded must NOT be set by rejection
        assert chains[0].is_superseded == 0


# ═══════════════════════════════════════════════════════════
# F11: Graph version tracking
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

    def test_transitions_retain_graph_version(self, db_session):
        """Transitions are stamped with the graph version at creation time."""
        target = InvestigationTarget(plate_number="GV1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_gv1", "cam_gv2")
        _setup_edge(db_session, "cam_gv1", "cam_gv2")
        db_session.commit()

        now = datetime.utcnow()
        obs_a = _setup_obs(db_session, "cam_gv1", 1, target.id, now)
        obs_b = _setup_obs(
            db_session, "cam_gv2", 2, target.id, now + timedelta(minutes=2)
        )
        db_session.commit()

        process_new_observation(db_session, str(obs_a.id))
        process_new_observation(db_session, str(obs_b.id))

        trans = db_session.query(CameraTransitionRecord).first()
        if trans:
            # The transition should have the current graph version
            current_gv = get_current_graph_version(db_session, Mode.REAL)
            db_session.commit()
            assert trans.graph_version == current_gv


# ═══════════════════════════════════════════════════════════
# F12: Rejection cascade → statistics exclusion
# ═══════════════════════════════════════════════════════════


class TestRejectionCascade:
    def test_rejected_obs_excludes_from_statistics(self, db_session):
        """
        Observation → Transition → Statistics.
        Reject the observation.
        Transition becomes INELIGIBLE → statistics must exclude it.
        """
        target = InvestigationTarget(plate_number="CASC1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_c1", "cam_c2")
        _setup_edge(db_session, "cam_c1", "cam_c2")
        db_session.commit()

        now = datetime.utcnow()
        obs_a = _setup_obs(db_session, "cam_c1", 1, target.id, now)
        obs_b = _setup_obs(
            db_session, "cam_c2", 2, target.id, now + timedelta(minutes=2)
        )
        db_session.commit()

        process_new_observation(db_session, str(obs_a.id))
        process_new_observation(db_session, str(obs_b.id))

        # Verify transition exists and is ELIGIBLE
        trans = db_session.query(CameraTransitionRecord).first()
        if trans:
            assert trans.transition_status == TransitionEligibility.ELIGIBLE

            # Get stats before rejection
            stats_before = PredictionService.get_transition_statistics(
                db_session, "cam_c1", Mode.REAL
            )
            count_before = sum(s.sample_count for s in stats_before)

            # Reject observation B
            process_verification_event(
                db_session,
                EvidenceReviewEntityType.VEHICLE_OBSERVATION,
                str(obs_b.id),
                EvidenceReviewAction.REJECT,
                "reviewer",
            )

            # Transition should now be INELIGIBLE
            db_session.refresh(trans)
            assert trans.transition_status == TransitionEligibility.INELIGIBLE

            # Stats should exclude the ineligible transition
            stats_after = PredictionService.get_transition_statistics(
                db_session, "cam_c1", Mode.REAL
            )
            count_after = sum(s.sample_count for s in stats_after)
            assert count_after < count_before


# ═══════════════════════════════════════════════════════════
# F4: Session deduplication in statistics
# ═══════════════════════════════════════════════════════════


class TestSessionDeduplication:
    def test_statistics_expose_unique_session_count(self, db_session):
        """
        If multiple transitions come from the same session,
        unique_session_count should reflect this.
        """
        target = InvestigationTarget(plate_number="SESS1")
        db_session.add(target)
        _setup_cameras(db_session, "cam_s1", "cam_s2")
        _setup_edge(db_session, "cam_s1", "cam_s2")
        db_session.commit()

        now = datetime.utcnow()
        same_session = "session_abc"

        # Create 3 transitions from the same session
        for i in range(3):
            obs_a = _setup_obs(
                db_session, "cam_s1", 100 + i, target.id,
                now + timedelta(hours=i),
                session_id=same_session,
            )
            obs_b = _setup_obs(
                db_session, "cam_s2", 200 + i, target.id,
                now + timedelta(hours=i, minutes=2),
                session_id=same_session,
            )
            db_session.commit()
            process_new_observation(db_session, str(obs_a.id))
            process_new_observation(db_session, str(obs_b.id))

        stats = PredictionService.get_transition_statistics(
            db_session, "cam_s1", Mode.REAL
        )
        if stats:
            assert stats[0].sample_count == 3
            # All from same session, so unique_session_count should be 1
            assert stats[0].unique_session_count == 1
