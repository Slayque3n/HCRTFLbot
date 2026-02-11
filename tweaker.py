import qi
import time
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class PepperKeyframeEditorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Pepper Keyframe Editor (qi / Python 3)")

        # qi/services
        self.session = None
        self.motion = None
        self.tts = None
        self.autolife = None
        self.awareness = None

        # data
        self.gesture = None        # dict with names + samples
        self.names = []            # joint names
        self.samples = []          # list of [t, angles] (angles list)
        self.frame_idx = 0

        # ui vars
        self.ip_var = tk.StringVar(value="10.219.0.35")
        self.port_var = tk.IntVar(value=9559)
        self.status_var = tk.StringVar(value="Not connected.")
        self.file_var = tk.StringVar(value="(no file loaded)")
        self.frame_var = tk.StringVar(value="Frame: - / -")
        self.time_var = tk.StringVar(value="t = -")

        self.lead_in_var = tk.DoubleVar(value=1.0)        # seconds to reach a target
        self.hold_stiff_var = tk.DoubleVar(value=1.0)     # stiffness during editing (for stability)
        self.live_preview_var = tk.BooleanVar(value=False)

        
        # slider widgets/vars per joint
        self.joint_vars = {}       # name -> DoubleVar
        self.joint_sliders = {}    # name -> Scale
        self.joint_labels = {}     # name -> Label for value
        
        self.selected_joint_idx = None
        self.joint_value_var = tk.StringVar(value="0.000")  # string to allow typing
        self.step_var = tk.DoubleVar(value=0.1)


        self._building_sliders = False  # prevent callback spam when programmatically setting vars
        self._awake_once = False
        self._build_ui()
    def _populate_joint_list(self):
        self.joint_list.delete(0, tk.END)
        for j in self.names:
            self.joint_list.insert(tk.END, j)
        if self.names:
            self.joint_list.selection_set(0)
            self._on_joint_select()

    def _on_joint_select(self, event=None):
        if not self.samples or not self.names:
            return
        sel = self.joint_list.curselection()
        if not sel:
            return
        self.selected_joint_idx = sel[0]
        name = self.names[self.selected_joint_idx]
        val = float(self.samples[self.frame_idx][1][self.selected_joint_idx])
        self.sel_joint_label.configure(text=name)
        self.joint_value_var.set(f"{val:.3f}")

    def _bump_joint(self, direction):
        if self.selected_joint_idx is None or not self.samples:
            return
        step = float(self.step_var.get())
        try:
            cur = float(self.joint_value_var.get())
        except ValueError:
            cur = float(self.samples[self.frame_idx][1][self.selected_joint_idx])

        new_val = cur + (direction * step)
        self.joint_value_var.set(f"{new_val:.3f}")

        # write into the current frame immediately
        self.samples[self.frame_idx][1][self.selected_joint_idx] = float(new_val)

        if self.live_preview_var.get():
            self.send_pose_preview()

    def _apply_typed_value(self):
        if self.selected_joint_idx is None or not self.samples:
            return
        try:
            val = float(self.joint_value_var.get())
        except ValueError:
            messagebox.showerror("Invalid number", "Angle must be a number (e.g. 0.123).")
            return
        self.samples[self.frame_idx][1][self.selected_joint_idx] = float(val)
        if self.live_preview_var.get():
            self.send_pose_preview()

    # -------------------- UI --------------------
    def _build_ui(self):
        # Make it bigger + readable
        self.root.geometry("1200x780")
        self.root.option_add("*Font", ("Segoe UI", 12))

        pad = dict(padx=10, pady=8)

        # ---------------- Connection ----------------
        frm_conn = ttk.LabelFrame(self.root, text="Connection")
        frm_conn.grid(row=0, column=0, sticky="ew", **pad)
        frm_conn.columnconfigure(5, weight=1)

        ttk.Label(frm_conn, text="IP").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_conn, textvariable=self.ip_var, width=18).grid(row=0, column=1, sticky="w", padx=(6, 16))

        ttk.Label(frm_conn, text="Port").grid(row=0, column=2, sticky="w")
        ttk.Entry(frm_conn, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w", padx=(6, 16))

        ttk.Button(frm_conn, text="Connect", command=self.connect).grid(row=0, column=4, sticky="w")

        # ---------------- File ----------------
        frm_file = ttk.LabelFrame(self.root, text="Gesture file")
        frm_file.grid(row=1, column=0, sticky="ew", **pad)
        frm_file.columnconfigure(2, weight=1)

        ttk.Button(frm_file, text="Load JSON…", command=self.load_json).grid(row=0, column=0, sticky="w")
        ttk.Button(frm_file, text="Save JSON…", command=self.save_json).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Label(frm_file, textvariable=self.file_var, anchor="w").grid(row=0, column=2, sticky="ew", padx=(16, 0))

        # ---------------- Nav / Move ----------------
        frm_nav = ttk.LabelFrame(self.root, text="Navigate / Move robot to keyframe")
        frm_nav.grid(row=2, column=0, sticky="ew", **pad)
        frm_nav.columnconfigure(10, weight=1)

        ttk.Button(frm_nav, text="<< Prev", command=self.prev_frame).grid(row=0, column=0, sticky="w")
        ttk.Button(frm_nav, text="Next >>", command=self.next_frame).grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(frm_nav, text="Go to frame (move & pause)", command=self.go_to_frame)\
            .grid(row=0, column=2, sticky="w", padx=(18, 0))

        ttk.Label(frm_nav, textvariable=self.frame_var).grid(row=0, column=3, sticky="w", padx=(18, 0))
        ttk.Label(frm_nav, textvariable=self.time_var).grid(row=0, column=4, sticky="w", padx=(12, 0))

        ttk.Label(frm_nav, text="Lead-in (s)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(frm_nav, textvariable=self.lead_in_var, width=10).grid(row=1, column=1, sticky="w", pady=(10, 0))

        ttk.Label(frm_nav, text="Edit stiffness").grid(row=1, column=2, sticky="w", padx=(18, 0), pady=(10, 0))
        ttk.Entry(frm_nav, textvariable=self.hold_stiff_var, width=10).grid(row=1, column=3, sticky="w", pady=(10, 0))

        ttk.Checkbutton(frm_nav, text="Live preview while nudging", variable=self.live_preview_var)\
            .grid(row=1, column=4, sticky="w", padx=(18, 0), pady=(10, 0))

        # ---------------- Editor ----------------
        frm_edit = ttk.LabelFrame(self.root, text="Edit current frame")
        frm_edit.grid(row=3, column=0, sticky="nsew", **pad)
        frm_edit.columnconfigure(0, weight=1)
        frm_edit.columnconfigure(1, weight=2)
        frm_edit.rowconfigure(0, weight=1)

        # Left: joint list
        left = ttk.Frame(frm_edit)
        left.grid(row=0, column=0, sticky="nsew", padx=(6, 12), pady=8)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Joints").grid(row=0, column=0, sticky="w")

        self.joint_list = tk.Listbox(left, height=16, exportselection=False)
        self.joint_list.grid(row=1, column=0, sticky="nsew")

        # scroll for joint list
        joint_scroll = ttk.Scrollbar(left, orient="vertical", command=self.joint_list.yview)
        joint_scroll.grid(row=1, column=1, sticky="ns")
        self.joint_list.configure(yscrollcommand=joint_scroll.set)

        self.joint_list.bind("<<ListboxSelect>>", self._on_joint_select)

        # Right: numeric editor
        right = ttk.Frame(frm_edit)
        right.grid(row=0, column=1, sticky="nw", padx=(12, 6), pady=8)

        ttk.Label(right, text="Selected joint", font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w")
        self.sel_joint_label = ttk.Label(right, text="(none)", font=("Segoe UI", 16, "bold"))
        self.sel_joint_label.grid(row=1, column=0, sticky="w", pady=(0, 14))

        ttk.Label(right, text="Angle (rad)").grid(row=2, column=0, sticky="w")

        row = ttk.Frame(right)
        row.grid(row=3, column=0, sticky="w", pady=(6, 0))

        ttk.Button(row, text="−", width=4, command=lambda: self._bump_joint(-1)).grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(row, textvariable=self.joint_value_var, width=12).grid(row=0, column=1)
        ttk.Button(row, text="+", width=4, command=lambda: self._bump_joint(+1)).grid(row=0, column=2, padx=(10, 0))

        step_row = ttk.Frame(right)
        step_row.grid(row=4, column=0, sticky="w", pady=(16, 0))
        ttk.Label(step_row, text="Step (rad)").grid(row=0, column=0, sticky="w")
        ttk.Entry(step_row, textvariable=self.step_var, width=10).grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Button(right, text="Apply typed value to joint", command=self._apply_typed_value)\
            .grid(row=5, column=0, sticky="w", pady=(18, 0))

        ttk.Label(
            right,
            text="Tip: Select a joint, then use +/-.\n(You can also type a number and Apply.)",
            justify="left"
        ).grid(row=6, column=0, sticky="w", pady=(16, 0))

        # ---------------- Actions ----------------
        frm_actions = ttk.Frame(self.root)
        frm_actions.grid(row=4, column=0, sticky="ew", **pad)

        ttk.Button(frm_actions, text="Apply (frame already updated in memory)", command=self.apply_to_frame)\
            .grid(row=0, column=0, sticky="w")
        ttk.Button(frm_actions, text="Send current frame pose", command=self.send_pose_preview)\
            .grid(row=0, column=1, sticky="w", padx=(10, 0))

        # ---------------- Status ----------------
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").grid(row=5, column=0, sticky="ew", **pad)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)


    def _set_status(self, msg: str):
        self.status_var.set(msg)

    # -------------------- helpers --------------------
    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception:
            return None

    def _force_quiet_mode(self):
        """Disable background motion: awareness, autonomous life, breathing."""
        if not self.motion:
            return
        self._safe(self.motion.stopMove)

        if self.awareness:
            self._safe(self.awareness.stopAwareness)
            self._safe(self.awareness.setEnabled, False)

        if self.autolife:
            self._safe(self.autolife.setState, "disabled")

        self._safe(self.motion.setBreathEnabled, "Body", False)
        self._safe(self.motion.setBreathEnabled, "Arms", False)
        self._safe(self.motion.setBreathEnabled, "Head", False)

    def _ensure_awake_ready(self, names):
        if not self.motion:
            return
        # Do NOT call _force_quiet_mode() here anymore.
        # Just ensure desired stiffness for editing/holding.
        stiff = float(self.hold_stiff_var.get())
        stiff = max(0.0, min(1.0, stiff))
        self._safe(self.motion.setStiffnesses, names, stiff)

    def _current_angles(self, names):
        if not self.motion:
            return None
        return self._safe(self.motion.getAngles, names, True)

    # -------------------- Connection --------------------
    def connect(self):
        ip = self.ip_var.get().strip()
        port = int(self.port_var.get())
        url = f"tcp://{ip}:{port}"

        try:
            self.session = qi.Session()
            self.session.connect(url)
            self.motion = self.session.service("ALMotion")
            self.tts = self.session.service("ALTextToSpeech")

            # Optional services (nice to have)
            try:
                self.autolife = self.session.service("ALAutonomousLife")
            except Exception:
                self.autolife = None
            try:
                self.awareness = self.session.service("ALBasicAwareness")
            except Exception:
                self.awareness = None

            self._force_quiet_mode()
            self._set_status(f"Connected to {url} (quiet mode ON)")
            try:
                self.tts.say("Connected")
            except Exception:
                pass
        except Exception as e:
            self.session = None
            self.motion = None
            self.tts = None
            messagebox.showerror("Connect failed", str(e))
            self._set_status("Connection failed.")

    # -------------------- Load / Save --------------------
    def load_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], title="Load gesture JSON")
        if not path:
            return
        try:
            with open(path, "r") as f:
                g = json.load(f)

            if "names" not in g or "samples" not in g:
                raise ValueError("JSON must contain keys: 'names' and 'samples'.")

            self.gesture = g
            self.names = list(g["names"])
            self.samples = list(g["samples"])

            if not self.samples:
                raise ValueError("Gesture has no samples.")

            # ensure samples are list-of-[t, angles]
            for i, s in enumerate(self.samples):
                if not (isinstance(s, list) and len(s) == 2):
                    raise ValueError(f"Bad sample format at index {i}. Expected [t, angles].")
                if len(s[1]) != len(self.names):
                    raise ValueError(f"Angles length mismatch at sample {i}: got {len(s[1])}, expected {len(self.names)}")

            self.frame_idx = 0
            self.file_var.set(path)
            self._update_frame_labels()
            self._populate_joint_list()
            # Wake up ONCE after loading (so playback/editing works without re-waking)
            if self.motion and (not self._awake_once):
                self._force_quiet_mode()
                self._safe(self.motion.wakeUp)
                time.sleep(0.2)
                self._awake_once = True

            self._set_status("Loaded gesture. Use 'Go to frame' to move Pepper to a keyframe.")
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    def save_json(self):
        if not self.gesture:
            messagebox.showwarning("Save", "No gesture loaded.")
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            title="Save edited gesture JSON"
        )
        if not path:
            return

        try:
            # write back edited samples
            self.gesture["names"] = self.names
            self.gesture["samples"] = self.samples
            self.gesture["hz"] = None  # keyframe mode

            with open(path, "w") as f:
                json.dump(self.gesture, f)
            self._set_status(f"Saved {path}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    # -------------------- Frame navigation --------------------
    def _update_frame_labels(self):
        if not self.samples:
            self.frame_var.set("Frame: - / -")
            self.time_var.set("t = -")
            return
        self.frame_var.set(f"Frame: {self.frame_idx + 1} / {len(self.samples)}")
        t = self.samples[self.frame_idx][0]
        self.time_var.set(f"t = {t:.3f}s")

    def prev_frame(self):
        if not self.samples:
            return
        self.frame_idx = max(0, self.frame_idx - 1)
        self._update_frame_labels()
        self._on_joint_select()
    
    def next_frame(self):
        if not self.samples:
            return
        self.frame_idx = min(len(self.samples) - 1, self.frame_idx + 1)
        self._update_frame_labels()
        self._on_joint_select()

    # -------------------- Slider building / syncing --------------------

    # -------------------- Robot motion --------------------

    def send_pose_preview(self):
        if not self.motion:
            messagebox.showwarning("Not connected", "Connect to Pepper first.")
            return
        if not self.names or not self.samples:
            messagebox.showwarning("No gesture", "Load a gesture first.")
            return

        # Don't reset state. Only set stiffness if desired.
        self._ensure_awake_ready(self.names)

        angs = self.samples[self.frame_idx][1]
        self._safe(self.motion.setAngles, self.names, angs, 0.10)
        self._set_status("Sent current frame pose.")

    def go_to_frame(self):
        """Move Pepper to current keyframe and pause there."""
        if not self.motion:
            messagebox.showwarning("Not connected", "Connect to Pepper first.")
            return
        if not self.samples:
            messagebox.showwarning("No gesture", "Load a gesture first.")
            return

        idx = self.frame_idx
        target = self.samples[idx][1]
        lead_in = float(self.lead_in_var.get())
        lead_in = max(0.2, lead_in)

        def worker():
            self._ensure_awake_ready(self.names)

            # start from current pose, to avoid "first keyframe in 0.05s" spike
            curr = self._current_angles(self.names)
            if curr is None:
                self.root.after(0, lambda: messagebox.showerror("Move failed", "Could not read current angles."))
                return

            # Build a 2-point interpolation: curr -> target
            # Times must be > 0 and strictly increasing.
            t0 = 0.05
            t1 = max(t0 + 0.05, lead_in)  # ensure strictly increasing
            
            angleLists = [[float(curr[j]), float(target[j])] for j in range(len(self.names))]
            timeLists = [[t0, t1] for _ in self.names]

            # For angleInterpolation: each joint gets list of angles & list of times
            # Here we give 2 points: at t=lead_in hit target; NAOqi uses current as initial state effectively.
            # But to be explicit and avoid velocity issues, we can give a small first time too:
            times = [max(0.05, lead_in)]
            timeLists = [times[:] for _ in self.names]

            # Use setAngles for a gentle ramp as fallback if angleInterpolation complains
            try:
                self._safe(self.motion.angleInterpolation, self.names, angleLists, timeLists, True)
            except Exception:
                # fallback: just setAngles with moderate speed
                self._safe(self.motion.setAngles, self.names, target, 0.15)
                time.sleep(lead_in)

            # Update UI sliders to match target
            self.root.after(0, self._on_joint_select)
            self.root.after(0, lambda: self._set_status(f"Arrived at frame {idx + 1}. Adjust sliders, then Apply."))

        threading.Thread(target=worker, daemon=True).start()

    # -------------------- Apply edits --------------------
    def apply_to_frame(self):
        if not self.samples:
            messagebox.showwarning("Apply", "No gesture loaded.")
            return
        self._set_status(f"Frame {self.frame_idx + 1} already updated in memory. Save JSON to persist.")

    # -------------------- run --------------------
def main():
    root = tk.Tk()
    app = PepperKeyframeEditorGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
