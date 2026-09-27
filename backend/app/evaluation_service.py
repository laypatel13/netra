"""
Prediction Evaluation Service — Phase 10 enhanced implementation.

Walk-forward backtesting engine with per-edge breakdown and
structured reporting.

Terminology (F9):
  This is "Prediction Validation" and "Statistical Dispersion" analysis.
  It is NOT "Calibrated Probability" — no frequency-vs-predicted calibration
  curve has been computed or validated.

F7 fix: Uses a savepoint to temporarily mutate InvestigationState
  for prediction, then always rolls back. The prediction service
  is called within the savepoint boundary.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict
from sqlalchemy.orm import Session

from app.models import (
    InvestigationTarget,
    RouteChain,
    RouteChainObservation,
    VehicleObservation,
    InvestigationState,
    CameraTransitionRecord,
    TransitionEligibility,
    Mode,
)
from app.prediction_service import PredictionService


class EvaluationService:
    @staticmethod
    def evaluate_route(
        db: Session, 
        route_chain_id: str,
        baseline_version: str = "3.0",
    ) -> Dict[str, Any]:
        """
        Backtests predictions against a known historical route.

        Walk-forward methodology:
          For each step i in the route (except the last), generate a prediction
          using data available *only up to step i's timestamp*, and compare
          against the actual next step.

        F7: Does NOT mutate InvestigationState. Instead, temporarily sets
            last_seen_observation_id within a savepoint that is always rolled back.

        Temporal separation:
          The data_cutoff ensures that only transitions created BEFORE the
          current observation's timestamp are used. This prevents temporal leakage.
        """
        chain = db.query(RouteChain).filter_by(id=route_chain_id).first()
        if not chain:
            raise ValueError("RouteChain not found")

        target = (
            db.query(InvestigationTarget).filter_by(id=chain.target_id).first()
        )
        if not target:
            raise ValueError("Target not found")

        obs_seq = (
            db.query(RouteChainObservation)
            .filter_by(route_chain_id=chain.id)
            .order_by(RouteChainObservation.sequence_order)
            .all()
        )

        if len(obs_seq) < 2:
            return {"status": "insufficient_length", "steps": []}

        state = (
            db.query(InvestigationState)
            .filter_by(target_id=target.id)
            .first()
        )
        if not state:
            raise ValueError("Target has no state")

        # F7: Save original state for guaranteed restoration
        original_last_seen_id = state.last_seen_observation_id

        results = []
        metrics = {
            "total_evaluations": 0,
            "top_1_hits": 0,
            "top_3_hits": 0,
            "time_window_hits": 0,
            "insufficient_data": 0,
            "no_prediction": 0,
            "false_candidate_avg": 0.0,
        }

        # Phase 10: per-edge breakdown
        per_edge_results = defaultdict(lambda: {
            "evaluations": 0,
            "top_1_hits": 0,
            "top_3_hits": 0,
            "time_window_hits": 0,
            "insufficient_data": 0,
        })

        total_false_candidates = 0

        try:
            # F7: Use a nested savepoint so we can always rollback the state mutation
            savepoint = db.begin_nested()

            try:
                for i in range(len(obs_seq) - 1):
                    current_obs_ref = obs_seq[i]
                    next_obs_ref = obs_seq[i + 1]

                    current_obs = (
                        db.query(VehicleObservation)
                        .filter_by(id=current_obs_ref.observation_id)
                        .first()
                    )
                    next_obs = (
                        db.query(VehicleObservation)
                        .filter_by(id=next_obs_ref.observation_id)
                        .first()
                    )

                    if (
                        not current_obs
                        or not next_obs
                        or not current_obs.observed_at
                        or not next_obs.observed_at
                    ):
                        continue

                    # F7: Temporarily point state to current observation (within savepoint)
                    state.last_seen_observation_id = current_obs.id
                    db.flush()

                    # Predict using data cutoff and strict hold-out to prevent temporal and session leakage
                    predictions = PredictionService.predict_next_cameras(
                        db, 
                        str(target.id), 
                        data_cutoff=current_obs.observed_at,
                        exclude_session_id=current_obs.session_id,
                        baseline_version=baseline_version,
                    )

                    metrics["total_evaluations"] += 1

                    actual_next_camera = next_obs.camera_id
                    actual_next_time = next_obs.observed_at

                    # Phase 10: track per-edge
                    edge_key = f"{current_obs.camera_id}->{actual_next_camera}"
                    per_edge_results[edge_key]["evaluations"] += 1

                    # Sort by hypothesis_score descending
                    predictions.sort(
                        key=lambda p: p.get("hypothesis_score", 0), reverse=True
                    )

                    top_1_hit = False
                    top_3_hit = False
                    time_window_hit = False
                    insufficient = False
                    false_candidates = 0

                    if not predictions or all(
                        p.get("status") == "INSUFFICIENT_DATA" for p in predictions
                    ):
                        insufficient = True
                        metrics["insufficient_data"] += 1
                        per_edge_results[edge_key]["insufficient_data"] += 1
                    else:
                        valid_preds = [
                            p
                            for p in predictions
                            if p.get("status") == "HYPOTHESIS"
                        ]

                        if not valid_preds:
                            metrics["no_prediction"] += 1
                        else:
                            false_candidates = len(valid_preds)

                            for rank, p in enumerate(valid_preds):
                                if p["candidate_camera_id"] == actual_next_camera:
                                    false_candidates -= 1
                                    if rank == 0:
                                        top_1_hit = True
                                        metrics["top_1_hits"] += 1
                                        per_edge_results[edge_key]["top_1_hits"] += 1
                                    if rank < 3:
                                        top_3_hit = True
                                        metrics["top_3_hits"] += 1
                                        per_edge_results[edge_key]["top_3_hits"] += 1

                                    window = p.get("estimated_time_window")
                                    if (
                                        window
                                        and window.get("start")
                                        and window.get("end")
                                    ):
                                        start_t = datetime.fromisoformat(
                                            window["start"]
                                        )
                                        end_t = datetime.fromisoformat(
                                            window["end"]
                                        )
                                        if start_t <= actual_next_time <= end_t:
                                            time_window_hit = True
                                            metrics["time_window_hits"] += 1
                                            per_edge_results[edge_key]["time_window_hits"] += 1
                                    break

                    total_false_candidates += false_candidates

                    results.append(
                        {
                            "step": i + 1,
                            "source_camera": current_obs.camera_id,
                            "actual_next_camera": actual_next_camera,
                            "actual_time": actual_next_time.isoformat(),
                            "top_1_hit": top_1_hit,
                            "top_3_hit": top_3_hit,
                            "time_window_hit": time_window_hit,
                            "insufficient_data": insufficient,
                            "false_candidates": false_candidates,
                            "model_version": predictions[0].get("model_version") if predictions else None,
                            "graph_version": predictions[0].get("graph_version") if predictions else None,
                            "data_cutoff": current_obs.observed_at.isoformat(),
                            "predictions": predictions,
                        }
                    )

            finally:
                # F7: Always rollback the savepoint to restore original state
                savepoint.rollback()

        except Exception:
            # Ensure state is restored even on unexpected error
            state.last_seen_observation_id = original_last_seen_id
            db.flush()
            raise

        total_eval = metrics["total_evaluations"]
        if total_eval > 0:
            metrics["false_candidate_avg"] = total_false_candidates / total_eval
            metrics["insufficient_data_rate"] = metrics["insufficient_data"] / total_eval
            metrics["no_prediction_rate"] = metrics["no_prediction"] / total_eval
            metrics["top_1_rate"] = metrics["top_1_hits"] / total_eval
            metrics["top_3_rate"] = metrics["top_3_hits"] / total_eval
            metrics["time_window_rate"] = metrics["time_window_hits"] / total_eval
        else:
            metrics["insufficient_data_rate"] = 0.0
            metrics["no_prediction_rate"] = 0.0
            metrics["top_1_rate"] = 0.0
            metrics["top_3_rate"] = 0.0
            metrics["time_window_rate"] = 0.0

        # Phase 10: compute per-edge rates
        per_edge_summary = {}
        for edge_key, edge_data in per_edge_results.items():
            n = edge_data["evaluations"]
            per_edge_summary[edge_key] = {
                "evaluations": n,
                "top_1_rate": edge_data["top_1_hits"] / n if n > 0 else 0.0,
                "top_3_rate": edge_data["top_3_hits"] / n if n > 0 else 0.0,
                "time_window_rate": edge_data["time_window_hits"] / n if n > 0 else 0.0,
                "insufficient_data_rate": edge_data["insufficient_data"] / n if n > 0 else 0.0,
            }

        return {
            "status": "success",
            "evaluation_method": "walk_forward",
            "metrics": metrics,
            "per_edge_breakdown": per_edge_summary,
            "steps": results,
        }

    @staticmethod
    def evaluate_all_routes(
        db: Session,
        target_id: str,
        baseline_version: str = "3.0",
    ) -> Dict[str, Any]:
        """
        Runs walk-forward evaluation across ALL route chains for a target.
        Aggregates metrics across routes.
        """
        chains = (
            db.query(RouteChain)
            .filter_by(target_id=target_id)
            .all()
        )

        if not chains:
            return {
                "status": "no_routes",
                "message": "No route chains found for this target",
                "route_results": [],
            }

        route_results = []
        aggregate = {
            "total_evaluations": 0,
            "top_1_hits": 0,
            "top_3_hits": 0,
            "time_window_hits": 0,
            "insufficient_data": 0,
            "no_prediction": 0,
        }

        for chain in chains:
            try:
                result = EvaluationService.evaluate_route(db, str(chain.id), baseline_version=baseline_version)
                route_results.append({
                    "route_chain_id": str(chain.id),
                    "mode": chain.mode.value if hasattr(chain.mode, 'value') else chain.mode,
                    "result": result,
                })
                if result["status"] == "success":
                    m = result["metrics"]
                    aggregate["total_evaluations"] += m["total_evaluations"]
                    aggregate["top_1_hits"] += m["top_1_hits"]
                    aggregate["top_3_hits"] += m["top_3_hits"]
                    aggregate["time_window_hits"] += m["time_window_hits"]
                    aggregate["insufficient_data"] += m["insufficient_data"]
                    aggregate["no_prediction"] += m["no_prediction"]
            except Exception as e:
                route_results.append({
                    "route_chain_id": str(chain.id),
                    "error": str(e),
                })

        total = aggregate["total_evaluations"]
        if total > 0:
            aggregate["top_1_rate"] = aggregate["top_1_hits"] / total
            aggregate["top_3_rate"] = aggregate["top_3_hits"] / total
            aggregate["time_window_rate"] = aggregate["time_window_hits"] / total
            aggregate["insufficient_data_rate"] = aggregate["insufficient_data"] / total
            aggregate["no_prediction_rate"] = aggregate["no_prediction"] / total
        else:
            aggregate["top_1_rate"] = 0.0
            aggregate["top_3_rate"] = 0.0
            aggregate["time_window_rate"] = 0.0
            aggregate["insufficient_data_rate"] = 0.0
            aggregate["no_prediction_rate"] = 0.0

        return {
            "status": "success",
            "routes_evaluated": len(chains),
            "aggregate_metrics": aggregate,
            "route_results": route_results,
        }

    @staticmethod
    def generate_evaluation_report(
        db: Session,
        target_id: str,
    ) -> Dict[str, Any]:
        """
        Generates a structured evaluation report benchmarking both Phase 8.1 (2.1)
        and Phase 10 (3.0) models on identical walk-forward splits.
        """
        eval_2_1 = EvaluationService.evaluate_all_routes(db, target_id, baseline_version="2.1")
        eval_3_0 = EvaluationService.evaluate_all_routes(db, target_id, baseline_version="3.0")

        if eval_2_1["status"] != "success" or eval_3_0["status"] != "success":
            return {
                "status": "error",
                "recommendation": "CANNOT_EVALUATE",
                "reason": "No routes available for evaluation",
            }

        total_evaluations = eval_3_0["aggregate_metrics"]["total_evaluations"]

        if total_evaluations == 0:
            return {
                "status": "evaluated",
                "recommendation": "CANNOT_EVALUATE",
                "reason": "Zero evaluation steps executed",
            }

        # Data sufficiency summary
        transition_count_real = (
            db.query(CameraTransitionRecord)
            .filter_by(transition_status=TransitionEligibility.ELIGIBLE, mode=Mode.REAL)
            .count()
        )

        if transition_count_real < 100:
            recommendation = "INSUFFICIENT_REAL_DATA"
            reason = f"Only {transition_count_real} REAL transitions available. A minimum of 100-500 is required to establish a trustworthy real-world baseline."
        else:
            recommendation = "EVALUATED_ON_REAL_DATA"
            reason = "Sufficient real data exists. See comparative baseline performance."

        return {
            "status": "evaluated",
            "evaluation_method": "strict_walk_forward_holdout",
            "data_summary": {
                "real_eligible_transitions": transition_count_real,
                "evaluation_steps": total_evaluations,
                "routes_evaluated": eval_3_0["routes_evaluated"],
            },
            "baselines": {
                "v2.1_support_count": eval_2_1["aggregate_metrics"],
                "v3.0_relative_frequency": eval_3_0["aggregate_metrics"],
            },
            "recommendation": recommendation,
            "reason": reason,

            "caveats": [
                "Validated on synthetic data only. Real-world performance unknown.",
                "Hypothesis scores are NOT calibrated probabilities.",
                "LAST_OBSERVED is not CURRENT_LOCATION.",
                "Predictions are hypotheses, never confirmed observations.",
            ],
        }
