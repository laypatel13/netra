"""
Frame sampling / throttling layer for NETRA pipeline.

Decouples camera capture rate from processing rate. The camera continues
receiving frames at its native FPS while only selected frames are passed
downstream to the detector.

This is a thin extraction of the grab_index % process_every_n logic that
already exists in pipeline.py and multi_camera.py, promoted to a first-class
configurable component.

Example:
    Camera FPS = 25
    Processing FPS = 5
    → process_every_n = 5
    → sampler.should_process() returns True for every 5th frame
"""
from dataclasses import dataclass


@dataclass
class FrameSampler:
    """
    Controls which frames are sent downstream for processing.

    Thread-safety: each camera worker owns its own FrameSampler instance.
    No cross-thread sharing needed.
    """
    process_every_n: int = 5
    _frame_count: int = 0

    def tick(self) -> bool:
        """
        Called once per grabbed frame. Returns True if this frame
        should be decoded and sent for processing.

        Frames where this returns False are grabbed (to keep the
        stream current) but not decoded or processed.
        """
        self._frame_count += 1
        return self._frame_count % self.process_every_n == 0

    @property
    def frame_count(self) -> int:
        """Total frames seen (grabbed) since creation/reset."""
        return self._frame_count

    def reset(self):
        """Reset counter — call after camera reconnect or scene cut."""
        self._frame_count = 0

    @classmethod
    def from_fps(cls, source_fps: float, target_fps: float) -> "FrameSampler":
        """
        Create a sampler that achieves approximately target_fps processing
        rate given the source stream's FPS.

        If source_fps is unknown or <= 0, defaults to process_every_n=5.
        If target_fps >= source_fps, processes every frame.
        """
        if source_fps <= 0 or target_fps <= 0:
            return cls(process_every_n=5)
        n = max(1, int(round(source_fps / target_fps)))
        return cls(process_every_n=n)
