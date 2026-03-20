# TFLBot – A Helping Hand in Underground Transport

## Overview
TFLBot is a mobile assistive robot designed to support passengers navigating complex underground transport environments.

It provides:
- Autonomous navigation guidance
- Multimodal interaction (speech, visual feedback, BSL)
- Accessibility-focused assistance

This repository contains **all materials required to understand, reproduce, and extend the project**, ensuring future teams can continue development seamlessly.

---

## Project Aim
The system investigates whether robotic guidance improves:
- Navigation efficiency
- User experience
- Cognitive workload

Two modes were evaluated:
1. Stationary robot (verbal guidance only)
2. Mobile robot (physical + multimodal guidance)

---

## Repository Contents

This repository includes ALL project deliverables:

### Software
- ROS2-based control system
- Speech recognition and NLP integration
- Navigation and path planning algorithms
- Gesture and interaction modules

### Hardware & Schematics

NEEDS DOING
- System architecture design
- Hardware integration (Pepper, Jetson, Raspberry Pi, sensors)
- Wiring/setup details (if applicable)

### Datasheets
- Sensors (RPLIDAR, Femto Bolt camera, microphone array)
- Processing units (Jetson Orin Nano, Raspberry Pi 5)
- Robot platform (Pepper)

### Experimental Results
- Navigation performance metrics
- User study data (time, errors, cognitive load, satisfaction)
- Logs and evaluation outputs

### Supplementary Figures
- System architecture diagrams
- UI screenshots
- Mapping and LiDAR outputs

### Documentation
- Final report (see below)
- Setup instructions
- Design decisions

📄 Full report: :contentReference[oaicite:0]{index=0}

---

## System Architecture

<p align="center">
  <img src="figures/SystemDesign.png" width="600"/>
</p>

<p align="center">
  <em>Figure: High-level system architecture of TFLBot</em>
</p>

The system consists of multiple integrated components:

- **Pepper Robot** (interaction + mobility)
- **Jetson Orin Nano** (central processing)
- **Raspberry Pi 5** (sensor interfacing)
- **Femto Bolt Camera** (vision + tracking)
- **RPLIDAR A2M12** (mapping + collision avoidance)
- **Microphone Array** (speech input)

Key technologies:
- ROS2 Humble
- NAOqi bridge
- Flask-based UI
- Gemini API (NLP)

---

## Features

### 1. Speech Interaction
- Natural language queries
- Google Speech Recognition integration
- Gemini-powered responses :contentReference[oaicite:1]{index=1}

### 2. Sign Language Recognition
- MediaPipe hand tracking
- SVM-based classification
- Supports BSL alphabet and numbers :contentReference[oaicite:2]{index=2}

### 3. Multimodal Communication
- Speech + gesture synchronisation
- Directional pointing
- Visual UI feedback

### 4. Navigation & Mobility
- A* path planning
- LiDAR-based obstacle avoidance
- SLAM mapping

### 5. User Interface
- Language selection
- Real-time transcription
- BSL interaction mode
- Accessibility-first design

---

## Team Contributions (Who Did What)

| Name | Contribution |
|------|-------------|
| Dhruv Varsani | BSL recognition and vision system (camera integration) |
| Sophie Jayson | User interface and speech processing |
| Sandro Enukidze | Microphone system and audio input |
| Arundhathi Pasquereau | 3D modelling and design assets |
| Adeola Olawoye | System integration |
| Tshepo Nkutlwang | Mapping and environment representation |
| Isobel Owens | Collision avoidance system |
| Dinushan Camilus | Lower body movement and locomotion control |
| Akin Falase | Upper body movement |
| Dylan Winters | Upper body movement, gesture control, and team leadership |