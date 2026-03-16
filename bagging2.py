import rclpy
from rclpy.node import Node
import sys
import time

# ROS 2 Bag and Serialization imports
import rosbag2_py
from rclpy.serialization import deserialize_message

# Message types
from sensor_msgs.msg import JointState
from naoqi_bridge_msgs.msg import JointAnglesWithSpeed


class BagGesturePlayer(Node):
    def __init__(self, bag_dir_path, playback_speed=1.0, trim_threshold=0.02):
        super().__init__('bag_gesture_player')
        
        # --- CONFIGURATION ---
        self.angles_topic = '/joint_angles'
        self.stiffness_topic = '/joint_stiffness'
        
        self.angles_pub_ = self.create_publisher(JointAnglesWithSpeed, self.angles_topic, 10)
        self.stiffness_pub_ = self.create_publisher(JointState, self.stiffness_topic, 10)
        
        self.bag_dir_path = bag_dir_path
        self.playback_speed = playback_speed
        self.trim_threshold = trim_threshold
        
        self.trajectory = []
        
        # Load the trajectory from the bag file into memory
        if not self.load_bag():
            self.get_logger().error("Failed to load trajectory from bag. Shutting down.")
            sys.exit(1)
            
        # Automatically cut off dead time at the start and end
        self.trim_trajectory()

    def load_bag(self):
        """Reads the ROS 2 bag, extracts /joint_states, and builds a trajectory timeline."""
        self.get_logger().info(f"Opening ROS 2 bag: {self.bag_dir_path}")
        
        reader = rosbag2_py.SequentialReader()
        
        # Define storage options (sqlite3 is the ROS 2 default)
        storage_options = rosbag2_py.StorageOptions(
            uri=self.bag_dir_path,
            storage_id='sqlite3' 
        )
        converter_options = rosbag2_py.ConverterOptions(
            input_serialization_format='cdr',
            output_serialization_format='cdr'
        )
        
        try:
            reader.open(storage_options, converter_options)
        except Exception as e:
            self.get_logger().error(f"Could not open bag file: {e}")
            return False

        t0 = None
        message_count = 0

        while reader.has_next():
            (topic, data, t_ns) = reader.read_next()
            
            if topic == '/joint_states':
                msg = deserialize_message(data, JointState)
                
                if not msg.name or not msg.position:
                    continue

                t_sec = t_ns / 1e9
                
                if t0 is None:
                    t0 = t_sec
                    
                relative_time = t_sec - t0
                
                self.trajectory.append({
                    'time': relative_time,
                    'names': list(msg.name),
                    'positions': list(msg.position)
                })
                message_count += 1
                
        self.get_logger().info(f"Successfully loaded {message_count} joint state messages.")
        return message_count > 0

    def trim_trajectory(self):
        """Removes leading and trailing frames where the robot is stationary."""
        if not self.trajectory:
            return

        start_idx = 0
        end_idx = len(self.trajectory) - 1
        
        first_positions = self.trajectory[0]['positions']
        last_positions = self.trajectory[-1]['positions']

        # 1. Scan forward to find where movement begins
        for i, step in enumerate(self.trajectory):
            # Calculate the maximum movement of any joint compared to the first frame
            max_diff = max(abs(a - b) for a, b in zip(step['positions'], first_positions))
            if max_diff > self.trim_threshold:
                # Keep a tiny 5-frame buffer before movement starts so it ramps up smoothly
                start_idx = max(0, i - 5) 
                break

        # 2. Scan backward to find where movement ended
        for i in range(len(self.trajectory) - 1, -1, -1):
            step = self.trajectory[i]
            max_diff = max(abs(a - b) for a, b in zip(step['positions'], last_positions))
            if max_diff > self.trim_threshold:
                # Keep a tiny 5-frame buffer after movement ends
                end_idx = min(len(self.trajectory) - 1, i + 5)
                break

        # Safety check in case the threshold is too high or there was no movement
        if start_idx >= end_idx:
            self.get_logger().warn("Trim threshold too high or no movement detected. Using full bag.")
            return

        # 3. Slice the list to remove the dead ends
        original_len = len(self.trajectory)
        self.trajectory = self.trajectory[start_idx:end_idx + 1]
        
        new_len = len(self.trajectory)
        frames_cut = original_len - new_len
        self.get_logger().info(f"Trimmed {frames_cut} dead frames. (Start: {start_idx}, End: {end_idx})")

        # 4. Normalize the timestamps so playback starts exactly at t = 0.0
        time_offset = self.trajectory[0]['time']
        for step in self.trajectory:
            step['time'] -= time_offset

    def set_stiffness(self, target_stiffness=1.0):
        """Publishes a JointState message to enable motor stiffness."""
        self.get_logger().info("Setting stiffness to 1.0 so the robot can move...")
        
        joint_names = self.trajectory[0]['names']
        
        msg = JointState()
        msg.name = joint_names
        msg.effort = [float(target_stiffness)] * len(joint_names)
        
        for _ in range(3):
            self.stiffness_pub_.publish(msg)
            time.sleep(0.2)

    def play_loop(self):
        """Plays the loaded trajectory back in a continuous loop."""
        self.set_stiffness(1.0)
        
        self.get_logger().info("Starting continuous playback loop. Press Ctrl+C to stop.")
        
        msg = JointAnglesWithSpeed()
        msg.speed = 0.5 
        msg.relative = 0
        
        loop_count = 1
        
        while rclpy.ok():
            self.get_logger().info(f"--- Starting Loop #{loop_count} ---")
            start_time = time.time()
            
            for step in self.trajectory:
                if not rclpy.ok():
                    break
                    
                target_time = start_time + (step['time'] / self.playback_speed)
                now = time.time()
                
                if target_time > now:
                    time.sleep(target_time - now)
                    
                msg.joint_names = step['names']
                msg.joint_angles = step['positions']
                
                self.angles_pub_.publish(msg)
                
            loop_count += 1
            
            # Small pause before restarting the loop
            time.sleep(0.5)


def main(args=None):
    rclpy.init(args=args)
    
    # --- Parameters ---
    BAG_DIRECTORY_PATH = "bag_files/didnt_hear" 
    
    PLAYBACK_SPEED = 1.0
    
    # Define how much a joint must move (in radians) before playback starts.
    # 0.02 radians is ~1.14 degrees. This ignores sensor noise but catches true movement.
    TRIM_THRESHOLD_RAD = 0.0
    
    player_node = BagGesturePlayer(
        bag_dir_path=BAG_DIRECTORY_PATH, 
        playback_speed=PLAYBACK_SPEED,
        trim_threshold=TRIM_THRESHOLD_RAD
    )
    
    try:
        player_node.play_loop()
    except KeyboardInterrupt:
        player_node.get_logger().info("Keyboard interrupt detected. Stopping playback.")
    finally:
        player_node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()

if __name__ == '__main__':
    main()