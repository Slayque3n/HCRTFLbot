#!/usr/bin/env python3
import math
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from std_msgs.msg import String


def yaw_to_quaternion(yaw):
    from geometry_msgs.msg import Quaternion
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


class Helper(Node):
    def __init__(self):
        super().__init__('pepper_nav2_smoke_test')
        self.init_pub = self.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
        self.platform_pub = self.create_publisher(String, '/pepper_nav/platform', 10)

    def send_initial_pose(self, x, y, yaw, frame='map'):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = frame
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = float(x)
        msg.pose.pose.position.y = float(y)
        msg.pose.pose.orientation = yaw_to_quaternion(float(yaw))
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.0685
        for _ in range(5):
            self.init_pub.publish(msg)

    def send_platform(self, name):
        msg = String()
        msg.data = name
        self.platform_pub.publish(msg)


def main():
    rclpy.init()
    node = Helper()
    print('1) publish initial pose')
    print('2) send platform command')
    choice = input('Choose 1 or 2: ').strip()

    if choice == '1':
        x = float(input('x: ').strip())
        y = float(input('y: ').strip())
        yaw = float(input('yaw radians: ').strip())
        node.send_initial_pose(x, y, yaw)
        print('Published /initialpose a few times.')
    elif choice == '2':
        name = input('platform/location name: ').strip()
        node.send_platform(name)
        print(f'Published /pepper_nav/platform = {name}')
    else:
        print('Nothing sent.')

    rclpy.spin_once(node, timeout_sec=0.2)
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
