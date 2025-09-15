import os
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from dataclasses import dataclass
from typing import List, Optional

# ---- Imágenes (Pillow opcional para reescalar) ----
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# ---- Serie (pyserial) ----
try:
    import serial
    import serial.tools.list_ports
except Exception:
    serial = None


# ------------------------- DATOS -------------------------
@dataclass
class Posicion:
    m1: int
    m2: int
    m3: int
    m4: int
    mag: int  # 0/1

    def to_list(self):
        return [self.m1, self.m2, self.m3, self.m4, self.mag]

    @staticmethod
    def from_list(lst):
        return Posicion(int(lst[0]), int(lst[1]), int(lst[2]), int(lst[3]), int(lst[4]))


# ------------------------- SERIAL -------------------------
class SerialClient:
    """Cliente serie simple para enviar/recibir líneas ASCII (un solo puerto: brazo + POT)."""
    def __init__(self):
        self.ser: Optional[object] = None

    def ports(self):
        if serial is None:
            return []
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port: str, baud: int = 230400, timeout: float = 0.1):
        if serial is None:
            raise RuntimeError("pyserial no está instalado. Ejecuta: pip install pyserial")
        self.close()
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def send_line(self, text: str):
        if not self.connected:
            return
        if not text.endswith("\n"):
            text += "\n"
        self.ser.write(text.encode("ascii", errors="ignore"))

    def send_set(self, p: Posicion):
        self.send_line(f"SET {p.m1} {p.m2} {p.m3} {p.m4} {p.mag}")

    def send_immediate(self, m1, m2, m3, m4, mag):
        self.send_line(f"SET {m1} {m2} {m3} {m4} {mag}")

    def readline(self) -> Optional[str]:
        if not self.connected:
            return None
        try:
            line = self.ser.readline().decode("ascii", errors="ignore")
            return line if line else None
        except Exception:
            return None


# ------------------------- APP -------------------------
class ArmControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Control de Brazo Robot")
        self.geometry("1180x740")

        # Serie (un solo puerto)
        self.serial_arm = SerialClient()

        # Flags
        self._reader_stop = False
        self._updating_from_telemetry = False
        self.ejecutando = False
        self._recording = False
        self._recording_thread: Optional[threading.Thread] = None

        # Telemetría POT reciente (para indicador)
        self._last_pot_ts = 0.0

        # HOME por defecto
        self.home = Posicion(512, 512, 512, 512, 0)

        # Rutas fijas que me pasaste
        self.assets_dir   = r"D:\UTEC\Semestre_4\PIC_2\SOFTWARE CONTROL\brazo_app\imagenes_recursos"
        self.logo_path    = os.path.join(self.assets_dir, "utec_logo.png")
        self.arm_img_path = os.path.join(self.assets_dir, "brazo.png")

        # Autores
        self.authors = ["Hector Pereira", "Priscila Rossi", "Mateo Lecuna"]

        self._build_ui()

        # Cargar imágenes si existen
        try:
            if os.path.exists(self.logo_path):
                self._cargar_logo(self.logo_path)
        except Exception:
            pass
        try:
            if os.path.exists(self.arm_img_path):
                self._cargar_brazo(self.arm_img_path)
        except Exception:
            pass

    # ---------------- UI ----------------
    def _build_ui(self):
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)

        # ==== Columna izquierda ====
        left = ttk.Frame(self, padding=10)
        left.grid(row=0, column=0, sticky="nsw")
        for i in range(30):
            left.rowconfigure(i, weight=0)

        # --- Conexión brazo (único puerto) ---
        ttk.Label(left, text="Puerto (Brazo / POT)").grid(row=0, column=0, sticky="w")
        self.port_arm_var = tk.StringVar()
        self.port_arm_combo = ttk.Combobox(left, textvariable=self.port_arm_var, width=14, state="readonly")
        self.port_arm_combo["values"] = self.serial_arm.ports()
        self.port_arm_combo.grid(row=1, column=0, sticky="w", pady=(0,4))
        ttk.Button(left, text="Actualizar", command=self._refrescar_puertos).grid(row=1, column=1, padx=5, sticky="w")

        ttk.Label(left, text="Baud").grid(row=2, column=0, sticky="w")
        self.baud_arm_var = tk.IntVar(value=230400)
        ttk.Entry(left, textvariable=self.baud_arm_var, width=14).grid(row=3, column=0, sticky="w", pady=(0,6))

        self.btn_connect_arm = ttk.Button(left, text="Conectar", command=self._toggle_conexion_arm)
        self.btn_connect_arm.grid(row=4, column=0, columnspan=2, sticky="we", pady=4)

        ttk.Separator(left).grid(row=5, column=0, columnspan=2, sticky="we", pady=8)

        # --- Minibrazo por el mismo puerto: Escuchar POT + indicador ---
        pot_row = ttk.Frame(left)
        pot_row.grid(row=6, column=0, columnspan=2, sticky="we")
        self.listen_pot_var = tk.IntVar(value=1)
        ttk.Checkbutton(pot_row, text="Escuchar minibrazo (POT)", variable=self.listen_pot_var)\
            .pack(side="left")
        self.pot_indicator = ttk.Label(pot_row, text="●", foreground="#666666")  # gris por defecto
        self.pot_indicator.pack(side="left", padx=8)

        # Teleop + seguridad
        teleop_frame = ttk.Frame(left)
        teleop_frame.grid(row=7, column=0, columnspan=2, sticky="we", pady=(8,4))
        self.teleop_var = tk.IntVar(value=0)
        ttk.Checkbutton(teleop_frame, text="Teleop ON/OFF (POT → SET)", variable=self.teleop_var)\
            .pack(side="left", padx=(0,10))
        ttk.Button(teleop_frame, text="HOME", command=self._ir_home).pack(side="left", padx=4)
        ttk.Button(teleop_frame, text="Definir HOME", command=self._definir_home).pack(side="left", padx=4)
        ttk.Button(teleop_frame, text="STOP", command=self._stop_seguro).pack(side="left", padx=4)

        ttk.Separator(left).grid(row=8, column=0, columnspan=2, sticky="we", pady=8)

        # --- Grabación continua (toggle) ---
        record_row = ttk.Frame(left)
        record_row.grid(row=9, column=0, columnspan=2, sticky="we", pady=(0,3))
        self.btn_record = ttk.Button(record_row, text="Grabar posición (Iniciar)", command=self._toggle_recording)
        self.btn_record.pack(side="left", fill="x", expand=True)

        ttk.Label(left, text="Intervalo de muestreo (ms)").grid(row=10, column=0, columnspan=2, sticky="w", pady=(6,3))
        self.sample_interval_var = tk.IntVar(value=100)
        ttk.Entry(left, textvariable=self.sample_interval_var, width=14).grid(row=11, column=0, columnspan=2, sticky="we")

        ttk.Button(left, text="Borrar posición", command=self._borrar_posicion).grid(row=12, column=0, columnspan=2, sticky="we", pady=3)
        ttk.Button(left, text="Ejecutar movimientos", command=self._ejecutar_movimientos).grid(row=13, column=0, columnspan=2, sticky="we", pady=3)

        ttk.Label(left, text="Delay entre pasos (ms)").grid(row=14, column=0, columnspan=2, sticky="w", pady=(10,3))
        self.delay_var = tk.IntVar(value=600)
        ttk.Entry(left, textvariable=self.delay_var, width=14).grid(row=15, column=0, columnspan=2, sticky="we")

        ttk.Separator(left).grid(row=16, column=0, columnspan=2, sticky="we", pady=10)

        ttk.Button(left, text="Guardar lista (JSON)", command=self._guardar_json).grid(row=17, column=0, columnspan=2, sticky="we", pady=3)
        ttk.Button(left, text="Cargar lista (JSON)", command=self._cargar_json).grid(row=18, column=0, columnspan=2, sticky="we", pady=3)

        # ==== Centro: imagen + lista ====
        center = ttk.Frame(self, padding=10)
        center.grid(row=0, column=1, sticky="nsew")
        center.columnconfigure(0, weight=1)
        center.rowconfigure(4, weight=1)

        self.arm_canvas = tk.Canvas(center, width=360, height=360, bg="#f4f4f4",
                                    highlightthickness=1, highlightbackground="#888")
        self.arm_canvas.grid(row=0, column=0, pady=5, sticky="n")
        self.arm_canvas.create_text(180, 180, text="IMAGEN DEL BRAZO", font=("Arial", 14))

        ttk.Button(center, text="Cargar imagen del brazo...", command=self._cargar_brazo_dialog)\
            .grid(row=1, column=0, sticky="w")

        ttk.Label(center, text="Posiciones guardadas").grid(row=3, column=0, sticky="w", pady=(10,3))
        list_frame = ttk.Frame(center)
        list_frame.grid(row=4, column=0, sticky="nsew")
        self.lista = tk.Listbox(list_frame, height=12)
        self.lista.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.lista.yview)
        sb.pack(side="right", fill="y")
        self.lista.configure(yscrollcommand=sb.set)

        # ==== Derecha: sliders + electroimán + branding ====
        right = ttk.Frame(self, padding=10)
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(1, weight=1)

        self.sl_vars = [tk.IntVar(value=0) for _ in range(4)]
        labels = ["Motor 1", "Motor 2", "Motor 3", "Motor 4"]
        self.value_labels = []
        for i, lbl in enumerate(labels):
            ttk.Label(right, text=f"{lbl} (0–1023)").grid(row=i*2, column=0, sticky="w")
            s = ttk.Scale(right, from_=0, to=1023, orient="horizontal",
                          variable=self.sl_vars[i], command=lambda _=None, i=i: self._on_slider(i))
            s.grid(row=i*2, column=1, sticky="we", padx=6)
            val = ttk.Label(right, text="0", width=6, anchor="e")
            val.grid(row=i*2, column=2, padx=4)
            self.value_labels.append(val)

        ttk.Separator(right).grid(row=8, column=0, columnspan=3, sticky="we", pady=10)

        self.mag_var = tk.IntVar(value=0)
        ttk.Checkbutton(right, text="Electroimán ON", variable=self.mag_var,
                        command=self._on_change_send).grid(row=9, column=0, columnspan=2, sticky="w")

        self.live_var = tk.IntVar(value=1)
        ttk.Checkbutton(right, text="Enviar en vivo al mover sliders",
                        variable=self.live_var).grid(row=10, column=0, columnspan=2, sticky="w")

        ttk.Separator(right).grid(row=11, column=0, columnspan=3, sticky="we", pady=10)
        brand = ttk.LabelFrame(right, text="Proyecto / Autores")
        brand.grid(row=12, column=0, columnspan=3, sticky="nsew")
        brand.columnconfigure(0, weight=1)

        self._logo_label = tk.Label(brand)
        self._logo_label.grid(row=0, column=0, sticky="n", pady=(8,6))

        self._authors_frame = ttk.Frame(brand)
        self._authors_frame.grid(row=1, column=0, sticky="we", padx=6, pady=(2,10))
        self._refrescar_autores_ui()

        # Indicador de actividad POT (verde si llega en <500ms, gris si no)
        self.after(200, self._pot_indicator_tick)

        # Estado inferior
        self.status = ttk.Label(self, text="Brazo: desconectado", anchor="w")
        self.status.grid(row=99, column=0, columnspan=3, sticky="we", padx=10, pady=5)

    # ---------------- Conexión única ----------------
    def _refrescar_puertos(self):
        self.port_arm_combo["values"] = self.serial_arm.ports()

    def _toggle_conexion_arm(self):
        if self.serial_arm.connected:
            # detener lector y cerrar
            self._reader_stop = True
            self.serial_arm.close()
            self.btn_connect_arm.config(text="Conectar")
            self._set_status()
            return

        port = self.port_arm_var.get()
        if not port:
            messagebox.showwarning("Serie", "Elegí un puerto.")
            return
        try:
            self.serial_arm.connect(port, self.baud_arm_var.get())
            self.btn_connect_arm.config(text="Desconectar")
            self._set_status()
            # arrancar hilo lector (POT y otros mensajes)
            self._reader_stop = False
            threading.Thread(target=self._reader_loop, daemon=True).start()
        except Exception as e:
            messagebox.showerror("Serie", f"No se pudo conectar:\n{e}")

    def _reader_loop(self):
        while self.serial_arm.connected and not self._reader_stop:
            line = self.serial_arm.readline()
            if not line:
                continue
            line = line.strip()
            if not line:
                continue

            # Manejo de POT si está habilitado
            if self.listen_pot_var.get() == 1 and line.startswith("POT"):
                parts = line.split()
                if len(parts) == 5:
                    try:
                        m1, m2, m3, m4 = map(int, parts[1:5])
                    except ValueError:
                        continue

                    # marca actividad
                    self._last_pot_ts = time.time()

                    # Actualiza sliders desde POT (sin eco)
                    def apply_pot():
                        self._updating_from_telemetry = True
                        try:
                            prev_live = self.live_var.get()
                            self.live_var.set(0)
                            self.sl_vars[0].set(m1); self.sl_vars[1].set(m2)
                            self.sl_vars[2].set(m3); self.sl_vars[3].set(m4)
                            for i in range(4):
                                self.value_labels[i].config(text=str(int(self.sl_vars[i].get())))
                            # Teleop → reenviar SET al brazo
                            if self.teleop_var.get() == 1:
                                p = self._pos_actual()
                                p.m1, p.m2, p.m3, p.m4 = m1, m2, m3, m4
                                self.serial_arm.send_set(p)
                            self.live_var.set(prev_live)
                        finally:
                            self._updating_from_telemetry = False
                    self.after(0, apply_pot)

                continue  # POT ya manejado; no lo mostramos en estado

            # Otros mensajes del firmware (si querés mostrarlos, podríamos loguearlos)
            # Por ahora, solo actualizamos un estado corto:
            self.after(0, lambda t=line: self._set_status_text(f"RX: {t}"))

        # si sale del loop
        if not self.serial_arm.connected:
            self.after(0, self._set_status)

    # ---------------- Handlers UI ----------------
    def _on_slider(self, idx: int):
        val = int(self.sl_vars[idx].get())
        self.value_labels[idx].config(text=str(val))
        if self._updating_from_telemetry:
            return
        if self.live_var.get():
            self._on_change_send()

    def _on_change_send(self):
        p = self._pos_actual()
        self.serial_arm.send_immediate(p.m1, p.m2, p.m3, p.m4, p.mag)

    def _pos_actual(self) -> Posicion:
        return Posicion(
            int(self.sl_vars[0].get()),
            int(self.sl_vars[1].get()),
            int(self.sl_vars[2].get()),
            int(self.sl_vars[3].get()),
            int(self.mag_var.get()),
        )

    # ---------------- Grabación continua (toggle) ----------------
    def _toggle_recording(self):
        if not self._recording:
            try:
                interval_ms = max(1, int(self.sample_interval_var.get()))
            except Exception:
                interval_ms = 100
                self.sample_interval_var.set(interval_ms)

            self._recording = True
            self.btn_record.config(text="Detener grabación")
            t = threading.Thread(target=self._recording_loop, args=(interval_ms,), daemon=True)
            self._recording_thread = t
            t.start()
            self._set_status_text("Grabación iniciada.")
        else:
            self._recording = False
            self.btn_record.config(text="Grabar posición (Iniciar)")
            self._set_status_text("Grabación detenida.")

    def _recording_loop(self, interval_ms: int):
        try:
            while self._recording:
                p = self._pos_actual()
                txt = f"{p.m1},{p.m2},{p.m3},{p.m4}, MAG={p.mag}"
                self.after(0, lambda t=txt: self.lista.insert(tk.END, t))
                time.sleep(interval_ms / 1000.0)
        finally:
            self.after(0, lambda: self.btn_record.config(text="Grabar posición (Iniciar)"))

    def _borrar_posicion(self):
        sel = self.lista.curselection()
        if not sel:
            self._set_status_text("Elegí una posición para borrar.")
            return
        self.lista.delete(sel[0])
        self._set_status_text("Posición borrada.")

    def _leer_lista(self) -> List[Posicion]:
        out = []
        for i in range(self.lista.size()):
            txt = self.lista.get(i)
            parts = txt.replace(" MAG=", ",").split(",")
            if len(parts) >= 5:
                out.append(Posicion.from_list(parts[:5]))
        return out

    def _ejecutar_movimientos(self):
        if self.ejecutando:
            self._set_status_text("Ya se está ejecutando.")
            return
        secuencia = self._leer_lista()
        if not secuencia:
            self._set_status_text("No hay posiciones guardadas.")
            return
        delay_ms = max(0, int(self.delay_var.get()))
        self.ejecutando = True
        threading.Thread(target=self._run_sequence, args=(secuencia, delay_ms), daemon=True).start()

    def _run_sequence(self, secuencia: List[Posicion], delay_ms: int):
        try:
            for p in secuencia:
                self._updating_from_telemetry = True
                self.sl_vars[0].set(p.m1); self.sl_vars[1].set(p.m2)
                self.sl_vars[2].set(p.m3); self.sl_vars[3].set(p.m4)
                self.mag_var.set(p.mag)
                for i in range(4):
                    self.value_labels[i].config(text=str(int(self.sl_vars[i].get())))
                self.update_idletasks()
                self._updating_from_telemetry = False

                self.serial_arm.send_set(p)
                time.sleep(delay_ms / 1000.0)
            self._set_status_text("Secuencia finalizada.")
        except Exception as e:
            self._set_status_text(f"Error durante la ejecución: {e}")
        finally:
            self.ejecutando = False

    # ---- HOME / STOP ----
    def _ir_home(self):
        self.teleop_var.set(0)
        self._apply_pose(self.home)
        self.serial_arm.send_set(self.home)
        self._set_status_text("HOME enviado.")

    def _definir_home(self):
        self.home = self._pos_actual()
        self._set_status_text(f"HOME definido: {self.home.m1},{self.home.m2},{self.home.m3},{self.home.m4}, MAG={self.home.mag}")

    def _stop_seguro(self):
        self.teleop_var.set(0)
        self._apply_pose(self.home)
        self.serial_arm.send_set(self.home)
        self._set_status_text("STOP: teleop OFF y HOME enviado.")

    def _apply_pose(self, p: Posicion):
        self._updating_from_telemetry = True
        try:
            self.sl_vars[0].set(p.m1); self.sl_vars[1].set(p.m2)
            self.sl_vars[2].set(p.m3); self.sl_vars[3].set(p.m4)
            self.mag_var.set(p.mag)
            for i in range(4):
                self.value_labels[i].config(text=str(int(self.sl_vars[i].get())))
            self.update_idletasks()
        finally:
            self._updating_from_telemetry = False

    # ---- Guardar / Cargar ----
    def _guardar_json(self):
        data = [p.to_list() for p in self._leer_lista()]
        if not data:
            self._set_status_text("No hay posiciones para guardar.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON","*.json")], initialfile="posiciones.json")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._set_status_text(f"Guardado: {path}")

    def _cargar_json(self):
        path = filedialog.askopenfilename(filetypes=[("JSON","*.json")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.lista.delete(0, tk.END)
            for lst in data:
                p = Posicion.from_list(lst)
                self.lista.insert(tk.END, f"{p.m1},{p.m2},{p.m3},{p.m4}, MAG={p.mag}")
            self._set_status_text(f"Cargado: {path}")
        except Exception as e:
            messagebox.showerror("JSON", f"No se pudo cargar:\n{e}")

    # ---- Branding / Imágenes ----
    def _refrescar_autores_ui(self):
        for w in self._authors_frame.winfo_children():
            w.destroy()
        ttk.Label(self._authors_frame, text="Autores:", font=("Arial", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0,4))
        for i, name in enumerate(self.authors, start=1):
            ttk.Label(self._authors_frame, text=f"• {name}").grid(row=i, column=0, sticky="w")

    def _cargar_logo(self, path: str, max_w: int = 260, max_h: int = 120):
        try:
            if PIL_AVAILABLE:
                img = Image.open(path)
                img.thumbnail((max_w, max_h), Image.LANCZOS)
                self._logo_tk = ImageTk.PhotoImage(img)
            else:
                self._logo_tk = tk.PhotoImage(file=path)
            self._logo_label.configure(image=self._logo_tk)
            self._logo_label.image = self._logo_tk
        except Exception as e:
            messagebox.showerror("Logo", f"No se pudo cargar la imagen:\n{e}")

    def _cargar_brazo_dialog(self):
        path = filedialog.askopenfilename(filetypes=[('Imágenes','*.png;*.jpg;*.jpeg;*.gif;*.bmp')])
        if not path:
            return
        self.arm_img_path = path
        self._cargar_brazo(path)

    def _cargar_brazo(self, path: str, max_w: int = 360, max_h: int = 360):
        try:
            if PIL_AVAILABLE:
                img = Image.open(path)
                img.thumbnail((max_w, max_h), Image.LANCZOS)
                self._arm_img_tk = ImageTk.PhotoImage(img)
            else:
                self._arm_img_tk = tk.PhotoImage(file=path)

            self.arm_canvas.delete("all")
            self.arm_canvas.create_image(max_w // 2, max_h // 2, image=self._arm_img_tk)
            self.arm_canvas.image = self._arm_img_tk
        except Exception as e:
            messagebox.showerror("Imagen del brazo", f"No se pudo cargar '{path}':\n{e}")

    # ---- Indicador POT (actividad reciente) ----
    def _pot_indicator_tick(self):
        recent = (time.time() - self._last_pot_ts) < 0.5
        try:
            if recent:
                self.pot_indicator.configure(foreground="#1e8449")  # verde
            else:
                self.pot_indicator.configure(foreground="#666666")  # gris
        except Exception:
            pass
        self.after(200, self._pot_indicator_tick)

    # ---- Estado ----
    def _set_status(self):
        arm = "conectado" if self.serial_arm.connected else "desconectado"
        self.status.config(text=f"Brazo: {arm}")

    def _set_status_text(self, text: str):
        arm = "conectado" if self.serial_arm.connected else "desconectado"
        self.status.config(text=f"{text}  |  Brazo: {arm}")


if __name__ == "__main__":
    app = ArmControlApp()
    app.mainloop()
