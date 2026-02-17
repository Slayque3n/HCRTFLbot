import cv2
import numpy as np
import mediapipe as mp
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBError

# --- CONFIGURATION ---
MIN_RANGE_MM = 500    # 1.0 meter
MAX_RANGE_MM = 2500    # 2.5 meters
ROI_WIDTH = 150        # Box Width (Total 300px)
ROI_HEIGHT = 100       # Box Height (Total 200px)

# --- MEDIAPIPE SETUP ---
mp_holistic = mp.solutions.holistic
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles

def main():
    pipeline = Pipeline()
    config = Config()

    # Initialize MediaPipe Holistic
    # min_detection_confidence=0.5: Lower value = faster but more jittery
    # min_tracking_confidence=0.5: Higher value = sticks to hands better once found
    with mp_holistic.Holistic(min_detection_confidence=0.5, min_tracking_confidence=0.5) as holistic:
        
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
                # 1. PROCESS DEPTH (THE TRIGGER)
                # ===============================
                depth_frame = frames.get_depth_frame()
                current_status = "WAITING"
                avg_dist = 0
                
                if depth_frame:
                    depth_data = np.frombuffer(depth_frame.get_data(), dtype=np.uint16)
                    depth_data = depth_data.reshape((depth_frame.get_height(), depth_frame.get_width()))

                    h, w = depth_data.shape
                    center_y, center_x = h // 2, w // 2
                    
                    # ROI Logic
                    start_x = max(0, center_x - ROI_WIDTH)
                    end_x = min(w, center_x + ROI_WIDTH)
                    start_y = max(0, center_y - ROI_HEIGHT)
                    end_y = min(h, center_y + ROI_HEIGHT)

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

                    # Visualization (Depth)
                    depth_vis = cv2.convertScaleAbs(depth_data, alpha=0.05)
                    depth_vis = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)

                    if current_status == "TOO_CLOSE":
                        color = (0, 165, 255)
                        text = "TOO CLOSE!"
                    elif current_status == "ACTIVE":
                        color = (0, 255, 0)
                        text = "ACTIVE - TRACKING"
                    else:
                        color = (0, 0, 255)
                        text = "WAITING..."

                    cv2.rectangle(depth_vis, (0,0), (w, 50), color, -1)
                    cv2.putText(depth_vis, f"{text} ({int(avg_dist)}mm)", (20, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,255), 2)
                    cv2.rectangle(depth_vis, (start_x, start_y), (end_x, end_y), color, 2)
                    cv2.imshow("Depth Controller", depth_vis)

                # ===============================
                # 2. PROCESS RGB + AI (CONDITIONAL)
                # ===============================
                if current_status == "ACTIVE":
                    color_frame = frames.get_color_frame()
                    if color_frame:
                        # Decode Image
                        color_raw = np.frombuffer(color_frame.get_data(), dtype=np.uint8)
                        if color_frame.get_format() == OBFormat.MJPG:
                            color_image = cv2.imdecode(color_raw, cv2.IMREAD_COLOR)
                        else:
                            color_image = color_raw.reshape((color_frame.get_height(), color_frame.get_width(), 3))
                            if color_frame.get_format() == OBFormat.RGB:
                                color_image = cv2.cvtColor(color_image, cv2.COLOR_RGB2BGR)

                        if color_image is not None:
                            # --- MEDIAPIPE PROCESSING ---
                            # 1. Convert BGR to RGB (MediaPipe requires RGB)
                            image_rgb = cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB)
                            
                            # 2. Make image unwriteable to improve performance
                            image_rgb.flags.writeable = False
                            
                            # 3. Process the image (Find Hands/Body)
                            results = holistic.process(image_rgb)
                            
                            # 4. Draw Landmarks on the original BGR image
                            # Draw Face Mesh (Contours)
                            mp_drawing.draw_landmarks(
                                color_image,
                                results.face_landmarks,
                                mp_holistic.FACEMESH_CONTOURS,
                                landmark_drawing_spec=None,
                                connection_drawing_spec=mp_drawing_styles.get_default_face_mesh_contours_style())
                            
                            # Draw Pose (Body/Arms)
                            mp_drawing.draw_landmarks(
                                color_image,
                                results.pose_landmarks,
                                mp_holistic.POSE_CONNECTIONS,
                                landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style())
                            
                            # Draw Left Hand
                            mp_drawing.draw_landmarks(
                                color_image,
                                results.left_hand_landmarks,
                                mp_holistic.HAND_CONNECTIONS)
                                
                            # Draw Right Hand
                            mp_drawing.draw_landmarks(
                                color_image,
                                results.right_hand_landmarks,
                                mp_holistic.HAND_CONNECTIONS)

                            # Resize for display
                            display_img = cv2.resize(color_image, (0,0), fx=0.5, fy=0.5)
                            cv2.imshow("BSL Tracker (MediaPipe)", display_img)
                            rgb_window_open = True
                
                else:
                    if rgb_window_open:
                        cv2.destroyWindow("BSL Tracker (MediaPipe)")
                        rgb_window_open = False

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