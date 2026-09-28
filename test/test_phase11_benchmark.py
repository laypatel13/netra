"""
Phase 11 — Real-Data Validation & Prediction Benchmark Tests.

Validates dual-baseline evaluation, strict hold-out (leakage prevention),
and correct reporting of REAL data sufficiency constraints.
"""
import pytest
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app.models import (
    InvestigationTarget,
    CameraTransitionRecord,
    Mode,
)
from app.evaluation_data import seed_evaluation_data
from app.evaluation_service import EvaluationService
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


def test_baseline_comparison(db_session: Session):
    """Benchmark evaluates both baselines and compares them."""
    result = seed_evaluation_data(db_session)
    target_id = result["target_id"]

    report = EvaluationService.generate_evaluation_report(db_session, target_id)

    assert report["status"] == "evaluated"
    assert "baselines" in report
    
    baselines = report["baselines"]
    assert "v2.1_support_count" in baselines
    assert "v3.0_relative_frequency" in baselines

    # Because synthetic data provides strong frequent patterns, top-1 hits should be identical
    # in rank, but we just verify the structure is populated.
    assert baselines["v2.1_support_count"]["total_evaluations"] > 0
    assert baselines["v3.0_relative_frequency"]["total_evaluations"] > 0


def test_real_data_requirement(db_session: Session):
    """The benchmark demands >=100 REAL transitions; correctly flags insufficiency."""
    result = seed_evaluation_data(db_session)
    target_id = result["target_id"]

    # We seeded SIMULATED data, so REAL should be 0.
    report = EvaluationService.generate_evaluation_report(db_session, target_id)

    assert report["status"] == "evaluated"
    assert report["recommendation"] == "INSUFFICIENT_REAL_DATA"
    assert report["data_summary"]["real_eligible_transitions"] == 0


def test_strict_holdout_no_session_leakage(db_session: Session):
    """Prediction service filters out the excluded session ID."""
    result = seed_evaluation_data(db_session)
    
    # Pick the first transition
    trans = db_session.query(CameraTransitionRecord).first()
    assert trans is not None

    # Get stats WITH the session
    stats_with = PredictionService.get_transition_statistics(
        db_session,
        source_camera_id=trans.source_camera_id,
        mode=trans.mode,
    )
    total_with = sum(s.sample_count for s in stats_with)

    # Get stats EXCLUDING the session
    stats_without = PredictionService.get_transition_statistics(
        db_session,
        source_camera_id=trans.source_camera_id,
        mode=trans.mode,
        exclude_session_id=trans.session_id,
    )
    total_without = sum(s.sample_count for s in stats_without)

    # The total without the session should be strictly less than with it,
    # proving the hold-out works.
    assert total_without < total_with
