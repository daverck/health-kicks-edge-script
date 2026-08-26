# HealthKicks Edge Agent

Agent local pour Raspberry Pi : lecture IMU Arduino, détection d'anomalies
IsolationForest et commandes haptiques via Mosquitto local.

## Flux

- `DATA:{"ax":...,"ay":...,"az":...,"gx":...,"gy":...,"gz":...}` depuis l'Arduino est validé puis enrichi avec un header Pydantic.
- La télémétrie normalisée est publiée sur `healthkicks/v1/{device_id}/telemetry/raw`.
- Une anomalie locale déclenche directement `CMD:VIB:255:500\n`, puis un événement QoS 1 sur `healthkicks/v1/{device_id}/events/fall`.
- Les commandes MQTT validées (`intensity` 0-255, `duration_ms` 50-10000) deviennent `CMD:VIB:<intensity>:<duration_ms>\n`.
- Les réponses Arduino sont `ACK:VIB:OK` ou `ERR:VIB:INVALID` et sont journalisées.
- Le statut utilise un LWT offline et un heartbeat online sur `healthkicks/v1/{device_id}/status`.

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

Le fichier `/etc/healthkicks_edge/healthkicks_edge.env` contient l'identité du device,
la connexion MQTT, les topics, le port série et les paramètres IA.
Il est volontairement exclu de Git. Le modèle est disponible dans
`healthkicks_edge.env.example`.

L'utilisateur du service doit avoir accès au port série via le groupe `dialout`.

Les paramètres sont documentés dans `healthkicks_edge.env.example`. Les commandes
ont un TTL par défaut de 2 secondes et le modèle est conservé dans
`/var/lib/healthkicks/model.joblib`.

Consulter les logs avec:

```sh
sudo journalctl -u healthkicks_edge.service -f
```

## Développement avec uv

```sh
uv sync
uv run python main.py
```