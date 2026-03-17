import numpy as np
import gtsam
from rplidar import RPLidar
import time
import cv2
import yaml

# --- CONFIGURATION (Based on your working script) ---
PORT_NAME = '/dev/ttyUSB0'
BAUDRATE = 256000
RESOLUTION = 0.05  # 5cm per pixel
MAP_SIZE_M = 20.0  # 20x20 meter map
MAP_PIXELS = int(MAP_SIZE_M / RESOLUTION)

class SLAMMapper:
    def __init__(self):
        # 1. Initialize GTSAM Factor Graph
        self.graph = gtsam.NonlinearFactorGraph()
        self.initial_estimates = gtsam.Values()
        self.pose_id = 0
        
        # Noise Models (How much we trust our LiDAR-based odometry)
        self.prior_noise = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.1, 0.1, 0.05]))
        self.odo_noise = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.1, 0.1, 0.05]))

        # Add Start Pose (0,0,0)
        start_pose = gtsam.Pose2(0, 0, 0)
        self.graph.add(gtsam.PriorFactorPose2(self.pose_id, start_pose, self.prior_noise))
        self.initial_estimates.insert(self.pose_id, start_pose)

        # 2. Map Buffer (Occupancy Grid)
        # 127 = Unknown, 0 = Obstacle, 255 = Free
        self.grid_map = np.ones((MAP_PIXELS, MAP_PIXELS), dtype=np.uint8) * 127
        self.prev_scan_pts = None

    def get_odometry_from_scans(self, current_pts):
        """
        Placeholder for Scan Matching (ICP). 
        In a real scenario, you'd align current_pts to self.prev_scan_pts.
        """
        # For now, we assume a small identity transform or simple movement
        # Integration with an ICP library like 'pylidar-slam' or 'open3d' is recommended here
        return gtsam.Pose2(0.05, 0.0, 0.01) 

    def update_map(self, pose, points):
        """ Projects LiDAR points into the global grid map """
        cx, cy = MAP_PIXELS // 2, MAP_PIXELS // 2
        
        # Robot's current optimized position in pixels
        rx = int(cx + pose.x() / RESOLUTION)
        ry = int(cy + pose.y() / RESOLUTION)

        for pt in points:
            # pt is [x, y] in robot local frame
            # Rotate and translate to global frame
            cos_t = np.cos(pose.theta())
            sin_t = np.sin(pose.theta())
            
            gx = pose.x() + (pt[0] * cos_t - pt[1] * sin_t)
            gy = pose.y() + (pt[0] * sin_t + pt[1] * cos_t)

            px = int(cx + gx / RESOLUTION)
            py = int(cy + gy / RESOLUTION)

            if 0 <= px < MAP_PIXELS and 0 <= py < MAP_PIXELS:
                # Mark hit (obstacle)
                self.grid_map[py, px] = 0
                # Optional: You could draw a line from (rx, ry) to (px, py) 
                # using cv2.line to mark 'free space' (255)

    def save_output(self, name="pitube_map"):
        # Save PGM image
        cv2.imwrite(f"{name}.pgm", self.grid_map)
        
        # Save YAML
        origin = [-(MAP_PIXELS//2)*RESOLUTION, -(MAP_PIXELS//2)*RESOLUTION, 0.0]
        meta = {
            "image": f"{name}.pgm",
            "resolution": RESOLUTION,
            "origin": origin,
            "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196
        }
        with open(f"{name}.yaml", 'w') as f:
            yaml.dump(meta, f)
        print(f"\nMap saved: {name}.pgm and {name}.yaml")

def main():
    mapper = SLAMMapper()
    try:
        lidar = RPLidar(PORT_NAME, baudrate=BAUDRATE)
        print("GTSAM Mapping Online. Move the LiDAR slowly...")
        
        for i, scan in enumerate(lidar.iter_scans()):
            # Convert scan to list of points [x, y] in meters
            points = []
            for (_, angle, dist) in scan:
                if dist > 150: # Ignore self-noise
                    d_m = dist / 1000.0
                    ang_rad = np.radians(angle)
                    points.append([d_m * np.cos(ang_rad), d_m * np.sin(ang_rad)])
            
            if not points: continue

            # 1. Calculate movement (Odometry)
            # In your tracker, you tracked 'moving' objects. 
            # In SLAM, we look at 'static' objects to see how WE moved.
            delta_pose = mapper.get_odometry_from_scans(points)

            # 2. Add to GTSAM Graph
            prev_pose = mapper.initial_estimates.atPose2(mapper.pose_id)
            new_pose_estimate = prev_pose.compose(delta_pose)
            
            mapper.pose_id += 1
            mapper.initial_estimates.insert(mapper.pose_id, new_pose_estimate)
            mapper.graph.add(gtsam.BetweenFactorPose2(mapper.pose_id-1, mapper.pose_id, delta_pose, mapper.odo_noise))

            # 3. Optimize (every 5 scans to save CPU on Pi)
            if i % 5 == 0:
                optimizer = gtsam.LevenbergMarquardtOptimizer(mapper.graph, mapper.initial_estimates)
                mapper.initial_estimates = optimizer.optimize()

            # 4. Update Map with the latest optimized pose
            current_pose = mapper.initial_estimates.atPose2(mapper.pose_id)
            mapper.update_map(current_pose, points)

            print(f"Pose: {current_pose.x():.2f}, {current_pose.y():.2f} | Scans: {i}", end='\r')

    except KeyboardInterrupt: print("\nSping...")
    finally: lidar.stop(); lidar.disconnect(); mapper.save_output()

if __name__ == "__main__": main()