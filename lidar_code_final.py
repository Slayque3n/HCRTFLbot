import numpy as np
from rplidar import RPLidar
import time
from sklearn.cluster import DBSCAN

import rclpy

from geometry_msgs.msg import Twist
from std_msgs.msg import String


PORT_NAME = '/dev/ttyUSB0'
BAUDRATE = 256000
SAFE_DISTANCE = 0.30 # Edited from 0.75 for integration test
SELF_FILTER_DIST = 0.15
MAX_DETECTION_RANGE = 4.0
PREDICTION_TIME = 3.0
MIN_MOVING_SPEED = 0.5    # Ignore tiny jitters
PRINT_THROTTLE = 0.4


class Obstacle:
    def __init__(self, id, x, y):
        self.id = id
        self.pos = np.array([x, y])
        self.vel = np.array([0.0, 0.0])
        self.last_time = time.time()
        self.vel_buffer = []

    def update(self, new_x, new_y):
        now = time.time()
        dt = now - self.last_time
        if dt < 0.05: return
        new_pos = np.array([new_x, new_y])
        raw_vel = (new_pos - self.pos) / dt
        if np.linalg.norm(raw_vel) < 5.0:
            self.vel_buffer.append(raw_vel)
            if len(self.vel_buffer) > 10: self.vel_buffer.pop(0)
            self.vel = np.mean(self.vel_buffer, axis=0)
        self.pos = new_pos
        self.last_time = now

    def predict_pos(self, t):
        return self.pos + (self.vel * t)

def find_safe_move(obstacles_dict):

    threat = None
    for o in obstacles_dict.values():
        if np.linalg.norm(o.vel) > MIN_MOVING_SPEED and np.dot(o.pos, o.vel) < 0:
            threat = o # Focus on the approaching object
            break
    if not threat: return None

    threat_angle = np.arctan2(threat.pos[1], threat.pos[0])
    # Try 90 degrees left, then 90 degrees right, then 45 degrees
    test_angles = [threat_angle + 1.57, threat_angle - 1.57, threat_angle + 0.78, threat_angle - 0.78]

    for dist in [0.3, 0.5]: # Prefer a small 30cm detour, but can do 50cm as well
        for angle in test_angles:
            candidate = np.array([dist * np.cos(angle), dist * np.sin(angle)])
            is_safe = True
            for obs in obstacles_dict.values():
                # Check if this spot is safe for the next 3 seconds, to be more robust could do for more or shorter intervals
                for t_check in [0, 1.0, 2.0, 3.0]:
                    if np.linalg.norm(obs.predict_pos(t_check) - candidate) < 0.5:
                        is_safe = False; break
                if not is_safe: break
            if is_safe: return candidate
    return None

def main():
    try:
        lidar = RPLidar(PORT_NAME, baudrate=BAUDRATE)
        print("System Online. Walk toward LiDAR to test...")
    except Exception as e:
        print(f"Error: {e}"); return
    tracked_obstacles = {}
    last_print_time = time.time()

    rclpy.init(args=None)

    node = rclpy.create_node('move_pub')

    vel_publisher = node.create_publisher(Twist, 'cmd_vel', 10)

    pos_publisher = node.create_publisher(String, 'cam_enable', 10)

    vel_msg = Twist()
    pos_msg = String()

    try:
        for scan in lidar.iter_scans():
            points = [[d/1000 * np.cos(np.radians(a)), d/1000 * np.sin(np.radians(a))]
                      for (_, a, d) in scan if SELF_FILTER_DIST < d/1000 < MAX_DETECTION_RANGE]
            if not points: continue

            # Cluster
            clustering = DBSCAN(eps=0.25, min_samples=3).fit(points)
            new_centers = [np.mean(np.array(points)[clustering.labels_ == cid], axis=0)
                           for cid in set(clustering.labels_) if cid != -1]

            # Cluster memory - compares new to old cluster to track between scans
            updated_obs = {}
            for center in new_centers:
                best_match, min_d = None, 0.8 # 80cm matching radius
                for oid, obj in tracked_obstacles.items():
                    dist = np.linalg.norm(center - obj.pos)
                    if dist < min_d: min_d = dist; best_match = oid

                if best_match is not None:
                    tracked_obstacles[best_match].update(center[0], center[1])
                    updated_obs[best_match] = tracked_obstacles[best_match]
                else:
                    new_id = int(time.time() * 1000) % 10000
                    updated_obs[new_id] = Obstacle(new_id, center[0], center[1])
            tracked_obstacles = updated_obs

            # Collision logic and distance printing
            for obs in tracked_obstacles.values():
                dist_now = np.linalg.norm(obs.pos)
                speed = np.linalg.norm(obs.vel)

                # Dot product: must be negative and significant, ensures it is moving towrds us
                approach_vector = np.dot(obs.pos, obs.vel)

                if speed > MIN_MOVING_SPEED and approach_vector < -0.1: # Make this more negative to be less sensitive to something moving towards
                    collision_imminent = False
                    for t in np.arange(0, PREDICTION_TIME, 0.3):
                        # P = P0 + Vt
                        future_dist = np.linalg.norm(obs.predict_pos(t))
                        if future_dist < SAFE_DISTANCE:
                            collision_imminent = True
                            break

                    if collision_imminent:
                        print(f"\n ID {obs.id} approaching speed: {speed:.2f}m/s")
                        print(f"\n X Velocity:", obs.vel[0])
                        print(f"\n Y Velocity:", obs.vel[1])
                        move = find_safe_move(tracked_obstacles)

                        vel_msg.linear.x = -1.0 * obs.vel[0]
                        vel_msg.linear.y = -1.0 * obs.vel[1]

                        vel_publisher.publish(vel_msg)
                        if move is not None:
                            print(f"x={move[0]:.2f}, y={move[1]:.2f}")
                            pos_msg.data = f"go to: x={move[0]:.2f}, y={move[1]:.2f}"
                            #pos_msg.data = "CAM_DISABLE"
                            pos_publisher.publish(pos_msg)

            if time.time() - last_print_time > PRINT_THROTTLE:
                m = sum(1 for o in tracked_obstacles.values() if np.linalg.norm(o.vel) > MIN_MOVING_SPEED)
                print(f"Tracking: {len(tracked_obstacles)}, Moving: {m}", end='\r')
                last_print_time = time.time()

    except KeyboardInterrupt: print("\nStopping...")
    finally: lidar.stop(); lidar.disconnect()

if __name__ == "__main__": main()
