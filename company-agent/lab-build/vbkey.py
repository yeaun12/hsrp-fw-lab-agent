"""Type into a VirtualBox VM's screen console and take screenshots.

usage:
  py -3 vbkey.py VM shot NAME.png
  py -3 vbkey.py VM line "text"          (types text + Enter)
  py -3 vbkey.py VM file PATH DEST       (cat > DEST on the guest, ends with Ctrl-D)
"""
import os, subprocess, sys, time

VB = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
HERE = os.path.dirname(os.path.abspath(__file__))


def vb(*a):
    subprocess.run([VB, "controlvm", *a], check=True)


def text(vm, s):
    if s:
        vb(vm, "keyboardputstring", s)


def enter(vm):
    vb(vm, "keyboardputscancode", "1c", "9c")


def line(vm, s, pause=0.3):
    text(vm, s); enter(vm); time.sleep(pause)


def ctrl_d(vm):
    vb(vm, "keyboardputscancode", "1d", "20", "a0", "9d")


if __name__ == "__main__":
    vm, cmd = sys.argv[1], sys.argv[2]
    if cmd == "shot":
        vb(vm, "screenshotpng", os.path.join(HERE, sys.argv[3]))
    elif cmd == "line":
        line(vm, sys.argv[3])
    elif cmd == "file":
        line(vm, f"cat > {sys.argv[4]}", 1)
        with open(sys.argv[3], encoding="utf-8") as f:
            for l in f.read().splitlines():
                line(vm, l, 0.4)
        time.sleep(1); ctrl_d(vm)
