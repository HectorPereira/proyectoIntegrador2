import tkinter as tk
from tkinter import ttk, messagebox
import serial, time
from serial.tools import list_ports

# ===================== Config =====================
BAUD = 115200
SEND_INTERVAL_MS = 40     # frecuencia de envio durante arrastre
POLL_INTERVAL_MS = 150    # frecuencia de consulta Q?

# ===================== Estado global =====================
ser = None
connected = False
angle_var = None
last_sent_deg = {"v": None}
pending_deg = {"v": 90}
send_after_id = {"id": None}
poll_after_id = {"id": None}

# ===================== Serial helpers =====================
def write_line(line: str):
    global ser, connected
    if not connected or ser is None:
        return
    try:
        ser.write((line + "\n").encode("ascii", errors="ignore"))
        ser.flush()
    except Exception as e:
        last_msg_var.set(f"TX error: {e}")

def read_available():
    global ser, connected
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

def parse_and_update(lines: str):
    """Procesa lineas y actualiza S1, Modo y el label de ultimo mensaje."""
    last_line = None
    for line in lines.splitlines():
        line = line.strip()
        if not line:
            continue
        last_line = line
        if line.startswith("ANG="):
            try:
                parts = line.split(",", 1)
                ang_part = parts[0].split("=")[1]
                mode_part = parts[1].split("=")[1] if len(parts) > 1 else ""
                servo_pos_var.set(f"S1: {ang_part} grados")
                mode_var.set(f"Modo: {mode_part}")
            except Exception:
                pass
    if last_line:
        last_msg_var.set(f"Ultimo: {last_line}")

# ===================== UI callbacks =====================
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
        messagebox.showwarning("Puerto", "Selecciona un puerto primero.")
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
        start_polling()
    except Exception as e:
        ser = None
        connected = False
        messagebox.showerror("Error", f"No se pudo abrir {port}\n{e}")

def disconnect_serial():
    global ser, connected
    stop_polling()
    if ser:
        try:
            ser.close()
        except Exception:
            pass
    ser = None
    connected = False
    connect_btn.config(text="Conectar")
    status_var.set("Desconectado")
    last_msg_var.set("Ultimo: -")

def start_polling():
    def _tick():
        if not connected:
            return
        write_line("Q?")
        root.after(20, _read_after_q)
        poll_after_id["id"] = root.after(POLL_INTERVAL_MS, _tick)
    _tick()

def _read_after_q():
    resp = read_available().strip()
    if resp:
        parse_and_update(resp)

def stop_polling():
    if poll_after_id["id"] is not None:
        root.after_cancel(poll_after_id["id"])
        poll_after_id["id"] = None

# ======= Envio continuo mientras se mueve el slider =======
def _send_pending():
    send_after_id["id"] = None
    deg = int(pending_deg["v"])
    if deg != last_sent_deg["v"]:
        write_line(f"S:{deg}")
        last_sent_deg["v"] = deg
        last_msg_var.set(f"Ultimo: > S:{deg}")
    if connected:
        send_after_id["id"] = root.after(SEND_INTERVAL_MS, _send_pending)

def on_slider_change(_val_str=None):
    pending_deg["v"] = angle_var.get()
    if connected and send_after_id["id"] is None:
        send_after_id["id"] = root.after(SEND_INTERVAL_MS, _send_pending)

def on_release(_event=None):
    if send_after_id["id"] is not None:
        root.after_cancel(send_after_id["id"])
        send_after_id["id"] = None
    deg = angle_var.get()
    if deg != last_sent_deg["v"]:
        write_line(f"S:{deg}")
        last_sent_deg["v"] = deg
        last_msg_var.set(f"Ultimo: > S:{deg}")

def set_mode_pot():
    write_line("MODE:POT")

# ===================== UI =====================
root = tk.Tk()
root.title("Control de Servomotor (Arduino UNO)")

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

# Fila 1: Estado conexion + Ultimo mensaje
status_var = tk.StringVar(value="Desconectado")
ttk.Label(main, textvariable=status_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6,0))
last_msg_var = tk.StringVar(value="Ultimo: -")
ttk.Label(main, textvariable=last_msg_var).grid(row=1, column=2, columnspan=2, sticky="e", pady=(6,0))

# Fila 2: Bloques de estado (S1 + Modo)
servo_pos_var = tk.StringVar(value="S1: -grados")
mode_var = tk.StringVar(value="Modo: -")
servo_pos_lbl = ttk.Label(main, textvariable=servo_pos_var, font=("Segoe UI", 12, "bold"))
servo_pos_lbl.grid(row=2, column=0, sticky="w", pady=(10,0))
mode_lbl = ttk.Label(main, textvariable=mode_var, font=("Segoe UI", 12, "bold"))
mode_lbl.grid(row=2, column=1, sticky="w", pady=(10,0))

# Fila 3: Slider y boton de modo
angle_var = tk.IntVar(value=90)
ttk.Label(main, text="Angulo (grados)").grid(row=3, column=0, sticky="w", pady=(12,0))
s = ttk.Scale(main, from_=0, to=180, orient="horizontal",
              variable=angle_var, length=300, command=on_slider_change)
s.grid(row=3, column=1, sticky="ew", padx=8, pady=(12,0))
s.bind("<ButtonRelease-1>", on_release)

ttk.Button(main, text="Modo POT", command=set_mode_pot).grid(row=3, column=2, sticky="w", padx=6, pady=(12,0))

# Inicializar lista de puertos
refresh_ports()

root.mainloop()

# Cierre limpio
try:
    disconnect_serial()
except Exception:
    pass
