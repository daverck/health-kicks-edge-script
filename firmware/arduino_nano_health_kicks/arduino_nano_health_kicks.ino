#include <Wire.h>

// Addresses and pins
const int MPU_ADDR = 0x68;
const int VIBRO_PIN = 3;

unsigned long vibroStopTime = 0;
bool isVibrating = false;

void setup() {
  // Initialize serial communication and I2C bus
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000); // Fast I2C mode (400 kHz)

  // Configure vibration motor pin
  pinMode(VIBRO_PIN, OUTPUT);
  analogWrite(VIBRO_PIN, 0);

  // 1. Software reset of MPU6050 (Register 0x6B = 0x80)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x80);
  Wire.endTransmission(true);
  delay(100);

  // 2. Wake up from sleep mode + Select Auto X-Gyro clock (Register 0x6B = 0x01)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x01);
  Wire.endTransmission(true);
  delay(50);

  // 3. Configure accelerometer full scale range to +/- 8g (Register 0x1C = 0x10)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C);
  Wire.write(0x10); 
  Wire.endTransmission(true);

  // 4. Configure hardware digital low-pass filter (DLPF to ~21 Hz) (Register 0x1A = 0x03)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1A);
  Wire.write(0x03);
  Wire.endTransmission(true);

  delay(100);
}

void loop() {
  // --------------------------------------------------------------------------
  // A. LISTEN AND PROCESS SERIAL COMMANDS FROM RASPBERRY PI
  // --------------------------------------------------------------------------
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    
    int intensity = 0;
    int durationMs = 0;
    bool validCmd = false;

    // Standard format: "CMD:VIB:200:500"
    if (cmd.startsWith("CMD:VIB:")) {
      int firstColon = cmd.indexOf(':', 8);
      if (firstColon != -1) {
        intensity = cmd.substring(8, firstColon).toInt();
        durationMs = cmd.substring(firstColon + 1).toInt();
      } else {
        intensity = cmd.substring(8).toInt();
        durationMs = 500; // Default duration if not specified
      }
      validCmd = true;
    } 
    // Fallback format: "VIB:200"
    else if (cmd.startsWith("VIB:")) {
      intensity = cmd.substring(4).toInt();
      durationMs = 500;
      validCmd = true;
    }

    if (validCmd) {
      intensity = constrain(intensity, 0, 255);
      analogWrite(VIBRO_PIN, intensity);
      
      if (intensity > 0 && durationMs > 0) {
        isVibrating = true;
        vibroStopTime = millis() + durationMs;
      } else {
        isVibrating = false;
      }
      
      Serial.println("ACK:VIB:OK");
    } else if (cmd.length() > 0) {
      Serial.println("ERR:VIB:INVALID");
    }
  }

  // --------------------------------------------------------------------------
  // B. AUTOMATIC HAPTIC SHUTOFF MANAGEMENT (Non-blocking)
  // --------------------------------------------------------------------------
  if (isVibrating && millis() >= vibroStopTime) {
    analogWrite(VIBRO_PIN, 0);
    isVibrating = false;
  }

  // --------------------------------------------------------------------------
  // C. IMU READING AND TRANSMISSION WITH DATA: PREFIX
  // --------------------------------------------------------------------------
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); // Starting register: ACCEL_XOUT_H
  Wire.endTransmission(false);

  // Request 14 bytes (6 Accel, 2 Temp, 6 Gyro)
  if (Wire.requestFrom(MPU_ADDR, 14, true) == 14) {
    int16_t chip_ax = (Wire.read() << 8) | Wire.read();
    int16_t chip_ay = (Wire.read() << 8) | Wire.read();
    int16_t chip_az = (Wire.read() << 8) | Wire.read();
    Wire.read(); Wire.read(); // Temperature bytes ignored
    int16_t chip_gx = (Wire.read() << 8) | Wire.read();
    int16_t chip_gy = (Wire.read() << 8) | Wire.read();
    int16_t chip_gz = (Wire.read() << 8) | Wire.read();

    // Raw register reads from MPU-6050
    // chip_ax: physical vertical axis (+1g at rest)
    // chip_ay: physical longitudinal axis (forward)
    // chip_az: physical lateral axis (inverted, rightward positive)

    // ISB Footwear Coordinate Alignment:
    // X (Anteroposterior / Forward) = +chip_ay
    // Y (Mediolateral / Left)       = -chip_az
    // Z (Vertical / Upward)         = +chip_ax
    float ax_isb = (float)chip_ay / 4096.0f;
    float ay_isb = -(float)chip_az / 4096.0f;
    float az_isb = (float)chip_ax / 4096.0f;

    // Apply identical orthogonal rotation to angular velocity (gyroscope)
    float gx_isb = (float)chip_gy / 131.0f;
    float gy_isb = -(float)chip_gz / 131.0f;
    float gz_isb = (float)chip_gx / 131.0f;

    Serial.print("DATA:{\"ax\":"); Serial.print(ax_isb, 2);
    Serial.print(",\"ay\":"); Serial.print(ay_isb, 2);
    Serial.print(",\"az\":"); Serial.print(az_isb, 2);
    Serial.print(",\"gx\":"); Serial.print(gx_isb, 1);
    Serial.print(",\"gy\":"); Serial.print(gy_isb, 1);
    Serial.print(",\"gz\":"); Serial.print(gz_isb, 1);
    Serial.println("}");
  }

  delay(50);
}