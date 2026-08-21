# SmartStride Edge Bridge

Bridge bidirectionnel entre un Arduino en série et Mosquitto local.

## Installation sur Raspberry Pi

```sh
sudo mkdir -p /opt/smartstride-edge
sudo cp edge_bridge.py pyproject.toml uv.lock /opt/smartstride-edge/
sudo uv sync --directory /opt/smartstride-edge --frozen
sudo install -d -m 0750 /etc/smartstride-edge
sudo install -m 0640 edge_bridge.env.example /etc/smartstride-edge/edge_bridge.env
sudoedit /etc/smartstride-edge/edge_bridge.env
sudo cp smartstride-edge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now smartstride-edge.service
```

Le fichier `/etc/smartstride-edge/edge_bridge.env` contient la connexion MQTT
(hôte, port, identifiant et mot de passe éventuels), les topics et le port série.
Il est volontairement exclu de Git. Le modèle est disponible dans
`edge_bridge.env.example`.

L'utilisateur du service doit avoir accès au port série via le groupe `dialout`.

Par défaut, le service utilise `/dev/ttyUSB0`, `115200`, `localhost:1883`,
`healthkicks/telemetry/raw` et `healthkicks/commands/haptic`. Ces valeurs peuvent
être remplacées par les variables `EDGE_SERIAL_DEVICE`, `EDGE_SERIAL_BAUDRATE`,
`EDGE_MQTT_HOST`, `EDGE_MQTT_PORT`, `EDGE_TELEMETRY_TOPIC` et
`EDGE_COMMAND_TOPIC`. Les identifiants MQTT optionnels sont
`EDGE_MQTT_USERNAME` et `EDGE_MQTT_PASSWORD`.

Consulter les logs avec:

```sh
sudo journalctl -u smartstride-edge.service -f
```

## Développement avec uv

```sh
uv sync
uv run python edge_bridge.py
```