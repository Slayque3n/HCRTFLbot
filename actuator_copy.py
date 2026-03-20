import os
import re
import time
import queue
import threading

import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor
from rclpy.serialization import deserialize_message

import rosbag2_py
import json

from std_msgs.msg import String, Bool
from sensor_msgs.msg import JointState
from naoqi_bridge_msgs.msg import JointAnglesWithSpeed


class LlmGestureSpeechNode(Node):
    def __init__(self):
        super().__init__('llm_gesture_speech_node')

        self.llm_topic = '/llm_topic'
        self.speech_topic = '/speech'
        self.angles_topic = '/joint_angles'
        self.stiffness_topic = '/joint_stiffness'
        self.platform_topic = '/platform_name' 
        self.status_topic = '/robot_at_goal'
         
         

        self.operating_mode = "guide_and_navigate"
        self.main_menu_greeting = "Hello. How can I help you?" 
        self.follow_me_bag = "bag/follow_mee"
 
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

        self.speech_pub_ = self.create_publisher(String, self.speech_topic, 10)
        self.angles_pub_ = self.create_publisher(JointAnglesWithSpeed, self.angles_topic, 10)
        self.stiffness_pub_ = self.create_publisher(JointState, self.stiffness_topic, 10)
        self.platform_pub_ = self.create_publisher(String, self.platform_topic, 10)

        self.crouch_bag = "bag/crouch"
        self.thinking_bag = "bag/thinking"
        self.thinking_active = threading.Event()
        self.thinking_thread = None
        self.thinking_stop_event = threading.Event()

        self.robot_ready_event = threading.Event()

        self.robot_ready_event.clear() 
        
        self.command_queue = queue.Queue()


        self.playback_speed = 1.0
        self.trim_threshold = 0.02
        self.fixed_gesture_delay_map = {
            "follow_me": 2.3,
            "thinking": 0.1,
        }

        self.gesture_map = {
            "didn't hear": "bag/didnt_hear",
            "left": "bag/point_left",
            "right": "bag/point_right",
            "northbound": "bag/point_left",
            "southbound": "bag/point_left",
            "eastbound": "bag/point_left",
            "westbound": "bag/point_right"
        }
        self.seconds_per_word = 0.25
        self.speech_startup_offset = 0.2
        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()

        self.get_logger().info('DEBUG: LlmGestureSpeechNode initialized. Sync event is cleared.')

    def count_words(self, text: str) -> int:
        return len(re.findall(r"\b\w+\b", text))
    def speak_with_optional_fixed_delay(
        self,
        text: str,
        bag_path: str,
        trigger_phrase: str = None,
        fixed_delay_key: str = None
    ):  
        speech_thread = threading.Thread(
            target=self.say_text,
            args=(text,),
            daemon=True
        )
        speech_thread.start()

        if fixed_delay_key and fixed_delay_key in self.fixed_gesture_delay_map:
            delay = self.fixed_gesture_delay_map[fixed_delay_key]
            self.get_logger().info(
                f'Fixed timing: "{fixed_delay_key}" -> delay {delay:.2f}s'
            )
        elif trigger_phrase:
            delay = self.estimate_delay_to_phrase(text, trigger_phrase)
            self.get_logger().info(
                f'Phrase timing: "{trigger_phrase}" -> delay {delay:.2f}s'
            )
        else:
            delay = 0.0

        if delay > 0:
            time.sleep(delay)

        if bag_path:
            self.play_gesture_bag_once(bag_path)

        speech_thread.join()
    def estimate_delay_to_phrase(self, full_text: str, trigger_phrase: str) -> float:
        if not full_text or not trigger_phrase:
            return 0.0

        normalized_text = self.normalize_text(full_text)
        normalized_trigger = self.normalize_text(trigger_phrase)

        idx = normalized_text.find(normalized_trigger)
        if idx == -1:
            return 0.0

        preceding_text = normalized_text[:idx]
        preceding_word_count = self.count_words(preceding_text)

        return self.speech_startup_offset + (preceding_word_count * self.seconds_per_word)
    def status_callback(self, msg: Bool):
        if msg.data is True:
            self.get_logger().info(f'DEBUG: Received SUCCESS signal on {self.status_topic}. Setting event.')
            self.robot_ready_event.set()
        else:
            self.get_logger().info('DEBUG: Received FALSE signal from Nav2. Still waiting...')

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

    def speak_then_phrase_timed_gesture(self, text: str, bag_path: str, trigger_phrase: str):
        speech_thread = threading.Thread(
            target=self.say_text,
            args=(text,),
            daemon=True
        )
        speech_thread.start()

        delay = self.estimate_delay_to_phrase(text, trigger_phrase)
        self.get_logger().info(
            f'Phrase timing: "{trigger_phrase}" -> delay {delay:.2f}s'
        )

        if delay > 0:
            time.sleep(delay)

        if bag_path:
            self.play_gesture_bag_once(bag_path)

        speech_thread.join()
    def worker_loop(self):
        while rclpy.ok():
            payload = self.command_queue.get()

            try:
                cmd_type = payload.get("type", "plain_text")
                text = payload.get("text", "").strip()
                station = payload.get("station")
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
                        self.say_text(text)
                    continue

                if self.operating_mode == "speech_and_gestures":
                    self.get_logger().info('DEBUG: Running in speech_and_gestures mode.')
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

                        speech_thread.join()
                    continue
                if cmd_type == "didnt_hear":
                    if text:
                        speech_thread = threading.Thread(
                            target=self.say_text,
                            args=(text,),
                            daemon=True
                        )
                        speech_thread.start()

                        self.play_gesture_bag_once("bag/didnt_hear")

                        speech_thread.join()
                    continue

                platform = payload.get("platform") or self.find_platform_in_text(text)
                bag_path = self.find_matching_bag(text)

                if cmd_type == "station_guidance" and station and platform:
                    self.stop_thinking_gesture()
                    self.get_logger().info(
                        f'DEBUG: guide_and_navigate mode: station={station}, platform={platform}'
                    )

                    follow_text = (
                        f"I will take you to {station}. "
                        f"We need to go to {self.platform_to_speech(platform)}. "
                        f"Follow me."
                    )

                    self.speak_with_optional_fixed_delay(
                        text=follow_text,
                        bag_path=self.follow_me_bag,
                        fixed_delay_key="follow_me"
                    )

                    self.get_logger().info("DEBUG: Performing crouch before navigation.")
                    
                    if os.path.exists(self.crouch_bag):
                        self.play_gesture_bag_once(self.crouch_bag)
                    else:
                        self.get_logger().warn(f"Crouch bag not found: {self.crouch_bag}")
                        
                    self.robot_ready_event.clear()
                    plat_msg = String()
                    plat_msg.data = platform
                    
                    self.platform_pub_.publish(plat_msg)

                    self.get_logger().info(
                        f'DEBUG: [WAITING] Waiting for {self.status_topic} == True'
                    )

                    arrived = self.robot_ready_event.wait(timeout=120.0)

                    if arrived:
                        self.get_logger().info('DEBUG: Arrived at goal.')

                        if text:
                            trigger_phrase = self.find_trigger_phrase(text)
                        
                            if bag_path and trigger_phrase:
                                self.speak_then_phrase_timed_gesture(
                                    text=text,
                                    bag_path=bag_path,
                                    trigger_phrase=trigger_phrase
                                )
                            else:
                                self.say_text(text)
                    else:
                        self.get_logger().error('DEBUG: Timed out waiting for arrival.')

                    continue

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

                    speech_thread.join()

            except Exception as e:
                self.get_logger().error(f'Worker error: {e}')
            finally:
                self.command_queue.task_done()
    
    def find_trigger_phrase(self, text: str):
        normalized = self.normalize_text(text)
        for phrase in sorted(self.gesture_map.keys(), key=len, reverse=True):
            if phrase in normalized:
                return phrase
        return None
    def start_thinking_gesture(self):
        if self.thinking_active.is_set():
            self.get_logger().info('DEBUG: Thinking gesture already active.')
            return

        if not os.path.exists(self.thinking_bag):
            self.get_logger().error(f'Thinking bag missing: {self.thinking_bag}')
            return

        thinking_text = "Give me a second to think about it."

        speech_thread = threading.Thread(
            target=self.say_text,
            args=(thinking_text,),
            daemon=True
        )
        speech_thread.start()

        delay = self.fixed_gesture_delay_map.get("thinking", 0.3)
        time.sleep(delay)

        self.thinking_stop_event.clear()
        self.thinking_active.set()
        self.thinking_thread = threading.Thread(
            target=self._thinking_gesture_loop,
            daemon=True
        )
        self.thinking_thread.start()
        self.get_logger().info('DEBUG: Thinking gesture started.')

    def stop_thinking_gesture(self):
        if not self.thinking_active.is_set():
            self.get_logger().info('DEBUG: Thinking gesture already stopped.')
            return

        self.thinking_active.clear()
        self.thinking_stop_event.set()

        if self.thinking_thread and self.thinking_thread.is_alive():
            self.thinking_thread.join(timeout=1.0)

        self.thinking_thread = None
        self.get_logger().info('DEBUG: Thinking gesture stopped.')
    def _thinking_gesture_loop(self):
        while rclpy.ok() and self.thinking_active.is_set():
            self.play_gesture_bag_once(self.thinking_bag, stop_event=self.thinking_stop_event)
            time.sleep(0.1)
    
    def find_platform_in_text(self, text: str):

        normalized = text.lower()

        lines = ["district", "circle", "piccadilly"]
        directions = ["eastbound", "westbound"]

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


    def say_follow_me_message(self, station: str, platform: str):
        spoken_platform = self.platform_to_speech(platform)
    
        msg = String()
        msg.data = (
            f"I will take you to {station}. "
            f"We need to go to {spoken_platform}. "
            f"Follow me."
        )
        self.speech_pub_.publish(msg)
        self.get_logger().info(f'Follow-me speech published: "{msg.data}"')
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

    def play_gesture_bag_once(self, bag_dir_path, stop_event=None):
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
            if stop_event is not None and stop_event.is_set():
                self.get_logger().info(f'Gesture interrupted for {bag_dir_path}')
                return

            target_time = start_time + (step['time'] / self.playback_speed)
            now = time.time()
            if target_time > now:
                time.sleep(target_time - now)

            msg.joint_names = step['names']
            msg.joint_angles = step['positions']
            self.angles_pub_.publish(msg)

        self.get_logger().info(f'Finished gesture for {bag_dir_path}')


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
        node.stop_thinking_gesture()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()