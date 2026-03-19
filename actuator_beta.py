import os
import re
import time
import queue
import threading
import json

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.serialization import deserialize_message

import rosbag2_py

from std_msgs.msg import String, Bool
from sensor_msgs.msg import JointState
from naoqi_bridge_msgs.msg import JointAnglesWithSpeed


class LlmGestureSpeechNode(Node):
    def __init__(self):
        super().__init__('llm_gesture_speech_node')

        # --- Topics ---
        self.llm_topic = '/llm_topic'
        self.speech_topic = '/speech'
        self.angles_topic = '/joint_angles'
        self.stiffness_topic = '/joint_stiffness'
        self.platform_topic = '/platform_name'
        self.status_topic = '/robot_at_goal'

        # New lift topics
        self.lift_at_origin_topic = '/lift_at_origin'
        self.lift_at_target_topic = '/lift_at_target'

        # Modes: "speech_only" or "guide_and_navigate"
        self.operating_mode = "guide_and_navigate"
        self.main_menu_greeting = "Hello. How can I help you?"
        self.follow_me_bag = "bag/follow_mee"

        # --- Platform / floor config ---
        # Change these to match your actual four platforms
        self.platform_config = {
            "district_eastbound": {
                "floor": "lower",
                "needs_elevator": False,
            },
            "district_westbound": {
                "floor": "upper",
                "needs_elevator": False,
            },
            "piccadilly_northbound": {
                "floor": "lower",
                "needs_elevator": True,
            },
            "piccadilly_southbound": {
                "floor": "upper",
                "needs_elevator": True,
            },
            # Add Circle if needed
            "circle_eastbound": {
                "floor": "upper",
                "needs_elevator": False,
            },
            "circle_westbound": {
                "floor": "upper",
                "needs_elevator": False,
            },
        }

        # Named waypoints expected in your YAML/navigation node
        self.floor_routes = {
            "lower": {
                "elevator_entry": "elevator_upper",       # robot goes here first from current floor
                "elevator_exit": "elevator_lower_exit",   # optional exit waypoint after lift ride
            },
            "upper": {
                "elevator_entry": "elevator_lower",
                "elevator_exit": "elevator_upper_exit",
            }
        }

        # --- ROS interfaces ---
        self.subscription = self.create_subscription(
            String,
            self.llm_topic,
            self.llm_callback,
            10
        )

        self.status_sub = self.create_subscription(
            Bool,
            self.status_topic,
            self.status_callback,
            10
        )

        self.lift_origin_sub = self.create_subscription(
            Bool,
            self.lift_at_origin_topic,
            self.lift_origin_callback,
            10
        )

        self.lift_target_sub = self.create_subscription(
            Bool,
            self.lift_at_target_topic,
            self.lift_target_callback,
            10
        )

        self.speech_pub_ = self.create_publisher(String, self.speech_topic, 10)
        self.angles_pub_ = self.create_publisher(JointAnglesWithSpeed, self.angles_topic, 10)
        self.stiffness_pub_ = self.create_publisher(JointState, self.stiffness_topic, 10)
        self.platform_pub_ = self.create_publisher(String, self.platform_topic, 10)

        # --- Coordination state ---
        self.robot_ready_event = threading.Event()
        self.lift_at_origin_event = threading.Event()
        self.lift_at_target_event = threading.Event()

        self.robot_ready_event.clear()
        self.lift_at_origin_event.clear()
        self.lift_at_target_event.clear()

        self.command_queue = queue.Queue()

        # --- Playback config ---
        self.playback_speed = 1.0
        self.trim_threshold = 0.02

        # --- Gesture mappings ---
        self.gesture_map = {
            "please": "bag/wave",
            "didn't hear": "bag/didnt_hear",
            "sorry": "bag/didnt_hear",
            "left": "bag/point_left",
            "right": "bag/point_right",
            "thinking": "bag/thinking",
            "northbound": "bag/dileft",
            "southbound": "bag/dileft",
            "eastbound": "bag/diright",
            "westbound": "bag/dileft"
        }

        # Start background worker thread
        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()

        self.get_logger().info('DEBUG: LlmGestureSpeechNode initialized.')

    # -------------------------------------------------------------------------
    # Callbacks
    # -------------------------------------------------------------------------

    def status_callback(self, msg: Bool):
        """Receives signal when robot reaches current nav goal."""
        if msg.data:
            self.get_logger().info(f'DEBUG: Received SUCCESS signal on {self.status_topic}.')
            self.robot_ready_event.set()
        else:
            self.get_logger().info('DEBUG: Received FALSE signal from Nav. Still waiting...')

    def lift_origin_callback(self, msg: Bool):
        """Lift has arrived for boarding on current floor."""
        if msg.data:
            self.get_logger().info(f'DEBUG: Lift arrived at origin floor on {self.lift_at_origin_topic}.')
            self.lift_at_origin_event.set()

    def lift_target_callback(self, msg: Bool):
        """Lift has arrived at destination floor."""
        if msg.data:
            self.get_logger().info(f'DEBUG: Lift arrived at target floor on {self.lift_at_target_topic}.')
            self.lift_at_target_event.set()

    def llm_callback(self, msg: String):
        raw = msg.data.strip()
        if not raw:
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"type": "plain_text", "text": raw}

        self.get_logger().info(f'Received command payload: {payload}')
        self.command_queue.put(payload)

    # -------------------------------------------------------------------------
    # Main worker loop
    # -------------------------------------------------------------------------

    def worker_loop(self):
        while rclpy.ok():
            payload = self.command_queue.get()

            try:
                # Clear sync events for this command cycle
                self.robot_ready_event.clear()
                self.lift_at_origin_event.clear()
                self.lift_at_target_event.clear()

                cmd_type = payload.get("type", "plain_text")
                text = payload.get("text", "").strip()
                station = payload.get("station")
                if not station and text:
                    station = self.find_station_in_text(text)

                if cmd_type == "thinking_start":
                    self.start_thinking_gesture()
                    continue

                if cmd_type == "thinking_stop":
                    self.stop_thinking_gesture()
                    continue

                if cmd_type == "main_menu":
                    self.get_logger().info('DEBUG: Main menu greeting triggered.')
                    self.say_text(self.main_menu_greeting)
                    continue

                if self.operating_mode == "speech_only":
                    self.get_logger().info('DEBUG: Running in speech_only mode.')
                    if text:
                        speech_thread = threading.Thread(
                            target=self.say_text,
                            args=(text,),
                            daemon=True
                        )
                        speech_thread.start()

                        bag_path = self.find_matching_bag(text)
                        if bag_path:
                            self.play_gesture_bag_once(bag_path)

                        speech_thread.join(timeout=0.1)
                    continue

                # guide_and_navigate mode
                platform = self.find_platform_in_text(text)
                bag_path = self.find_matching_bag(text)

                if cmd_type == "station_guidance" and station and platform:
                    self.get_logger().info(
                        f'DEBUG: station_guidance -> station={station}, platform={platform}'
                    )

                    platform_info = self.platform_config.get(
                        platform,
                        {"floor": "upper", "needs_elevator": False}
                    )

                    needs_elevator = platform_info.get("needs_elevator", False)
                    target_floor = platform_info.get("floor", "upper")

                    if needs_elevator:
                        self.handle_elevator_guidance(
                            station=station,
                            platform=platform,
                            target_floor=target_floor,
                            final_text=text,
                            final_bag_path=bag_path
                        )
                    else:
                        self.handle_direct_guidance(
                            station=station,
                            platform=platform,
                            final_text=text,
                            final_bag_path=bag_path
                        )

                    continue

                # Fallback normal behavior
                self.get_logger().info('DEBUG: guide_and_navigate fallback path.')

                if text:
                    speech_thread = threading.Thread(
                        target=self.say_text,
                        args=(text,),
                        daemon=True
                    )
                    speech_thread.start()

                    if bag_path:
                        self.play_gesture_bag_once(bag_path)

                    speech_thread.join(timeout=0.1)

            except Exception as e:
                self.get_logger().error(f'Worker error: {e}')
            finally:
                self.command_queue.task_done()

    # -------------------------------------------------------------------------
    # Guidance flows
    # -------------------------------------------------------------------------

    def handle_direct_guidance(self, station: str, platform: str, final_text: str, final_bag_path: str):
        """Existing single-stage navigation flow."""
        self.get_logger().info(f'DEBUG: Direct guidance flow for {platform}')

        speech_thread = threading.Thread(
            target=self.say_follow_me_message,
            args=(station, platform),
            daemon=True
        )
        speech_thread.start()

        self.play_gesture_bag_once(self.follow_me_bag)
        speech_thread.join(timeout=0.1)

        self.send_navigation_target(platform)

        self.get_logger().info('DEBUG: Waiting for arrival at final platform.')
        arrived = self.wait_for_robot_arrival(timeout=120.0)

        if not arrived:
            self.get_logger().error('DEBUG: Timed out waiting for final platform arrival.')
            return

        self.get_logger().info('DEBUG: Arrived at final platform.')

        if final_text:
            arrival_speech_thread = threading.Thread(
                target=self.say_text,
                args=(final_text,),
                daemon=True
            )
            arrival_speech_thread.start()

            if final_bag_path:
                self.play_gesture_bag_once(final_bag_path)

            arrival_speech_thread.join(timeout=0.1)

    def handle_elevator_guidance(self, station: str, platform: str, target_floor: str, final_text: str, final_bag_path: str):
        """Multi-stage elevator flow for lower/other floor platforms."""
        self.get_logger().info(
            f'DEBUG: Elevator guidance flow for {platform}, target_floor={target_floor}'
        )

        route = self.floor_routes.get(target_floor)
        if not route:
            self.get_logger().error(f'No floor route configured for target floor: {target_floor}')
            return

        elevator_entry = route["elevator_entry"]
        elevator_exit = route["elevator_exit"]

        # Step 1: speak and gesture
        speech_thread = threading.Thread(
            target=self.say_floor_transition_message,
            args=(station, platform, target_floor),
            daemon=True
        )
        speech_thread.start()

        self.play_gesture_bag_once(self.follow_me_bag)
        speech_thread.join(timeout=0.1)

        # Step 2: go to elevator entry
        self.get_logger().info(f'DEBUG: Navigating to elevator entry waypoint: {elevator_entry}')
        self.send_navigation_target(elevator_entry)

        if not self.wait_for_robot_arrival(timeout=120.0):
            self.get_logger().error('Failed to reach elevator entry waypoint.')
            return

        # Step 3: announce waiting for lift
        self.say_text("Please wait here. We need to take the lift.")

        # Step 4: wait for lift to arrive at origin floor
        self.lift_at_origin_event.clear()
        self.get_logger().info('DEBUG: Waiting for lift at origin floor.')
        if not self.lift_at_origin_event.wait(timeout=180.0):
            self.get_logger().error('Timed out waiting for lift at origin floor.')
            return

        # Step 5: board lift
        self.say_text("The lift is here. Please follow me inside.")

        # Optional:
        # If you later add a waypoint inside the lift, call navigation here.
        # Example:
        # self.send_navigation_target("elevator_inside")
        # if not self.wait_for_robot_arrival(timeout=30.0):
        #     return

        # Step 6: wait for lift to reach target floor
        self.lift_at_target_event.clear()
        self.get_logger().info(f'DEBUG: Waiting for lift to reach target floor: {target_floor}')
        if not self.lift_at_target_event.wait(timeout=180.0):
            self.get_logger().error('Timed out waiting for lift at target floor.')
            return

        # Step 7: exit lift
        self.say_text(f"We have arrived at the {target_floor} floor. Please follow me.")

        # Optional exit waypoint for robust positioning
        if elevator_exit:
            self.get_logger().info(f'DEBUG: Navigating to elevator exit waypoint: {elevator_exit}')
            self.send_navigation_target(elevator_exit)

            if not self.wait_for_robot_arrival(timeout=60.0):
                self.get_logger().error('Failed to reach elevator exit waypoint.')
                return

        # Step 8: go to final platform
        self.get_logger().info(f'DEBUG: Navigating to final platform: {platform}')
        self.send_navigation_target(platform)

        if not self.wait_for_robot_arrival(timeout=120.0):
            self.get_logger().error('Failed to reach final platform after lift journey.')
            return

        self.get_logger().info('DEBUG: Arrived at final platform.')

        # Step 9: final guidance speech + gesture
        if final_text:
            arrival_speech_thread = threading.Thread(
                target=self.say_text,
                args=(final_text,),
                daemon=True
            )
            arrival_speech_thread.start()

            if final_bag_path:
                self.play_gesture_bag_once(final_bag_path)

            arrival_speech_thread.join(timeout=0.1)

    # -------------------------------------------------------------------------
    # Navigation helpers
    # -------------------------------------------------------------------------

    def send_navigation_target(self, location_name: str):
        msg = String()
        msg.data = location_name
        self.platform_pub_.publish(msg)
        self.get_logger().info(f'Navigation target published: "{location_name}"')

    def wait_for_robot_arrival(self, timeout=120.0) -> bool:
        self.robot_ready_event.clear()
        arrived = self.robot_ready_event.wait(timeout=timeout)
        if not arrived:
            self.get_logger().error('Timed out waiting for /robot_at_goal.')
        return arrived

    # -------------------------------------------------------------------------
    # Parsing helpers
    # -------------------------------------------------------------------------

    def find_platform_in_text(self, text: str):
        """
        Identifies the specific platform.
        Returns strings like: 'district_eastbound', 'piccadilly_northbound', etc.
        """
        normalized = text.lower()

        lines = ["district", "circle", "piccadilly"]
        directions = ["eastbound", "westbound", "northbound", "southbound"]

        found_line = None
        line_idx = float('inf')
        for line in lines:
            idx = normalized.find(line)
            if idx != -1 and idx < line_idx:
                line_idx = idx
                found_line = line

        if not found_line:
            return None

        remaining_text = normalized[line_idx:]
        found_direction = None
        for direction in directions:
            if direction in remaining_text:
                found_direction = direction
                break

        if found_line and found_direction:
            return f"{found_line}_{found_direction}"

        return found_line

    def find_station_in_text(self, text: str):
        normalized = text.lower().strip()

        known_stations = [
            "south kensington",
            "victoria",
            "green park",
            "gloucester road",
            "earls court",
            "paddington",
            "kings cross",
            "waterloo",
        ]

        for station in sorted(known_stations, key=len, reverse=True):
            if station in normalized:
                return station.title()

        patterns = [
            r"(?:to|towards|for)\s+([a-z\s]+?)\s+station",
            r"(?:to|towards|for)\s+([a-z\s]+?)(?:\s+(district|circle|piccadilly|eastbound|westbound|northbound|southbound)|$)",
        ]

        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                station = match.group(1).strip()
                if station:
                    return " ".join(word.capitalize() for word in station.split())

        return None

    def normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    def find_matching_bag(self, text: str):
        normalized = self.normalize_text(text)
        for phrase in sorted(self.gesture_map.keys(), key=len, reverse=True):
            if phrase in normalized:
                bag_path = self.gesture_map[phrase]
                self.get_logger().info(f'Matched "{phrase}" -> {bag_path}')
                return bag_path
        return None

    # -------------------------------------------------------------------------
    # Speech helpers
    # -------------------------------------------------------------------------

    def say_text(self, text: str):
        msg = String()
        msg.data = text
        self.speech_pub_.publish(msg)
        self.get_logger().info(f'Speech published: "{text}"')

    def platform_to_speech(self, platform: str) -> str:
        if not platform:
            return ""

        parts = platform.split("_")
        if len(parts) == 2:
            line, direction = parts
            return f"{line.capitalize()} line {direction} platform"

        return platform.replace("_", " ").capitalize()

    def say_follow_me_message(self, station: str, platform: str):
        spoken_platform = self.platform_to_speech(platform)

        msg = String()
        msg.data = (
            f"I will take you to {station}. "
            f"We need to go to the {spoken_platform}. "
            f"Follow me."
        )
        self.speech_pub_.publish(msg)
        self.get_logger().info(f'Follow-me speech published: "{msg.data}"')

    def say_floor_transition_message(self, station: str, platform: str, floor: str):
        spoken_platform = self.platform_to_speech(platform)

        if floor == "lower":
            floor_text = "lower floor"
        elif floor == "upper":
            floor_text = "upper floor"
        else:
            floor_text = f"{floor} floor"

        msg = String()
        msg.data = (
            f"I will take you to {station}. "
            f"We need to go to the {floor_text} to get to the {spoken_platform}. "
            f"Follow me."
        )
        self.speech_pub_.publish(msg)
        self.get_logger().info(f'Floor transition speech published: "{msg.data}"')

    # -------------------------------------------------------------------------
    # Gesture / bag playback
    # -------------------------------------------------------------------------

    def load_bag(self, bag_dir_path):
        trajectory = []
        reader = rosbag2_py.SequentialReader()
        storage_options = rosbag2_py.StorageOptions(
            uri=bag_dir_path,
            storage_id='sqlite3'
        )
        converter_options = rosbag2_py.ConverterOptions(
            input_serialization_format='cdr',
            output_serialization_format='cdr'
        )

        reader.open(storage_options, converter_options)
        t0 = None
        message_count = 0

        while reader.has_next():
            topic, data, t_ns = reader.read_next()
            if topic != '/joint_states':
                continue

            msg = deserialize_message(data, JointState)
            if not msg.name or not msg.position:
                continue

            t_sec = t_ns / 1e9
            if t0 is None:
                t0 = t_sec

            trajectory.append({
                'time': t_sec - t0,
                'names': list(msg.name),
                'positions': list(msg.position)
            })
            message_count += 1

        self.get_logger().info(f'Loaded {message_count} joint states from {bag_dir_path}')
        return trajectory

    def trim_trajectory(self, trajectory):
        if not trajectory:
            return trajectory

        start_idx = 0
        end_idx = len(trajectory) - 1
        first_positions = trajectory[0]['positions']
        last_positions = trajectory[-1]['positions']

        for i, step in enumerate(trajectory):
            max_diff = max(abs(a - b) for a, b in zip(step['positions'], first_positions))
            if max_diff > self.trim_threshold:
                start_idx = max(0, i - 5)
                break

        for i in range(len(trajectory) - 1, -1, -1):
            step = trajectory[i]
            max_diff = max(abs(a - b) for a, b in zip(step['positions'], last_positions))
            if max_diff > self.trim_threshold:
                end_idx = min(len(trajectory) - 1, i + 5)
                break

        if start_idx >= end_idx:
            return trajectory

        trimmed = trajectory[start_idx:end_idx + 1]
        offset = trimmed[0]['time']
        for step in trimmed:
            step['time'] -= offset

        self.get_logger().info(f'Trimmed to {len(trimmed)} frames')
        return trimmed

    def set_stiffness(self, joint_names, target_stiffness=1.0):
        msg = JointState()
        msg.name = joint_names
        msg.effort = [float(target_stiffness)] * len(joint_names)

        for _ in range(3):
            self.stiffness_pub_.publish(msg)
            time.sleep(0.2)

    def play_gesture_bag_once(self, bag_dir_path):
        if not os.path.exists(bag_dir_path):
            self.get_logger().error(f'Path missing: {bag_dir_path}')
            return

        trajectory = self.load_bag(bag_dir_path)
        if not trajectory:
            return

        trajectory = self.trim_trajectory(trajectory)
        self.set_stiffness(trajectory[0]['names'], 1.0)

        msg = JointAnglesWithSpeed()
        msg.speed = 0.5
        msg.relative = 0
        start_time = time.time()

        for step in trajectory:
            target_time = start_time + (step['time'] / self.playback_speed)
            now = time.time()
            if target_time > now:
                time.sleep(target_time - now)

            msg.joint_names = step['names']
            msg.joint_angles = step['positions']
            self.angles_pub_.publish(msg)

        self.get_logger().info(f'Finished gesture for {bag_dir_path}')

    # -------------------------------------------------------------------------
    # Optional placeholders for thinking gestures
    # -------------------------------------------------------------------------

    def start_thinking_gesture(self):
        self.get_logger().info('DEBUG: start_thinking_gesture called (placeholder).')
        # Implement if needed

    def stop_thinking_gesture(self):
        self.get_logger().info('DEBUG: stop_thinking_gesture called (placeholder).')
        # Implement if needed


def main(args=None):
    rclpy.init(args=args)
    node = LlmGestureSpeechNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info('KeyboardInterrupt: Shutting down.')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()