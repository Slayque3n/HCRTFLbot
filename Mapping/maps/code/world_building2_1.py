import numpy as np
import gtsam
from rplidar import RPLidar
import threading
import time
import cv2
import yaml
from collections import deque

# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION  ← only section you need to edit
# ═══════════════════════════════════════════════════════════════════

PORT_NAME  = '/dev/ttyUSB0'
BAUDRATE   = 256000

# ── Map ─────────────────────────────────────────────────────────────
RESOLUTION = 0.05          # metres per pixel  (5 cm)
MAP_SIZE_M = 40.0          # ← increased from 20 m to 40 m (800 × 800 px)
MAP_PIXELS = int(MAP_SIZE_M / RESOLUTION)

# ── LiDAR filtering ─────────────────────────────────────────────────
DIST_MIN_MM = 150           # drop returns < 15 cm  (self-noise)
DIST_MAX_MM = 8000          # drop returns > 8 m    (spurious)

# ── Scan processing (buffer-overflow prevention) ─────────────────────
PROCESS_EVERY_N = 2         # only process 1-in-N raw scans
MAX_POINTS_ICP  = 120       # downsample to this many pts for ICP
MAX_POINTS_MAP  = 300       # denser set for map painting

# ── ICP ──────────────────────────────────────────────────────────────
ICP_MAX_ITER        = 15
ICP_MAX_DIST        = 0.5   # metres
ICP_MIN_POINTS      = 20
ICP_FITNESS_THRESH  = 0.30

# ── Open-space detection ─────────────────────────────────────────────
OPEN_SPACE_SECTOR_THRESH = 0.5   # < 50 % sectors filled → open space
N_SECTORS                = 12

# ── Graph memory management ──────────────────────────────────────────
MARGINALISE_EVERY = 50      # freeze old poses every N processed scans
GRAPH_WINDOW      = 30      # keep this many recent poses live

# ── Keyframe / loop-closure ──────────────────────────────────────────
MAX_KEYFRAMES      = 300    # sliding window (deque) — ~600 KB fixed
LC_KEYFRAME_EVERY  = 15     # store 1 keyframe per N processed scans
LC_SEARCH_SKIP     = 5      # ignore the N most-recent keyframes in LC search
LC_MATCH_THRESH    = 0.65   # min ICP fitness to accept a loop closure
LC_MAX_DIST_M      = 3.0    # only attempt LC within this radius (m)
LC_NOISE_SIGMAS    = np.array([0.10, 0.10, 0.03])

# ── GTSAM optimiser ──────────────────────────────────────────────────
OPTIMISE_EVERY = 10
OPT_MAX_ITER   = 20

# ── Reconnect logic ──────────────────────────────────────────────────
# If the LiDAR throws an exception (descriptor bytes error), the code
# will attempt to reconnect this many times before giving up.
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY_S      = 2.0

# ── Output ───────────────────────────────────────────────────────────
AUTOSAVE_EVERY = 100        # write .pgm every N processed scans (0 = off)
OUTPUT_NAME    = "floor10_3"


# ═══════════════════════════════════════════════════════════════════
#  MEMORY MONITOR
# ═══════════════════════════════════════════════════════════════════

def _rss_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


# ═══════════════════════════════════════════════════════════════════
#  2-D ICP  (pure numpy)
# ═══════════════════════════════════════════════════════════════════

def icp_2d(src_pts: np.ndarray, tgt_pts: np.ndarray,
           max_iter: int = ICP_MAX_ITER,
           tolerance: float = 1e-5,
           max_dist: float = ICP_MAX_DIST):
    src  = src_pts.copy()
    tgt  = tgt_pts
    T    = np.eye(3)
    mask = np.zeros(len(src), dtype=bool)

    for _ in range(max_iter):
        diff    = src[:, np.newaxis, :] - tgt[np.newaxis, :, :]
        dists   = np.linalg.norm(diff, axis=2)
        nn_idx  = np.argmin(dists, axis=1)
        nn_dist = dists[np.arange(len(src)), nn_idx]
        mask    = nn_dist < max_dist
        if mask.sum() < 4:
            break

        ms, mt     = src[mask], tgt[nn_idx[mask]]
        mu_s, mu_t = ms.mean(0), mt.mean(0)
        H          = (ms - mu_s).T @ (mt - mu_t)
        U, _, Vt   = np.linalg.svd(H)
        R2         = Vt.T @ U.T
        if np.linalg.det(R2) < 0:
            Vt[-1] *= -1
            R2 = Vt.T @ U.T
        t2 = mu_t - R2 @ mu_s

        src        = (R2 @ src.T).T + t2
        dT         = np.eye(3)
        dT[:2, :2] = R2
        dT[:2,  2] = t2
        T          = dT @ T

        if (np.linalg.norm(t2) < tolerance and
                abs(np.arctan2(R2[1, 0], R2[0, 0])) < tolerance):
            break

    fitness = float(mask.sum()) / max(len(src_pts), 1)
    return T[0, 2], T[1, 2], np.arctan2(T[1, 0], T[0, 0]), fitness


def downsample(points, max_pts: int) -> np.ndarray:
    arr = np.array(points, dtype=np.float64)
    if len(arr) <= max_pts:
        return arr
    step = max(1, len(arr) // max_pts)
    return arr[::step][:max_pts]


# ═══════════════════════════════════════════════════════════════════
#  OPEN-SPACE DETECTOR
# ═══════════════════════════════════════════════════════════════════

def scan_sector_coverage(pts: np.ndarray, n: int = N_SECTORS) -> float:
    if len(pts) == 0:
        return 0.0
    angles   = np.arctan2(pts[:, 1], pts[:, 0])
    sw       = 2 * np.pi / n
    hit_set  = set(int((a + np.pi) / sw) % n for a in angles)
    return len(hit_set) / n


# ═══════════════════════════════════════════════════════════════════
#  KEYFRAME
# ═══════════════════════════════════════════════════════════════════

class Keyframe:
    __slots__ = ('pose_id', 'pose', 'pts')

    def __init__(self, pose_id, pose, pts):
        self.pose_id = pose_id
        self.pose    = pose
        self.pts     = pts


# ═══════════════════════════════════════════════════════════════════
#  SLAM MAPPER
# ═══════════════════════════════════════════════════════════════════

class SLAMMapper:

    def __init__(self):
        # ── GTSAM ──────────────────────────────────────────────────────
        self.graph             = gtsam.NonlinearFactorGraph()
        self.current_estimates = gtsam.Values()
        self.pose_id           = 0
        self._frontier_id      = 0

        self._prior_noise    = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.05, 0.05, 0.02]))
        self._odo_noise      = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.15, 0.15, 0.05]))
        self._open_noise     = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.40, 0.40, 0.10]))
        self._fallback_noise = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.50, 0.50, 0.20]))
        self._lc_noise       = gtsam.noiseModel.Diagonal.Sigmas(LC_NOISE_SIGMAS)

        start = gtsam.Pose2(0.0, 0.0, 0.0)
        self.graph.add(gtsam.PriorFactorPose2(0, start, self._prior_noise))
        self.current_estimates.insert(0, start)

        # ── Occupancy grid (127=unknown | 0=obstacle | 255=free) ────────
        self.grid_map = np.full((MAP_PIXELS, MAP_PIXELS), 127, dtype=np.uint8)

        # ── ICP state ──────────────────────────────────────────────────
        self.prev_scan_pts = None

        # ── Keyframe sliding-window deque ───────────────────────────────
        self.keyframes: deque[Keyframe] = deque(maxlen=MAX_KEYFRAMES)

        # ── Async optimiser ─────────────────────────────────────────────
        self._opt_lock   = threading.Lock()
        self._opt_thread = None

        # ── Stats ───────────────────────────────────────────────────────
        self.scans_processed   = 0
        self.icp_ok_count      = 0
        self.icp_fail_count    = 0
        self.lc_count          = 0
        self.marginalise_count = 0
        self._t_start          = time.time()
        self._last_mem_log     = time.time()

    # ── helpers ──────────────────────────────────────────────────────

    def _world_to_pixel(self, wx, wy):
        c = MAP_PIXELS // 2
        return int(c + wx / RESOLUTION), int(c + wy / RESOLUTION)

    # ── graph marginalisation ─────────────────────────────────────────

    def marginalise_old_poses(self):
        """
        Rebuild the factor graph keeping only the most recent GRAPH_WINDOW
        poses.  Older poses are frozen; a tight prior anchors the new frontier.
        Keeps graph at a fixed O(GRAPH_WINDOW) size for arbitrarily long runs.
        """
        if self.pose_id - self._frontier_id <= GRAPH_WINDOW:
            return

        new_frontier = self.pose_id - GRAPH_WINDOW
        anchor_pose  = self.current_estimates.atPose2(new_frontier)
        anchor_noise = gtsam.noiseModel.Diagonal.Sigmas(np.array([0.01, 0.01, 0.005]))

        new_graph  = gtsam.NonlinearFactorGraph()
        new_values = gtsam.Values()

        new_graph.add(gtsam.PriorFactorPose2(new_frontier, anchor_pose, anchor_noise))

        for pid in range(new_frontier, self.pose_id + 1):
            if self.current_estimates.exists(pid):
                new_values.insert(pid, self.current_estimates.atPose2(pid))

        for i in range(self.graph.size()):
            factor = self.graph.at(i)
            # factor.keys() may return a plain list OR a gtsam.KeyVector
            # depending on the installed GTSAM Python binding version.
            # Wrapping in list() normalises both cases safely.
            keys = list(factor.keys())
            if all(k >= new_frontier for k in keys):
                new_graph.add(factor)

        self.graph             = new_graph
        self.current_estimates = new_values
        self._frontier_id      = new_frontier
        self.marginalise_count += 1

    # ── odometry ─────────────────────────────────────────────────────

    def get_odometry(self, pts_full: np.ndarray):
        pts        = downsample(pts_full, MAX_POINTS_ICP)
        open_space = scan_sector_coverage(pts) < OPEN_SPACE_SECTOR_THRESH

        if self.prev_scan_pts is None or len(self.prev_scan_pts) < ICP_MIN_POINTS:
            self.prev_scan_pts = pts
            return gtsam.Pose2(0.0, 0.0, 0.0), self._fallback_noise, False

        dx, dy, dtheta, fitness = icp_2d(pts, self.prev_scan_pts)
        self.prev_scan_pts = pts

        if fitness < ICP_FITNESS_THRESH:
            self.icp_fail_count += 1
            return (gtsam.Pose2(0.0, 0.0, 0.0),
                    self._open_noise if open_space else self._fallback_noise,
                    False)

        self.icp_ok_count += 1
        return (gtsam.Pose2(dx, dy, dtheta),
                self._open_noise if open_space else self._odo_noise,
                True)

    # ── loop closure ─────────────────────────────────────────────────

    def try_loop_closure(self, cur_pose, cur_pts) -> bool:
        kf_list = list(self.keyframes)
        if len(kf_list) <= LC_SEARCH_SKIP:
            return False

        best_fitness = LC_MATCH_THRESH
        best_kf = best_dx = best_dy = best_dt = None

        for kf in kf_list[:-LC_SEARCH_SKIP]:
            if kf.pose_id < self._frontier_id:
                continue
            d = np.hypot(cur_pose.x() - kf.pose.x(),
                         cur_pose.y() - kf.pose.y())
            if d > LC_MAX_DIST_M:
                continue
            dx, dy, dt, fitness = icp_2d(cur_pts, kf.pts,
                                         max_iter=20,
                                         max_dist=ICP_MAX_DIST * 1.5)
            if fitness > best_fitness:
                best_fitness = fitness
                best_kf, best_dx, best_dy, best_dt = kf, dx, dy, dt

        if best_kf is None:
            return False

        self.graph.add(gtsam.BetweenFactorPose2(
            best_kf.pose_id, self.pose_id,
            gtsam.Pose2(best_dx, best_dy, best_dt),
            self._lc_noise))
        self.lc_count += 1
        return True

    def maybe_add_keyframe(self, proc_i, pose, pts):
        if proc_i % LC_KEYFRAME_EVERY == 0:
            self.keyframes.append(Keyframe(self.pose_id, pose, pts.copy()))

    # ── async optimiser ───────────────────────────────────────────────

    def trigger_optimise(self):
        if self._opt_thread and self._opt_thread.is_alive():
            return

        def _run():
            with self._opt_lock:
                try:
                    params = gtsam.LevenbergMarquardtParams()
                    params.setMaxIterations(OPT_MAX_ITER)
                    result = gtsam.LevenbergMarquardtOptimizer(
                        self.graph, self.current_estimates, params).optimize()
                    self.current_estimates = result
                except Exception as e:
                    print(f"\n[WARN] Optimiser: {e}")

        self._opt_thread = threading.Thread(target=_run, daemon=True)
        self._opt_thread.start()

    # ── map update ────────────────────────────────────────────────────

    def update_map(self, pose, pts_full: np.ndarray):
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

    # ── memory log ────────────────────────────────────────────────────

    def _log_memory(self):
        now = time.time()
        if now - self._last_mem_log > 60:
            rss  = _rss_mb()
            live = self.pose_id - self._frontier_id
            print(f"\n[MEM] RSS={rss:.0f} MB | "
                  f"live_poses={live} | "
                  f"keyframes={len(self.keyframes)}/{MAX_KEYFRAMES} | "
                  f"margin={self.marginalise_count} | "
                  f"LC={self.lc_count}")
            self._last_mem_log = now

    # ── save ─────────────────────────────────────────────────────────

    def save_output(self, name: str = OUTPUT_NAME):
        pgm = f"{name}.pgm"
        yml = f"{name}.yaml"
        cv2.imwrite(pgm, self.grid_map)
        origin = [-(MAP_PIXELS//2)*RESOLUTION,
                  -(MAP_PIXELS//2)*RESOLUTION, 0.0]
        with open(yml, 'w') as f:
            yaml.dump({"image": pgm, "resolution": RESOLUTION,
                       "origin": origin, "negate": 0,
                       "occupied_thresh": 0.65, "free_thresh": 0.196},
                      f, default_flow_style=False)

        elapsed = time.time() - self._t_start
        total   = self.icp_ok_count + self.icp_fail_count
        pct     = 100 * self.icp_ok_count / max(total, 1)
        rss     = _rss_mb()
        print(f"\n{'='*56}")
        print(f"  Map saved        ->  {pgm}  +  {yml}")
        print(f"  Map size         :   {MAP_SIZE_M:.0f} x {MAP_SIZE_M:.0f} m  "
              f"({MAP_PIXELS} x {MAP_PIXELS} px)")
        print(f"  Scans processed  :   {self.scans_processed}")
        print(f"  ICP success      :   {self.icp_ok_count}/{total}  ({pct:.0f}%)")
        print(f"  Loop closures    :   {self.lc_count}")
        print(f"  Marginalisations :   {self.marginalise_count}")
        print(f"  Peak RSS         :   {rss:.0f} MB")
        print(f"  Runtime          :   {elapsed:.0f}s")
        print(f"{'='*56}")


# ═══════════════════════════════════════════════════════════════════
#  LIDAR CONTEXT MANAGER  (handles reconnection on packet errors)
# ═══════════════════════════════════════════════════════════════════

def connect_lidar():
    """Connect and return a running RPLidar instance."""
    lidar = RPLidar(PORT_NAME, baudrate=BAUDRATE)
    time.sleep(0.5)   # let the motor spin up
    return lidar


def disconnect_lidar(lidar):
    try:
        lidar.stop()
        lidar.disconnect()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main():
    mapper = SLAMMapper()

    print("=" * 60)
    print("  GTSAM 2-D SLAM  —  RPLidar A2M12")
    print("=" * 60)
    print(f"  Map size         : {MAP_SIZE_M:.0f} x {MAP_SIZE_M:.0f} m  "
          f"({MAP_PIXELS} x {MAP_PIXELS} px)")
    print(f"  Processing       : every {PROCESS_EVERY_N} raw scans")
    print(f"  ICP pts / Map pts: {MAX_POINTS_ICP} / {MAX_POINTS_MAP}")
    print(f"  Graph window     : {GRAPH_WINDOW} poses  "
          f"(marginalise every {MARGINALISE_EVERY} scans)")
    print(f"  Keyframe store   : {MAX_KEYFRAMES} sliding window")
    print(f"  Auto-save        : every {AUTOSAVE_EVERY} processed scans")
    print(f"  Reconnect        : up to {MAX_RECONNECT_ATTEMPTS} attempts")
    print("  Speed tip        : < 0.3 m/s,  < 15 deg/s")
    print("  Ctrl-C to stop and save.\n")

    raw_i  = 0
    proc_i = 0
    reconnect_attempts = 0
    lidar = None

    while True:
        try:
            # ── Connect ──────────────────────────────────────────────────
            print(f"[INFO] Connecting to LiDAR on {PORT_NAME}…")
            lidar = connect_lidar()
            print("[INFO] LiDAR connected.\n")
            reconnect_attempts = 0   # reset on successful connect

            for scan in lidar.iter_scans():
                raw_i += 1

                # ── Drop scans to stay ahead of serial buffer ─────────────
                if raw_i % PROCESS_EVERY_N != 0:
                    continue

                # ── 1. Parse & filter ─────────────────────────────────────
                points = []
                for (_, angle_deg, dist_mm) in scan:
                    if not (DIST_MIN_MM < dist_mm < DIST_MAX_MM):
                        continue
                    d = dist_mm / 1000.0
                    a = -np.radians(angle_deg)   # A2M12 CW → CCW
                    points.append([d * np.cos(a), d * np.sin(a)])

                if len(points) < ICP_MIN_POINTS:
                    continue

                pts_arr = np.array(points, dtype=np.float64)

                # ── 2. ICP odometry ───────────────────────────────────────
                delta, noise, icp_ok = mapper.get_odometry(pts_arr)

                # ── 3. Add odometry factor ────────────────────────────────
                with mapper._opt_lock:
                    prev  = mapper.current_estimates.atPose2(mapper.pose_id)
                    new_p = prev.compose(delta)
                    mapper.pose_id += 1
                    mapper.current_estimates.insert(mapper.pose_id, new_p)
                    mapper.graph.add(gtsam.BetweenFactorPose2(
                        mapper.pose_id - 1, mapper.pose_id, delta, noise))

                # ── 4. Marginalise old poses ──────────────────────────────
                if proc_i % MARGINALISE_EVERY == 0 and proc_i > 0:
                    with mapper._opt_lock:
                        mapper.marginalise_old_poses()

                # ── 5. Loop closure ───────────────────────────────────────
                with mapper._opt_lock:
                    cur_pose = mapper.current_estimates.atPose2(mapper.pose_id)

                icp_pts = downsample(pts_arr, MAX_POINTS_ICP)
                with mapper._opt_lock:
                    lc_found = mapper.try_loop_closure(cur_pose, icp_pts)

                if lc_found:
                    if mapper._opt_thread and mapper._opt_thread.is_alive():
                        mapper._opt_thread.join(timeout=2.0)
                    mapper.trigger_optimise()
                elif proc_i % OPTIMISE_EVERY == 0:
                    mapper.trigger_optimise()

                # ── 6. Store keyframe ─────────────────────────────────────
                mapper.maybe_add_keyframe(proc_i, cur_pose, icp_pts)

                # ── 7. Map update ─────────────────────────────────────────
                mapper.update_map(cur_pose, pts_arr)

                # ── 8. Auto-save ──────────────────────────────────────────
                if AUTOSAVE_EVERY and proc_i > 0 and proc_i % AUTOSAVE_EVERY == 0:
                    cv2.imwrite(f"{OUTPUT_NAME}.pgm", mapper.grid_map)
                    print(f"\n[SAVE] Auto-saved at scan {proc_i}")

                # ── 9. Memory log ─────────────────────────────────────────
                mapper._log_memory()

                # ── 10. Status line ───────────────────────────────────────
                lc_tag = f" LC={mapper.lc_count}" if mapper.lc_count else ""
                print(
                    f"raw={raw_i:6d}  proc={proc_i:5d}  "
                    f"ICP={'ok' if icp_ok else '--'}  "
                    f"x={cur_pose.x():+.2f}m  y={cur_pose.y():+.2f}m  "
                    f"th={np.degrees(cur_pose.theta()):+5.1f}d  "
                    f"kf={len(mapper.keyframes)}{lc_tag}",
                    end='\r'
                )

                proc_i += 1
                mapper.scans_processed = proc_i

        except KeyboardInterrupt:
            print("\n\nCtrl-C — saving map…")
            break

        except Exception as e:
            # Only reconnect on known serial / LiDAR transport errors.
            # Any other exception (AttributeError, ValueError, etc.) is a
            # code bug and must surface immediately rather than loop forever.
            lidar_keywords = (
                "descriptor", "bytes", "serial", "timeout",
                "connection", "rplidar", "checksum", "sync",
            )
            err_str = str(e).lower()
            is_lidar_error = any(kw in err_str for kw in lidar_keywords)

            if not is_lidar_error:
                import traceback
                print(f"\n[ERROR] Non-LiDAR exception — stopping: {e}")
                traceback.print_exc()
                break   # fall through to save_output, don't reconnect

            # ── Reconnect on LiDAR packet errors ─────────────────────────
            reconnect_attempts += 1
            print(f"\n[WARN] LiDAR error (attempt {reconnect_attempts}/"
                  f"{MAX_RECONNECT_ATTEMPTS}): {e}")

            disconnect_lidar(lidar)
            lidar = None

            if reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                print("[ERROR] Too many reconnect failures — saving and exiting.")
                break

            # Save what we have so far before reconnecting
            cv2.imwrite(f"{OUTPUT_NAME}_reconnect_{reconnect_attempts}.pgm",
                        mapper.grid_map)
            print(f"[INFO] Partial map saved. Retrying in {RECONNECT_DELAY_S}s…")
            time.sleep(RECONNECT_DELAY_S)
            # Continue outer while loop → reconnects

    # ── Final cleanup ─────────────────────────────────────────────────
    if lidar:
        disconnect_lidar(lidar)
    mapper.save_output()


if __name__ == "__main__":
    main()