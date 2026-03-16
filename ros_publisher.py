import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class LlmPublisher(Node):
    def __init__(self):
        super().__init__("llm_response_publisher")
        self.publisher_ = self.create_publisher(String, "llm_response", 10)

    def publish_response(self, text: str) -> None:
        msg = String()
        msg.data = text
        self.publisher_.publish(msg)
        self.get_logger().info(f'Published to /llm_response: "{text}"')


_ros_initialized = False
_ros_node = None


def get_ros_publisher() -> LlmPublisher:
    global _ros_initialized, _ros_node

    if not _ros_initialized:
        rclpy.init()
        _ros_initialized = True

    if _ros_node is None:
        _ros_node = LlmPublisher()

    return _ros_node


def shutdown_ros() -> None:
    global _ros_initialized, _ros_node

    if _ros_node is not None:
        _ros_node.destroy_node()
        _ros_node = None

    if _ros_initialized:
        rclpy.shutdown()
        _ros_initialized = False