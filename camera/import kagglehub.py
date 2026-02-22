import cv2
import numpy as np
import mediapipe as mp
import pickle
import sys
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBError

# --- CONFIGURATION ---
MIN_RANGE_MM = 500   # 0.5 meters
MAX_RANGE_MM = 2500  # 2.5 meters
ROI_WIDTH = 200      # Detection Box Width
ROI_HEIGHT = 150     # Detection Box Height

# --- PATHS TO YOUR BRAINS ---
# Double check these paths match exactly where your files are
PATH_1_HAND = r'C:\Users\admin\Documents\uni\HCRTFLbot\camera\one_hand_model.pkl'
PATH_2_HAND = r'C:\Users\admin\Documents\uni\HCRTFLbot\camera\two_hand_model.pkl'

# --- LOAD MODELS ---
print("Loading BSL Brains...")
try:
    with open(PATH_1_HAND, 'rb') as f:
        model_1h = pickle.load(f)
    print(" - One-Hand Model: LOADED")
    
    with open(PATH_2_HAND, 'rb') as f:
        model_2h = pickle.load(f)
    print(" - Two-Hand Model: LOADED")
except Exception as e:
    print(f"CRITICAL ERROR loading models: {e}")
    sys.exit(1)

# --- MEDIAPIPE SETUP ---
mp_holistic = mp.solutions.holistic
mp_drawing = mp.solutions.drawing_utils

def get_hand_features(landmarks):
    """
    Extracts 63 features (x, y, z) for 21 points.
    FIX: Sends RAW coordinates (0.0 - 1.0) instead of relative ones.
    """
    if not landmarks:
        return [0.0] * 63

    features = []
    for lm in landmarks.landmark:
        # DO NOT subtract base_x/y/z. Send the raw coordinate.
        features.append(lm.x)
        features.append(lm.y)
        features.append(lm.z)
        
    return features

def main():
    pipeline = Pipeline()
    config = Config()

    try:
        # 1. Start Camera Streams
        print("Starting Orbbec Camera...")
        profile_list = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
        config.enable_stream(profile_list.get_default_video_stream_profile())
        
        profile_list = pipeline.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
        config.enable_stream(profile_list.get_default_video_stream_profile())
        
        pipeline.start(config)

        # 2. Start AI Engine
        with mp_holistic.Holistic(min_detection_confidence=0.5, min_tracking_confidence=0.5) as holistic:
            
            while True:
                frames = pipeline.wait_for_frames(100)
                if not frames: continue

                # --- DEPTH LOGIC (The "Trigger") ---
                depth_frame = frames.get_depth_frame()
                current_status = "WAITING"
                
                if depth_frame:
                    # Quick math to check if you are in range
                    depth_data = np.frombuffer(depth_frame.get_data(), dtype=np.uint16)
                    depth_data = depth_data.reshape((depth_frame.get_height(), depth_frame.get_width()))
                    
                    # Extract center box
                    h, w = depth_data.shape
                    cy, cx = h // 2, w // 2
                    roi = depth_data[cy-ROI_HEIGHT:cy+ROI_HEIGHT, cx-ROI_WIDTH:cx+ROI_WIDTH]
                    
                    # Filter out 0 (invalid) pixels
                    valid_depth = roi[roi > 0]
                    if len(valid_depth) > 0:
                        dist = np.mean(valid_depth)
                        if MIN_RANGE_MM < dist < MAX_RANGE_MM:
                            current_status = "ACTIVE"
                        elif dist < MIN_RANGE_MM:
                            current_status = "TOO CLOSE"

                # --- RGB & AI LOGIC ---
                if current_status == "ACTIVE":
                    color_frame = frames.get_color_frame()
                    if color_frame:
                        # Decode Image
                        data = np.frombuffer(color_frame.get_data(), dtype=np.uint8)
                        # Handle different formats (MJPG vs RGB)
                        if color_frame.get_format() == OBFormat.MJPG:
                            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
                        else:
                            img = data.reshape((color_frame.get_height(), color_frame.get_width(), 3))
                            if color_frame.get_format() == OBFormat.RGB:
                                img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

                        # Process with MediaPipe
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        results = holistic.process(img_rgb)

                        # Logic Switcher: 1 Hand vs 2 Hands
                        pred_text = "Ready..."
                        
                        # Case A: Two Hands Detected (Use 126-feature model)
                        if results.left_hand_landmarks and results.right_hand_landmarks:
                            # 1. Draw both
                            mp_drawing.draw_landmarks(img, results.left_hand_landmarks, mp_holistic.HAND_CONNECTIONS)
                            mp_drawing.draw_landmarks(img, results.right_hand_landmarks, mp_holistic.HAND_CONNECTIONS)
                            
                            # 2. Combine features (Left + Right = 126 features)
                            feat_l = get_hand_features(results.left_hand_landmarks)
                            feat_r = get_hand_features(results.right_hand_landmarks)
                            full_features = feat_l + feat_r
                            
                            # 3. Predict
                            pred = model_2h.predict([np.asarray(full_features)])
                            pred_text = f"BSL (2-Hand): {pred[0]}"

                        # Case B: Only One Hand Detected (Use 63-feature model)
                        elif results.left_hand_landmarks or results.right_hand_landmarks:
                            # Pick whichever hand is visible
                            hand_lms = results.left_hand_landmarks if results.left_hand_landmarks else results.right_hand_landmarks
                            
                            mp_drawing.draw_landmarks(img, hand_lms, mp_holistic.HAND_CONNECTIONS)
                            
                            # Extract 63 features
                            features = get_hand_features(hand_lms)
                            
                            # Predict
                            pred = model_1h.predict([np.asarray(features)])
                            pred_text = f"BSL (1-Hand): {pred[0]}"

                        # Display Result
                        cv2.rectangle(img, (0,0), (640, 60), (0,0,0), -1)
                        color = (0, 255, 0) if "BSL" in pred_text else (0, 255, 255)
                        cv2.putText(img, pred_text, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1, color, 2)
                        #display_img = cv2.resize(img (0,0), fx=0.5, fy=0.5)
                        cv2.imshow("Orbbec BSL System", img)

                else:
                    # Simple status window if not active
                    blank = np.zeros((400, 600, 3), dtype=np.uint8)
                    cv2.putText(blank, f"Status: {current_status}", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
                    cv2.imshow("Orbbec BSL System", blank)

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