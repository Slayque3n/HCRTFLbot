import numpy as np
import gtsam
from rplidar import RPLidar
import threading
import time
import cv2
import yaml

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION  — tune these without touching the rest of the code
# ═══════════════════════════════════════════════════════════════════

PORT_NAME  = '/dev/ttyUSB0'
BAUDRATE   = 256000

# Map
RESOLUTION = 0.05        # metres per pixel (5 cm)
MAP_SIZE_M = 20.0        # 20 × 20 m world
MAP_PIXELS = int(MAP_SIZE_M / RESOLUTION)   # → 400 × 400 px

# LiDAR filtering
DIST_MIN_MM = 150        # ignore returns < 15 cm  (self-noise)
DIST_MAX_MM = 8000       # ignore returns > 8 m    (spurious)

# Scan processing — KEY buffer-overflow knobs
PROCESS_EVERY_N = 2      # only process 1-in-N scans  (2 = half the CPU load)
MAX_POINTS_ICP  = 120    # downsample scan to this many points for ICP
MAX_POINTS_MAP  = 300    # use more points for map update (detail > speed)

# ICP
ICP_MAX_ITER       = 15   # fewer iterations = faster per scan
ICP_MAX_DIST       = 0.5  # metres — max correspondence distance
ICP_MIN_POINTS     = 20   # skip ICP if scan has fewer points
ICP_FITNESS_THRESH = 0.30 # reject result if < 30 % of points matched

# GTSAM
OPTIMISE_EVERY = 10      # run optimiser every N *processed* scans
OPT_MAX_ITER   = 20      # Levenberg-Marquardt iterations per call

# Output
AUTOSAVE_EVERY = 200     # save .pgm every N processed scans  (0 = off)
OUTPUT_NAME    = "pitube_map"


# ═══════════════════════════════════════════════════════════════════
#  2-D ICP  (pure numpy — no open3d needed)
# ═══════════════════════════════════════════════════════════════════

def icp_2d(src_pts: np.ndarray, tgt_pts: np.ndarray,
           max_iter: int = ICP_MAX_ITER,
           tolerance: float = 1e-5,
           max_dist: float = ICP_MAX_DIST):
    """
    Point-to-point ICP in 2-D via SVD.

    Returns
    -------
    dx, dy, dtheta : float   transform that maps src -> tgt
    fitness        : float   fraction of src points matched (0-1)
    """
    src  = src_pts.copy()
    tgt  = tgt_pts
    T    = np.eye(3)
    mask = np.zeros(len(src), dtype=bool)

    for _ in range(max_iter):
        diff    = src[:, np.newaxis, :] - tgt[np.newaxis, :, :]  # (N,M,2)
        dists   = np.linalg.norm(diff, axis=2)                    # (N,M)
        nn_idx  = np.argmin(dists, axis=1)
        nn_dist = dists[np.arange(len(src)), nn_idx]

        mask = nn_dist < max_dist
        if mask.sum() < 4:
            break

        ms   = src[mask]
        mt   = tgt[nn_idx[mask]]
        mu_s = ms.mean(axis=0)
        mu_t = mt.mean(axis=0)
        H    = (ms - mu_s).T @ (mt - mu_t)
        U, _, Vt = np.linalg.svd(H)
        R2 = Vt.T @ U.T
        if np.linalg.det(R2) < 0:
            Vt[-1, :] *= -1
            R2 = Vt.T @ U.T
        t2 = mu_t - R2 @ mu_s

        src       = (R2 @ src.T).T + t2
        dT        = np.eye(3)
        dT[:2,:2] = R2
        dT[:2, 2] = t2
        T         = dT @ T

        if np.linalg.norm(t2) < tolerance and \
           abs(np.arctan2(R2[1, 0], R2[0, 0])) < tolerance:
            break

    fitness = float(mask.sum()) / max(len(src_pts), 1)
    return T[0, 2], T[1, 2], np.arctan2(T[1, 0], T[0, 0]), fitness


def downsample(points, max_pts: int) -> np.ndarray:
    """Uniform stride downsample to at most max_pts points."""
    arr = np.array(points, dtype=np.float64)
    if len(arr) <= max_pts:
        return arr
    step = max(1, len(arr) // max_pts)
    return arr[::step][:max_pts]


# ═══════════════════════════════════════════════════════════════════
#  SLAM MAPPER
# ═══════════════════════════════════════════════════════════════════

class SLAMMapper:

    def __init__(self):
        # ── GTSAM ──────────────────────────────────────────────────────
        self.graph             = gtsam.NonlinearFactorGraph()
        self.current_estimates = gtsam.Values()
        self.pose_id           = 0

        self._prior_noise    = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.05, 0.05, 0.02]))
        self._odo_noise      = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.15, 0.15, 0.05]))
        self._fallback_noise = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.50, 0.50, 0.20]))

        start = gtsam.Pose2(0.0, 0.0, 0.0)
        self.graph.add(gtsam.PriorFactorPose2(0, start, self._prior_noise))
        self.current_estimates.insert(0, start)

        # ── Occupancy grid  (127=unknown | 0=obstacle | 255=free) ──────
        self.grid_map = np.full((MAP_PIXELS, MAP_PIXELS), 127, dtype=np.uint8)

        # ── ICP state ──────────────────────────────────────────────────
        self.prev_scan_pts = None

        # ── Async optimiser ────────────────────────────────────────────
        # Lock guards both graph reads (optimiser) and graph writes (main loop)
        self._opt_lock   = threading.Lock()
        self._opt_thread = None

        # ── Stats ──────────────────────────────────────────────────────
        self.scans_processed = 0
        self.icp_ok_count    = 0
        self.icp_fail_count  = 0
        self._t_start        = time.time()

    # ── helpers ──────────────────────────────────────────────────────

    def _world_to_pixel(self, wx: float, wy: float):
        c = MAP_PIXELS // 2
        return int(c + wx / RESOLUTION), int(c + wy / RESOLUTION)

    # ── ICP odometry ─────────────────────────────────────────────────

    def get_odometry(self, pts_full: np.ndarray):
        """ICP on a downsampled scan.  Returns (Pose2 delta, ok: bool)."""
        pts = downsample(pts_full, MAX_POINTS_ICP)

        if self.prev_scan_pts is None or len(self.prev_scan_pts) < ICP_MIN_POINTS:
            self.prev_scan_pts = pts
            return gtsam.Pose2(0.0, 0.0, 0.0), False

        dx, dy, dtheta, fitness = icp_2d(pts, self.prev_scan_pts)
        self.prev_scan_pts = pts

        if fitness < ICP_FITNESS_THRESH:
            self.icp_fail_count += 1
            return gtsam.Pose2(0.0, 0.0, 0.0), False

        self.icp_ok_count += 1
        return gtsam.Pose2(dx, dy, dtheta), True

    # ── async optimiser ───────────────────────────────────────────────

    def trigger_optimise(self):
        """
        Fire-and-forget background LM optimisation.
        Skips the cycle if a previous run is still active so the
        main scan loop is never blocked waiting for the optimiser.
        """
        if self._opt_thread and self._opt_thread.is_alive():
            return   # still running — skip

        def _run():
            with self._opt_lock:
                try:
                    params = gtsam.LevenbergMarquardtParams()
                    params.setMaxIterations(OPT_MAX_ITER)
                    result = gtsam.LevenbergMarquardtOptimizer(
                        self.graph, self.current_estimates, params).optimize()
                    self.current_estimates = result
                except Exception as exc:
                    print(f"\n[WARN] Optimiser: {exc}")

        self._opt_thread = threading.Thread(target=_run, daemon=True)
        self._opt_thread.start()

    # ── map update ────────────────────────────────────────────────────

    def update_map(self, pose: gtsam.Pose2, pts_full: np.ndarray):
        """
        For every LiDAR return (using a denser point set than ICP):
          • draw a free-space line from robot origin → hit point  (255)
          • stamp the hit pixel as obstacle                       (0)
        """
        pts   = downsample(pts_full, MAX_POINTS_MAP)
        rx,ry = self._world_to_pixel(pose.x(), pose.y())
        cos_t = np.cos(pose.theta())
        sin_t = np.sin(pose.theta())

        for lx, ly in pts:
            gx = pose.x() + lx * cos_t - ly * sin_t
            gy = pose.y() + lx * sin_t + ly * cos_t
            px, py = self._world_to_pixel(gx, gy)
            if not (0 <= px < MAP_PIXELS and 0 <= py < MAP_PIXELS):
                continue
            cv2.line(self.grid_map, (rx, ry), (px, py), 255, 1)
            self.grid_map[py, px] = 0

    # ── save ─────────────────────────────────────────────────────────

    def save_output(self, name: str = OUTPUT_NAME):
        pgm = f"{name}.pgm"
        yml = f"{name}.yaml"
        cv2.imwrite(pgm, self.grid_map)

        origin = [-(MAP_PIXELS // 2) * RESOLUTION,
                  -(MAP_PIXELS // 2) * RESOLUTION, 0.0]
        with open(yml, 'w') as f:
            yaml.dump({"image": pgm, "resolution": RESOLUTION,
                       "origin": origin, "negate": 0,
                       "occupied_thresh": 0.65, "free_thresh": 0.196},
                      f, default_flow_style=False)

        elapsed = time.time() - self._t_start
        rate    = self.scans_processed / max(elapsed, 1)
        total   = self.icp_ok_count + self.icp_fail_count
        pct     = 100 * self.icp_ok_count / max(total, 1)
        print(f"\n{'='*50}")
        print(f"  Map saved  ->  {pgm}  +  {yml}")
        print(f"  Scans processed  : {self.scans_processed}")
        print(f"  ICP success rate : {self.icp_ok_count}/{total}  ({pct:.0f}%)")
        print(f"  Runtime          : {elapsed:.0f}s  ({rate:.1f} scans/s)")
        print(f"{'='*50}")


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main():
    mapper = SLAMMapper()
    lidar  = None

    print("=" * 55)
    print("  GTSAM 2-D SLAM  —  RPLidar A2M12")
    print("=" * 55)
    print(f"  Processing   : every {PROCESS_EVERY_N} scans")
    print(f"  ICP points   : {MAX_POINTS_ICP}  |  Map points : {MAX_POINTS_MAP}")
    print(f"  Optimise     : every {OPTIMISE_EVERY} processed scans (async)")
    print(f"  Speed tip    : < 0.3 m/s translation, < 15 deg/s rotation")
    print("  Ctrl-C to stop and save.\n")

    try:
        lidar = RPLidar(PORT_NAME, baudrate=BAUDRATE)

        raw_i  = 0   # every scan the hardware sends
        proc_i = 0   # every scan we actually process

        for scan in lidar.iter_scans():
            raw_i += 1

            # ── drop scans to stay ahead of the serial buffer ────────────
            if raw_i % PROCESS_EVERY_N != 0:
                continue

            # ── 1. Parse & filter ────────────────────────────────────────
            # A2M12 reports angle 0 = forward, increases CLOCKWISE.
            # Negate to convert to standard counter-clockwise math frame.
            points = []
            for (_, angle_deg, dist_mm) in scan:
                if not (DIST_MIN_MM < dist_mm < DIST_MAX_MM):
                    continue
                d = dist_mm / 1000.0
                a = -np.radians(angle_deg)          # flip chirality
                points.append([d * np.cos(a), d * np.sin(a)])

            if len(points) < ICP_MIN_POINTS:
                continue

            pts_arr = np.array(points, dtype=np.float64)

            # ── 2. ICP odometry ──────────────────────────────────────────
            delta, icp_ok = mapper.get_odometry(pts_arr)

            # ── 3. Add factor to graph (under lock) ──────────────────────
            with mapper._opt_lock:
                prev  = mapper.current_estimates.atPose2(mapper.pose_id)
                new_p = prev.compose(delta)
                mapper.pose_id += 1
                mapper.current_estimates.insert(mapper.pose_id, new_p)
                noise = mapper._odo_noise if icp_ok else mapper._fallback_noise
                mapper.graph.add(gtsam.BetweenFactorPose2(
                    mapper.pose_id - 1, mapper.pose_id, delta, noise))

            # ── 4. Async optimisation ────────────────────────────────────
            if proc_i % OPTIMISE_EVERY == 0:
                mapper.trigger_optimise()

            # ── 5. Map update ────────────────────────────────────────────
            with mapper._opt_lock:
                cur_pose = mapper.current_estimates.atPose2(mapper.pose_id)
            mapper.update_map(cur_pose, pts_arr)

            # ── 6. Periodic auto-save ────────────────────────────────────
            if AUTOSAVE_EVERY and proc_i > 0 and proc_i % AUTOSAVE_EVERY == 0:
                cv2.imwrite(f"{OUTPUT_NAME}.pgm", mapper.grid_map)

            # ── 7. Status line ───────────────────────────────────────────
            print(
                f"raw={raw_i:5d}  proc={proc_i:4d}  "
                f"ICP={'ok' if icp_ok else '--'}  "
                f"x={cur_pose.x():+.2f}m  y={cur_pose.y():+.2f}m  "
                f"theta={np.degrees(cur_pose.theta()):+5.1f}deg  "
                f"fails={mapper.icp_fail_count}",
                end='\r'
            )

            proc_i += 1
            mapper.scans_processed = proc_i

    except KeyboardInterrupt:
        print("\n\nCtrl-C — saving map…")
    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        raise
    finally:
        if lidar:
            try:
                lidar.stop()
                lidar.disconnect()
            except Exception:
                pass
        mapper.save_output()


if __name__ == "__main__":
    main()