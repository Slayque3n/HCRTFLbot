from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, ExecuteProcess
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            PathJoinSubstitution([
                FindPackageShare('your_nav_package'),
                'launch',
                'pepper_nav2_test_with_rviz_fixed.launch.py'
            ])
        ]),
        launch_arguments={
            'map': 'pitube_map2_edited.yaml',
            'params_file': 'pepper_nav2_params.yaml',
            'rviz_config': 'pepper_nav_test.rviz',
            'bridge_script': 'pepper_nav_script.py',
        }.items()
    )

    actuator = ExecuteProcess(
        cmd=['python3', '/absolute/path/to/actuator.py'],
        output='screen'
    )

    ros_app = ExecuteProcess(
        cmd=['python3', '/absolute/path/to/ros_app.py'],
        output='screen'
    )

    naoqi_bridge = ExecuteProcess(
        cmd=['ros2', 'run', 'naoqi_driver', 'naoqi_driver_node'],
        output='screen'
    )

    return LaunchDescription([
        naoqi_bridge,
        actuator,
        ros_app,
        nav2_launch,
    ])