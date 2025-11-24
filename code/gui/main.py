import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial, time
from serial.tools import list_ports
import math
import json
import os

# ===================== Config =====================
BAUD = 9600                 # Debe coincidir con el firmware del Arduino
SEND_INTERVAL_MS = 40       # Envío durante arrastre
READ_POLL_MS = 60           # Lectura periódica del puerto
CENTER_STEP_DEG = 2         # Paso del centrado (°)
CENTER_INTERVAL_MS = 40     # Intervalo entre pasos (ms)

RECORD_INTERVAL_MS = 100    # Periodo para grabar trayectoria (ms)

# HUD (Canvas) tamaño mínimo
ARM_CANVAS_W = 320
ARM_CANVAS_H = 240
BASE_CANVAS_W = 240
BASE_CANVAS_H = 240

# Longitudes "estéticas" del brazo (px)
L1 = 80   # hombro
L2 = 70   # codo
L3 = 50   # muñeca

# ===================== Estado global =====================
ser = None
connected = False
is_centering = False  # Centrado suave en progreso

# Grabación y reproducción de trayectorias
recording = False
recorded_frames = []
record_after_id = None

is_playing = False
play_index = 0
play_after_id = None

# Prealineado suave antes de reproducir
preplay_active = False
preplay_after_id = None

# Flag para distinguir cambios generados por código (replay/centro) de los del usuario
updating_from_code = False

# Archivo de trayectoria actual
trajectory_filename = None

# Electroimán
mag_state = 0  # 0 = apagado, 1 = encendido
mag_btn = None

# Estado por-servo
servos = {
    "S1": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None,
           "min": 0,  "max": 180, "pin": "D9",          "scale": None},
    "S2": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None,
           "min": 30, "max": 150, "pin": "D10 (lim.)", "scale": None},
    "S3": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None,
           "min": 0,  "max": 180, "pin": "D11",        "scale": None},
    "S4": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None,
           "min": 0,  "max": 180, "pin": "D6",         "scale": None},
}

# ===================== Serial helpers =====================
def write_line(line: str):
    global ser, connected
    if not connected or ser is None:
        return
    try:
        ser.write((line + "\n").encode("ascii", errors="ignore"))
        ser.flush()
        last_msg_var.set(f"Último: > {line}")
    except Exception as e:
        last_msg_var.set(f"TX error: {e}")

def read_available():
    if not connected or ser is None:
        return ""
    out = b""
    try:
        chunk = ser.read(ser.in_waiting or 1)
        out += chunk
        time.sleep(0.002)
        while ser.in_waiting:
            out += ser.read(ser.in_waiting)
    except Exception:
        pass
    return out.decode(errors="ignore")

def periodic_read():
    if not connected:
        return
    data = read_available().strip()
    if data:
        parse_and_update(data)
    root.after(READ_POLL_MS, periodic_read)

# ===================== Helpers de servos =====================
def set_servo_angle(key: str, val: int):
    """Pone un servo en cierto ángulo sin disparar la lógica normal de sliders."""
    global updating_from_code
    lo, hi = servos[key]["min"], servos[key]["max"]
    val = max(lo, min(hi, val))
    updating_from_code = True
    servos[key]["var"].set(float(val))
    servos[key]["disp"].set(str(val))
    updating_from_code = False

# ===================== Parser (eco/estado) =====================
def parse_and_update(text: str):
    last_line = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        last_line = line

        # Telemetría: "S1=90 S2=120 S3=45 S4=30"
        if ("S1=" in line) and ("S2=" in line) and ("S3=" in line) and ("S4=" in line):
            if mode_var.get() == "CE" and not is_centering:
                try:
                    parts = line.split()
                    vals = {}
                    for tok in parts:
                        if tok.startswith("S") and "=" in tok:
                            name, val = tok.split("=", 1)
                            if name in ("S1", "S2", "S3", "S4"):
                                vals[name] = int(val)
                    for name, key in (("S1", "S1"), ("S2", "S2"),
                                      ("S3", "S3"), ("S4", "S4")):
                        if name in vals:
                            ang = vals[name]
                            lo, hi = servos[key]["min"], servos[key]["max"]
                            ang = max(lo, min(hi, ang))
                            set_servo_angle(key, ang)
                    update_hud()
                except Exception:
                    pass

        # Back-compat: "ANG=xx,MODE=yy"
        elif line.startswith("ANG="):
            try:
                parts = line.split(",", 1)
                ang = int(parts[0].split("=")[1])
                lo, hi = servos["S1"]["min"], servos["S1"]["max"]
                ang = max(lo, min(hi, ang))
                set_servo_angle("S1", ang)
                update_hud()
            except Exception:
                pass

        # Mensajes de modo del firmware: "MODE=APP" / "MODE=CE"
        elif line.startswith("MODE="):
            mode_name = line.split("=", 1)[1].strip().upper()
            if mode_name == "APP":
                mode_var.set("APP")
            elif mode_name == "CE":
                mode_var.set("CE")

    if last_line:
        last_msg_var.set(f"Último: {last_line}")

# ===================== Conexión =====================
def refresh_ports():
    ports = [p.device for p in list_ports.comports()]
    port_combo["values"] = ports
    if ports and port_combo.get() == "":
        port_combo.set(ports[0])

def connect_serial():
    global ser, connected
    if connected:
        disconnect_serial()
        return
    port = port_combo.get().strip()
    if not port:
        messagebox.showwarning("Puerto", "Seleccioná un puerto primero.")
        return
    try:
        ser = serial.Serial(port, BAUD, timeout=0.2)
        time.sleep(0.4)  # espera al reset del UNO
        connected = True
        connect_btn.config(text="Desconectar")
        status_var.set(f"Conectado a {port} @ {BAUD} baud")
        pending = read_available().strip()
        if pending:
            parse_and_update(pending)
        periodic_read()

        # Sincronizar modo actual con el firmware
        on_mode_change()

    except Exception as e:
        ser = None
        connected = False
        messagebox.showerror("Error", f"No se pudo abrir {port}\n{e}")

def disconnect_serial():
    global ser, connected, is_centering, recording, is_playing
    global record_after_id, play_after_id, preplay_active, preplay_after_id, mag_state

    for k in servos:
        aid = servos[k]["after_id"]
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
            servos[k]["after_id"] = None

    if record_after_id is not None:
        try:
            root.after_cancel(record_after_id)
        except Exception:
            pass
        record_after_id = None
        recording = False

    if play_after_id is not None:
        try:
            root.after_cancel(play_after_id)
        except Exception:
            pass
        play_after_id = None
        is_playing = False

    if preplay_after_id is not None:
        try:
            root.after_cancel(preplay_after_id)
        except Exception:
            pass
        preplay_after_id = None
        preplay_active = False

    is_centering = False
    set_scales_state("normal")

    # Reset electroimán
    mag_state = 0
    if mag_btn is not None:
        try:
            mag_btn.config(text="Electroimán: OFF")
        except Exception:
            pass

    if ser:
        try:
            ser.close()
        except Exception:
            pass
    ser = None
    connected = False
    connect_btn.config(text="Conectar")
    status_var.set("Desconectado")
    last_msg_var.set("Último: —")

    # Por si el botón de grabar estaba en "Detener grabación"
    if "record_btn" in globals():
        try:
            record_btn.config(text="Grabar trayectoria")
        except Exception:
            pass

# ===================== Grabación de trayectorias =====================
def record_tick():
    """Se llama periódicamente mientras `recording` es True para guardar la pose."""
    global record_after_id
    if not recording:
        record_after_id = None
        return

    frame = {}
    for k in ("S1", "S2", "S3", "S4"):
        frame[k] = int(round(servos[k]["var"].get()))
    recorded_frames.append(frame)

    record_after_id = root.after(RECORD_INTERVAL_MS, record_tick)

def toggle_record():
    global recording, recorded_frames, record_after_id

    if not recording:
        # Ahora solo permite grabar si hay brazo conectado
        if not connected or ser is None:
            messagebox.showwarning("Conexión", "Conecte el brazo para grabar una trayectoria.")
            return
        recorded_frames = []
        recording = True
        record_btn.config(text="Detener grabación")
        status_var.set("Grabando trayectoria.")
        record_tick()
    else:
        recording = False
        if record_after_id is not None:
            try:
                root.after_cancel(record_after_id)
            except Exception:
                pass
            record_after_id = None
        record_btn.config(text="Grabar trayectoria")
        status_var.set(f"Grabación detenida. Pasos: {len(recorded_frames)}")

def save_trajectory():
    global trajectory_filename
    if not recorded_frames:
        messagebox.showinfo("Trayectoria", "No hay puntos grabados para guardar.")
        return
    path = filedialog.asksaveasfilename(
        title="Guardar trayectoria",
        defaultextension=".json",
        filetypes=[("JSON", "*.json"), ("Todos", "*.*")]
    )
    if not path:
        return

    data = {
        "dt_ms": RECORD_INTERVAL_MS,
        "frames": recorded_frames
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        trajectory_filename = path
        traj_file_var.set(f"Trayectoria actual: {os.path.basename(path)}")
        status_var.set(f"Trayectoria guardada en {path}")
        messagebox.showinfo("Trayectoria", "Trayectoria guardada correctamente.")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo guardar el archivo:\n{e}")

def load_trajectory():
    global recorded_frames, trajectory_filename
    path = filedialog.askopenfilename(
        title="Cargar trayectoria",
        filetypes=[("JSON", "*.json"), ("Todos", "*.*")]
    )
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        frames = data.get("frames", [])
        if not isinstance(frames, list) or not frames:
            messagebox.showwarning("Trayectoria", "El archivo no contiene frames válidos.")
            return
        recorded_frames = frames
        trajectory_filename = path
        traj_file_var.set(f"Trayectoria actual: {os.path.basename(path)}")
        status_var.set(f"Trayectoria cargada ({len(recorded_frames)} pasos)")
        messagebox.showinfo("Trayectoria", "Trayectoria cargada correctamente.")
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo cargar el archivo:\n{e}")

# ===================== Movimiento suave hacia una pose =====================
def smooth_move_to_frame(frame, done_callback):
    """
    Mueve todos los servos de forma suave hasta la pose indicada en 'frame',
    usando el mismo paso que el centrado suave. Al terminar llama done_callback().
    """
    global preplay_active, preplay_after_id, is_centering

    if preplay_active:
        return

    preplay_active = True
    is_centering = True
    set_scales_state("disabled")

    targets = {}
    for k in ("S1", "S2", "S3", "S4"):
        lo, hi = servos[k]["min"], servos[k]["max"]
        t = int(frame.get(k, int(round(servos[k]["var"].get()))))
        if t < lo:
            t = lo
        if t > hi:
            t = hi
        targets[k] = t

    def _tick():
        global preplay_active, preplay_after_id, is_centering

        if not preplay_active:
            return

        all_done = True
        for k in ("S1", "S2", "S3", "S4"):
            cur = int(round(servos[k]["var"].get()))
            tgt = targets[k]
            if cur != tgt:
                all_done = False
                if cur < tgt:
                    cur = min(cur + CENTER_STEP_DEG, tgt)
                else:
                    cur = max(cur - CENTER_STEP_DEG, tgt)
                set_servo_angle(k, cur)

        if connected and mode_var.get() == "APP":
            cmds = [f"{k}:{int(servos[k]['var'].get()):d}" for k in ("S1", "S2", "S3", "S4")]
            write_line(" ".join(cmds))

        update_hud()

        if all_done:
            preplay_active = False
            preplay_after_id = None
            is_centering = False
            set_scales_state("normal")
            for k in servos:
                cur = int(round(servos[k]["var"].get()))
                servos[k]["last_sent"] = cur
                servos[k]["disp"].set(str(cur))
            last_msg_var.set("Último: Pose alineada con la trayectoria.")
            if done_callback is not None:
                done_callback()
        else:
            preplay_after_id = root.after(CENTER_INTERVAL_MS, _tick)

    _tick()

# ===================== Reproducción de trayectorias =====================
def start_playback():
    global is_playing, play_index

    if not recorded_frames:
        messagebox.showinfo("Reproducción", "No hay trayectoria cargada o grabada.")
        return

    if not connected or ser is None:
        messagebox.showwarning("Conexión", "Conecte el brazo para reproducir una trayectoria.")
        return

    if is_playing:
        return

    # Primero alineamos suavemente la pose actual al primer frame
    first_frame = recorded_frames[0]

    def after_align():
        global is_playing, play_index
        is_playing = True
        play_index = 0
        status_var.set("Reproduciendo trayectoria...")
        playback_step()

    smooth_move_to_frame(first_frame, after_align)

def stop_playback():
    global is_playing, play_after_id, preplay_active, preplay_after_id, is_centering

    # Detener reproducción
    if play_after_id is not None:
        try:
            root.after_cancel(play_after_id)
        except Exception:
            pass
        play_after_id = None
    is_playing = False

    # Detener prealineado
    if preplay_after_id is not None:
        try:
            root.after_cancel(preplay_after_id)
        except Exception:
            pass
        preplay_after_id = None
    preplay_active = False

    is_centering = False
    set_scales_state("normal")
    status_var.set("Reproducción detenida por el usuario.")

def playback_step():
    global is_playing, play_index, play_after_id
    if not is_playing:
        return

    if play_index >= len(recorded_frames):
        is_playing = False
        status_var.set("Reproducción finalizada")
        return

    frame = recorded_frames[play_index]

    # Mover sliders a la posición grabada
    for k in ("S1", "S2", "S3", "S4"):
        if k in frame:
            set_servo_angle(k, int(frame[k]))

    # Solo envía por UART si realmente hay conexión y estás en APP
    if connected and mode_var.get() == "APP":
        cmd = " ".join(f"{k}:{int(servos[k]['var'].get()):d}" for k in ("S1", "S2", "S3", "S4"))
        write_line(cmd)

    update_hud()

    play_index += 1
    if play_index < len(recorded_frames):
        play_after_id = root.after(RECORD_INTERVAL_MS, playback_step)
    else:
        is_playing = False
        status_var.set("Reproducción finalizada")

# ===================== Envío por sliders =====================
def _send_pending_for(key: str):
    if is_centering:
        servos[key]["after_id"] = None
        return
    servos[key]["after_id"] = None
    deg = int(servos[key]["pending"])
    lo, hi = servos[key]["min"], servos[key]["max"]
    deg = max(lo, min(hi, deg))
    servos[key]["disp"].set(str(deg))
    if mode_var.get() == "APP" and deg != servos[key]["last_sent"]:
        write_line(f"{key}:{deg:d}")
        servos[key]["last_sent"] = deg
    if connected and mode_var.get() == "APP":
        servos[key]["after_id"] = root.after(SEND_INTERVAL_MS, _send_pending_for, key)
    update_hud()

def on_slider_change(key: str, _val=None):
    if is_centering or updating_from_code:
        return

    cur = int(round(servos[key]["var"].get()))
    lo, hi = servos[key]["min"], servos[key]["max"]
    cur = max(lo, min(hi, cur))
    servos[key]["disp"].set(str(cur))

    if mode_var.get() != "APP":
        update_hud()
        return

    servos[key]["pending"] = cur
    if connected and servos[key]["after_id"] is None:
        servos[key]["after_id"] = root.after(SEND_INTERVAL_MS, _send_pending_for, key)
    update_hud()

def on_slider_release(key: str, _event=None):
    if is_centering or updating_from_code:
        return

    if mode_var.get() != "APP":
        return

    aid = servos[key]["after_id"]
    if aid is not None:
        try:
            root.after_cancel(aid)
        except Exception:
            pass
        servos[key]["after_id"] = None

    cur = int(round(servos[key]["var"].get()))
    lo, hi = servos[key]["min"], servos[key]["max"]
    cur = max(lo, min(hi, cur))
    servos[key]["var"].set(float(cur))
    servos[key]["disp"].set(str(cur))
    if cur != servos[key]["last_sent"]:
        write_line(f"{key}:{cur:d}")
        servos[key]["last_sent"] = cur

    update_hud()

# ===================== Centrado suave =====================
def set_scales_state(state: str):
    for k in servos:
        sc = servos[k]["scale"]
        if sc is not None:
            sc.configure(state="disabled" if state == "disabled" else "normal")

def center_all_smooth():
    global is_centering
    if is_centering:
        return
    is_centering = True
    set_scales_state("disabled")

    targets = {}
    for k in ("S1", "S2", "S3", "S4"):
        lo, hi = servos[k]["min"], servos[k]["max"]
        t = 90
        if t < lo:
            t = lo
        if t > hi:
            t = hi
        targets[k] = int(t)

    def _tick():
        nonlocal targets
        global is_centering

        all_done = True
        for k in ("S1", "S2", "S3", "S4"):
            cur = int(round(servos[k]["var"].get()))
            tgt = targets[k]
            if cur != tgt:
                all_done = False
                if cur < tgt:
                    cur = min(cur + CENTER_STEP_DEG, tgt)
                else:
                    cur = max(cur - CENTER_STEP_DEG, tgt)
                set_servo_angle(k, cur)

        if connected and mode_var.get() == "APP":
            cmds = [f"{k}:{int(servos[k]['var'].get()):d}" for k in ("S1", "S2", "S3", "S4")]
            write_line(" ".join(cmds))

        update_hud()

        if all_done:
            for k in servos:
                cur = int(round(servos[k]["var"].get()))
                servos[k]["last_sent"] = cur
                servos[k]["disp"].set(str(cur))
            set_scales_state("normal")
            is_centering = False
            last_msg_var.set("Último: Centrado suave completado")
        else:
            root.after(CENTER_INTERVAL_MS, _tick)

    _tick()

# ===================== Electroimán =====================
def toggle_magnet():
    global mag_state
    if not connected or ser is None:
        messagebox.showwarning("Conexión", "Conecte el brazo para controlar el electroimán.")
        return
    mag_state = 0 if mag_state == 1 else 1
    if mag_btn is not None:
        mag_btn.config(text=f"Electroimán: {'ON' if mag_state else 'OFF'}")
    write_line(f"MAG:{mag_state}")

# ===================== HUD (Canvas) =====================
def update_hud():
    # Tamaño actual del canvas de base
    bw = base_canvas.winfo_width() or BASE_CANVAS_W
    bh = base_canvas.winfo_height() or BASE_CANVAS_H

    base_canvas.delete("all")
    base_canvas.create_rectangle(10, 10, bw - 10, bh - 10,
                                 outline="#666")

    cx, cy = bw // 2, bh // 2
    base_canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4,
                            fill="#333", outline="")

    s1 = int(round(servos["S1"]["var"].get()))
    phi = math.radians(s1)
    r = min(bw, bh) // 2 - 20

    x2 = cx + r * math.cos(phi)
    y2 = cy - r * math.sin(phi)
    base_canvas.create_line(cx, cy, x2, y2, width=4, fill="#1a73e8")
    base_canvas.create_text(16, bh - 16, anchor="w",
                            text=f"Base (S1): {s1:d}°", fill="#222")

    # Tamaño actual del canvas del brazo
    aw = arm_canvas.winfo_width() or ARM_CANVAS_W
    ah = arm_canvas.winfo_height() or ARM_CANVAS_H

    arm_canvas.delete("all")
    arm_canvas.create_rectangle(10, 10, aw - 10, ah - 10,
                                outline="#666")

    ox, oy = aw // 2, ah - 20

    Lsum = L1 + L2 + L3
    inner_half_w = (aw - 20) / 2.0
    inner_h = (ah - 20)

    scale = min(inner_half_w / Lsum, inner_h / Lsum) * 0.95
    eL1, eL2, eL3 = L1 * scale, L2 * scale, L3 * scale

    s2 = int(round(servos["S2"]["var"].get()))
    s3 = int(round(servos["S3"]["var"].get()))
    s4 = int(round(servos["S4"]["var"].get()))

    a1 = math.radians(s2)                 # hombro absoluto
    a2 = a1 + math.radians(s3 - 90)       # codo relativo
    a3 = a2 + math.radians(s4 - 90)       # muñeca relativa

    x1 = ox + eL1 * math.cos(a1)
    y1 = oy - eL1 * math.sin(a1)
    x2p = x1 + eL2 * math.cos(a2)
    y2p = y1 - eL2 * math.sin(a2)
    x3 = x2p + eL3 * math.cos(a3)
    y3 = y2p - eL3 * math.sin(a3)

    arm_canvas.create_line(ox, oy, x1, y1, width=6, fill="#0f9d58")
    arm_canvas.create_line(x1, y1, x2p, y2p, width=6, fill="#fbbc05")
    arm_canvas.create_line(x2p, y2p, x3, y3, width=6, fill="#ea4335")

    for (x, y) in [(ox, oy), (x1, y1), (x2p, y2p), (x3, y3)]:
        arm_canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                               fill="#333", outline="")

    arm_canvas.create_text(16, ah - 16, anchor="w",
                           text=f"Hombro S2={s2:d}°, Codo S3={s3:d}°, Muñeca S4={s4:d}°",
                           fill="#222")

    # Texto con la pose actual de todos los sliders
    current_pose_var.set(
        f"Pose actual: S1={s1}°, S2={s2}°, S3={s3}°, S4={s4}°"
    )

# ===================== UI =====================
root = tk.Tk()
root.title("Control de Servos (UART) — S1..S4 + HUD")

# Atajos de pantalla completa
def toggle_fullscreen(event=None):
    root.attributes("-fullscreen", not root.attributes("-fullscreen"))

def end_fullscreen(event=None):
    root.attributes("-fullscreen", False)
    root.state("zoomed")

root.bind("<F11>", toggle_fullscreen)
root.bind("<Escape>", end_fullscreen)

# Arrancar maximizado (en Windows suele funcionar bien)
try:
    root.state("zoomed")
except Exception:
    pass

main = ttk.Frame(root, padding=12)
main.pack(fill="both", expand=True)

# Hacer que las columnas de main se expandan
for col in range(4):
    main.columnconfigure(col, weight=1)

# Variables de UI
status_var = tk.StringVar(value="Desconectado")
last_msg_var = tk.StringVar(value="Último: —")
current_pose_var = tk.StringVar(value="Pose actual: S1=90°, S2=90°, S3=90°, S4=90°")
traj_file_var = tk.StringVar(value="Trayectoria actual: (ninguna)")
mode_var = tk.StringVar(value="APP")

# Fila 0: Puerto serie
ttk.Label(main, text="Puerto:").grid(row=0, column=0, sticky="w")
port_combo = ttk.Combobox(main, state="readonly", width=18)
port_combo.grid(row=0, column=1, sticky="w")
ttk.Button(main, text="Refrescar", command=refresh_ports).grid(row=0, column=2,
                                                               sticky="w", padx=6)
connect_btn = ttk.Button(main, text="Conectar", command=connect_serial)
connect_btn.grid(row=0, column=3, sticky="e")

# Fila 1: Estado + Último mensaje
ttk.Label(main, textvariable=status_var).grid(row=1, column=0,
                                              sticky="w", pady=(6, 0))
ttk.Label(main, textvariable=last_msg_var).grid(row=1, column=1, columnspan=3,
                                                sticky="w", pady=(6, 0))

# Fila 2: Pose actual
ttk.Label(main, textvariable=current_pose_var).grid(row=2, column=0,
                                                    columnspan=4, sticky="w",
                                                    pady=(4, 0))

# Fila 3: Trayectoria actual
ttk.Label(main, textvariable=traj_file_var).grid(row=3, column=0,
                                                 columnspan=4, sticky="w",
                                                 pady=(2, 0))

# Fila 4..7: Sliders S1..S4
row = 4
for key, label in (("S1", "Servo 1 (D9, Base)"),
                   ("S2", "Servo 2 (D10, Hombro, 30–150°)"),
                   ("S3", "Servo 3 (D11, Codo)"),
                   ("S4", "Servo 4 (D6, Muñeca)")):
    lo, hi = servos[key]["min"], servos[key]["max"]
    val_ini = 90 if 90 >= lo and 90 <= hi else lo
    servos[key]["var"] = tk.DoubleVar(value=float(val_ini))
    servos[key]["disp"] = tk.StringVar(value=str(int(val_ini)))
    servos[key]["pending"] = int(val_ini)
    servos[key]["last_sent"] = None

    ttk.Label(main, text=f"{label}").grid(row=row, column=0,
                                          sticky="w", pady=(12, 0))

    scale = ttk.Scale(main, from_=lo, to=hi, orient="horizontal",
                      variable=servos[key]["var"], length=320,
                      command=lambda _v, k=key: on_slider_change(k))
    scale.grid(row=row, column=1, sticky="ew", padx=8, pady=(12, 0))
    scale.bind("<ButtonRelease-1>", lambda e, k=key: on_slider_release(k))
    servos[key]["scale"] = scale

    ttk.Label(main, textvariable=servos[key]["disp"]).grid(row=row, column=2,
                                                           sticky="w", padx=(6, 0),
                                                           pady=(12, 0))
    ttk.Label(main, text=f"Pin: {servos[key]['pin']}").grid(row=row, column=3,
                                                            sticky="e", pady=(12, 0))
    row += 1

# Fila siguiente: botón centro suave + modo
def on_mode_change():
    m = mode_var.get()
    if m == "APP":
        write_line("MODE:APP")
        set_scales_state("normal")
    else:
        write_line("MODE:CE")
        set_scales_state("normal")

ttk.Button(main, text="Centrar suave",
           command=center_all_smooth).grid(row=row, column=0,
                                           sticky="w", pady=(14, 0))

ttk.Label(main, text="Modo:").grid(row=row, column=1,
                                   sticky="e", pady=(14, 0))
ttk.Radiobutton(main, text="App", value="APP",
                variable=mode_var, command=on_mode_change)\
   .grid(row=row, column=2, sticky="w", pady=(14, 0))
ttk.Radiobutton(main, text="Comando espejo", value="CE",
                variable=mode_var, command=on_mode_change)\
   .grid(row=row, column=3, sticky="w", pady=(14, 0))

# Fila de controles de trayectoria
row += 1
traj_frame = ttk.Frame(main)
traj_frame.grid(row=row, column=0, columnspan=4, sticky="w", pady=(10, 0))

record_btn = ttk.Button(traj_frame, text="Grabar trayectoria",
                        command=toggle_record)
record_btn.grid(row=0, column=0, padx=(0, 6))

ttk.Button(traj_frame, text="Guardar",
           command=save_trajectory).grid(row=0, column=1, padx=6)
ttk.Button(traj_frame, text="Cargar",
           command=load_trajectory).grid(row=0, column=2, padx=6)
ttk.Button(traj_frame, text="Reproducir",
           command=start_playback).grid(row=0, column=3, padx=6)
ttk.Button(traj_frame, text="Detener",
           command=stop_playback).grid(row=0, column=4, padx=6)

# Botón electroimán
mag_btn = ttk.Button(traj_frame, text="Electroimán: OFF",
                     command=toggle_magnet)
mag_btn.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

# HUD
row += 1
hud = ttk.Frame(main)
hud.grid(row=row, column=0, columnspan=4, sticky="nsew", pady=(16, 0))

# Hacer que el HUD crezca
main.rowconfigure(row, weight=1)
hud.columnconfigure(0, weight=1)
hud.columnconfigure(1, weight=1)
hud.rowconfigure(1, weight=1)

ttk.Label(hud, text="Arm View (S2-S3-S4)").grid(row=0, column=0, sticky="w")
arm_canvas = tk.Canvas(hud, bg="#fafafa", highlightthickness=0)
arm_canvas.grid(row=1, column=0, padx=(0, 8), sticky="nsew")

ttk.Label(hud, text="Base View (S1)").grid(row=0, column=1, sticky="w")
base_canvas = tk.Canvas(hud, bg="#fafafa", highlightthickness=0)
base_canvas.grid(row=1, column=1, sticky="nsew")

# Inicializar puertos, HUD y loop
refresh_ports()
update_hud()
root.mainloop()

# Cierre limpio
try:
    disconnect_serial()
except Exception:
    pass
