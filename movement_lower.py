import qi
import sys
import time
import termios
import tty
import select

# Configuration
ROBOT_IP = "192.168.1.100"  # <--- Change this to your Pepper's IP
PORT = 9559

msg = """
Direct Control of Pepper (No ROS 2)
---------------------------
Moving around:        Rotation:
    w                    q (Left)
a   s   d                e (Right)
    x

w/x : Forward/Backward
a/d : Sideways Left/Right
q/e : Rotate Left/Right

space key, s : stop
CTRL-C to quit
"""

def get_key(settings):
    tty.setraw(sys.stdin.fileno())
    rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
    if rlist:
        key = sys.stdin.read(1)
    else:
        key = ''
    termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
    return key

def main():
    # Initialize qi session
    session = qi.Session()
    try:
        session.connect("tcp://" + ROBOT_IP + ":" + str(PORT))
    except Exception as e:
        print("Could not connect to Pepper: %s" % e)
        sys.exit(1)

    # Get the motion service
    motion_service = session.service("ALMotion")
    
    # Wake up robot (Enable stiffness)
    motion_service.wakeUp()

    settings = termios.tcgetattr(sys.stdin)
    print(msg)

    try:
        while True:
            key = get_key(settings)
            
            # x: forward, y: lateral, theta: rotation
            vx, vy, vtheta = 0.0, 0.0, 0.0
            
            if key == 'w':
                vx = 0.5
            elif key == 'x':
                vx = -0.5
            elif key == 'a':
                vy = 0.5
            elif key == 'd':
                vy = -0.5
            elif key == 'q':
                vtheta = 0.5
            elif key == 'e':
                vtheta = -0.5
            elif key == 's' or key == ' ':
                vx, vy, vtheta = 0.0, 0.0, 0.0
            elif key == '\x03': # CTRL-C
                break

            # moveToward uses normalized velocities (-1 to 1)
            motion_service.moveToward(vx, vy, vtheta)
            time.sleep(0.1)

    except Exception as e:
        print(e)

    finally:
        motion_service.stopMove()
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)

if __name__ == "__main__":
    main()
