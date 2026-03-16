#!/usr/bin/env python3
import json
import math
import os
from pathlib import Path
from typing import Dict, Optional

import rclpy
import yaml
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String


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

        home = str(Path.home())
        self.declare_parameter('locations_file', os.path.join(home, 'pepper_named_locations_map.yaml'))
        self.declare_parameter('platform_topic', '/pepper_nav/platform')
        self.declare_parameter('save_topic', '/pepper_nav/save_named_location')
        self.declare_parameter('delete_topic', '/pepper_nav/delete_named_location')
        self.declare_parameter('status_topic', '/pepper_nav/status')
        self.declare_parameter('pose_topic', '/amcl_pose')
        self.declare_parameter('default_frame_id', 'map')
        self.declare_parameter('navigate_action_name', 'navigate_to_pose')

        self.locations_file = str(Path(self.get_parameter('locations_file').value).expanduser())
        self.platform_topic = self.get_parameter('platform_topic').value
        self.save_topic = self.get_parameter('save_topic').value
        self.delete_topic = self.get_parameter('delete_topic').value
        self.status_topic = self.get_parameter('status_topic').value
        self.pose_topic = self.get_parameter('pose_topic').value
        self.default_frame_id = self.get_parameter('default_frame_id').value
        self.navigate_action_name = self.get_parameter('navigate_action_name').value

        self.locations: Dict[str, Dict[str, float]] = {}
        self.current_pose: Optional[PoseWithCovarianceStamped] = None
        self.active_goal_name: Optional[str] = None

        self._load_locations()

        self.create_subscription(String, self.platform_topic, self.on_platform, 10)
        self.create_subscription(String, self.save_topic, self.on_save, 10)
        self.create_subscription(String, self.delete_topic, self.on_delete, 10)
        self.create_subscription(PoseWithCovarianceStamped, self.pose_topic, self.on_pose, 10)

        self.status_pub = self.create_publisher(String, self.status_topic, 10)
        self.nav_client = ActionClient(self, NavigateToPose, self.navigate_action_name)

        self.publish_status('idle', f'Loaded {len(self.locations)} locations.', None)

    def _load_locations(self):
        p = Path(self.locations_file)
        if not p.exists():
            self.locations = {}
            return
        with open(p, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
        out = {}
        for name, pose in data.items():
            if isinstance(pose, dict):
                out[str(name)] = {
                    'x': float(pose['x']),
                    'y': float(pose['y']),
                    'yaw': float(pose['yaw']),
                    'frame_id': str(pose.get('frame_id', self.default_frame_id)),
                }
        self.locations = out

    def _save_locations(self):
        p = Path(self.locations_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            yaml.safe_dump(self.locations, f, sort_keys=True)

    def publish_status(self, state: str, message: str, active_goal: Optional[str]):
        msg = String()
        msg.data = json.dumps({'state': state, 'message': message, 'active_goal': active_goal})
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
            'frame_id': frame_id,
        }

        self._save_locations()
        self.publish_status('saved', f'Saved {name}', None)

    def on_delete(self, msg: String):
        name = msg.data.strip()
        if name in self.locations:
            del self.locations[name]
            self._save_locations()
            self.publish_status('deleted', f'Deleted {name}', None)

    def on_platform(self, msg: String):
        name = msg.data.strip()
        if name not in self.locations:
            self.publish_status('unknown_location', f'{name} not known', None)
            return

        if not self.nav_client.wait_for_server(timeout_sec=2.0):
            self.publish_status('nav_unavailable', 'Nav2 action server not available', None)
            return

        pose_dict = self.locations[name]

        goal_pose = PoseStamped()
        stamp = self.get_clock().now().to_msg()
        goal_pose.header.stamp = stamp

        self.get_logger().info(
            f"Sending goal {name} with stamp {stamp.sec}.{stamp.nanosec:09d} "
            f"in frame {goal_pose.header.frame_id} "
            f"at x={goal_pose.pose.position.x}, y={goal_pose.pose.position.y}"
        )
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
        else:
            self.publish_status('failed', f'Navigation failed {status}', name)


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
