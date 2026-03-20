# Obstacle Avoidance for Pepper Robot

## Overview
The obstacle avoidance system focuses on generating real-time collision avoidance commands using the RPLidar A2M12 for the Pepper robot. It does this by tracking moving obstacels and predicting where they'll be in the next few seconds.

## Approach
Instead of relying on Pepper's onboard sensors (which operate at a lower frequency), this system uses a custom predictive algorithm that processes raw laser scans to track dynamic obstacles in real-time. 

The core logic is executed in a Python ROS2 node (`lidar_code_final.py`) that uses the following techniques:

* **DBSCAN Clustering:** Groups raw LiDAR points to differentiate distinct solid objects (e.g., humans) from random sensor noise.
* **Nearest Neighbour Association:** Uses 'nearest neightbour' techniques to assign persistent IDs to clusters across consecutive frames to track individual targets.
* **Constant Velocity Model:** Calculates the velocity vector over a multi-scan moving average to eliminate jitter, predicting the object's position up to 3 seconds into the future.
* **Dot Product Filtering:** Ignores objects moving parallel to or away from the robot, focusing computing power solely on approaching obstacles.
* **Tangential Avoidance:** Computes a safe location for the Pepper to go to (at 90° or 45°) to find the shortest escape route when a collision is predicted within the 0.30m safety radius.

### Outcome

The system successfully identifies moving threats and calculates safe escape coordinates. By implementing the multi-scan velocity buffer, the node effectively ignores minute errors and jitter, providing a stable calculation of movement commands to Pepper.

---

## Setup and Execution

The script is designed to run within a dockerised ROS2 (Humble) environment on the Raspberry Pi connected to the RPLidar A2.

**Required Python Dependencies:**

```bash
pip install numpy rplidar-python scikit-learn rclpy
```
---

### Execution Guide
Follow the following code to run the file:

Open a terminal on the host Raspberry Pi and open the ROS2 docker in this:

```bash
sudo docker exec -it lidar_read_usb /bin/bash
```

Navigate to the correct directory:

```bash
cd /root/ros2_ws/src/sllidar_ros2/scripts
```

Run the Python script:

```bash
python3 lidar_code_final.py
```
