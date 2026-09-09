# HealthKicks Edge Agent (`healthkicks_edge`)

Local edge agent for Raspberry Pi: Arduino IMU acquisition, real-time edge AI fall detection inference, and haptic actuator control via local Mosquitto, bridged to AWS IoT Core.

---

## Architecture & Data Flows

- **IMU Serial Telemetry**: Incoming serial frames from Arduino formatted as `DATA:{"ax":...,"ay":...,"az":...,"gx":...,"gy":...,"gz":...}` are parsed, validated, and normalized with a Pydantic header.
- **Continuous Local Ingestion**: Telemetry readings continuously feed a sliding FIFO memory buffer on the Raspberry Pi for real-time edge ML fall detection inference.
- **Real-Time Fall Detection (`activity_classifier.py` & `features.py`)**:
  - A background inference worker periodically evaluates the rolling IMU window (every 250 ms by default).
  - Computes 16 biomechanical features (acceleration and gyroscope magnitudes, dispersion, dynamic energy) via NumPy.
  - Runs the trained `activity_classifier.joblib` classifier.
  - When an event of type `fall_*` is predicted with confidence $\ge 0.65$ outside the cooldown window:
    - Publishes a QoS 1 detection alert to `healthkicks/v1/{device_id}/events/detection`.
    - Triggers emergency haptic pulses on the Arduino (`CMD:VIB:255:500\n`).
    - Enforces a 5.0-second cooldown (debouncing) to prevent MQTT event spam for a single fall incident.
- **Haptic Actuation**: Incoming MQTT haptic commands (`intensity` 0–255, `duration_ms` 50–10000) are converted to serial frames: `CMD:VIB:<intensity>:<duration_ms>\n`.
- **Bidirectional Acknowledgment**: Arduino telemetry frames use the `DATA:` prefix. Firmware acknowledgments (`ACK:VIB:OK` and `ERR:VIB:INVALID`) are logged and forwarded to `healthkicks/v1/{device_id}/commands/ack`.
- **Device Status & LWT**: Heartbeat messages are periodically published to `healthkicks/v1/{device_id}/status` with an automatic Last Will and Testament (LWT) ensuring offline state reporting upon disconnection.
- **Studio Capture Mode**: On-demand IMU recording sessions triggered remotely from the Cloud or locally:
  1. A sensory haptic countdown (3 alert pulses: 150 ms ON / 350 ms OFF) is played via a dedicated background thread without interrupting serial sensor reading.
  2. Any pre-existing nominal telemetry is cleared, and inference is paused during the capture window.
  3. A timed IMU capture window (default 5.0 seconds) records readings tagged with `session_id` and `label`.
  4. At window close, the batch is immediately flushed to `healthkicks/v1/{device_id}/telemetry/raw` with metadata trigger `"studio"`.
- **Continuous Telemetry Flag (`EDGE_CONTINUOUSLY_SEND_TELEMETRY`)**:
  - `false` (default): Nominal periodic flushes only recycle local staging memory without publishing to AWS IoT Core, conserving network bandwidth. Only explicit Studio capture sessions are sent to the Cloud.
  - `true`: All nominal periodic telemetry batches are forwarded to AWS IoT Core in real time.
- **AWS IoT Core Bridge**: A local Mosquitto bridge (`aws-iot-bridge`) securely forwards telemetry batches and detection events to AWS IoT Core and subscribes to incoming commands using TLS mutual authentication.

---

## Debian Package Build

Build the package on Debian or Raspberry Pi OS with standard packaging utilities:

```sh
sudo apt install dpkg-dev debhelper
./build-deb.sh
```

The resulting package is written to the parent directory: `../healthkicks-edge_0.1.0_all.deb`.

The package relies on Debian system Python packages (`python3-paho-mqtt`, `python3-serial`, `python3-pydantic`, `python3-sklearn`, `python3-joblib`, `python3-numpy`), `adduser`, and `mosquitto`. The trained model artifact is bundled in `models/activity_classifier.joblib` and automatically packaged to `/opt/healthkicks_edge/models/activity_classifier.joblib`.

---

## Installation on Raspberry Pi

### 1. Set the Device Identifier

Define the hardware device identifier (`EDGE_DEVICE_ID`) prior to package installation. The `postinst` script uses this variable to automatically configure `/etc/healthkicks_edge/healthkicks_edge.env` and parameterize the Mosquitto bridge topic routing rules in `/etc/mosquitto/conf.d/aws-bridge.conf`:

```sh
# Define the hardware device identifier (e.g. HK-1, HK-2, etc.)
export EDGE_DEVICE_ID="HK-1"

# Install the Debian package (-E preserves environment variables for postinst)
sudo -E apt install ./healthkicks-edge_0.1.0_all.deb

# Or alternatively using dpkg:
# sudo EDGE_DEVICE_ID="HK-1" dpkg -i healthkicks-edge_0.1.0_all.deb
```

### 2. Configuration & Service Management

Edit the environment file if custom adjustments (such as serial port or broker credentials) are required:

```sh
sudoedit /etc/healthkicks_edge/healthkicks_edge.env
sudo systemctl restart healthkicks_edge.service
```

The `/etc/healthkicks_edge/healthkicks_edge.env` configuration file controls device identity, MQTT connection parameters, topics, serial port settings, buffer intervals, model path, and detection thresholds:
- `EDGE_MODEL_PATH`: Path to the pre-trained fall detection artifact (default: `/opt/healthkicks_edge/models/activity_classifier.joblib`). If missing, inference is disabled gracefully without failing the service.
- `EDGE_DETECTION_TOPIC`: MQTT topic for fall alerts (default: `healthkicks/v1/{device_id}/events/detection`).
- `EDGE_INFERENCE_INTERVAL_SEC`: Evaluation frequency in seconds (default: `0.25`).
- `EDGE_CONFIDENCE_THRESHOLD`: Minimum model probability for triggering an alert (default: `0.65`).
- `EDGE_DETECTION_COOLDOWN_SEC`: Cooldown in seconds before a new alert can be emitted (default: `5.0`).
- `EDGE_MIN_FALL_IMPACT_THRESHOLD`: Minimum peak acceleration impact in m/s² required to confirm a fall (default: `18.0`). Prevents false positive alerts at rest.

Monitor live service logs:

```sh
sudo journalctl -u healthkicks_edge.service -f
```

---

## Mosquitto Bridge to AWS IoT Core

### 1. Certificates and ATS Endpoint

In the AWS IoT Core console, create a Thing and use the **Connect Device** workflow to generate credentials and download the connection kit (ZIP). The archive contains:

- `AmazonRootCA1.pem` — Amazon Root CA certificate;
- `device.pem.crt` — Device certificate;
- `private.pem.key` — Private key;
- `start.sh` — Contains your account's unique **ATS endpoint** (`-h <xxx-ats.iot.eu-north-1.amazonaws.com>`). Note this endpoint URL for the bridge configuration.

Install the certificate files into the Mosquitto certificate directory:

```sh
sudo install -d -o mosquitto -g mosquitto -m 0700 /etc/mosquitto/certs
sudo cp AmazonRootCA1.pem device.pem.crt private.pem.key /etc/mosquitto/certs/
sudo chown mosquitto:mosquitto /etc/mosquitto/certs/*
sudo chmod 600 /etc/mosquitto/certs/AmazonRootCA1.pem \
               /etc/mosquitto/certs/device.pem.crt \
               /etc/mosquitto/certs/private.pem.key
```

### 2. AWS IoT Core IAM Policy (Least Privilege)

Attach the following policy to the device certificate, ensuring `HK-1` matches your configured `EDGE_DEVICE_ID`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["iot:Connect"],
      "Resource": ["arn:aws:iot:eu-north-1:693906847467:client/HK-1"]
    },
    {
      "Effect": "Allow",
      "Action": ["iot:Publish", "iot:Receive"],
      "Resource": [
        "arn:aws:iot:eu-north-1:693906847467:topic/healthkicks/v1/HK-1/telemetry/raw",
        "arn:aws:iot:eu-north-1:693906847467:topic/healthkicks/v1/HK-1/events/detection",
        "arn:aws:iot:eu-north-1:693906847467:topic/healthkicks/v1/HK-1/commands/haptic",
        "arn:aws:iot:eu-north-1:693906847467:topic/healthkicks/v1/HK-1/commands/studio/start"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["iot:Subscribe"],
      "Resource": [
        "arn:aws:iot:eu-north-1:693906847467:topicfilter/healthkicks/v1/HK-1/commands/haptic",
        "arn:aws:iot:eu-north-1:693906847467:topicfilter/healthkicks/v1/HK-1/commands/studio/start"
      ]
    }
  ]
}
```

### 3. Mosquitto Bridge Configuration

The package installs `/etc/mosquitto/conf.d/aws-bridge.conf` (managed as a `conffile`). Ensure the `address` directive points to your ATS endpoint on port `8883`:

```ini
address a2k10w7ebf2tx9-ats.iot.eu-north-1.amazonaws.com:8883
```

Verify syntax and restart Mosquitto:

```sh
sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v   # Syntax test (Ctrl+C to exit)
sudo systemctl restart mosquitto
mosquitto_sub -t '$SYS/broker/bridge/+/connected' -v   # 1 = bridge connected
```

---

## Local Studio Testing CLI

Test the Studio acquisition sequence directly on the Raspberry Pi without requiring Cloud connectivity:

```sh
# Run with live Arduino hardware
uv run python -m scripts.test_studio_local --label walk --duration 5.0

# Run in simulation mode (synthetic IMU stream, mock haptics)
uv run python -m scripts.test_studio_local --simulate --label sprint --duration 3.0
```

CLI options:
- `--label`: Activity label (e.g. `walk`, `run`, `fall`, `stairs`).
- `--duration`: Capture duration in seconds (1.0 to 30.0).
- `--session-id`: Unique UUID session identifier.
- `--pulse-count`: Number of alert countdown vibration pulses (default: 3).
- `--pulse-duration-ms`: Pulse ON duration in milliseconds (default: 150).
- `--pulse-pause-ms`: Pulse OFF interval in milliseconds (default: 350).
- `--pulse-intensity`: Haptic intensity PWM 50–255 (default: 180).
- `--simulate`: Emits synthetic 50 Hz IMU telemetry.

---

## Development

```sh
# Install Python dependencies
uv sync

# Run the edge agent directly
uv run python main.py

# Execute the test suite
uv run pytest
```
