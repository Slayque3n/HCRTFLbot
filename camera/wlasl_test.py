import cv2
import numpy as np
import mediapipe as mp
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBError

# --- CONFIGURATION ---
MIN_RANGE_MM = 500    
MAX_RANGE_MM = 2500   
ROI_WIDTH = 200        
ROI_HEIGHT = 100       

# --- MEDIAPIPE SETUP ---
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=2,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.5
)
mp_draw = mp.solutions.drawing_utils

def process_asl(frame):
    """
    Processes the frame for hand landmarks. 
    In a full WLASL implementation, you would buffer these landmarks 
    over 30-60 frames and pass them to your trained .h5 or .tflite model.
    """
    f_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = hands.process(f_rgb)
    
    label = "Scanning for Hands..."
    
    if results.multi_hand_landmarks:
        label = "Hand Detected - Ready to Sign"
        for hand_landmarks in results.multi_hand_landmarks:
            mp_draw.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)
            
    return frame, label

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

            # 1. PROCESS DEPTH
            depth_frame = frames.get_depth_frame()
            current_status = "WAITING"
            avg_dist = 0
            
            if depth_frame:
                depth_data = np.frombuffer(depth_frame.get_data(), dtype=np.uint16)
                depth_data = depth_data.reshape((depth_frame.get_height(), depth_frame.get_width()))

                h, w = depth_data.shape
                center_y, center_x = h // 2, w // 2
                
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

                # GUI for Depth
                depth_vis = cv2.convertScaleAbs(depth_data, alpha=0.05)
                depth_vis = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)
                
                color = (0, 0, 255) # Default Red
                if current_status == "TOO_CLOSE": color = (0, 165, 255)
                elif current_status == "ACTIVE": color = (0, 255, 0)

                cv2.rectangle(depth_vis, (start_x, start_y), (end_x, end_y), color, 2)
                cv2.imshow("Depth Controller", depth_vis)

            # 2. PROCESS RGB & ASL (Only when ACTIVE)
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
                        # --- ASL DETECTION TRIGGER ---
                        # This runs only when the user is in the correct depth range
                        processed_img, asl_label = process_asl(color_image)
                        
                        cv2.putText(processed_img, f"ASL: {asl_label}", (10, 30), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                        # Change fx and fy (scale factors) 
                        # 0.5 = 50% size, 1.0 = 100% size, 2.0 = 200% size
                        display_img = cv2.resize(color_image, (0,0), fx=0.5, fy=0.5)
                        cv2.imshow("ASL Detection Stream", display_img)
                        rgb_window_open = True
            
            else:
                if rgb_window_open:
                    cv2.destroyWindow("ASL Detection Stream")
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