# Orbbec BSL Alphanumeric Recogniser
This module provides real-time, depth-aware British Sign Language (BSL) recognition using an Orbbec 3D camera. It combines MediaPipe's robust hand-tracking with custom Support Vector Machine (SVM) classifiers to translate spatial coordinates into BSL letters and numbers instantaneously.

It is designed to handle both static alphanumeric characters and dynamic (motion-based) signs using a hybrid machine-learning and heuristic approach.

## Key Technical Features
Depth-Gated Activation: Uses the Orbbec depth sensor to create a physical "active zone" (0.5m - 2.5m). The system only processes RGB frames when a user is within range, saving compute resources and ignoring background interference.

Dual-Brain SVM Architecture: Dynamically switches between two pre-trained .pkl models based on landmark detection:

One-Hand Model: 63-feature input vector for numbers and simple letters.

Two-Hand Model: 126-feature input vector for complex BSL alphabet characters.

## Prerequisites & Installation
###Hardware Requirements
Orbbec 3D Camera (Astra, Femto, or similar series supported by pyorbbecsdk).

###Software Requirements
Python 3.10+

Orbbec SDK is installed and configured on your system.

Python Libraries
Install the required dependencies using pip:
```bash
pip install opencv-python numpy mediapipe scikit-learn
```
