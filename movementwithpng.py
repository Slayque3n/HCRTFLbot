import qi
import sys
import termios
import tty
import select
import math
import numpy as np
import heapq
import cv2
import yaml
import time

# ==========================================================
ROBOT_IP = "192.168.137.86"
PORT = 9559

MAP_FILE = "map.pgm"
MAP_YAML = "map.yaml"

SPEED_RATIO = 0.4
TURN_RATIO = 0.6

POS_TOLERANCE = 0.15
ANG_TOLERANCE = 0.1
# ==========================================================

msg = """
==================================
 Pepper Navigation with PGM Map
==================================
p : set origin
r : return to origin
1-9 : save waypoint
SHIFT+1-9 : go to waypoint

Manual:
w/x forward/back
a/d strafe
q/e rotate

CTRL-C to exit
==================================
"""

SHIFT_KEYS = {'!':1,'@':2,'#':3,'$':4,'%':5,'^':6,'&':7,'*':8,'(':9}
MANUAL_KEYS = set('wxadqesx')

# ==========================================================
# MAP CLASS
# ==========================================================

class GlobalMap:

    def __init__(self, pgm, yaml_file):

        self.image = cv2.imread(pgm, cv2.IMREAD_GRAYSCALE)

        with open(yaml_file) as f:
            data = yaml.safe_load(f)

        self.resolution = data["resolution"]
        self.origin = data["origin"]

        self.height, self.width = self.image.shape

        self.grid = np.zeros_like(self.image)

        self.grid[self.image < 50] = 1
        self.grid[self.image > 200] = 0
        self.grid[(self.image >= 50) & (self.image <= 200)] = -1

    def world_to_grid(self,x,y):

        gx = int((x - self.origin[0]) / self.resolution)
        gy = int((y - self.origin[1]) / self.resolution)

        return gx,gy

    def grid_to_world(self,gx,gy):

        x = gx * self.resolution + self.origin[0]
        y = gy * self.resolution + self.origin[1]

        return x,y

    def is_free(self,gx,gy):

        if gx < 0 or gy < 0 or gx >= self.width or gy >= self.height:
            return False

        return self.grid[gy][gx] == 0


# ==========================================================
# GLOBAL A*
# ==========================================================

def astar(global_map,start,goal):

    start = global_map.world_to_grid(*start)
    goal = global_map.world_to_grid(*goal)

    frontier=[]
    heapq.heappush(frontier,(0,start))

    came_from={start:None}
    cost={start:0}

    while frontier:

        _,current = heapq.heappop(frontier)

        if current == goal:

            path=[]

            while current:

                wx,wy = global_map.grid_to_world(*current)
                path.append((wx,wy))

                current = came_from[current]

            return path[::-1]

        for dx,dy in [(1,0),(-1,0),(0,1),(0,-1)]:

            neighbor=(current[0]+dx,current[1]+dy)

            if not global_map.is_free(*neighbor):
                continue

            new_cost = cost[current] + 1

            if neighbor not in cost or new_cost < cost[neighbor]:

                cost[neighbor]=new_cost

                priority = new_cost + math.hypot(goal[0]-neighbor[0],goal[1]-neighbor[1])

                heapq.heappush(frontier,(priority,neighbor))

                came_from[neighbor]=current

    return None


# ==========================================================
# HELPERS
# ==========================================================

def get_key(settings):

    tty.setraw(sys.stdin.fileno())

    rlist,_,_ = select.select([sys.stdin],[],[],0.05)

    key = sys.stdin.read(1) if rlist else ''

    termios.tcsetattr(sys.stdin,termios.TCSADRAIN,settings)

    return key


def normalize_angle(a):

    return (a + math.pi) % (2*math.pi) - math.pi


def get_position(motion):

    pos = motion.getRobotPosition(True)

    return pos[0],pos[1],pos[2]


# ==========================================================
# MAIN
# ==========================================================

def main():

    session = qi.Session()

    try:
        session.connect("tcp://"+ROBOT_IP+":"+str(PORT))
    except:
        sys.exit("Connection failed")

    motion = session.service("ALMotion")

    motion.wakeUp()
    motion.moveInit()

    global_map = GlobalMap(MAP_FILE,MAP_YAML)

    origin=None
    setpoints={}
    nav_path=[]
    nav_target=None
    mode="idle"

    settings = termios.tcgetattr(sys.stdin)

    print(msg)


    def start_nav(target):

        nonlocal nav_path,nav_target,mode

        ax,ay,at = get_position(motion)

        print("Planning path...")

        path = astar(global_map,(ax,ay),(target[0],target[1]))

        if path is None:
            print("No path found")
            return

        nav_path = path
        nav_target = target
        mode="nav"

        print("Path length:",len(nav_path))


    try:

        while True:

            key = get_key(settings)

            if key == '\x03':
                break

            # ----------------------------------
            # Navigation update
            # ----------------------------------

            if mode=="nav" and nav_path:

                ax,ay,at = get_position(motion)

                wx,wy = nav_path[0]

                dist = math.hypot(wx-ax,wy-ay)

                if dist < POS_TOLERANCE:

                    nav_path.pop(0)

                    if not nav_path:
                        mode="idle"
                        motion.stopMove()
                        print("Arrived")
                        continue

                heading = math.atan2(wy-ay,wx-ax)

                err = normalize_angle(heading-at)

                vt = max(-TURN_RATIO,min(TURN_RATIO,2*err))

                motion.moveToward(SPEED_RATIO,0,vt)

                continue


            # ----------------------------------
            # KEYBOARD COMMANDS
            # ----------------------------------

            if key=='p':

                origin = get_position(motion)

                print("Origin saved")


            elif key=='r' and origin:

                start_nav(origin)


            elif key in "123456789":

                setpoints[int(key)] = get_position(motion)

                print("Waypoint",key,"saved")


            elif key in SHIFT_KEYS:

                idx = SHIFT_KEYS[key]

                if idx in setpoints:

                    start_nav(setpoints[idx])


            # ----------------------------------
            # MANUAL CONTROL
            # ----------------------------------

            if key in MANUAL_KEYS and mode=="idle":

                vx,vy,vt = 0,0,0

                if key=='w':
                    vx=SPEED_RATIO

                elif key=='x':
                    vx=-SPEED_RATIO

                elif key=='a':
                    vy=SPEED_RATIO

                elif key=='d':
                    vy=-SPEED_RATIO

                elif key=='q':
                    vt=TURN_RATIO

                elif key=='e':
                    vt=-TURN_RATIO

                motion.moveToward(vx,vy,vt)


    finally:

        motion.stopMove()

        termios.tcsetattr(sys.stdin,termios.TCSADRAIN,settings)


if __name__=="__main__":
    main()