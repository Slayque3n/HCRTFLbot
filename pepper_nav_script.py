#!/usr/bin/env python3
import json
import math
from typing import Dict, Optional

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String, Bool


def yaw_to_quaternion(yaw: float):
    from geometry_msgs.msg import Quaternion
    q = Quaternion()
    q.x = 0.0
    q.y = 0.0
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


class PepperPlatformNavNav2(Node):

    def __init__(self):
        super().__init__('pepper_platform_nav_nav2')

        # Topic / nav parameters
        self.declare_parameter('platform_topic', '/platform_name')
        self.declare_parameter('at_goal_topic', '/robot_at_goal')
        self.declare_parameter('speech_topic', '/speech')
        self.declare_parameter('save_topic', '/pepper_nav/save_named_location')
        self.declare_parameter('status_topic', '/pepper_nav/status')
        self.declare_parameter('pose_topic', '/amcl_pose')
        self.declare_parameter('default_frame_id', 'map')
        self.declare_parameter('navigate_action_name', 'navigate_to_pose')

        self.platform_topic = self.get_parameter('platform_topic').value
        self.at_goal_topic = self.get_parameter('at_goal_topic').value
        self.speech_topic = self.get_parameter('speech_topic').value
        self.save_topic = self.get_parameter('save_topic').value
        self.status_topic = self.get_parameter('status_topic').value
        self.pose_topic = self.get_parameter('pose_topic').value
        self.default_frame_id = self.get_parameter('default_frame_id').value
        self.navigate_action_name = self.get_parameter('navigate_action_name').value
        
        self.declare_parameter(
            'locations_file',
            '/mnt/c/Users/dylan/Documents/imperial/year4/Human-Centred Robotics/HCRTFLbot/locations.json'
        )
        self.locations_file = self.get_parameter('locations_file').value

        self.locations: Dict[str, Dict[str, float]] = {}
        self.current_pose: Optional[PoseWithCovarianceStamped] = None
        self.active_goal_name: Optional[str] = None

        self._load_locations()

        # Subscriptions
        self.create_subscription(String, self.platform_topic, self.on_platform, 10)
        self.create_subscription(String, self.save_topic, self.on_save, 10)
        self.create_subscription(PoseWithCovarianceStamped, self.pose_topic, self.on_pose, 10)

        # Publishers
        self.status_pub = self.create_publisher(String, self.status_topic, 10)
        self.at_goal_pub = self.create_publisher(Bool, self.at_goal_topic, 10)
        self.speech_pub = self.create_publisher(String, self.speech_topic, 10)

        self.nav_client = ActionClient(self, NavigateToPose, self.navigate_action_name)

        self.publish_status('idle', f'Loaded {len(self.locations)} locations.', None)

    def _load_locations(self):
        try:
            with open(self.locations_file, 'r') as f:
                self.locations = json.load(f)
            self.get_logger().info(f"Loaded locations from {self.locations_file}")
        except Exception:
            self.get_logger().warn("No saved locations file found. Using defaults.")
            self.locations = {
                "district_eastbound": {
                    "x": 1.25,
                    "y": 0.50,
                    "yaw": 1.57,
                    "frame_id": "map",
                },
                "piccadilly_southbound": {
                    "x": -2.10,
                    "y": 3.45,
                    "yaw": 0.0,
                    "frame_id": "map",
                },
            }

    def publish_status(self, state: str, message: str, active_goal: Optional[str]):
        msg = String()
        msg.data = json.dumps({
            'state': state,
            'message': message,
            'active_goal': active_goal
        })
        self.status_pub.publish(msg)
        self.get_logger().info(f'[{state}] {message}')

    def on_pose(self, msg: PoseWithCovarianceStamped):
        self.current_pose = msg

    def on_save(self, msg: String):
        name = msg.data.strip()
        if not name:
            return

        if self.current_pose is None:
            self.publish_status('waiting_for_pose', 'No pose received yet.', None)
            return

        pose = self.current_pose.pose.pose
        yaw = quaternion_to_yaw(
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w
        )
        frame_id = self.current_pose.header.frame_id or self.default_frame_id

        self.locations[name] = {
            'x': float(pose.position.x),
            'y': float(pose.position.y),
            'yaw': float(yaw),
            'frame_id': frame_id
        }

        self._save_locations()
        self.publish_status('saved', f'Saved {name}', None)

    def on_platform(self, msg: String):
        name = msg.data.strip()
        if not name:
            return
        if name not in self.locations:
            self.publish_status('unknown_location', f'{name} not known', None)
            return

        speech_msg = String()
        speech_msg.data = "Now moving"
        self.speech_pub.publish(speech_msg)

        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.publish_status('nav_unavailable', 'Nav2 action server not available', None)
            return

        pose_dict = self.locations["test_point"]
        goal_pose = PoseStamped()
        goal_pose.header.stamp = self.get_clock().now().to_msg()
        goal_pose.header.frame_id = pose_dict.get('frame_id', self.default_frame_id)
        goal_pose.pose.position.x = float(pose_dict['x'])
        goal_pose.pose.position.y = float(pose_dict['y'])
        goal_pose.pose.orientation = yaw_to_quaternion(float(pose_dict['yaw']))

        goal = NavigateToPose.Goal()
        goal.pose = goal_pose

        self.active_goal_name = name
        self.publish_status('goal_sent', f'Sending {name} to Nav2', name)

        send_future = self.nav_client.send_goal_async(goal)
        send_future.add_done_callback(self._goal_response_cb)

    def _goal_response_cb(self, future):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.publish_status('rejected', 'Goal rejected', self.active_goal_name)
            self.active_goal_name = None
            return

        self.publish_status('navigating', 'Goal accepted', self.active_goal_name)
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(self._result_cb)

    def _result_cb(self, future):
        result = future.result()
        status = result.status
        name = self.active_goal_name
        self.active_goal_name = None

        if status == GoalStatus.STATUS_SUCCEEDED:
            self.publish_status('arrived', f'Arrived at {name}', name)
            at_goal_msg = Bool()
            at_goal_msg.data = True
            self.at_goal_pub.publish(at_goal_msg)
        else:
            self.publish_status('failed', f'Navigation failed {status}', name)

    def _save_locations(self):
        try:
            with open(self.locations_file, 'w') as f:
                json.dump(self.locations, f, indent=2)
            self.get_logger().info(f"Saved locations to {self.locations_file}")
        except Exception as e:
            self.get_logger().error(f"Failed to save locations: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = PepperPlatformNavNav2()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()