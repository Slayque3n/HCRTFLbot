import cv2
import numpy as np
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBError

# 1. Initialize the Pipeline (connection to the camera)
print("begin")
pipeline = Pipeline()
print("1")
config = Config()

try:
    # 2. Configure Color Stream (RGB)
    # 1920x1080 @ 30FPS is a standard safe configuration for Femto Bolt
    profile_list = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
    color_profile = profile_list.get_default_video_stream_profile()
    config.enable_stream(color_profile)

    # 3. Configure Depth Stream
    # 640x576 is the standard NFOV (Narrow Field of View) depth mode
    profile_list = pipeline.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
    depth_profile = profile_list.get_default_video_stream_profile()
    config.enable_stream(depth_profile)

    # 4. Start the camera
    pipeline.start(config)
    print("Camera started. Press 'ESC' to exit.")

    while True:
        # Wait up to 100ms for new frames
        frames = pipeline.wait_for_frames(100)
        if not frames:
            continue

        # Get Color Frame
       # Get Color Frame
        color_frame = frames.get_color_frame()
        if color_frame:
            print("colour")
            # 1. Get the raw byte data
            color_data = np.frombuffer(color_frame.get_data(), dtype=np.uint8)
            
            # 2. Check the format
            if color_frame.get_format() == OBFormat.MJPG:
                # If it's MJPEG, we must DECODE it rather than reshape it
                color_image = cv2.imdecode(color_data, cv2.IMREAD_COLOR)
            else:
                # If it's already Raw RGB/BGR, we can reshape it
                # Note: The Femto Bolt often sends RGB as BGR natively
                color_image = color_data.reshape((color_frame.get_height(), color_frame.get_width(), 3))
                # If the colors look blue/red swapped, uncomment the next line:
                # color_image = cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR)

            if color_image is not None:
                scale_percent = 50 
                width = int(color_image.shape[1] * scale_percent / 100)
                height = int(color_image.shape[0] * scale_percent / 100)
                dim = (width, height)
                
                # Create a smaller version for display
                small_preview = cv2.resize(color_image, dim, interpolation=cv2.INTER_AREA)
                
                cv2.imshow("Color Stream", small_preview)
        # Get Depth Frame
        depth_frame = frames.get_depth_frame()
        print("depth")
        if depth_frame:
            depth_data = np.frombuffer(depth_frame.get_data(), dtype=np.uint16)
            depth_image = depth_data.reshape((depth_frame.get_height(), depth_frame.get_width()))
            
            # Depth data is 16-bit (0-65535). To visualize, we scale it down to 8-bit.
            # This is a simple visualization (multiply by scale factor to make it visible)
            depth_image = cv2.convertScaleAbs(depth_image, alpha=0.05)
            # Apply a colormap for better visibility
            depth_image = cv2.applyColorMap(depth_image, cv2.COLORMAP_JET)
            
            cv2.imshow("Depth Stream", depth_image)

        # Exit on ESC key
        key = cv2.waitKey(1)
        if key == 27:
            break

except OBError as e:
    print(f"Orbbec Error: {e}")
except Exception as e:
    print(f"General Error: {e}")

finally:
    # 5. Clean up
    pipeline.stop()
    cv2.destroyAllWindows()
    print("Camera stopped.")