#include <Servo.h>

Servo s1, s2, s3, s4;

const int potPins[4] = {A0, A1, A2, A3};
const int servoPins[4] = {6, 9, 10, 11};

int potCenter[4];  // store neutral (center) readings

void setup() {
  // Attach servos
  s1.attach(servoPins[0]);
  s2.attach(servoPins[1]);
  s3.attach(servoPins[2]);
  s4.attach(servoPins[3]);

  // --- Calibrate on startup ---
  for (int i = 0; i < 4; i++) {
    potCenter[i] = analogRead(potPins[i]);  // store current positions as neutral
  }

  // Move all servos to 90° initially
  s1.write(90);
  s2.write(90);
  s3.write(90);
  s4.write(90);

  delay(1000);
}

void loop() {
  for (int i = 0; i < 4; i++) {
    int val = analogRead(potPins[i]);
    int delta = val - potCenter[i];  // difference from neutral

    // Map ±512 range around center to ±90° around 90
    int angle = 90 + map(delta, -512, 512, -135, 135);
    angle = constrain(angle, 0, 180);

    switch (i) {
      case 0: s1.write(180-angle); break;
      case 1: s2.write(constrain((180-angle), 30, 150)); break;
      case 2: s3.write(angle); break;
      case 3: s4.write(180-angle); break;
    }
  }

  delay(20);  // smooth update rate (~50 Hz)
}


