# HealthKicks Edge Agent (`healthkicks_edge`)

Agent local pour Raspberry Pi : lecture IMU Arduino, détection d'anomalies
IsolationForest et commandes haptiques via Mosquitto local, avec un pont MQTT
vers AWS IoT Core.

## Flux

- Une ligne IMU au format `DATA:{"ax":...,"ay":...,"az":...,"gx":...,"gy":...,"gz":...}` depuis l'Arduino est validée puis enrichie avec un header Pydantic.
- La télémétrie normalisée est publiée sur `healthkicks/v1/{device_id}/telemetry/raw`.
- Une anomalie locale déclenche directement `CMD:VIB:255:500\n`, puis un événement QoS 1 sur `healthkicks/v1/{device_id}/events/fall`.
- Les commandes MQTT validées (`intensity` 0-255, `duration_ms` 50-10000) deviennent `CMD:VIB:<intensity>:<duration_ms>\n`.
- Les lignes de télémétrie Arduino utilisent le préfixe `DATA:`. Les réponses `ACK:VIB:OK` et `ERR:VIB:INVALID` sont journalisées et publiées sur le topic d'ACK.
- Le statut utilise un LWT offline et un heartbeat online sur `healthkicks/v1/{device_id}/status`.
- Le pont Mosquitto `aws-iot-bridge` relaye la télémétrie vers AWS IoT Core et les commandes haptiques vers le broker local.

## Construction du paquet Debian

La construction s'effectue sur Debian ou Raspberry Pi OS, avec `dpkg-deb` installé :

```sh
sudo apt install dpkg-dev debhelper
VERSION=0.1.0 ./build-deb.sh
```

Le fichier produit est `../healthkicks-edge_0.1.0_all.deb` (`dpkg-buildpackage` écrit un niveau au-dessus du dépôt). Le paquet utilise les
dépendances Python Debian (`python3-paho-mqtt`, `python3-serial`,
`python3-pydantic`, `python3-sklearn`, `python3-joblib`) et `mosquitto`.

## Installation sur Raspberry Pi

```sh
sudo apt install ./healthkicks-edge_0.1.0_all.deb
sudoedit /etc/healthkicks_edge/agent.env
sudo systemctl restart healthkicks_edge.service
```

Le fichier `/etc/healthkicks_edge/agent.env` contient l'identité du device,
la connexion MQTT, les topics, le port série et les paramètres IA. Il est
volontairement exclu de Git ; le modèle d'exemple est `healthkicks_edge.env.example`.
L'utilisateur `healthkicks_edge` est ajouté au groupe `dialout` pour l'accès au
port série. Les commandes ont un TTL par défaut de 2 secondes et le modèle est
conservé dans `/var/lib/healthkicks/model.joblib`.

Consulter les logs avec :

```sh
sudo journalctl -u healthkicks_edge.service -f
```

## Pont Mosquitto vers AWS IoT Core

### 1. Certificats et endpoint ATS

Dans la console AWS IoT Core, créez un objet ("thing") puis utilisez l'assistant
**Connect Device** : il génère le certificat, la clé privée et propose de
télécharger un **kit de démarrage** (ZIP Linux/macOS). Ce ZIP contient :

- `AmazonRootCA1.pem` — CA racine Amazon ;
- `device.pem.crt` — certificat du device ;
- `private.pem.key` — clé privée ;
- un script `start.sh` dans lequel figure **l'endpoint ATS unique** de votre
  compte (option `-h <xxx-ats.iot.eu-north-1.amazonaws.com>` / variable
  `ENDPOINT`). Copiez cette valeur : c'est elle qui alimentera la configuration
  du pont.

Placez ensuite les trois fichiers dans le dossier des certificats :

```sh
sudo install -d -o mosquitto -g mosquitto -m 0700 /etc/mosquitto/certs
sudo cp AmazonRootCA1.pem device.pem.crt private.pem.key /etc/mosquitto/certs/
sudo chown mosquitto:mosquitto /etc/mosquitto/certs/*
sudo chmod 600 /etc/mosquitto/certs/AmazonRootCA1.pem \
               /etc/mosquitto/certs/device.pem.crt \
               /etc/mosquitto/certs/private.pem.key
```

### 2. Politique IAM AWS IoT Core (moindre privilège)

Attachez au certificat la policy suivante : connexion limitée au client id
`healthkicks-edge`, publication sur le topic de télémétrie et
réception/abonnement uniquement sur le topic de commandes haptiques.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["iot:Connect"],
      "Resource": ["arn:aws:iot:eu-north-1:693906847467:client/healthkicks-edge"]
    },
    {
      "Effect": "Allow",
      "Action": ["iot:Publish", "iot:Receive"],
      "Resource": [
        "arn:aws:iot:eu-north-1:693906847467:topic/healthkicks/v1/healthkicks-pi-001/telemetry/raw",
        "arn:aws:iot:eu-north-1:693906847467:topic/healthkicks/v1/healthkicks-pi-001/commands/haptic"
      ]
    },
    {
      "Effect": "Allow",
      "Action": ["iot:Subscribe"],
      "Resource": ["arn:aws:iot:eu-north-1:693906847467:topicfilter/healthkicks/v1/healthkicks-pi-001/commands/haptic"]
    }
  ]
}
```

### 3. Configuration du pont

Éditez `/etc/mosquitto/conf.d/aws-bridge.conf` (installé par le paquet et
déclaré `conffile`) et remplacez le placeholder `<YOUR_AWS_ENDPOINT_ATS>` par
l'endpoint ATS récupéré dans `start.sh` :

```ini
address <YOUR_AWS_ENDPOINT_ATS>.iot.eu-north-1.amazonaws.com:8883
```

Puis vérifiez et rechargez :

```sh
sudo mosquitto -c /etc/mosquitto/mosquitto.conf -v   # test de syntaxe (Ctrl+C)
sudo systemctl restart mosquitto
mosquitto_sub -t '$SYS/broker/bridge/+/connected' -v   # 1 = pont connecté
```

## Développement avec uv

```sh
uv sync
uv run python main.py
```
