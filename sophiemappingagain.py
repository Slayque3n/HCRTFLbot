import qi
import sys
import termios
import tty
import select
import math
import numpy as np
import heapq
import cv2
import yaml
import time

# ==========================================================
ROBOT_IP = "192.168.137.241"
PORT     = 9559

MAP_FILE = "map.pgm"
MAP_YAML = "map.yaml"

SPEED_RATIO = 0.4
TURN_RATIO  = 0.5

POS_TOLERANCE = 0.30
ANG_TOLERANCE = 0.08

STUCK_TIMEOUT       = 3.0   # seconds per stuck check window
STUCK_MIN_PROGRESS  = 0.10  # must close 10cm per window or replan
MAX_REPLANS         = 5     # give up after this many replans

# Laser / local grid settings
DEPTH_THRESHOLD  = 0.8
GRID_RESOLUTION  = 0.1
MAX_GRID_SIZE    = 60
OBSTACLE_BUFFER  = 0.3   # safety inflation on local grid
OBSTACLE_MARK_R  = 0.3   # radius to mark on global map when obstacle found

LASER_KEYS = {
    "front": "Device/SubDeviceList/Platform/LaserSensor/Front/Horizontal/Sensor/Value",
    "left":  "Device/SubDeviceList/Platform/LaserSensor/Left/Horizontal/Sensor/Value",
    "right": "Device/SubDeviceList/Platform/LaserSensor/Right/Horizontal/Sensor/Value"
}
# ==========================================================

msg = """
==================================
 Pepper Navigation (Merged)
==================================
p : set origin       r : return
1-9 : save waypoint  SHIFT+1-9 : go
g : run tour

Manual: w/x forward/back
        a/d strafe
        q/e rotate

CTRL-C to exit
==================================
"""

SHIFT_KEYS  = {'!':1,'@':2,'#':3,'$':4,'%':5,'^':6,'&':7,'*':8,'(':9}
MANUAL_KEYS = set('wxadqesx')


# ==========================================================
# GLOBAL MAP (PGM)
# ==========================================================

class GlobalMap:

    def __init__(self, pgm, yaml_file):
        self.image = cv2.imread(pgm, cv2.IMREAD_GRAYSCALE)
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        self.resolution = data["resolution"]
        self.origin     = data["origin"]
        self.height, self.width = self.image.shape
        self.grid = np.zeros_like(self.image)
        self.grid[self.image < 50]  = 1
        self.grid[self.image > 200] = 0
        self.grid[(self.image >= 50) & (self.image <= 200)] = -1

    def world_to_grid(self, x, y):
        gx = int((x - self.origin[0]) / self.resolution)
        gy = int((y - self.origin[1]) / self.resolution)
        return gx, gy

    def grid_to_world(self, gx, gy):
        x = gx * self.resolution + self.origin[0]
        y = gy * self.resolution + self.origin[1]
        return x, y

    def is_free(self, gx, gy):
        if gx < 0 or gy < 0 or gx >= self.width or gy >= self.height:
            return False
        return self.grid[gy][gx] == 0

    def mark_obstacle(self, wx, wy, radius_m):
        """Permanently mark a world-frame circle as blocked."""
        cx, cy = self.world_to_grid(wx, wy)
        r = int(radius_m / self.resolution) + 1
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if dx*dx + dy*dy <= r*r:
                    gx, gy = cx + dx, cy + dy
                    if 0 <= gx < self.width and 0 <= gy < self.height:
                        self.grid[gy][gx] = 1
        print(f"  [Map] Marked obstacle at ({wx:.2f}, {wy:.2f}) r={radius_m}m")


# ==========================================================
# LOCAL LASER GRID
# ==========================================================

class LocalGrid:
    """
    Small occupancy grid in the robot's local frame.
    Used to read live laser data and convert detections
    into world-frame coordinates for the global map.
    """

    def __init__(self, size, resolution):
        self.size       = size
        self.resolution = resolution
        self.center     = size // 2
        self.grid       = np.zeros((size, size))

    def reset(self):
        self.grid.fill(0)

    def _mark_local(self, lx, ly):
        gx = int(self.center + lx / self.resolution)
        gy = int(self.center + ly / self.resolution)
        if 0 <= gx < self.size and 0 <= gy < self.size:
            self.grid[gy][gx] = 1

    def scan_lasers(self, memory):
        """Read all three laser arrays and mark detections in local grid."""
        self.reset()
        ranges = []
        for key, (start, end) in [("front",(-0.52,0.52)), ("left",(0.52,1.57)), ("right",(-1.57,-0.52))]:
            try:
                dists = memory.getData(LASER_KEYS[key])
                if not dists:
                    continue
                step = (end - start) / len(dists)
                for i, d in enumerate(dists):
                    if 0.1 < d < DEPTH_THRESHOLD:
                        a = start + i * step
                        lx = d * math.cos(a)
                        ly = d * math.sin(a)
                        self._mark_local(lx, ly)
                        ranges.append((lx, ly))
            except Exception as e:
                print(f"  [Laser] {key} error: {e}")
        return ranges   # list of (local_x, local_y) detections

    def local_to_world(self, lx, ly, robot_x, robot_y, robot_theta):
        """Transform a local-frame point to world frame."""
        wx = robot_x + lx * math.cos(robot_theta) - ly * math.sin(robot_theta)
        wy = robot_y + lx * math.sin(robot_theta) + ly * math.cos(robot_theta)
        return wx, wy


# ==========================================================
# GLOBAL A*
# ==========================================================

def astar(global_map, start, goal):
    start = global_map.world_to_grid(*start)
    goal  = global_map.world_to_grid(*goal)

    frontier  = []
    heapq.heappush(frontier, (0, start))
    came_from = {start: None}
    cost      = {start: 0}

    while frontier:
        _, current = heapq.heappop(frontier)
        if current == goal:
            path = []
            while current:
                wx, wy = global_map.grid_to_world(*current)
                path.append((wx, wy))
                current = came_from[current]
            return path[::-1]

        for dx, dy in [(1,0),(-1,0),(0,1),(0,-1)]:
            neighbor = (current[0]+dx, current[1]+dy)
            if not global_map.is_free(*neighbor):
                continue
            new_cost = cost[current] + 1
            if neighbor not in cost or new_cost < cost[neighbor]:
                cost[neighbor] = new_cost
                priority = new_cost + math.hypot(
                    goal[0]-neighbor[0], goal[1]-neighbor[1]
                )
                heapq.heappush(frontier, (priority, neighbor))
                came_from[neighbor] = current
    return None


# ==========================================================
# HELPERS
# ==========================================================

def get_key(settings):
    tty.setraw(sys.stdin.fileno())
    rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
    key = sys.stdin.read(1) if rlist else ''
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key

def normalize_angle(a):
    return (a + math.pi) % (2*math.pi) - math.pi

def get_position(motion):
    pos = motion.getRobotPosition(True)
    return pos[0], pos[1], pos[2]


# ==========================================================
# MAIN
# ==========================================================

def main():
    session = qi.Session()
    try:
        session.connect("tcp://" + ROBOT_IP + ":" + str(PORT))
    except:
        sys.exit("Connection failed")

    motion = session.service("ALMotion")
    memory = session.service("ALMemory")

    try:
        session.service("ALBasicAwareness").stopAwareness()
    except:
        pass

    motion.wakeUp()
    motion.moveInit()

    global_map = GlobalMap(MAP_FILE, MAP_YAML)
    local_grid = LocalGrid(MAX_GRID_SIZE, GRID_RESOLUTION)

    origin      = None
    setpoints   = {}
    nav_path    = []
    nav_target  = None
    mode        = "idle"
    tour_queue  = []

    last_dist      = None
    last_dist_time = None
    replan_count   = 0

    settings = termios.tcgetattr(sys.stdin)
    print(msg)


    def scan_and_mark():
        """
        Read lasers right now, convert detections to world frame,
        and permanently mark them on the global map.
        Returns the number of new obstacles marked.
        """
        ax, ay, at = get_position(motion)
        detections = local_grid.scan_lasers(memory)
        count = 0
        for lx, ly in detections:
            wx, wy = local_grid.local_to_world(lx, ly, ax, ay, at)
            global_map.mark_obstacle(wx, wy, OBSTACLE_MARK_R)
            count += 1
        return count


    def start_nav(target, replanning=False):
        nonlocal nav_path, nav_target, mode, last_dist, last_dist_time, replan_count

        ax, ay, at = get_position(motion)

        if replanning:
            print("  [Nav] Replanning around obstacle...")
        else:
            print("  [Nav] Planning path...")
            replan_count = 0

        path = astar(global_map, (ax, ay), (target[0], target[1]))

        if path is None:
            print("  [Nav] No path found — all routes blocked")
            mode = "idle"
            motion.stopMove()
            return

        nav_path       = path
        nav_target     = target
        mode           = "nav"
        last_dist      = None
        last_dist_time = None

        print(f"  [Nav] Path length: {len(nav_path)} waypoints")


    try:
        while True:
            key = get_key(settings)

            if key == '\x03':
                break

            # ----------------------------------
            # Navigation update
            # ----------------------------------

            if mode == "nav" and nav_path:

                ax, ay, at = get_position(motion)
                wx, wy     = nav_path[0]
                dist       = math.hypot(wx-ax, wy-ay)

                # ---- Waypoint reached ----
                if dist < POS_TOLERANCE:
                    nav_path.pop(0)
                    last_dist      = None
                    last_dist_time = None

                    if not nav_path:
                        # Final heading alignment
                        if nav_target and len(nav_target) > 2:
                            err = normalize_angle(nav_target[2] - at)
                            if abs(err) > ANG_TOLERANCE:
                                motion.moveToward(0, 0, max(-0.4, min(0.4, 1.5 * err)))
                                continue
                        motion.stopMove()
                        print("  [Nav] Arrived")
                        if mode == "tour" and tour_queue:
                            start_nav(tour_queue.pop(0))
                        else:
                            mode = "idle"
                        continue

                    wx, wy = nav_path[0]
                    dist   = math.hypot(wx-ax, wy-ay)

                # ---- Stuck detection ----
                now = time.time()

                if last_dist is None:
                    last_dist      = dist
                    last_dist_time = now
                elif now - last_dist_time > STUCK_TIMEOUT:
                    if last_dist - dist < STUCK_MIN_PROGRESS:

                        replan_count += 1
                        if replan_count > MAX_REPLANS:
                            print("  [Nav] Too many replans — giving up")
                            mode = "idle"
                            motion.stopMove()
                            continue

                        # Scan lasers NOW, mark what they see on the global map
                        print("  [Nav] Stuck — scanning lasers for obstacles...")
                        n = scan_and_mark()
                        print(f"  [Nav] Marked {n} obstacle points on global map")

                        motion.stopMove()
                        time.sleep(0.5)
                        start_nav(nav_target, replanning=True)
                        continue
                    else:
                        last_dist      = dist
                        last_dist_time = now

                # ---- Steering ----
                heading = math.atan2(wy-ay, wx-ax)
                err     = normalize_angle(heading - at)

                if abs(err) < 0.08:
                    err = 0.0

                vt        = max(-TURN_RATIO, min(TURN_RATIO, 1.0 * err))
                alignment = max(0.0, 1.0 - abs(err) / (math.pi / 3))
                vx        = SPEED_RATIO * alignment

                motion.moveToward(vx, 0, vt)
                continue

            # ----------------------------------
            # KEYBOARD COMMANDS
            # ----------------------------------

            if key == 'p':
                origin = get_position(motion)
                print(f"  Origin saved: {origin}")

            elif key == 'r' and origin:
                start_nav(origin)

            elif key and key in "123456789":
                setpoints[int(key)] = get_position(motion)
                print(f"  Waypoint {key} saved")

            elif key and key in SHIFT_KEYS:
                idx = SHIFT_KEYS[key]
                if idx in setpoints:
                    start_nav(setpoints[idx])

            elif key == 'g' and setpoints and origin:
                tour_queue = [setpoints[i] for i in sorted(setpoints)] + [origin]
                mode = "tour"
                start_nav(tour_queue.pop(0))

            # ----------------------------------
            # MANUAL CONTROL
            # ----------------------------------

            if key in MANUAL_KEYS and mode == "idle":
                vx, vy, vt = 0, 0, 0
                if key == 'w':   vx =  SPEED_RATIO
                elif key == 'x': vx = -SPEED_RATIO
                elif key == 'a': vy =  SPEED_RATIO
                elif key == 'd': vy = -SPEED_RATIO
                elif key == 'q': vt =  TURN_RATIO
                elif key == 'e': vt = -TURN_RATIO
                motion.moveToward(vx, vy, vt)

    finally:
        motion.stopMove()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)


if __name__ == "__main__":
    main()