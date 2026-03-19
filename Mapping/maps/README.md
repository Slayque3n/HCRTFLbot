# 2D Map Generation via SLAM for Pepper Robot

## Overview

This component of the project focuses on generating a 2D occupancy grid map using Simultaneous Localisation and Mapping (SLAM) for the Pepper mobile robot. The map provides a global reference frame for localisation and serves as a foundation for downstream navigation algorithms.

---

## Attempt 1 — Onboard Sensors with ROS2 and SLAM Toolbox

### Approach

The initial implementation leverages ROS2 (Humble) and [SLAM Toolbox](https://github.com/SteveMacenski/slam_toolbox), using Pepper's onboard sensors to construct the occupancy grid. The goal was to minimise additional hardware by relying on the robot's built-in sensors. The sensors considered, as documented on the [Aldebaran technical reference](http://doc.aldebaran.com/2-5/family/pepper_technical/pepper_dcm/actuator_sensor_names.html), included the laser rangefinders, RGB camera, and infrared (IR) sensors.

A standard SLAM Toolbox configuration was used alongside RViz for visualisation. A custom node was developed to republish depth point cloud data from the camera to the `/scan` topic, supplementing the laser data with additional points for compatibility with SLAM Toolbox.

### Outcome

A map was successfully being generated in RViz during data collection. However, the resulting map was unusable: as the robot changed orientation, the map rotated with it, causing overlapping and misaligned features.

### Root Cause

Two candidate causes were identified:

- **Incorrect TF frame definitions** — verifiable using `ros2 run tf2_tools view_frames`
- **Wheel odometry drift** — the default SLAM Toolbox configuration relies on wheel odometry, which is susceptible to drift and cumulative error

Investigation confirmed that wheel odometry drift was the primary cause. The unreliable odometry corrupted the robot's estimated pose, resulting in the observed map distortion. An alternative odometry source was required.

---

## Attempt 2 — LiDAR-Derived Odometry via ICP and GTSAM

### Approach

To address the odometry problem, a custom odometry system was developed that estimates robot motion directly from consecutive LiDAR scans, bypassing wheel encoder data entirely.

The key algorithm employed was **Iterative Closest Point (ICP)**, which computes the relative transformation between successive scans by aligning detected points against fixed environmental features. This provides a scan-to-scan motion estimate.

To mitigate drift accumulation over time, these incremental estimates were integrated into a **factor graph** using the [GTSAM](https://gtsam.org/) library. Graph optimisation was then applied to produce globally consistent pose estimates.

This approach was inspired by [LeGO-LOAM-SR](https://github.com/eperdices/LeGO-LOAM-SR) and the methodology demonstrated in [this overview video](https://www.youtube.com/watch?v=i5bt5gLs7zo).

**Required dependencies:**

```bash
pip install gtsam rplidar-python numpy pyyaml opencv-python open3d
```

The relevant scripts are located in the `world_building` directory of this repository. They were executed inside a Docker environment running on a Raspberry Pi, which was integrated with the Pepper robot's system to subscribe to `/scan` data.

### Outcome

Multiple iterations were developed and the maps produced broadly reflected the physical layout of the environment. However, the method proved operationally sensitive: the LiDAR unit needed to be held level and moved slowly, with vibrations carefully avoided throughout data collection. The resulting maps, while indicative, were not of sufficient quality or reliability for production use.

---

## Attempt 3 — Scan Matching with Foxglove and `laser_scan_matcher`

### Approach

During an exploratory phase, [Foxglove Studio](https://docs.foxglove.dev/docs/getting-started/frameworks/ros2) was introduced to visualise live LiDAR data streams. This raised the question of whether scan data could be preserved and transformed into a usable map representation. While Foxglove itself does not support map saving, it prompted investigation into **scan matching** as a mechanism for generating reliable odometry from the LiDAR.

The following packages were cloned and built inside the Docker environment:

- [`ros2_laser_scan_matcher`](https://github.com/AlexKaravaev/ros2_laser_scan_matcher) — publishes odometry and TF transforms derived from scan matching
- [`csm` (Canonical Scan Matcher)](https://github.com/AndreaCensi/csm) — the underlying scan matching library

This combination provides LiDAR-derived odometry without reliance on wheel encoder data.

### Setup and Execution

Six terminal sessions are required. Five run concurrently; the sixth is used at the end to save the map.

**1. Launch the LiDAR driver:**
```bash
ros2 launch sllidar_ros2 sllidar_a2m12_launch.py \
  serial_port:=/dev/ttyUSB0 \
  serial_baudrate:=256000 \
  scan_mode:=Standard
```

**2. Launch the Foxglove bridge (for live visualisation):**
```bash
ros2 launch foxglove_bridge foxglove_bridge_launch.xml
```

**3. Launch SLAM Toolbox:**
```bash
ros2 launch slam_toolbox online_async_launch.py \
  use_time:=false \
  slam_params_file:=/root/mapper_params_online_async.yaml
```

> A custom YAML configuration file (`mapper_params_online_async.yaml`) is required to publish `/base_link` in place of `/base_footprint`.

**4. Launch the scan matcher (LiDAR odometry):**
```bash
ros2 run ros2_laser_scan_matcher laser_scan_matcher \
  --ros-args \
  -p publish_odom:=/odom \
  -p publish_tf:=true
```

**5. Publish the static transform between `base_link` and `laser`:**
```bash
ros2 run tf2_ros static_transform_publisher \
  --x 0 --y 0 --z 0 \
  --yaw 0 --pitch 0 --roll 0 \
  --frame-id base_link \
  --child-frame-id laser
```

**6. Save the completed map (run after data collection is complete):**
```bash
ros2 run nav2_map_server map_saver_cli -f <map_name>
```

> Ensure all required Foxglove, SLAM Toolbox, Nav2, and ROS 2 packages and their dependencies are installed before running.

### Outcome

A complete and usable 2D occupancy grid map was successfully generated using this pipeline.

---

## Summary

| Attempt | Odometry Source | Outcome |
|---|---|---|
| 1 | Wheel encoders (default) | Map distorted due to odometry drift |
| 2 | ICP + GTSAM factor graph | Indicative maps; operationally fragile |
| 3 | `laser_scan_matcher` (scan matching) | Successful map generation ✓ |