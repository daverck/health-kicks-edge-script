# HealthKicks Edge Bridge

Bridge bidirectionnel entre un Arduino en série et Mosquitto local.

## Installation sur Raspberry Pi

```sh
sudo mkdir -p /opt/healthkicks_edge
sudo cp healthkicks_edge.py pyproject.toml uv.lock /opt/healthkicks_edge/
sudo uv sync --directory /opt/healthkicks_edge --frozen
sudo install -d -m 0750 /etc/healthkicks_edge
sudo install -m 0640 healthkicks_edge.env.example /etc/healthkicks_edge/healthkicks_edge.env
sudoedit /etc/healthkicks_edge/healthkicks_edge.env
sudo cp healthkicks_edge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now healthkicks_edge.service
```

Le fichier `/etc/healthkicks_edge/healthkicks_edge.env` contient la connexion MQTT
(hôte, port, identifiant et mot de passe éventuels), les topics et le port série.
Il est volontairement exclu de Git. Le modèle est disponible dans
`healthkicks_edge.env.example`.

L'utilisateur du service doit avoir accès au port série via le groupe `dialout`.

Par défaut, le service utilise `/dev/ttyUSB0`, `115200`, `localhost:1883`,
`healthkicks/telemetry/raw` et `healthkicks/commands/haptic`. Ces valeurs peuvent
être remplacées par les variables `EDGE_SERIAL_DEVICE`, `EDGE_SERIAL_BAUDRATE`,
`EDGE_MQTT_HOST`, `EDGE_MQTT_PORT`, `EDGE_TELEMETRY_TOPIC` et
`EDGE_COMMAND_TOPIC`. Les identifiants MQTT optionnels sont
`EDGE_MQTT_USERNAME` et `EDGE_MQTT_PASSWORD`.

Consulter les logs avec:

```sh
sudo journalctl -u healthkicks_edge.service -f
```

## Développement avec uv

```sh
uv sync
uv run python healthkicks_edge.py
```