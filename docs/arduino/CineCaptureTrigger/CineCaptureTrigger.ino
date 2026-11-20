/*
  Cine Capture Pro - projector trigger for Arduino-type boards.

  Wiring (open-collector hall sensor such as A3144 / US5881, or a microswitch):
    sensor output -> pin 2      (uses the internal pull-up, so no resistor needed)
    sensor GND    -> GND
    sensor VCC    -> 5V (or 3.3V for 3.3 V boards)

  Every time the magnet on the projector shaft passes the sensor the board
  sends the letter 'T' over USB. In Cine Capture Pro choose
  Trigger input -> "Arduino / serial board" and pick the board's COM port.

  Boards with native USB (Leonardo, Micro, Pro Micro, Raspberry Pi Pico)
  give the lowest and steadiest delay. Boards with a USB-serial chip
  (Uno, Nano) work too; the trigger-delay test absorbs their extra delay.

  Licence: GPL-3.0, part of Cine Capture Pro.
*/

const uint8_t SENSOR_PIN = 2;          // must be an interrupt pin
const unsigned long DEBOUNCE_US = 5000;  // ignore re-triggers within 5 ms

volatile bool pulse = false;
volatile unsigned long lastPulse = 0;

void onSensor() {
  unsigned long now = micros();
  if (now - lastPulse >= DEBOUNCE_US) {
    lastPulse = now;
    pulse = true;
  }
}

void setup() {
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), onSensor, FALLING);
}

void loop() {
  if (pulse) {
    pulse = false;
    Serial.write('T');
    digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
  }
}
