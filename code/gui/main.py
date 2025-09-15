# main.py
# App GUI: Control de brazo (4 DOF) por Serial/Bluetooth
# Organización en 3 columnas. Python 3.13 + Tkinter. Requiere: pip install pyserial

import os
import json
import time
import queue
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# ---- PySerial ----
try:
    import serial
    import serial.tools.list_ports as list_ports
except Exception:
    serial = None
    list_ports = None

APP_TITLE = "App Brazo - 4 DOF"
CONFIG_FILE = "config.json"
DEFAULT_BAUD = 230400
DOF_NAMES = ["Base", "Hombro", "Codo", "Muñeca"]  # 4 DOF
CMD_PREFIX = "J"  # Formato: J,base,hombro,codo,muñeca\n
RATE_LIMIT_MS = 25  # limitador de envío en vivo para no spamear el puerto

def now():
    return datetime.now().strftime("%H:%M:%S")

def clamp_int(v, lo=0, hi=180):
    try:
        n = int(v)
    except:
        n = lo
    return max(lo, min(hi, n))

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

# ---------------- Serial Manager ----------------

class SerialManager:
    def __init__(self, on_line=None, on_status=None):
        self.ser = None
        self.on_line = on_line
        self.on_status = on_status
        self.running = False
        self.reader = None

    def list_ports(self):
        if list_ports is None:
            return []
        return [p.device for p in list_ports.comports()]

    def open(self, port, baud):
        if serial is None:
            raise RuntimeError("PySerial no está instalado. Ejecuta: pip install pyserial")
        self.close()
        try:
            self.ser = serial.Serial(port=port, baudrate=int(baud), timeout=0.05)
            self.running = True
            self.reader = threading.Thread(target=self._reader_loop, daemon=True)
            self.reader.start()
            if self.on_status:
                self.on_status(f"[{now()}] Conectado a {port} @ {baud}\n")
        except Exception as e:
            self.ser = None
            raise

    def _reader_loop(self):
        buf = bytearray()
        while self.running and self.ser and self.ser.is_open:
            try:
                chunk = self.ser.read(256)
                if chunk:
                    buf.extend(chunk)
                    while b"\n" in buf:
                        line, _, rest = buf.partition(b"\n")
                        buf = bytearray(rest)
                        try:
                            text = line.decode("utf-8", errors="replace").strip()
                        except Exception:
                            text = repr(line)
                        if self.on_line:
                            self.on_line(text)
                else:
                    time.sleep(0.01)
            except Exception as e:
                if self.on_status:
                    self.on_status(f"[{now()}] Error de lectura: {e}\n")
                break

    def write_line(self, s: str):
        if not self.ser or not self.ser.is_open:
            raise RuntimeError("Puerto no conectado")
        if not s.endswith("\n"):
            s += "\n"
        self.ser.write(s.encode("utf-8"))

    def close(self):
        self.running = False
        try:
            if self.ser and self.ser.is_open:
                self.ser.flush()
        except Exception:
            pass
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None

# ---------------- App UI ----------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x680")
        self.minsize(1000, 620)

        # Estado
        self.cfg = load_config()
        self.serial = SerialManager(on_line=self.on_serial_line, on_status=self.log_append)
        self.connected = False
        self.send_live = tk.BooleanVar(value=bool(self.cfg.get("send_live", True)))
        self.port = tk.StringVar(value=self.cfg.get("port", ""))
        self.baud = tk.StringVar(value=str(self.cfg.get("baud", DEFAULT_BAUD)))
        self.pose_vars = [tk.IntVar(value=90) for _ in DOF_NAMES]
        self.poses = self.cfg.get("poses", [])  # lista de {"name": str, "vals": [..]}
        self.last_send_ts = 0.0
        self._send_scheduled = False

        # UI
        self._build_styles()
        self._build_layout()

        # Rellenar puertos al inicio
        self.after(300, self.refresh_ports)

        # Recuperar última pose
        last_pose = self.cfg.get("last_pose")
        if isinstance(last_pose, list) and len(last_pose) == len(self.pose_vars):
            for v, n in zip(self.pose_vars, last_pose):
                v.set(clamp_int(n))

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- UI building ----------
    def _build_styles(self):
        style = ttk.Style(self)
        try:
            self.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass
        style.configure("Danger.TButton", foreground="#ffffff")
        style.map("Danger.TButton",
                  background=[("!disabled", "#c0392b"), ("pressed", "#922b21"), ("active", "#a93226")])
        style.configure("Good.TLabel", foreground="#1e8449")
        style.configure("Bad.TLabel", foreground="#cb4335")

    def _build_layout(self):
        root = ttk.Frame(self, padding=8)
        root.pack(fill=tk.BOTH, expand=True)

        # Tres columnas
        left = ttk.Frame(root)
        mid = ttk.Frame(root)
        right = ttk.Frame(root)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=6)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        # -------- Columna 1: Conexión & Estado --------
        conn = ttk.LabelFrame(left, text="Conexión", padding=8)
        conn.pack(fill=tk.X)

        ttk.Label(conn, text="Puerto:").pack(anchor="w")
        self.port_cb = ttk.Combobox(conn, textvariable=self.port, width=22, state="readonly")
        self.port_cb.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(conn, text="Actualizar puertos", command=self.refresh_ports).pack(fill=tk.X, pady=2)

        ttk.Label(conn, text="Baudrate:").pack(anchor="w", pady=(6, 0))
        self.baud_entry = ttk.Entry(conn, textvariable=self.baud, width=10)
        self.baud_entry.pack(fill=tk.X, pady=(0, 8))

        self.state_lbl = ttk.Label(conn, text="🔴 Desconectado", style="Bad.TLabel")
        self.state_lbl.pack(anchor="w", pady=(0, 6))

        self.btn_connect = ttk.Button(conn, text="Conectar", command=self.toggle_connect)
        self.btn_connect.pack(fill=tk.X, pady=2)

        self.chk_live = ttk.Checkbutton(conn, text="Enviar en vivo", variable=self.send_live,
                                        command=self._save_send_live)
        self.chk_live.pack(anchor="w", pady=(8, 0))

        quick = ttk.LabelFrame(left, text="Comandos rápidos", padding=8)
        quick.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(quick, text="HOME", command=self.cmd_home).pack(fill=tk.X, pady=2)
        ttk.Button(quick, text="STOP", style="Danger.TButton", command=self.cmd_stop).pack(fill=tk.X, pady=2)

        fb = ttk.LabelFrame(left, text="Feedback (RX)", padding=8)
        fb.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.fb_vars = [tk.StringVar(value="-") for _ in DOF_NAMES]
        for name, var in zip(DOF_NAMES, self.fb_vars):
            row = ttk.Frame(fb)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"{name}:").pack(side=tk.LEFT)
            ttk.Label(row, textvariable=var).pack(side=tk.LEFT, padx=(6, 0))

        # -------- Columna 2: Sliders (4 DOF) + poses --------
        sliders = ttk.LabelFrame(mid, text="Articulaciones (0–180°)", padding=8)
        sliders.pack(fill=tk.BOTH, expand=True)

        self.slider_widgets = []
        for i, name in enumerate(DOF_NAMES):
            row = ttk.Frame(sliders)
            row.pack(fill=tk.X, pady=10)

            ttk.Label(row, text=f"{name}", width=14).pack(side=tk.LEFT)

            s = ttk.Scale(row, from_=0, to=180, orient=tk.HORIZONTAL,
                          command=lambda _e=None, idx=i: self._on_slider(idx))
            s.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
            s.set(self.pose_vars[i].get())
            self.slider_widgets.append(s)

            val = ttk.Entry(row, width=5, justify="center")
            val.insert(0, str(self.pose_vars[i].get()))
            val.pack(side=tk.LEFT, padx=6)
            val.bind("<Return>", lambda e, idx=i, ent=val: self._on_entry_val(idx, ent))

            btns = ttk.Frame(row)
            btns.pack(side=tk.LEFT)
            ttk.Button(btns, text="-5", width=3, command=lambda idx=i: self._bump(idx, -5)).pack(side=tk.LEFT, padx=1)
            ttk.Button(btns, text="-1", width=3, command=lambda idx=i: self._bump(idx, -1)).pack(side=tk.LEFT, padx=1)
            ttk.Button(btns, text="+1", width=3, command=lambda idx=i: self._bump(idx, +1)).pack(side=tk.LEFT, padx=1)
            ttk.Button(btns, text="+5", width=3, command=lambda idx=i: self._bump(idx, +5)).pack(side=tk.LEFT, padx=1)
            ttk.Button(btns, text="90", width=3, command=lambda idx=i: self._set_center(idx)).pack(side=tk.LEFT, padx=6)

        # Acciones de pose
        pose_box = ttk.LabelFrame(mid, text="Poses", padding=8)
        pose_box.pack(fill=tk.X, pady=(8, 0))
        rowp = ttk.Frame(pose_box)
        rowp.pack(fill=tk.X)
        ttk.Button(rowp, text="Enviar Pose", command=self.send_pose).pack(side=tk.LEFT, padx=2)
        ttk.Button(rowp, text="Guardar Pose", command=self.save_pose_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(rowp, text="Cargar Pose", command=self.load_pose_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(rowp, text="Exportar", command=self.export_poses).pack(side=tk.LEFT, padx=6)
        ttk.Button(rowp, text="Importar", command=self.import_poses).pack(side=tk.LEFT, padx=2)

        # -------- Columna 3: Consola & comando crudo --------
        right_box = ttk.LabelFrame(right, text="Consola", padding=8)
        right_box.pack(fill=tk.BOTH, expand=True)

        self.console = tk.Text(right_box, height=20, wrap="word")
        self.console.pack(fill=tk.BOTH, expand=True)
        self.console.configure(state="disabled")

        tools = ttk.Frame(right_box)
        tools.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(tools, text="Limpiar", command=self.console_clear).pack(side=tk.LEFT, padx=2)
        ttk.Button(tools, text="Guardar log", command=self.console_save).pack(side=tk.LEFT, padx=2)

        raw = ttk.LabelFrame(right, text="Comando crudo (TX)", padding=8)
        raw.pack(fill=tk.X, pady=(8, 0))
        self.raw_entry = ttk.Entry(raw)
        self.raw_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(raw, text="Enviar", command=self.send_raw).pack(side=tk.LEFT)

    # ---------- Helpers UI ----------
    def console_write(self, txt: str):
        self.console.configure(state="normal")
        self.console.insert("end", txt)
        self.console.see("end")
        self.console.configure(state="disabled")

    def log_append(self, line: str):
        self.console_write(line)

    def console_clear(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def console_save(self):
        fn = filedialog.asksaveasfilename(defaultextension=".txt",
                                          filetypes=[("Text", "*.txt"), ("All", "*.*")])
        if not fn:
            return
        content = self.console.get("1.0", "end")
        try:
            with open(fn, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Consola", f"Guardado en:\n{fn}")
        except Exception as e:
            messagebox.showerror("Consola", f"No se pudo guardar:\n{e}")

    # ---------- Puertos ----------
    def refresh_ports(self):
        ports = self.serial.list_ports()
        self.port_cb["values"] = ports
        # autoselect si coincide con último
        if self.port.get() and self.port.get() in ports:
            self.port_cb.set(self.port.get())
        elif ports:
            self.port_cb.set(ports[0])

    def toggle_connect(self):
        if self.connected:
            self.serial.close()
            self.connected = False
            self.state_lbl.config(text="🔴 Desconectado", style="Bad.TLabel")
            self.btn_connect.config(text="Conectar")
            self.log_append(f"[{now()}] Desconectado\n")
            return
        # Conectar
        port = self.port.get().strip()
        baud = self.baud.get().strip()
        if not port:
            messagebox.showwarning("Serial", "Elegí un puerto.")
            return
        try:
            self.serial.open(port, baud)
            self.connected = True
            self.state_lbl.config(text=f"🟢 Conectado ({port})", style="Good.TLabel")
            self.btn_connect.config(text="Desconectar")
            # persistir
            self.cfg["port"] = port
            self.cfg["baud"] = int(baud)
            save_config(self.cfg)
        except Exception as e:
            messagebox.showerror("Serial", f"No se pudo conectar:\n{e}")

    # ---------- RX ----------
    def on_serial_line(self, line: str):
        self.log_append(f"[{now()}] ← {line}\n")
        # Si firmware manda "FB,x,y,z,w" actualiza feedback
        if line.startswith("FB,"):
            parts = line.split(",")
            vals = parts[1:]
            for i in range(min(len(vals), len(self.fb_vars))):
                self.fb_vars[i].set(vals[i])

    # ---------- TX ----------
    def _fmt_pose_cmd(self, vals):
        # J,base,hombro,codo,muñeca\n
        parts = [CMD_PREFIX] + [str(clamp_int(v)) for v in vals]
        return ",".join(parts) + "\n"

    def send_pose(self):
        if not self.connected:
            messagebox.showinfo("TX", "No estás conectado.")
            return
        vals = [v.get() for v in self.pose_vars]
        cmd = self._fmt_pose_cmd(vals).strip()
        try:
            self.serial.write_line(cmd)
            self.log_append(f"[{now()}] → {cmd}\n")
        except Exception as e:
            messagebox.showerror("TX", f"Error al enviar:\n{e}")

    def send_raw(self):
        if not self.connected:
            messagebox.showinfo("TX", "No estás conectado.")
            return
        txt = self.raw_entry.get().strip()
        if not txt:
            return
        try:
            self.serial.write_line(txt)
            self.log_append(f"[{now()}] → {txt}\n")
        except Exception as e:
            messagebox.showerror("TX", f"Error al enviar:\n{e}")

    def cmd_home(self):
        if not self.connected:
            messagebox.showinfo("HOME", "No estás conectado.")
            return
        try:
            self.serial.write_line("H")
            self.log_append(f"[{now()}] → H\n")
        except Exception as e:
            messagebox.showerror("HOME", f"Error:\n{e}")

    def cmd_stop(self):
        if not self.connected:
            messagebox.showinfo("STOP", "No estás conectado.")
            return
        try:
            self.serial.write_line("S")
            self.log_append(f"[{now()}] → S\n")
        except Exception as e:
            messagebox.showerror("STOP", f"Error:\n{e}")

    # ---------- Sliders ----------
    def _on_slider(self, idx: int):
        # sincroniza Entry y Var
        val = int(self.slider_widgets[idx].get())
        self.pose_vars[idx].set(val)
        # actualizar Entry asociado (buscamos el Entry a la derecha del slider)
        row = self.slider_widgets[idx].master
        for child in row.winfo_children():
            if isinstance(child, ttk.Entry):
                child.delete(0, tk.END)
                child.insert(0, str(val))
                break

        if self.send_live.get():
            self._schedule_live_send()

        # guardar última pose (para persistencia)
        self.cfg["last_pose"] = [v.get() for v in self.pose_vars]
        save_config(self.cfg)

    def _on_entry_val(self, idx: int, entry: ttk.Entry):
        v = clamp_int(entry.get())
        self.pose_vars[idx].set(v)
        self.slider_widgets[idx].set(v)
        entry.delete(0, tk.END)
        entry.insert(0, str(v))
        if self.send_live.get():
            self._schedule_live_send()
        self.cfg["last_pose"] = [vv.get() for vv in self.pose_vars]
        save_config(self.cfg)

    def _bump(self, idx: int, delta: int):
        v = clamp_int(self.pose_vars[idx].get() + delta)
        self.pose_vars[idx].set(v)
        self.slider_widgets[idx].set(v)
        # actualizar entry
        row = self.slider_widgets[idx].master
        for child in row.winfo_children():
            if isinstance(child, ttk.Entry):
                child.delete(0, tk.END)
                child.insert(0, str(v))
                break
        if self.send_live.get():
            self._schedule_live_send()
        self.cfg["last_pose"] = [vv.get() for vv in self.pose_vars]
        save_config(self.cfg)

    def _set_center(self, idx: int):
        self.pose_vars[idx].set(90)
        self.slider_widgets[idx].set(90)
        # actualizar entry
        row = self.slider_widgets[idx].master
        for child in row.winfo_children():
            if isinstance(child, ttk.Entry):
                child.delete(0, tk.END)
                child.insert(0, "90")
                break
        if self.send_live.get():
            self._schedule_live_send()
        self.cfg["last_pose"] = [vv.get() for vv in self.pose_vars]
        save_config(self.cfg)

    # rate-limit del envío en vivo
    def _schedule_live_send(self):
        if self._send_scheduled:
            return
        self._send_scheduled = True
        def later():
            try:
                # respetar RATE_LIMIT_MS
                dt = (time.time() - self.last_send_ts) * 1000.0
                if dt < RATE_LIMIT_MS:
                    time.sleep((RATE_LIMIT_MS - dt) / 1000.0)
                if self.connected and self.send_live.get():
                    vals = [v.get() for v in self.pose_vars]
                    cmd = self._fmt_pose_cmd(vals).strip()
                    try:
                        self.serial.write_line(cmd)
                        self.log_append(f"[{now()}] → {cmd}\n")
                        self.last_send_ts = time.time()
                    except Exception as e:
                        self.log_append(f"[{now()}] Error TX en vivo: {e}\n")
            finally:
                self._send_scheduled = False
        threading.Thread(target=later, daemon=True).start()

    # ---------- Poses ----------
    def save_pose_dialog(self):
        name = tk.simpledialog.askstring("Guardar Pose", "Nombre de la pose:")
        if not name:
            return
        vals = [v.get() for v in self.pose_vars]
        self.poses.append({"name": name, "vals": vals})
        self.cfg["poses"] = self.poses
        save_config(self.cfg)
        self.log_append(f"[{now()}] Pose guardada: {name} {vals}\n")

    def load_pose_dialog(self):
        if not self.poses:
            messagebox.showinfo("Cargar Pose", "No hay poses guardadas.")
            return
        # diálogo simple
        win = tk.Toplevel(self)
        win.title("Cargar Pose")
        win.transient(self)
        win.grab_set()
        lb = tk.Listbox(win, height=min(10, len(self.poses)), width=40)
        for i, p in enumerate(self.poses):
            lb.insert("end", f"{i+1:02d} - {p['name']}  {p['vals']}")
        lb.pack(padx=8, pady=8, fill=tk.BOTH, expand=True)

        def do_load():
            sel = lb.curselection()
            if not sel:
                return
            p = self.poses[sel[0]]
            vals = p["vals"]
            for i, v in enumerate(vals[:len(self.pose_vars)]):
                vv = clamp_int(v)
                self.pose_vars[i].set(vv)
                self.slider_widgets[i].set(vv)
            # actualizar entries
            for i in range(len(self.pose_vars)):
                row = self.slider_widgets[i].master
                for child in row.winfo_children():
                    if isinstance(child, ttk.Entry):
                        child.delete(0, tk.END)
                        child.insert(0, str(self.pose_vars[i].get()))
                        break
            if self.send_live.get():
                self._schedule_live_send()
            win.destroy()

        ttk.Button(win, text="Cargar", command=do_load).pack(pady=(0, 8))

    def export_poses(self):
        if not self.poses:
            messagebox.showinfo("Exportar", "No hay poses guardadas.")
            return
        fn = filedialog.asksaveasfilename(defaultextension=".json",
                                          filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not fn:
            return
        try:
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(self.poses, f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Exportar", f"Poses exportadas a:\n{fn}")
        except Exception as e:
            messagebox.showerror("Exportar", f"No se pudo exportar:\n{e}")

    def import_poses(self):
        fn = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All", "*.*")])
        if not fn:
            return
        try:
            with open(fn, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError("Formato inválido (se esperaba lista)")
            # Validar contenido básico
            cleaned = []
            for item in data:
                if isinstance(item, dict) and "name" in item and "vals" in item:
                    cleaned.append({"name": str(item["name"]),
                                    "vals": [clamp_int(v) for v in item["vals"]]})
            self.poses = cleaned
            self.cfg["poses"] = self.poses
            save_config(self.cfg)
            messagebox.showinfo("Importar", f"Se importaron {len(self.poses)} poses.")
        except Exception as e:
            messagebox.showerror("Importar", f"No se pudo importar:\n{e}")

    # ---------- Persistencia ----------
    def _save_send_live(self):
        self.cfg["send_live"] = bool(self.send_live.get())
        save_config(self.cfg)

    # ---------- Cierre ----------
    def on_close(self):
        # guardar último estado
        self.cfg["last_pose"] = [v.get() for v in self.pose_vars]
        self.cfg["port"] = self.port.get()
        try:
            self.cfg["baud"] = int(self.baud.get())
        except Exception:
            self.cfg["baud"] = DEFAULT_BAUD
        save_config(self.cfg)
        try:
            self.serial.close()
        except Exception:
            pass
        self.destroy()

# ---------------- Main ----------------
if __name__ == "__main__":
    app = App()
    app.mainloop()
