"""
Generate a synthetic test video for integration testing.

Creates a simple video with moving colored rectangles (simulating vehicles)
that YOLO can detect. This validates the full pipeline wiring:
  video → YOLO → tracking → target evaluation → evidence → backend → frontend

This does NOT validate real OCR — use a real traffic MP4 for that.

Usage:
    python generate_test_video.py
    python generate_test_video.py --output my_test.mp4 --frames 300 --fps 15
"""
import argparse
import os
import cv2
import numpy as np


def generate_test_video(
    output_path: str = "sample.mp4",
    width: int = 640,
    height: int = 480,
    fps: int = 15,
    total_frames: int = 150,
):
    """
    Generate a video with moving rectangles that resemble vehicles.
    
    Uses realistic-ish proportions and colors that YOLO might detect
    as vehicles (car-sized bounding boxes on a road-like background).
    """
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    if not out.isOpened():
        print(f"ERROR: Could not create video writer for {output_path}")
        return False

    # "Vehicles" — each is a colored rectangle that moves across the frame
    vehicles = [
        {
            "color": (0, 0, 200),       # Red (BGR)
            "label": "red car",
            "x": 50, "y": 200,
            "w": 120, "h": 80,
            "dx": 3, "dy": 0,
        },
        {
            "color": (200, 200, 200),   # White/silver
            "label": "white car",
            "x": 400, "y": 280,
            "w": 130, "h": 85,
            "dx": -2, "dy": 0,
        },
        {
            "color": (180, 100, 30),    # Blue
            "label": "blue truck",
            "x": 100, "y": 120,
            "w": 160, "h": 100,
            "dx": 2, "dy": 1,
        },
    ]

    for frame_idx in range(total_frames):
        # Road-like background (gray asphalt)
        bg = np.full((height, width, 3), (80, 80, 80), dtype=np.uint8)
        
        # Add some road markings
        for lane_y in [180, 260, 340]:
            for x_start in range(0, width, 60):
                cv2.rectangle(bg, (x_start, lane_y - 2), (x_start + 30, lane_y + 2), (200, 200, 200), -1)
        
        # Draw and move vehicles
        for v in vehicles:
            x, y = int(v["x"]), int(v["y"])
            w, h = v["w"], v["h"]
            
            # Draw vehicle body
            cv2.rectangle(bg, (x, y), (x + w, y + h), v["color"], -1)
            
            # Add some detail (windows, wheels) to make it more vehicle-like
            # Windshield
            cv2.rectangle(bg, (x + 10, y + 5), (x + w - 10, y + int(h * 0.4)), 
                         (int(v["color"][0] * 0.6), int(v["color"][1] * 0.6), int(v["color"][2] * 0.6)), -1)
            # Wheels
            wheel_y = y + h - 10
            cv2.circle(bg, (x + 20, wheel_y), 10, (30, 30, 30), -1)
            cv2.circle(bg, (x + w - 20, wheel_y), 10, (30, 30, 30), -1)
            
            # Add a fake "plate" region (white rectangle with text)
            plate_x = x + w // 2 - 25
            plate_y = y + h - 25
            cv2.rectangle(bg, (plate_x, plate_y), (plate_x + 50, plate_y + 15), (255, 255, 255), -1)
            cv2.putText(bg, "GJ01XX", (plate_x + 2, plate_y + 12), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0, 0, 0), 1)
            
            # Move vehicle
            v["x"] += v["dx"]
            v["y"] += v["dy"]
            
            # Wrap around
            if v["x"] > width:
                v["x"] = -v["w"]
            elif v["x"] + v["w"] < 0:
                v["x"] = width
            if v["y"] > height:
                v["y"] = -v["h"]
            elif v["y"] + v["h"] < 0:
                v["y"] = height

        # Add frame counter
        cv2.putText(bg, f"Frame {frame_idx}", (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
        
        out.write(bg)

    out.release()
    print(f"Generated test video: {output_path}")
    print(f"  Resolution: {width}x{height}")
    print(f"  FPS: {fps}")
    print(f"  Frames: {total_frames}")
    print(f"  Duration: {total_frames / fps:.1f}s")
    print(f"  Vehicles: {len(vehicles)}")
    print(f"\nNOTE: This synthetic video tests pipeline wiring only.")
    print(f"      Use a real traffic MP4 to validate OCR accuracy.")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic test video for ANPR pipeline")
    parser.add_argument("--output", default="sample.mp4", help="Output path")
    parser.add_argument("--frames", type=int, default=150, help="Total frames")
    parser.add_argument("--fps", type=int, default=15, help="Frames per second")
    args = parser.parse_args()
    
    generate_test_video(output_path=args.output, total_frames=args.frames, fps=args.fps)
