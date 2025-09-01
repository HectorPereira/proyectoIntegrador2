import tkinter as tk                            # Interfaz gráfica
import serial.tools.list_ports as serial_ports  # Comunicación serial


root = tk.Tk()

root.geometry("800x500")
root.title("Brazo Robótico Didáctico")

label = tk.Label(root, text="Ventana de prueba", font=("Arial", 20))
label.pack(padx=20, pady=20)

myentry = tk.Entry(root)
myentry.pack()

button = tk.Button(root, text="Enviar", command=lambda: print(myentry.get()))
button.pack()

grid = tk.Grid()

root.mainloop()
