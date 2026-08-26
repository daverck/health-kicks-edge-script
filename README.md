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

## Construction du paquet Debian

La construction s'effectue sur Debian ou Raspberry Pi OS, avec `dpkg-deb` installé :

```sh
sudo apt install dpkg-dev
VERSION=0.1.0 ./build-deb.sh
```

Le fichier produit est `healthkicks-edge-agent_0.1.0_all.deb`. Le paquet utilise
les dépendances Python Debian (`python3-paho-mqtt`, `python3-serial`,
`python3-pydantic`, `python3-sklearn` et `python3-joblib`) et installe le code
dans `/opt/healthkicks-edge-agent`.

## Installation sur Raspberry Pi

```sh
sudo apt install ./healthkicks-edge-agent_0.1.0_all.deb
sudoedit /etc/healthkicks-edge-agent/agent.env
sudo systemctl restart healthkicks-edge.service
```

Le fichier `/etc/healthkicks-edge-agent/agent.env` contient l'identité du device,
la connexion MQTT, les topics, le port série et les paramètres IA.
Il est volontairement exclu de Git. Le modèle est disponible dans
`healthkicks_edge.env.example`.

L'utilisateur du service doit avoir accès au port série via le groupe `dialout`.

Les paramètres sont documentés dans `debian/agent.env.example`. Les commandes
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