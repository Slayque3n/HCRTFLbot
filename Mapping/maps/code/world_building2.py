import numpy as np
import gtsam
from rplidar import RPLidar
import time
import cv2
import yaml

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
PORT_NAME  = '/dev/ttyUSB0'
BAUDRATE   = 256000
RESOLUTION = 0.05        # metres per pixel  (5 cm)
MAP_SIZE_M = 20.0        # map covers 20 × 20 m
MAP_PIXELS = int(MAP_SIZE_M / RESOLUTION)   # 400 × 400 px

# ICP tuning
ICP_MAX_DIST      = 0.5   # metres – max point-pair distance for ICP
ICP_MIN_POINTS    = 20    # need at least this many points to attempt ICP
ICP_FITNESS_THRESH = 0.30 # reject ICP result if fitness score is below this

# LiDAR filtering
DIST_MIN_MM = 150         # ignore returns closer than 15 cm (self-noise)
DIST_MAX_MM = 8000        # ignore returns further than 8 m (spurious)

# Graph optimisation cadence
OPTIMISE_EVERY = 5        # re-run LM optimiser every N scans


# ─────────────────────────────────────────────
#  ICP HELPER  (pure-numpy, no open3d needed)
# ─────────────────────────────────────────────
def icp_2d(src_pts, tgt_pts,
           max_iter=15, tolerance=1e-5, max_dist=ICP_MAX_DIST): #max_iter was 30
    """
    Vanilla point-to-point ICP in 2-D.

    Parameters
    ----------
    src_pts : (N, 2) ndarray   – current scan (will be aligned to target)
    tgt_pts : (M, 2) ndarray   – previous scan (reference)

    Returns
    -------
    dx, dy, dtheta : floats    – transform that maps src → tgt frame
    fitness        : float     – fraction of src points that found a
                                 correspondence within max_dist
                                 (higher is better, range 0-1)
    """
    src = np.array(src_pts, dtype=np.float64)
    tgt = np.array(tgt_pts, dtype=np.float64)

    # Accumulated transform (starts at identity)
    T = np.eye(3)

    for _ in range(max_iter):
        # --- 1. Find nearest neighbours (brute-force, fast enough for <500 pts)
        # Expand dims for broadcasting: (N,1,2) - (1,M,2) → (N,M,2)
        diff  = src[:, np.newaxis, :] - tgt[np.newaxis, :, :]
        dists = np.linalg.norm(diff, axis=2)          # (N, M)
        nn_idx  = np.argmin(dists, axis=1)             # closest tgt for each src
        nn_dist = dists[np.arange(len(src)), nn_idx]   # distances

        # Keep only close-enough correspondences
        mask = nn_dist < max_dist
        if mask.sum() < 4:
            break                       # not enough overlap – give up

        matched_src = src[mask]
        matched_tgt = tgt[nn_idx[mask]]

        # --- 2. Compute optimal rigid transform via SVD
        mu_s = matched_src.mean(axis=0)
        mu_t = matched_tgt.mean(axis=0)
        cs   = matched_src - mu_s
        ct   = matched_tgt - mu_t

        H = cs.T @ ct                  # 2×2 cross-covariance
        U, _, Vt = np.linalg.svd(H)
        R2 = (Vt.T @ U.T)

        # Ensure proper rotation (det = +1)
        if np.linalg.det(R2) < 0:
            Vt[-1, :] *= -1
            R2 = Vt.T @ U.T

        t2 = mu_t - R2 @ mu_s         # 2-D translation

        # Build 3×3 homogeneous update
        dT       = np.eye(3)
        dT[:2,:2] = R2
        dT[:2, 2] = t2

        # Apply update to src cloud
        src = (R2 @ src.T).T + t2

        # Accumulate
        T = dT @ T

        # Convergence check
        if np.linalg.norm(t2) < tolerance and abs(np.arctan2(R2[1,0], R2[0,0])) < tolerance:
            break

    fitness = float(mask.sum()) / len(src_pts) if len(src_pts) > 0 else 0.0

    dx     = T[0, 2]
    dy     = T[1, 2]
    dtheta = np.arctan2(T[1, 0], T[0, 0])

    return dx, dy, dtheta, fitness


# ─────────────────────────────────────────────
#  MAIN SLAM CLASS
# ─────────────────────────────────────────────
class SLAMMapper:
    def __init__(self):
        # ── GTSAM factor graph ──────────────────────────────────────────
        self.graph             = gtsam.NonlinearFactorGraph()
        self.current_estimates = gtsam.Values()   # always kept up-to-date
        self.pose_id           = 0

        # Noise models  [x-sigma (m), y-sigma (m), theta-sigma (rad)]
        self.prior_noise = gtsam.noiseModel.Diagonal.Sigmas(
            np.array([0.05, 0.05, 0.02]))
        self.odo_noise = gtsam.noiseModel.Diagonal.Sigmas(
            np.array([0.15, 0.15, 0.05]))
        # Wider noise for ICP failures (we fall back to zero-motion)
        self.fallback_noise = gtsam.noiseModel.Diagonal.Sigmas(
            np.array([0.50, 0.50, 0.20]))

        # Add start pose (0, 0, 0)
        start_pose = gtsam.Pose2(0.0, 0.0, 0.0)
        self.graph.add(gtsam.PriorFactorPose2(
            self.pose_id, start_pose, self.prior_noise))
        self.current_estimates.insert(self.pose_id, start_pose)

        # ── Occupancy grid ──────────────────────────────────────────────
        # 127 = unknown  |  0 = obstacle  |  255 = free
        self.grid_map = np.full((MAP_PIXELS, MAP_PIXELS), 127, dtype=np.uint8)

        # ── Previous scan (for ICP) ─────────────────────────────────────
        self.prev_scan_pts = None

        # ── Statistics ──────────────────────────────────────────────────
        self.icp_failures = 0
        self.scan_count   = 0

    # ── Coordinate helpers ──────────────────────────────────────────────
    def _world_to_pixel(self, wx, wy):
        cx = MAP_PIXELS // 2
        cy = MAP_PIXELS // 2
        px = int(cx + wx / RESOLUTION)
        py = int(cy + wy / RESOLUTION)
        return px, py

    # ── ICP-based odometry ──────────────────────────────────────────────
    def get_odometry_from_scans(self, current_pts):
        """
        Returns (gtsam.Pose2 delta, bool icp_ok).
        Falls back to zero-motion if ICP is unreliable.
        """
        pts = np.array(current_pts, dtype=np.float64)

        if self.prev_scan_pts is None or len(self.prev_scan_pts) < ICP_MIN_POINTS:
            self.prev_scan_pts = pts
            return gtsam.Pose2(0.0, 0.0, 0.0), False

        dx, dy, dtheta, fitness = icp_2d(pts, self.prev_scan_pts)

        icp_ok = fitness >= ICP_FITNESS_THRESH
        if not icp_ok:
            self.icp_failures += 1
            dx, dy, dtheta = 0.0, 0.0, 0.0   # zero-motion fallback

        self.prev_scan_pts = pts
        return gtsam.Pose2(dx, dy, dtheta), icp_ok

    # ── Occupancy-grid update ───────────────────────────────────────────
    def update_map(self, pose, points):
        """
        For each LiDAR return:
          • raycast free-space from robot → hit  (draw 255 line)
          • mark hit pixel as obstacle           (draw 0)
        Uses the A2M12 correct coordinate convention.
        """
        rx, ry = self._world_to_pixel(pose.x(), pose.y())
        cos_t  = np.cos(pose.theta())
        sin_t  = np.sin(pose.theta())

        for pt in points:
            lx, ly = pt[0], pt[1]

            # Rotate local → global
            gx = pose.x() + (lx * cos_t - ly * sin_t)
            gy = pose.y() + (lx * sin_t + ly * cos_t)

            px, py = self._world_to_pixel(gx, gy)

            if not (0 <= px < MAP_PIXELS and 0 <= py < MAP_PIXELS):
                continue

            # Draw free-space ray (255) then re-stamp obstacle (0)
            cv2.line(self.grid_map, (rx, ry), (px, py), 255, 1)
            self.grid_map[py, px] = 0

    # ── Save outputs ────────────────────────────────────────────────────
    def save_output(self, name="pitube_map"):
        pgm_path  = f"{name}.pgm"
        yaml_path = f"{name}.yaml"

        cv2.imwrite(pgm_path, self.grid_map)

        origin = [-(MAP_PIXELS // 2) * RESOLUTION,
                  -(MAP_PIXELS // 2) * RESOLUTION,
                  0.0]
        meta = {
            "image":           pgm_path,
            "resolution":      RESOLUTION,
            "origin":          origin,
            "negate":          0,
            "occupied_thresh": 0.65,
            "free_thresh":     0.196,
        }
        with open(yaml_path, 'w') as f:
            yaml.dump(meta, f, default_flow_style=False)

        print(f"\n✓ Map saved → {pgm_path}  +  {yaml_path}")
        print(f"  Scans processed : {self.scan_count}")
        print(f"  ICP failures    : {self.icp_failures}")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
def main():
    mapper = SLAMMapper()

    try:
        lidar = RPLidar(PORT_NAME, baudrate=BAUDRATE)
        print("GTSAM SLAM online — move the LiDAR slowly for best results.\n")
        print("Press Ctrl-C to stop and save the map.\n")

        for scan_i, scan in enumerate(lidar.iter_scans()):
            mapper.scan_count = scan_i

            # ── 1. Parse + filter scan ──────────────────────────────────
            # A2M12: angle=0 is forward, increases CLOCKWISE
            # Convert to standard math frame: negate angle so CCW = positive
            points = []
            for (_, angle_deg, dist_mm) in scan:
                if not (DIST_MIN_MM < dist_mm < DIST_MAX_MM):
                    continue
                d_m     = dist_mm / 1000.0
                ang_rad = -np.radians(angle_deg)   # ← flip for correct chirality
                points.append([d_m * np.cos(ang_rad),
                                d_m * np.sin(ang_rad)])

            if len(points) < ICP_MIN_POINTS:
                continue

            if len(points) > 150:
                step = len(points) // 150
                points = points[::step]

            # ── 2. ICP odometry ─────────────────────────────────────────
            delta_pose, icp_ok = mapper.get_odometry_from_scans(points)

            # ── 3. Build GTSAM graph ─────────────────────────────────────
            prev_pose = mapper.current_estimates.atPose2(mapper.pose_id)
            new_pose  = prev_pose.compose(delta_pose)

            mapper.pose_id += 1
            mapper.current_estimates.insert(mapper.pose_id, new_pose)

            noise = mapper.odo_noise if icp_ok else mapper.fallback_noise
            mapper.graph.add(gtsam.BetweenFactorPose2(
                mapper.pose_id - 1, mapper.pose_id, delta_pose, noise))

            # ── 4. Optimise (every N scans) ──────────────────────────────
            if scan_i % 2 != 0: #scan_i % OPTIMISE_EVERY == 0:, it now iterates over every other scan
                try:
                    params = gtsam.LevenbergMarquardtParams()
                    params.setMaxIterations(20)
                    optimizer = gtsam.LevenbergMarquardtOptimizer(
                        mapper.graph, mapper.current_estimates, params)
                    mapper.current_estimates = optimizer.optimize()
                except Exception as e:
                    print(f"\n[WARN] Optimiser failed at scan {scan_i}: {e}")

            # ── 5. Update occupancy grid ─────────────────────────────────
            current_pose = mapper.current_estimates.atPose2(mapper.pose_id)
            mapper.update_map(current_pose, points)

            # ── 6. Progress ──────────────────────────────────────────────
            icp_str = "✓" if icp_ok else "✗"
            print(
                f"Scan {scan_i:5d} | ICP {icp_str} | "
                f"x={current_pose.x():+.2f}m  "
                f"y={current_pose.y():+.2f}m  "
                f"θ={np.degrees(current_pose.theta()):+.1f}°",
                end='\r'
            )

    except KeyboardInterrupt:
        print("\n\nStopping…")
    finally:
        try:
            lidar.stop()
            lidar.disconnect()
        except Exception:
            pass
        mapper.save_output()


if __name__ == "__main__":
    main()