import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBError

# --- CONFIGURATION ---
MIN_RANGE_MM = 500     # 0.5 meter (Trigger distance)
MAX_RANGE_MM = 2500    # 2.5 meters
ROI_WIDTH = 150        # Box Width
ROI_HEIGHT = 100       # Box Height
# Make sure this path is correct for your machine
MODEL_PATH = 'c:/Users/admin/Documents/uni/HCRTFLbot/camera/gesture_recognizer.task' 

# --- MEDIAPIPE SETUP (VISUALIZATION) ---
mp_holistic = mp.solutions.holistic
mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles

sequence = []        # The "Buffer"
sentence = []        # History of predictions
predictions = []
THRESHOLD = 0.5

def main():
    # 1. SETUP ASL BRAIN (Gesture Recognizer)
    try:
        base_options = python.BaseOptions(model_asset_path=MODEL_PATH)
        options = vision.GestureRecognizerOptions(base_options=base_options)
        recognizer = vision.GestureRecognizer.create_from_options(options)
        print("ASL Brain Loaded Successfully.")
    except Exception as e:
        print(f"Error loading ASL Model: {e}")
        return

    # 2. SETUP CAMERA (Orbbec)
    pipeline = Pipeline()
    config = Config()

    # We use Holistic here strictly for the VISUALS (drawing the lines)
    with mp_holistic.Holistic(min_detection_confidence=0.5, min_tracking_confidence=0.5) as holistic:
        try:
            # Enable Color & Depth Streams
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
                        color = (0, 165, 255) # Orange
                        text = "TOO CLOSE!"
                    elif current_status == "ACTIVE":
                        color = (0, 255, 0)   # Green
                        text = "ACTIVE - SCANNING"
                    else:
                        color = (0, 0, 255)   # Red
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
                            # --- A. PREPARE IMAGE ---
                            color_image = cv2.resize(color_image, (640, 480))
                            image_rgb = cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB)
                            
                            # --- B. RUN HOLISTIC (Visuals) ---
                            # This generates the landmarks for drawing
                            results = holistic.process(image_rgb)
                            
                            # --- DRAWING SECTION (UPDATED) ---
                            
                            # 1. Draw Face Mesh (DETAILED CONTOURS) - This is the new part
                            mp_drawing.draw_landmarks(
                                color_image,
                                results.face_landmarks,
                                mp_holistic.FACEMESH_CONTOURS,
                                landmark_drawing_spec=None,
                                connection_drawing_spec=mp_drawing_styles.get_default_face_mesh_contours_style())

                            # 2. Draw Pose (Body/Arms)
                            mp_drawing.draw_landmarks(
                                color_image, 
                                results.pose_landmarks, 
                                mp_holistic.POSE_CONNECTIONS,
                                landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style())
                            
                            # 3. Draw Hands
                            mp_drawing.draw_landmarks(color_image, results.left_hand_landmarks, mp_holistic.HAND_CONNECTIONS)
                            mp_drawing.draw_landmarks(color_image, results.right_hand_landmarks, mp_holistic.HAND_CONNECTIONS)

                            # --- C. RUN ASL BRAIN (Logic) ---
                            # Convert to MediaPipe Image object
                            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)
                            
                            # Recognize Gesture
                            recognition_result = recognizer.recognize(mp_image)
                            
                            gesture_text = "None"
                            confidence = 0.0

                            if recognition_result.gestures:
                                top_gesture = recognition_result.gestures[0][0]
                                gesture_text = top_gesture.category_name
                                confidence = top_gesture.score

                            # --- D. DISPLAY RESULTS ---
                            # Only show high-confidence gestures
                            if gesture_text != "None" and confidence > 0.3:
                                # ROBOT COMMAND LOGIC
                                cmd_color = (0, 255, 0)
                                if gesture_text == "Closed_Fist":
                                    cv2.putText(color_image, "CMD: STOP", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 4)
                                elif gesture_text == "Thumbs_Up":
                                    cv2.putText(color_image, "CMD: GO / YES", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 4)
                                elif gesture_text == "Victory":
                                    cv2.putText(color_image, "CMD: PEACE", (50, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 0), 4)
                                
                                # Show Gesture Name
                                cv2.putText(color_image, f"Sign: {gesture_text} ({confidence:.2f})", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, cmd_color, 2)
                            
                            # Show final image
                            cv2.imshow("Robot Vision (ASL + Depth)", color_image)
                            rgb_window_open = True
                
                else:
                    # Close the RGB window if we are too far away or waiting
                    if rgb_window_open:
                        cv2.destroyWindow("Robot Vision (ASL + Depth)")
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