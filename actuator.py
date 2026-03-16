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

from std_msgs.msg import String
from sensor_msgs.msg import JointState
from naoqi_bridge_msgs.msg import JointAnglesWithSpeed


class LlmGestureSpeechNode(Node):
    def __init__(self):
        super().__init__('llm_gesture_speech_node')

        # Topics
        self.llm_topic = '/llm_response'
        self.speech_topic = '/speech'
        self.angles_topic = '/joint_angles'
        self.stiffness_topic = '/joint_stiffness'

        # ROS interfaces
        self.subscription = self.create_subscription(
            String,
            self.llm_topic,
            self.llm_callback,
            10
        )
        self.speech_pub_ = self.create_publisher(String, self.speech_topic, 10)
        self.angles_pub_ = self.create_publisher(JointAnglesWithSpeed, self.angles_topic, 10)
        self.stiffness_pub_ = self.create_publisher(JointState, self.stiffness_topic, 10)

        # Playback config
        self.playback_speed = 1.0
        self.trim_threshold = 0.02

        # Replace these with your real keyword -> bag mappings
        self.gesture_map = {
            "hello": "bag/wave",
            "didn't hear": "bag_files/didnt_hear",
            "sorry": "bag_files/sorry",
            "left": "bag_files/left",
            "right": "bag_files/right",
            "northbound": "bag_files/northbound",
            "southbound": "bag_files/southbound",
            "eastbound": "bag_files/eastbound",
            "westbound": "bag_files/westbound",
            "victoria": "bag_files/victoria",
            "central": "bag_files/central",
            "northern": "bag_files/northern",
            "piccadilly": "bag_files/piccadilly",
            "jubilee": "bag_files/jubilee",
            "district": "bag_files/district",
            "circle": "bag_files/circle",
            "bakerloo": "bag_files/bakerloo",
            "hammersmith": "bag_files/hammersmith",
            "metropolitan": "bag_files/metropolitan",
        }

        self.command_queue = queue.Queue()
        self.worker = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker.start()

        self.get_logger().info('Listening on /llm_response')

    def llm_callback(self, msg: String):
        text = msg.data.strip()
        if not text:
            return

        self.get_logger().info(f'Received: "{text}"')
        self.command_queue.put(text)

    def worker_loop(self):
        while True:
            text = self.command_queue.get()
            try:
                bag_path = self.find_matching_bag(text)

                # Speak and gesture in parallel
                speech_thread = threading.Thread(
                    target=self.say_text,
                    args=(text,),
                    daemon=True
                )
                speech_thread.start()

                if bag_path:
                    self.play_gesture_bag_once(bag_path)
                else:
                    self.get_logger().warn(f'No gesture mapping found for: "{text}"')

                speech_thread.join(timeout=0.1)
            except Exception as e:
                self.get_logger().error(f'Worker error: {e}')
            finally:
                self.command_queue.task_done()

    def say_text(self, text: str):
        msg = String()
        msg.data = text
        self.speech_pub_.publish(msg)
        self.get_logger().info(f'Published speech to /speech: "{text}"')

    def normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    def find_matching_bag(self, text: str):
        normalized = self.normalize_text(text)

        # longest phrase first
        for phrase in sorted(self.gesture_map.keys(), key=len, reverse=True):
            if phrase in normalized:
                bag_path = self.gesture_map[phrase]
                self.get_logger().info(f'Matched "{phrase}" -> {bag_path}')
                return bag_path

        return None

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
            self.get_logger().warn('Trim threshold too high or no movement detected. Using full bag.')
            return trajectory

        trimmed = trajectory[start_idx:end_idx + 1]
        offset = trimmed[0]['time']

        for step in trimmed:
            step['time'] -= offset

        self.get_logger().info(f'Trimmed trajectory to {len(trimmed)} frames')
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
            self.get_logger().error(f'Bag path does not exist: {bag_dir_path}')
            return

        trajectory = self.load_bag(bag_dir_path)
        if not trajectory:
            self.get_logger().warn(f'No trajectory found in bag: {bag_dir_path}')
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

        self.get_logger().info(f'Finished playback for {bag_dir_path}')


def main(args=None):
    rclpy.init(args=args)
    node = LlmGestureSpeechNode()

    executor = MultiThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()
    except KeyboardInterrupt:
        node.get_logger().info('Shutting down node.')
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()