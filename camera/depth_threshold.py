import cv2
import numpy as np
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBError

# --- CONFIGURATION ---
MIN_RANGE_MM = 500    # 1.0 meter
MAX_RANGE_MM = 2500    # 2.5 meters

# ROI SETTINGS (Rectangle Shape)
# Width: How far left/right to scan from center
# Height: How far up/down to scan from center
ROI_WIDTH = 200        # Total width will be 400 pixels (200 left + 200 right)
ROI_HEIGHT = 100       # Total height will be 200 pixels (100 up + 100 down)

def main():
    pipeline = Pipeline()
    config = Config()

    try:
        # 1. Configure Streams
        profile_list = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
        color_profile = profile_list.get_default_video_stream_profile()
        config.enable_stream(color_profile)

        profile_list = pipeline.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
        depth_profile = profile_list.get_default_video_stream_profile()
        config.enable_stream(depth_profile)

        pipeline.start(config)
        print(f"System Started. Range: {MIN_RANGE_MM}-{MAX_RANGE_MM}mm")
        
        rgb_window_open = False

        while True:
            frames = pipeline.wait_for_frames(100)
            if not frames:
                continue

            # ===============================
            # 1. PROCESS DEPTH (ALWAYS ON)
            # ===============================
            depth_frame = frames.get_depth_frame()
            current_status = "WAITING"
            avg_dist = 0
            
            if depth_frame:
                depth_data = np.frombuffer(depth_frame.get_data(), dtype=np.uint16)
                depth_data = depth_data.reshape((depth_frame.get_height(), depth_frame.get_width()))

                # --- NEW RECTANGLE LOGIC ---
                h, w = depth_data.shape
                center_y, center_x = h // 2, w // 2
                
                # Define corners using Width and Height separately
                start_x = center_x - ROI_WIDTH
                end_x = center_x + ROI_WIDTH
                start_y = center_y - ROI_HEIGHT
                end_y = center_y + ROI_HEIGHT

                # Safety Check: Ensure box doesn't go off screen
                start_x = max(0, start_x)
                start_y = max(0, start_y)
                end_x = min(w, end_x)
                end_y = min(h, end_y)

                # Extract the rectangular ROI
                roi = depth_data[start_y:end_y, start_x:end_x]
                valid_pixels = roi[roi > 0]

                if len(valid_pixels) > 0:
                    avg_dist = np.mean(valid_pixels)
                    
                    if avg_dist < MIN_RANGE_MM:
                        current_status = "TOO_CLOSE"
                    elif avg_dist < MAX_RANGE_MM:
                        current_status = "ACTIVE"
                    else:
                        current_status = "WAITING"

                # --- DRAW GUI ---
                depth_vis = cv2.convertScaleAbs(depth_data, alpha=0.05)
                depth_vis = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)

                if current_status == "TOO_CLOSE":
                    color = (0, 165, 255) # Orange
                    text = f"TOO CLOSE! ({int(avg_dist)}mm)"
                elif current_status == "ACTIVE":
                    color = (0, 255, 0)   # Green
                    text = f"ACTIVE ({int(avg_dist)}mm)"
                else:
                    color = (0, 0, 255)   # Red
                    text = f"WAITING... ({int(avg_dist)}mm)"

                cv2.rectangle(depth_vis, (0,0), (w, 50), color, -1)
                cv2.putText(depth_vis, text, (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
                
                # Draw the Rectangular Box
                cv2.rectangle(depth_vis, (start_x, start_y), (end_x, end_y), color, 2)

                cv2.imshow("Depth Controller", depth_vis)

            # ===============================
            # 2. PROCESS RGB (CONDITIONAL)
            # ===============================
            if current_status == "ACTIVE":
                color_frame = frames.get_color_frame()
                if color_frame:
                    color_raw = np.frombuffer(color_frame.get_data(), dtype=np.uint8)
                    
                    if color_frame.get_format() == OBFormat.MJPG:
                        color_image = cv2.imdecode(color_raw, cv2.IMREAD_COLOR)
                    else:
                        color_image = color_raw.reshape((color_frame.get_height(), color_frame.get_width(), 3))
                        if color_frame.get_format() == OBFormat.RGB:
                            color_image = cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR)

                    if color_image is not None:
                        display_img = cv2.resize(color_image, (0,0), fx=0.5, fy=0.5)
                        cv2.imshow("RGB Stream", display_img)
                        rgb_window_open = True
            
            else:
                if rgb_window_open:
                    cv2.destroyWindow("RGB Stream")
                    rgb_window_open = False

            # ===============================
            # 3. EXIT
            # ===============================
            key = cv2.waitKey(1)
            if key == 27 or key == ord('q'):
                break

    except OBError as e:
        print(f"Orbbec Error: {e}")
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()