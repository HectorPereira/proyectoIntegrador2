import serial
import tkinter as tk
from tkinter import ttk

# === CONFIG ===
PORT = "COM5"   # change to your Arduino port
BAUD = 9600
ser = serial.Serial(PORT, BAUD, timeout=1)

# === SEND FUNCTION ===
def send_angles(*args):
    values = [slider1.get(), slider2.get(), slider3.get(), slider4.get()]
    msg = f"{values[0]},{values[1]},{values[2]},{values[3]}\n"
    ser.write(msg.encode())

# === GUI ===
root = tk.Tk()
root.title("4 Servo Controller")

sliders = []
for i in range(4):
    tk.Label(root, text=f"Servo {i+1}").grid(row=i, column=0, padx=10, pady=10)
    s = tk.Scale(root, from_=0, to=180, orient="horizontal", command=send_angles, length=300)
    s.set(90)
    s.grid(row=i, column=1)
    sliders.append(s)

slider1, slider2, slider3, slider4 = sliders

root.mainloop()
