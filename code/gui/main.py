import tkinter as tk
from tkinter import ttk, messagebox
import serial, time
from serial.tools import list_ports
import math

# ===================== Config =====================
BAUD = 9600                 # Debe coincidir con el firmware del Arduino
SEND_INTERVAL_MS = 40       # Envío durante arrastre
READ_POLL_MS = 60           # Lectura periódica del puerto
CENTER_STEP_DEG = 2         # Paso del centrado (°)
CENTER_INTERVAL_MS = 40     # Intervalo entre pasos (ms)

# HUD (Canvas)
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

# Estado por-servo
servos = {
    "S1": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None, "min": 0,  "max": 180, "pin": "D9",           "scale": None},
    "S2": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None, "min": 30, "max": 150, "pin": "D10 (lim.)",  "scale": None},
    "S3": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None, "min": 0,  "max": 180, "pin": "D11",         "scale": None},
    "S4": {"var": None, "disp": None, "pending": None, "last_sent": None, "after_id": None, "min": 0,  "max": 180, "pin": "D6",          "scale": None},
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
                    for name, key in (("S1", "S1"), ("S2", "S2"), ("S3", "S3"), ("S4", "S4")):
                        if name in vals:
                            ang = vals[name]
                            lo, hi = servos[key]["min"], servos[key]["max"]
                            ang = max(lo, min(hi, ang))
                            servos[key]["var"].set(float(ang))
                            servos[key]["disp"].set(str(ang))
                    update_hud()
                except Exception:
                    pass

        # Back-compat: "ANG=xx,MODE=yy" (no lo usamos ahora)
        elif line.startswith("ANG="):
            try:
                parts = line.split(",", 1)
                ang = int(parts[0].split("=")[1])
                lo, hi = servos["S1"]["min"], servos["S1"]["max"]
                ang = max(lo, min(hi, ang))
                servos["S1"]["var"].set(float(ang))
                servos["S1"]["disp"].set(str(int(ang)))
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
    global ser, connected, is_centering
    for k in servos:
        aid = servos[k]["after_id"]
        if aid is not None:
            try:
                root.after_cancel(aid)
            except Exception:
                pass
            servos[k]["after_id"] = None
    is_centering = False
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
    set_scales_state("normal")

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
    if is_centering:
        return

    cur = int(round(servos[key]["var"].get()))
    lo, hi = servos[key]["min"], servos[key]["max"]
    cur = max(lo, min(hi, cur))
    servos[key]["disp"].set(str(cur))

    if mode_var.get() != "APP":
        # En modo CE: solo actualizamos HUD, no mandamos por UART
        update_hud()
        return

    servos[key]["pending"] = cur
    if connected and servos[key]["after_id"] is None:
        servos[key]["after_id"] = root.after(SEND_INTERVAL_MS, _send_pending_for, key)
    update_hud()

def on_slider_release(key: str, _event=None):
    if is_centering:
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
    for k in ("S1","S2","S3","S4"):
        lo, hi = servos[k]["min"], servos[k]["max"]
        t = 90
        if t < lo:
            t = lo
        if t > hi:
            t = hi
        targets[k] = int(t)

    def _tick():
        nonlocal targets
        all_done = True
        for k in ("S1","S2","S3","S4"):
            cur = int(round(servos[k]["var"].get()))
            tgt = targets[k]
            if cur != tgt:
                all_done = False
                if cur < tgt:
                    cur = min(cur + CENTER_STEP_DEG, tgt)
                else:
                    cur = max(cur - CENTER_STEP_DEG, tgt)
                servos[k]["var"].set(float(cur))
                servos[k]["disp"].set(str(cur))

        cmds = [f"{k}:{int(servos[k]['var'].get()):d}" for k in ("S1","S2","S3","S4")]
        if mode_var.get() == "APP":
            write_line(" ".join(cmds))
        update_hud()

        if all_done:
            for k in servos:
                cur = int(round(servos[k]["var"].get()))
                servos[k]["last_sent"] = cur
                servos[k]["disp"].set(str(cur))
            set_scales_state("normal")
            finish_centering()
        else:
            root.after(CENTER_INTERVAL_MS, _tick)

    def finish_centering():
        global is_centering
        is_centering = False
        last_msg_var.set("Último: Centrado suave completado")

    _tick()

# ===================== HUD (Canvas) =====================
def update_hud():
    # Base View (S1)
    base_canvas.delete("all")
    base_canvas.create_rectangle(10, 10, BASE_CANVAS_W-10, BASE_CANVAS_H-10, outline="#666")

    cx, cy = BASE_CANVAS_W//2, BASE_CANVAS_H//2
    base_canvas.create_oval(cx-4, cy-4, cx+4, cy+4, fill="#333", outline="")

    s1 = int(round(servos["S1"]["var"].get()))
    phi = math.radians(s1)
    r = min(BASE_CANVAS_W, BASE_CANVAS_H)//2 - 20

    x2 = cx + r*math.cos(phi)
    y2 = cy - r*math.sin(phi)
    base_canvas.create_line(cx, cy, x2, y2, width=4, fill="#1a73e8")
    base_canvas.create_text(16, BASE_CANVAS_H-16, anchor="w",
                            text=f"Base (S1): {s1:d}°", fill="#222")

    # Arm View (S2,S3,S4)
    arm_canvas.delete("all")
    arm_canvas.create_rectangle(10, 10, ARM_CANVAS_W-10, ARM_CANVAS_H-10, outline="#666")

    ox, oy = ARM_CANVAS_W//2, ARM_CANVAS_H - 20

    Lsum = L1 + L2 + L3
    inner_half_w = (ARM_CANVAS_W - 20) / 2.0
    inner_h      = (ARM_CANVAS_H - 20)

    scale = min(inner_half_w / Lsum, inner_h / Lsum) * 0.95
    eL1, eL2, eL3 = L1*scale, L2*scale, L3*scale

    s2 = int(round(servos["S2"]["var"].get()))
    s3 = int(round(servos["S3"]["var"].get()))
    s4 = int(round(servos["S4"]["var"].get()))

    a1 = math.radians(s2)                 # hombro absoluto
    a2 = a1 + math.radians(s3 - 90)       # codo relativo
    a3 = a2 + math.radians(s4 - 90)       # muñeca relativa

    x1  = ox  + eL1*math.cos(a1);  y1  = oy - eL1*math.sin(a1)
    x2p = x1  + eL2*math.cos(a2);  y2p = y1 - eL2*math.sin(a2)
    x3  = x2p + eL3*math.cos(a3);  y3  = y2p - eL3*math.sin(a3)

    arm_canvas.create_line(ox,  oy,  x1,  y1,  width=6, fill="#0f9d58")
    arm_canvas.create_line(x1,  y1,  x2p, y2p, width=6, fill="#fbbc05")
    arm_canvas.create_line(x2p, y2p, x3,  y3,  width=6, fill="#ea4335")

    for (x,y) in [(ox,oy),(x1,y1),(x2p,y2p),(x3,y3)]:
        arm_canvas.create_oval(x-3, y-3, x+3, y+3, fill="#333", outline="")

    arm_canvas.create_text(16, ARM_CANVAS_H-16, anchor="w",
                           text=f"Hombro S2={s2:d}°, Codo S3={s3:d}°, Muñeca S4={s4:d}°",
                           fill="#222")

# ===================== UI =====================
root = tk.Tk()
root.title("Control de Servos (UART) — S1..S4 + HUD")

main = ttk.Frame(root, padding=12)
main.pack(fill="both", expand=True)
root.rowconfigure(0, weight=1)
root.columnconfigure(0, weight=1)

# Fila 0: Puerto serie
ttk.Label(main, text="Puerto:").grid(row=0, column=0, sticky="w")
port_combo = ttk.Combobox(main, state="readonly", width=18)
port_combo.grid(row=0, column=1, sticky="w")
ttk.Button(main, text="Refrescar", command=refresh_ports).grid(row=0, column=2, sticky="w", padx=6)
connect_btn = ttk.Button(main, text="Conectar", command=connect_serial)
connect_btn.grid(row=0, column=3, sticky="e")

# Fila 1: Estado + Último mensaje
status_var = tk.StringVar(value="Desconectado")
ttk.Label(main, textvariable=status_var).grid(row=1, column=0, sticky="w", pady=(6,0))
last_msg_var = tk.StringVar(value="Último: —")
ttk.Label(main, textvariable=last_msg_var).grid(row=1, column=1, columnspan=3, sticky="w", pady=(6,0))

# Modo
mode_var = tk.StringVar(value="APP")

def on_mode_change():
    m = mode_var.get()
    if m == "APP":
        write_line("MODE:APP")
        set_scales_state("normal")
    else:
        write_line("MODE:CE")
        set_scales_state("normal")

# Fila 2..5: Sliders S1..S4
row = 2
for key, label in (("S1","Servo 1 (D9, Base)"),
                   ("S2","Servo 2 (D10, Hombro, 30–150°)"),
                   ("S3","Servo 3 (D11, Codo)"),
                   ("S4","Servo 4 (D6, Muñeca)")):
    lo, hi = servos[key]["min"], servos[key]["max"]
    val_ini = 90 if 90>=lo and 90<=hi else lo
    servos[key]["var"]  = tk.DoubleVar(value=float(val_ini))
    servos[key]["disp"] = tk.StringVar(value=str(int(val_ini)))
    servos[key]["pending"] = int(val_ini)
    servos[key]["last_sent"] = None

    ttk.Label(main, text=f"{label}").grid(row=row, column=0, sticky="w", pady=(12,0))

    scale = ttk.Scale(main, from_=lo, to=hi, orient="horizontal",
                      variable=servos[key]["var"], length=320,
                      command=lambda _v, k=key: on_slider_change(k))
    scale.grid(row=row, column=1, sticky="ew", padx=8, pady=(12,0))
    scale.bind("<ButtonRelease-1>", lambda e, k=key: on_slider_release(k))
    servos[key]["scale"] = scale

    ttk.Label(main, textvariable=servos[key]["disp"]).grid(row=row, column=2, sticky="w", padx=(6,0), pady=(12,0))
    ttk.Label(main, text=f"Pin: {servos[key]['pin']}").grid(row=row, column=3, sticky="e", pady=(12,0))
    row += 1

# Fila siguiente: botón centro suave + modo
ttk.Button(main, text="Centrar suave", command=center_all_smooth).grid(row=row, column=0, sticky="w", pady=(14,0))

ttk.Label(main, text="Modo:").grid(row=row, column=1, sticky="e", pady=(14,0))
ttk.Radiobutton(main, text="App", value="APP",
                variable=mode_var, command=on_mode_change)\
   .grid(row=row, column=2, sticky="w", pady=(14,0))
ttk.Radiobutton(main, text="Comando espejo", value="CE",
                variable=mode_var, command=on_mode_change)\
   .grid(row=row, column=3, sticky="w", pady=(14,0))

# HUD
hud = ttk.Frame(main)
hud.grid(row=row+1, column=0, columnspan=4, sticky="ew", pady=(16,0))
hud.columnconfigure(0, weight=1)
hud.columnconfigure(1, weight=1)

ttk.Label(hud, text="Arm View (S2-S3-S4)").grid(row=0, column=0, sticky="w")
arm_canvas = tk.Canvas(hud, width=ARM_CANVAS_W, height=ARM_CANVAS_H, bg="#fafafa", highlightthickness=0)
arm_canvas.grid(row=1, column=0, padx=(0,8), sticky="w")

ttk.Label(hud, text="Base View (S1)").grid(row=0, column=1, sticky="w")
base_canvas = tk.Canvas(hud, width=BASE_CANVAS_W, height=BASE_CANVAS_H, bg="#fafafa", highlightthickness=0)
base_canvas.grid(row=1, column=1, sticky="w")

# Inicializar puertos, HUD y loop
refresh_ports()
update_hud()
root.mainloop()

# Cierre limpio
try:
    disconnect_serial()
except Exception:
    pass
