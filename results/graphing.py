import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

df = pd.read_csv("Survey_Data.csv")

df = df[df["Timestamp"].astype(str).str.match(r"\d{2}/\d{2}/\d{4}", na=False)].copy()

condition_map = {
    "No Guidance": "No Aid",
    "Speech Guidance": "Static Aid",
    "Speech and Navigation Guidance": "Mobile Aid"
}

df["Condition"] = df["Which test did you partake in"].map(condition_map)

time_col = "Time Taken (seconds)"
nasa_cols = [
    "How mentally demanding was the task?",
    "How hurried or rushed was the pace of the task? ",
    "How hard did you have to work to accomplish your level of performance? ",
    "How insecure, discouraged, irritated, stressed, and annoyed were you while completing the task? "
]

survey_cols = [
    "“I found the guidance provided by the robot easy to understand.”",
    "“The robot helped me reach my destination efficiently.”",
    "“I felt confident navigating the station with the robot’s assistance.”",
    "“Using the robot reduced the mental effort required to navigate the station.”",
    "“I would prefer using this robot over traditional signage or maps.”"
]

for col in [time_col] + nasa_cols + survey_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

df["Cognitive Load"] = df[nasa_cols].mean(axis=1)

summary = df.groupby("Condition", dropna=True).agg({
    time_col: "mean",
    "Cognitive Load": "mean"
}).reindex(["No Aid", "Static Aid", "Mobile Aid"])

conditions = summary.index.tolist()
time_taken = summary[time_col].tolist()
cognitive_load = summary["Cognitive Load"].tolist()

plt.figure(figsize=(8, 5))
plt.bar(conditions, time_taken)
plt.ylabel("Time Taken (s)")
plt.title("Task Completion Time by Condition")
plt.tight_layout()
plt.savefig("time_taken.png")
plt.close()

plt.figure(figsize=(8, 5))
plt.bar(conditions, cognitive_load)
plt.ylabel("Cognitive Load")
plt.title("Cognitive Load by Condition")
plt.tight_layout()
plt.savefig("cognitive_load.png")
plt.close()

radar_df = df[df["Condition"].isin(["Static Aid", "Mobile Aid"])].copy()

categories = [
    "Valuable",
    "Helpful",
    "Confidence",
    "Reduced mental\neffort",
    "Prefer over\ntraditional aid"
]

radar_summary = radar_df.groupby("Condition")[survey_cols].mean().reindex(["Static Aid", "Mobile Aid"])

static_robot_scores = radar_summary.loc["Static Aid"].tolist()
mobile_robot_scores = radar_summary.loc["Mobile Aid"].tolist()

N = len(categories)
angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
angles += angles[:1]

static_plot = static_robot_scores + static_robot_scores[:1]
mobile_plot = mobile_robot_scores + mobile_robot_scores[:1]

fig, ax = plt.subplots(figsize=(8, 8), subplot_kw=dict(polar=True))

ax.plot(angles, static_plot, linewidth=2, label="Stationary Robot")
ax.fill(angles, static_plot, alpha=0.1)

ax.plot(angles, mobile_plot, linewidth=2, label="Mobile Robot")
ax.fill(angles, mobile_plot, alpha=0.1)

ax.set_xticks(angles[:-1])
ax.set_xticklabels(categories)

ax.set_ylim(0, 5)
ax.set_yticks([1, 2, 3, 4, 5])
ax.set_yticklabels(["1", "2", "3", "4", "5"])

plt.title("User Satisfaction Survey Comparison")
plt.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1))
plt.tight_layout()
plt.savefig("spider_plot.png", dpi=300, bbox_inches="tight")
plt.show()