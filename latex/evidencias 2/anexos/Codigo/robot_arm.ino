#include <Servo.h>

/* ====== Configuración general ====== */
const uint8_t N_CHANNELS = 4;

/* Potes y servos por canal (ajusta si querés otros pines) */
const uint8_t POT_PINS[N_CHANNELS]   = {A0, A1, A2, A3};
const uint8_t SERVO_PINS[N_CHANNELS] = {9, 10, 11, 6};

/* ====== Calibración (puedes ajustar por canal si hace falta) ====== */
int RAW_MIN[N_CHANNELS] = {0, 0, 0, 0};
int RAW_MAX[N_CHANNELS] = {1020, 1020, 1020, 1020};

/* ====== Límites suaves del ángulo (evita topes mecánicos) ====== */
const int ANGLE_MIN[N_CHANNELS] = {0, 0, 0, 0};
const int ANGLE_MAX[N_CHANNELS] = {180, 180, 180, 180};

/* ====== Filtro EMA (exponencial) en enteros ======
   Alpha ≈ 1/6 es un buen compromiso: menos jitter sin lag excesivo.
*/
const int ALPHA_NUM = 1;
const int ALPHA_DEN = 6;

/* ====== Zona muerta (en grados) para evitar micro-movimientos ====== */
const int DEAD_DEG = 2;

/* ====== Tasas de actualización ====== */
const unsigned long UPDATE_PERIOD_MS = 15;   // ~66 Hz, acorde a servo
const unsigned long SERIAL_PERIOD_MS = 120;  // para no inundar Serial

/* ====== Pulsos del servo (usualmente no hace falta tocar) ====== */
const int SERVO_MIN_US = 544;
const int SERVO_MAX_US = 2400;

/* ====== Estado por canal ====== */
Servo servos[N_CHANNELS];
int32_t ema[N_CHANNELS];        // EMA en escala ADC
int lastAngle[N_CHANNELS];      // último ángulo escrito
int observedMin[N_CHANNELS];    // min observado (ayuda a calibrar)
int observedMax[N_CHANNELS];    // max observado (ayuda a calibrar)

unsigned long tUpdate = 0;
unsigned long tSerial = 0;

/* ---------- Utilidades ---------- */

// Mediana de 3 lecturas en un pin analógico
int readPotMedian3(uint8_t pin) {
  int a = analogRead(pin);
  int b = analogRead(pin);
  int c = analogRead(pin);

  int hi = (a > b) ? a : b;
  int lo = (a < b) ? a : b;
  if (c > hi) return hi;
  if (c < lo) return lo;
  return c;
}

// map con límites y protección división por cero
int mapConstrain(long x, long in_min, long in_max, long out_min, long out_max) {
  if (in_max == in_min) return (int)out_min;
  long y = (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
  if (y < out_min) y = out_min;
  if (y > out_max) y = out_max;
  return (int)y;
}

void setup() {
  Serial.begin(230400);

  for (uint8_t i = 0; i < N_CHANNELS; i++) {
    servos[i].attach(SERVO_PINS[i], SERVO_MIN_US, SERVO_MAX_US);

    int r0 = readPotMedian3(POT_PINS[i]); // inicializa EMA sin saltos
    ema[i] = r0;
    lastAngle[i] = -1000; // fuerza primera escritura

    observedMin[i] = 1020;
    observedMax[i] = 0;
  }

  tUpdate = millis();
  tSerial = millis();
}

void loop() {
  unsigned long now = millis();

  // ====== Control de servos (todos los canales) ======
  if (now - tUpdate >= UPDATE_PERIOD_MS) {
    tUpdate = now;

    for (uint8_t i = 0; i < N_CHANNELS; i++) {
      // 1) Lectura robusta
      int raw = readPotMedian3(POT_PINS[i]);

      // 2) Track min/max observados (para calibrar RAW_MIN/MAX luego)
      if (raw < observedMin[i]) observedMin[i] = raw;
      if (raw > observedMax[i]) observedMax[i] = raw;

      // 3) Filtro EMA entero
      ema[i] += ((int32_t)raw - ema[i]) * ALPHA_NUM / ALPHA_DEN;

      // 4) Mapeo a ángulo con límites suaves
      int angle = mapConstrain(ema[i], RAW_MIN[i], RAW_MAX[i], ANGLE_MIN[i], ANGLE_MAX[i]);

      // 5) Zona muerta y escritura condicional
      int diff = angle - lastAngle[i];
      if (diff < 0) diff = -diff;

      if (diff >= DEAD_DEG) {
        servos[i].write(angle);
        lastAngle[i] = angle;
      }
    }
  }

  // ====== Salida Serial resumida y con throttle ======
  if (now - tSerial >= SERIAL_PERIOD_MS) {
    tSerial = now;

    Serial.print(F("CH | raw  ema  deg  obs[min,max]\n"));
    for (uint8_t i = 0; i < N_CHANNELS; i++) {
      int emaInt = (int)ema[i];
      int angleNow = mapConstrain(emaInt, RAW_MIN[i], RAW_MAX[i], ANGLE_MIN[i], ANGLE_MAX[i]);

      // Lectura rápida directa para mostrar (no filtrada)
      int rawQuick = analogRead(POT_PINS[i]);

      Serial.print(i); Serial.print(F("  | "));
      Serial.print(rawQuick); Serial.print(F("   "));
      Serial.print(emaInt);   Serial.print(F("   "));
      Serial.print(angleNow); Serial.print(F("   ["));
      Serial.print(observedMin[i]); Serial.print(F(","));
      Serial.print(observedMax[i]); Serial.println(F("]"));
    }
    Serial.println();
  }
}
