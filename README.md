# To Run
To run webbased translation run App.py, to run rkinter based talking run UI.py




## Development Workflow

To keep `main` stable, **direct pushes to `main` are blocked**.  
All changes **must go through a Pull Request (PR)**.

---

### How to Make Changes

#### 1. Create a New Branch
Always branch off `main`.


#### 2. updating ur branch with main
Before opening or updating a Pull Request, you must sync your feature branch with the latest changes from `main`.  
This helps prevent merge conflicts and ensures your code is reviewed against the most up-to-date version of the project.

Run the following commands from your feature branch:




# Make sure you are on your feature branch
git checkout your-branch-name

# Fetch the latest changes from the remote
git fetch origin

# Merge main into your branch
git merge origin/main

# If conflicts occur:
# 1. Resolve them in the files
# 2. Then run:
# git add .
# git commit

# Push the updated branch
git push
