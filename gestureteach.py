import qi
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

class QiPepperGestureGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Pepper Gesture Teacher (qi / Python 3)")

        # qi / services
        self.session = None
        self.motion = None
        self.tts = None
        self.autolife = None
        self.awareness = None

        # UI state
        self.ip_var = tk.StringVar(value="10.123.134.35")
        self.port_var = tk.IntVar(value=9559)

        self.group_var = tk.StringVar(value="Right arm (basic)")
        self.hz_var = tk.DoubleVar(value=25.0)

        self.teach_stiff_var = tk.DoubleVar(value=0.15)
        self.play_speed_var = tk.DoubleVar(value=1.0) 


        self.status_var = tk.StringVar(value="Not connected.")
        self.count_var = tk.StringVar(value="Raw ticks: 0 | Kept points: 0")

        # Recording/playback buffers
        self.is_recording = False
        self.samples = []        
        self._raw_ticks = 0
        self._t0 = None
        self._last_kept_t = None
        self._last_kept_angles = None
        self.is_teaching = False


        self.names = []            # joint list for current group
        self.gesture = None

        self._build_ui()

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = dict(padx=8, pady=6)

        frm_conn = ttk.LabelFrame(self.root, text="Connection (qi)")
        frm_conn.grid(row=0, column=0, sticky="ew", **pad)

        ttk.Label(frm_conn, text="IP").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_conn, textvariable=self.ip_var, width=18).grid(row=0, column=1, sticky="w")

        ttk.Label(frm_conn, text="Port").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(frm_conn, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w")

        ttk.Button(frm_conn, text="Connect", command=self.connect).grid(row=0, column=4, padx=(12, 0))

        frm_rec = ttk.LabelFrame(self.root, text="Record (sample sensors while enabled)")
        frm_rec.grid(row=1, column=0, sticky="ew", **pad)

        ttk.Label(frm_rec, text="Joint group").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            frm_rec,
            textvariable=self.group_var,
            values=[
                "Right arm (basic)",
                "Left arm (basic)",
                "Head (yaw/pitch)",
                "Both arms (basic)",
                "Full Body"
            ],
            state="readonly",
            width=18
        ).grid(row=0, column=1, sticky="w")

        ttk.Label(frm_rec, text="Hz").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(frm_rec, textvariable=self.hz_var, width=8).grid(row=0, column=3, sticky="w")

        ttk.Label(frm_rec, text="Teach stiffness").grid(row=1, column=0, sticky="w")
        ttk.Scale(frm_rec, variable=self.teach_stiff_var, from_=0.0, to=0.4, orient="horizontal", length=220)\
            .grid(row=1, column=1, columnspan=3, sticky="w")


        ttk.Button(frm_rec, text="Start teach", command=self.start_teach).grid(row=3, column=0, pady=(8, 0))
        ttk.Button(frm_rec, text="Capture pose", command=self.capture_pose).grid(row=3, column=1, pady=(8, 0))
        ttk.Button(frm_rec, text="Stop teach", command=self.stop_teach).grid(row=3, column=2, pady=(8, 0))
        ttk.Button(frm_rec, text="Clear", command=self.clear).grid(row=3, column=3, pady=(8, 0))

        ttk.Label(frm_rec, textvariable=self.count_var).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))

        frm_play = ttk.LabelFrame(self.root, text="Playback")
        frm_play.grid(row=2, column=0, sticky="ew", **pad)

        ttk.Label(frm_play, text="Speed").grid(row=0, column=0, sticky="w")
        ttk.Scale(frm_play, variable=self.play_speed_var, from_=0.25, to=2.5, orient="horizontal", length=220)\
            .grid(row=0, column=1, columnspan=2, sticky="w")
        ttk.Label(frm_play, text="(0.5 slow, 2.0 fast)").grid(row=0, column=3, sticky="w")

        ttk.Button(frm_play, text="Playback", command=self.playback).grid(row=1, column=0, pady=(8, 0))
        ttk.Button(frm_play, text="Stop motion", command=self.stop_motion).grid(row=1, column=1, pady=(8, 0))

        frm_io = ttk.LabelFrame(self.root, text="Save / Load")
        frm_io.grid(row=3, column=0, sticky="ew", **pad)
        ttk.Button(frm_io, text="Save JSON…", command=self.save_json).grid(row=0, column=0)
        ttk.Button(frm_io, text="Load JSON…", command=self.load_json).grid(row=0, column=1)

        ttk.Label(self.root, textvariable=self.status_var, anchor="w").grid(row=4, column=0, sticky="ew", **pad)
        self.root.columnconfigure(0, weight=1)

    def _set_status(self, msg):
        self.status_var.set(msg)

    # ---------------- Connection ----------------
    def _force_quiet_mode(self):
        """Hard-disable anything that causes autonomous/pulsing motion."""
        if not self.motion:
            return

        # Stop current motion tasks
        self._safe(self.motion.stopMove)

        # Stop Basic Awareness
        if self.awareness:
            self._safe(self.awareness.stopAwareness)
            # Some builds also expose setEnabled
            self._safe(self.awareness.setEnabled, False)

        # Disable Autonomous Life
        if self.autolife:
            # 'disabled' is strongest; some versions may throw, so keep safe()
            self._safe(self.autolife.setState, "disabled")

        # Kill breathing on all common chains
        self._safe(self.motion.setBreathEnabled, "Body", False)
        self._safe(self.motion.setBreathEnabled, "Arms", False)
        self._safe(self.motion.setBreathEnabled, "Head", False)

    def connect(self):
        ip = self.ip_var.get().strip()
        port = int(self.port_var.get())
        url = f"tcp://{ip}:{port}"

        try:
            self.session = qi.Session()
            self.session.connect(url)
            self.motion = self.session.service("ALMotion")
            self.tts = self.session.service("ALTextToSpeech")
            try: self.autolife = self.session.service("ALAutonomousLife")
            except: self.autolife = None
            
            try: self.awareness = self.session.service("ALBasicAwareness")
            except: self.awareness = None

            self._set_status(f"Connected to {url}")
            self.tts.say("Connected")
            self._force_quiet_mode()
        except Exception as e:
            self.session = None
            self.motion = None
            self.tts = None
            messagebox.showerror("Connect failed", str(e))
            self._set_status("Connection failed.")

    # ---------------- Joint groups ----------------
    def _resolve_joint_names(self):
        g = self.group_var.get()
        if g == "Right arm (basic)":
            return ["RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw","RHand"]
        if g == "Left arm (basic)":
            return ["LShoulderPitch", "LShoulderRoll", "LElbowYaw", "LElbowRoll", "LWristYaw","LHand"]
        if g == "Head (yaw/pitch)":
            return ["HipPitch", "HipRoll", "KneePitch"]
        if g == "Both arms (basic)":
            return [
                "RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw",
                "LShoulderPitch", "LShoulderRoll", "LElbowYaw", "LElbowRoll", "LWristYaw",
                "HipPitch", "HipRoll", "KneePitch"
            ]
        if g == "Full Body":
            return [
                "HeadYaw", "HeadPitch",
                "RShoulderPitch", "RShoulderRoll", "RElbowYaw", "RElbowRoll", "RWristYaw","RHand",
                "LShoulderPitch", "LShoulderRoll", "LElbowYaw", "LElbowRoll", "LWristYaw","LHand",
                "HipPitch", "HipRoll", "KneePitch","WheelFL", "WheelFR", "WheelB"
            ]
        return []
    
    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except:
            return None

    def _enter_freedrive(self, names):
        # Stop anything that might keep commanding joints
        self._safe(self.motion.stopMove)

        # Kill autonomous behaviors that "fight" you
        if self.awareness:
            self._safe(self.awareness.stopAwareness)
        if self.autolife:
            self._safe(self.autolife.setState, "disabled")

        # Disable breathing (common "pulsing" source)
        self._safe(self.motion.setBreathEnabled, "Body", False)
        self._safe(self.motion.setBreathEnabled, "Arms", False)
        self._safe(self.motion.setBreathEnabled, "Head", False)

        # Disable collision protection while teaching (can feel like resistance)
        self._safe(self.motion.setExternalCollisionProtectionEnabled, "All", False)

        # Disable smart stiffness if supported (this is big on some builds)
        self._safe(self.motion.setSmartStiffnessEnabled, False)

        # Now actually go limp (try whole body first)
        ok = self._safe(self.motion.setStiffnesses, "Body", 0.0)
        if ok is None:
            self._safe(self.motion.setStiffnesses, names, 0.0)

        # Repeat once after a short delay (some controllers “reassert” briefly)
        time.sleep(0.2)
        if ok is None:
            self._safe(self.motion.setStiffnesses, names, 0.0)
        else:
            self._safe(self.motion.setStiffnesses, "Body", 0.0)

    def _exit_freedrive(self, names):
        # Re-enable safety-ish defaults
        self._safe(self.motion.setExternalCollisionProtectionEnabled, "All", True)
        self._safe(self.motion.setSmartStiffnessEnabled, True)

        # Optional: bring Autonomous Life back
        
    def _stop_autonomous_pulsing(self):
    # Stops the common sources of "pulsing"/micro-motion.
        if not self.motion:
            return

        # 1) Basic Awareness can move head/upper body
        try:
            if self.awareness:
                self.awareness.stopAwareness()
        except:
            pass

        # 2) Autonomous Life can restart behaviors that re-stiffen joints
        try:
            if self.autolife:
                # "disabled" is the strongest; "solitary" still moves sometimes.
                self.autolife.setState("disabled")
        except:
            pass

        # 3) "Breathing" animation causes periodic motor commands
        try:
            self.motion.setBreathEnabled("Body", False)
        except:
            pass
        try:
            self.motion.setBreathEnabled("Arms", False)
        except:
            pass
        try:
            self.motion.setBreathEnabled("Head", False)
        except:
            pass

        # 4) Stop anything currently interpolating angles
        try:
            self.motion.stopMove()
        except:
            pass

    # ---------------- Record ----------------
    def start_record(self):
        if not self.motion:
            messagebox.showwarning("Not connected", "Connect to Pepper first.")
            return
        if self.is_recording:
            return

        hz = float(self.hz_var.get())
        if hz <= 0:
            messagebox.showerror("Invalid Hz", "Hz must be > 0.")
            return

        self.names = self._resolve_joint_names()
        if not self.names:
            messagebox.showerror("No joints", "No joints selected.")
            return

        self.clear(keep_status=True)

        try:

            self.motion.setStiffnesses(self.names, 0.0)
        except Exception as e:
            messagebox.showerror("Stiffness failed", str(e))
            return

        self.is_recording = True
        self._t0 = time.time()
        self._set_status("Recording… move the joints gently, then press Stop record.")
        if self.tts:
            self.tts.say("Recording")

        threading.Thread(target=self._record_loop, daemon=True).start()

    def _record_loop(self):
        hz = float(self.hz_var.get())
        dt = 1.0 / hz
        angle_eps = float(self.angle_eps_var.get())
        max_gap = float(self.max_gap_var.get())

        while self.is_recording:
            t = time.time() - self._t0
            try:
                angles = self.motion.getAngles(self.names, True)  # useSensors=True
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Sampling failed", str(e)))
                self.is_recording = False
                break

            self._raw_ticks += 1

            keep = False
            if self._last_kept_angles is None:
                keep = True
            else:
                md = max(abs(a - b) for a, b in zip(angles, self._last_kept_angles))
                if md >= angle_eps or (t - self._last_kept_t) >= max_gap:
                    keep = True

            if keep:
                self.samples.append((t, list(angles)))
                self._last_kept_t = t
                self._last_kept_angles = list(angles)

            self.root.after(0, self._update_counts)
            time.sleep(dt)

        # Finalize gesture
        if self.samples:
            self.gesture = {
                "names": self.names,
                "hz": float(self.hz_var.get()),
                "samples": self.samples,
            }
            self.root.after(
                0,
                lambda: self._set_status(f"Recording stopped. Raw ticks={self._raw_ticks}, kept points={len(self.samples)}")
            )
        else:
            self.root.after(0, lambda: self._set_status("Recording stopped. No points captured."))

    def stop_record(self):
        self.is_recording = False
        if self.tts:
            try: self.tts.say("Stopped")
            except: pass

    def _update_counts(self):
        self.count_var.set(f"Keyframes: {len(self.samples)}")
    def clear(self, keep_status=False):
        self.is_recording = False
        self.samples = []
        self.gesture = None
        self._raw_ticks = 0
        self._t0 = None
        self._last_kept_t = None
        self._last_kept_angles = None
        self._update_counts()
        if not keep_status:
            self._set_status("Cleared.")

    # ---------------- Teach + Keyframes (limp / no resistance) ----------------
    def _safe_call(self, fn, *args, **kwargs):
        """Call a qi service method without crashing if not supported."""
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    def _set_collision_protection(self, enabled: bool):
        """
        Pepper/NAO often support: setExternalCollisionProtectionEnabled(chain, bool)
        chain can be 'All', 'Arms', etc. We'll try 'All' first.
        """
        if not self.motion:
            return
        # Try a couple common chain names; ignore failures.
        self._safe_call(self.motion.setExternalCollisionProtectionEnabled, "All", enabled)
        self._safe_call(self.motion.setExternalCollisionProtectionEnabled, "Arms", enabled)

    def _go_limp(self, names):
        if not self.motion:
            return

        self._safe_call(self.motion.stopMove)
        self._set_collision_protection(False)

        # REMOVE wakeUp here — it can re-assert control/stiffness
        # self._safe_call(self.motion.wakeUp)

        # Just set limp
        if self._safe_call(self.motion.setStiffnesses, "Body", 0.0) is None:
            self._safe_call(self.motion.setStiffnesses, names, 0.0)

        time.sleep(0.15)

        # Re-apply limp once (some controllers reassert)
        if self._safe_call(self.motion.setStiffnesses, "Body", 0.0) is None:
            self._safe_call(self.motion.setStiffnesses, names, 0.0)

    def start_teach(self):
        if not self.motion:
            messagebox.showwarning("Not connected", "Connect to Pepper first.")
            return
        if self.is_recording:
            messagebox.showwarning("Busy", "Stop recording first.")
            return
        if self.is_teaching:
            return

        self.names = self._resolve_joint_names()
        if not self.names:
            messagebox.showerror("No joints", "No joints selected.")
            return

        # Don’t wipe out an already-recorded gesture unless you want to
        # If you want a fresh gesture each time, keep the next line:
        # self.clear(keep_status=True)

        self.is_teaching = True
        self._t0 = time.time()
        self._force_quiet_mode()
        self._enter_freedrive(self.names)

        #self._stop_autonomous_pulsing()

        # Go limp with collision protection off (least resistance)
        #self._go_limp(self.names)

        self._set_status("Teach mode ON (collision protection OFF). Pose the robot, press 'Capture pose'.")
        #if self.tts:
        #    self._safe_call(self.tts.say, "Teach mode")

    def stop_teach(self):
        self.is_teaching = False

        if not self.motion:
            self._set_status("Teach mode OFF.")
            return

        # Restore safety defaults first
        self._safe(self.motion.setExternalCollisionProtectionEnabled, "All", True)
        self._safe(self.motion.setSmartStiffnessEnabled, True)

        # Optionally bring Autonomous Life back # or "interactive"

        # Wake up so robot is ready for playback / holds its posture
        self._force_quiet_mode()
        self._safe(self.motion.wakeUp)

        # Optional: give the taught joints a small holding stiffness
        # Use 0.1–0.3 if you want it to not flop. Use 0.0 if you want limp after teaching.
        hold = 0.2
        if self.names:
            self._safe(self.motion.setStiffnesses, self.names, hold)

        self._set_status("Teach mode OFF (collision protection ON, awake).")
        if self.tts:
            self._safe_call(self.tts.say, "Stopped")

    def capture_pose(self):
        if not self.motion:
            messagebox.showwarning("Not connected", "Connect to Pepper first.")
            return
        if not self.is_teaching:
            messagebox.showwarning("Teach mode", "Press 'Start teach' first.")
            return
        if not self.names:
            messagebox.showerror("No joints", "No joints selected.")
            return

        try:
            t = time.time() - (self._t0 if self._t0 else time.time())
            angs = self.motion.getAngles(self.names, True)  # sensors
            pos = self.motion.getRobotPosition(True) #returns x,y,z, z is angle in rads
        except Exception as e:
            messagebox.showerror("Capture failed", str(e))
            return

        # Optional: don’t store duplicates (simple max-delta threshold)
        angle_eps = 0.03
        if self.samples:
            last = self.samples[-1][1]
            md = max(abs(a - b) for a, b in zip(angs, last))
            if md < angle_eps:
                self._set_status("Pose too similar to last keyframe (ignored).")
                return

        # Store tuple: (time, [joint_angles], [x,y,theta])
        self.samples.append((t, list(angs), list(pos)))        

        self.samples.append((t, list(angs)))
        self.gesture = {"names": self.names, "hz": None, "samples": self.samples}

        self._raw_ticks += 1
        self._update_counts()
        self._set_status(f"Captured keyframe #{len(self.samples)}")
        if self.tts:
            self._safe_call(self.tts.say, "Captured")

    
    # ---------------- Playback ----------------
    def playback(self):
        if not self.motion:
            messagebox.showwarning("Not connected", "Connect to Pepper first.")
            return
        if self.is_recording:
            messagebox.showwarning("Recording", "Stop recording first.")
            return
        if not self.gesture or not self.gesture.get("samples"):
            messagebox.showwarning("No gesture", "Record or load a gesture first.")
            return

        threading.Thread(target=self._playback_worker, daemon=True).start()

    def _playback_worker(self):
        
        #self._force_quiet_mode()
        #self._safe(self.motion.wakeUp)
        time.sleep(0.2)
    
        #self._safe(self.awareness.stopAwareness)

        #self._safe(self.autolife.setState, "disabled")

        time.sleep(0.2)
        names = self.gesture["names"]
        samples = self.gesture["samples"]
        speed = float(self.play_speed_var.get())
        if speed <= 0:
            self.root.after(0, lambda: messagebox.showerror("Playback", "Speed must be > 0"))
            return

        try:
            self.motion.setStiffnesses(names, 1.0)
        except:
            pass

        self.root.after(0, lambda: self._set_status("Playing back…"))
        if self.tts:
            try: self.tts.say("Playing back")
            except: pass

        # Build angleInterpolation inputs:
        # - angleLists: list per joint, each list is angles over time
        # - timeLists:  list per joint, each list is times over time
        t0 = samples[0][0]
        eps = 2  # 50 ms safety margin (NAOqi-friendly)
        min_step = 0.55  # seconds between keyframes AFTER speed scaling

        raw_times = [(t - t0) + eps for (t, _) in samples]
        # Enforce strictly increasing times with a minimum step
        times = [raw_times[0]]
        for i in range(1, len(raw_times)):
            times.append(min(raw_times[i], times[-1] + min_step))
        angleLists = [[] for _ in names]
        for (_, angs) in samples:
            for j, a in enumerate(angs):
                angleLists[j].append(a)

        timeLists = [times[:] for _ in names]  # same timestamps for every joint

        try:
            # isAbsolute=True means times are absolute from start (not deltas)
            self.motion.angleInterpolation(names, angleLists, timeLists, True)
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Playback failed", str(e)))
            self.root.after(0, lambda: self._set_status("Playback failed."))
            return

        self.root.after(0, lambda: self._set_status("Playback finished."))

    def stop_motion(self):
        if not self.motion:
            return
        try:
            self.motion.setStiffnesses(self.gesture["names"] if self.gesture else self.names, 0.2)
            self._set_status("Stop requested (lowered stiffness).")
        except Exception as e:
            messagebox.showerror("Stop motion failed", str(e))

    # ---------------- Save / Load ----------------
    def save_json(self):
        if not self.gesture:
            messagebox.showwarning("Save", "Nothing to save.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            title="Save gesture JSON"
        )
        if not path:
            return
        try:
            with open(path, "w") as f:
                json.dump(self.gesture, f)
            self._set_status(f"Saved {path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def load_json(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")],
            title="Load gesture JSON"
        )
        if not path:
            return
        try:
            with open(path, "r") as f:
                self.gesture = json.load(f)
            self.samples = self.gesture.get("samples", [])
            self._raw_ticks = len(self.samples)
            self._update_counts()
            self._set_status(f"Loaded {path}")
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

def main():
    root = tk.Tk()

    app = QiPepperGestureGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
