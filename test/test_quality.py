import numpy as np
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../anpr')))

from quality import sharpness_score, size_score, compute_quality


def test_quality_sharpness_empty():
    assert sharpness_score(None) == 0.0
    assert sharpness_score(np.array([])) == 0.0


def test_quality_metrics_synthetic():
    # Create a synthetic 100x100 random noise image (should be very "sharp" in variance)
    crop = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
    q = compute_quality(crop)
    
    # Just check it runs and produces reasonable normalized values
    assert 0.0 <= q.sharpness <= 1.0
    assert 0.0 <= q.blur <= 1.0
    assert 0.0 <= q.exposure <= 1.0
    assert 0.0 <= q.size <= 1.0
    assert 0.0 <= q.overall <= 1.0

def test_size_score():
    small_crop = np.zeros((10, 10, 3), dtype=np.uint8)
    large_crop = np.zeros((500, 500, 3), dtype=np.uint8)
    
    assert size_score(small_crop) == 0.0
    assert size_score(large_crop) == 1.0
