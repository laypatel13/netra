"""
Tests for Phase 4 Cross-Camera Linking Engine.
"""
from datetime import datetime, timedelta
import pytest
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'backend'))
from app.models import (
    VehicleObservation,
    CameraGraphEdge,
    VehicleType,
    TimestampSource,
    TimestampQuality,
)

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'anpr'))
from linking import evaluate_temporal_feasibility, compare_observations


def test_temporal_feasibility_valid():
    edge = CameraGraphEdge(min_travel_time=30.0, max_travel_time=120.0)
    
    t1 = datetime(2023, 1, 1, 12, 0, 0)
    obs_a = VehicleObservation(observed_at=t1, timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP, timestamp_quality=TimestampQuality.VALID)
    obs_b = VehicleObservation(observed_at=t1 + timedelta(seconds=60), timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP, timestamp_quality=TimestampQuality.VALID)
    
    assert evaluate_temporal_feasibility(obs_a, obs_b, edge) == "valid"


def test_temporal_feasibility_impossible_too_fast():
    edge = CameraGraphEdge(min_travel_time=30.0, max_travel_time=120.0)
    
    t1 = datetime(2023, 1, 1, 12, 0, 0)
    obs_a = VehicleObservation(observed_at=t1, timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP, timestamp_quality=TimestampQuality.VALID)
    obs_b = VehicleObservation(observed_at=t1 + timedelta(seconds=10), timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP, timestamp_quality=TimestampQuality.VALID)
    
    assert evaluate_temporal_feasibility(obs_a, obs_b, edge) == "impossible"


def test_compare_observations_possible():
    edge = CameraGraphEdge(min_travel_time=30.0, max_travel_time=120.0)
    
    t1 = datetime(2023, 1, 1, 12, 0, 0)
    obs_a = VehicleObservation(
        observed_at=t1, 
        vehicle_type="car", 
        color="red", 
        plate="ABC1234",
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
    )
    obs_b = VehicleObservation(
        observed_at=t1 + timedelta(seconds=60), 
        vehicle_type="car", 
        color="orange", # Approximate match for red
        plate="ABC1234", # Exact plate match immediately yields possible
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
    )
    
    res = compare_observations(obs_a, obs_b, edge)
    assert res.temporal_feasibility == "valid"
    assert res.status == "possible"
    assert res.attribute_comparisons["type"] == "match"
    assert res.attribute_comparisons["color"] == "approximate"
    assert res.attribute_comparisons["plate"] == "match"
    

def test_compare_observations_conflicting():
    edge = CameraGraphEdge(min_travel_time=30.0, max_travel_time=120.0)
    
    t1 = datetime(2023, 1, 1, 12, 0, 0)
    obs_a = VehicleObservation(
        observed_at=t1, 
        vehicle_type="truck", 
        color="blue", 
        plate="XYZ9999",
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
    )
    obs_b = VehicleObservation(
        observed_at=t1 + timedelta(seconds=60), 
        vehicle_type="truck", 
        color="blue", 
        plate="XYZ1111", # Plate mismatch
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
    )
    
    res = compare_observations(obs_a, obs_b, edge)
    assert res.status == "conflicting"
    assert res.link_score == 0.0


def test_compare_observations_unknown_plate():
    edge = CameraGraphEdge(min_travel_time=30.0, max_travel_time=120.0)
    
    t1 = datetime(2023, 1, 1, 12, 0, 0)
    obs_a = VehicleObservation(
        observed_at=t1, 
        vehicle_type="car", 
        color="white", 
        plate=None,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
    )
    obs_b = VehicleObservation(
        observed_at=t1 + timedelta(seconds=60), 
        vehicle_type="car", 
        color="white", 
        plate=None,
        timestamp_source=TimestampSource.ABSOLUTE_TIMESTAMP,
        timestamp_quality=TimestampQuality.VALID,
    )
    
    res = compare_observations(obs_a, obs_b, edge)
    assert res.status == "possible" # partial match without plate but no contradictions
