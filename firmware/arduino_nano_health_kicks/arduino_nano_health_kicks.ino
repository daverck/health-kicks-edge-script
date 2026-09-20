#include <Wire.h>

// Adresses et broches
const int MPU_ADDR = 0x68;
const int VIBRO_PIN = 3;

unsigned long vibroStopTime = 0;
bool isVibrating = false;

void setup() {
  // Initialisation de la liaison série et du bus I2C
  Serial.begin(115200);
  Wire.begin();
  Wire.setClock(400000); // Mode I2C rapide (400 kHz)

  // Configuration du pin de vibration
  pinMode(VIBRO_PIN, OUTPUT);
  analogWrite(VIBRO_PIN, 0);

  // 1. Reset logiciel du MPU6050 (Registre 0x6B = 0x80)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x80);
  Wire.endTransmission(true);
  delay(100);

  // 2. Sortie du mode veille + Sélection horloge Auto X-Gyro (Registre 0x6B = 0x01)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0x01);
  Wire.endTransmission(true);
  delay(50);

  // 3. Configuration de la plage de l'accéléromètre à +/- 8g (Registre 0x1C = 0x10)[cite: 1]
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C);
  Wire.write(0x10); 
  Wire.endTransmission(true);

  // 4. Configuration du filtre passe-bas matériel (DLPF à ~21 Hz) (Registre 0x1A = 0x03)
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1A);
  Wire.write(0x03);
  Wire.endTransmission(true);

  delay(100);
}

void loop() {
  // --------------------------------------------------------------------------
  // A. ÉCOUTE ET TRAITEMENT DES COMMANDES SÉRIE DU RASPBERRY PI
  // --------------------------------------------------------------------------
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    
    int intensity = 0;
    int durationMs = 0;
    bool validCmd = false;

    // Format standard : "CMD:VIB:200:500"
    if (cmd.startsWith("CMD:VIB:")) {
      int firstColon = cmd.indexOf(':', 8);
      if (firstColon != -1) {
        intensity = cmd.substring(8, firstColon).toInt();
        durationMs = cmd.substring(firstColon + 1).toInt();
      } else {
        intensity = cmd.substring(8).toInt();
        durationMs = 500; // Durée par défaut si non précisée
      }
      validCmd = true;
    } 
    // Format de fallback : "VIB:200"
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
  // B. GESTION AUTOMATIQUE DE L'ARRÊT DE VIBRATION (Non bloquant)
  // --------------------------------------------------------------------------
  if (isVibrating && millis() >= vibroStopTime) {
    analogWrite(VIBRO_PIN, 0);
    isVibrating = false;
  }

  // --------------------------------------------------------------------------
  // C. LECTURE IMU ET ENVOI AVEC PRÉFIXE DATA:
  // --------------------------------------------------------------------------
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); // Registre de départ : ACCEL_XOUT_H
  Wire.endTransmission(false);

  // Demande de 14 octets (6 Accel, 2 Temp, 6 Gyro)
  if (Wire.requestFrom(MPU_ADDR, 14, true) == 14) {
    int16_t ax = (Wire.read() << 8) | Wire.read();
    int16_t ay = (Wire.read() << 8) | Wire.read();
    int16_t az = (Wire.read() << 8) | Wire.read();
    Wire.read(); Wire.read(); // Octets de température ignorés
    int16_t gx = (Wire.read() << 8) | Wire.read();
    int16_t gy = (Wire.read() << 8) | Wire.read();
    int16_t gz = (Wire.read() << 8) | Wire.read();

    float accelX = ax / 4096.0;
    float accelY = ay / 4096.0;
    float accelZ = az / 4096.0;
    float gyroX  = gx / 131.0;
    float gyroY  = gy / 131.0;
    float gyroZ  = gz / 131.0;

    Serial.print("DATA:{\"ax\":"); Serial.print(accelX, 2);
    Serial.print(",\"ay\":"); Serial.print(accelY, 2);
    Serial.print(",\"az\":"); Serial.print(accelZ, 2);
    Serial.print(",\"gx\":"); Serial.print(gyroX, 1);
    Serial.print(",\"gy\":"); Serial.print(gyroY, 1);
    Serial.print(",\"gz\":"); Serial.print(gyroZ, 1);
    Serial.println("}");
  }

  delay(50);
}