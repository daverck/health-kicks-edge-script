# AWS IoT Presence & Lifecycle Events Ingestion (Legacy Edge Architecture)

Ce document décrit le mécanisme historique de suivi de présence basé sur les **événements de cycle de vie AWS IoT Core** (`$aws/events/presence/+/+`).

Cette approche sert de solution de secours (fallback) si le mécanisme basé sur le topic de statut (`healthkicks/v1/{device_id}/status`) ne peut pas capturer une déconnexion brutale (ex: coupure d'alimentation de la Raspberry Pi ou crash immédiat du démon Mosquitto sans exécution du LWT).

---

## 1. Principe de Fonctionnement

Contrairement aux messages de présence applicatifs envoyés sur des topics réguliers, AWS IoT Core émet automatiquement des messages sur le topic réservé `$aws/events/presence/{eventType}/{clientId}` à chaque connexion (`connected`) ou déconnexion (`disconnected`) TLS d'un client MQTT (ici, le client distant du pont Mosquitto dont le `remote_clientid` commence par `HK-`).

### Avantages pour le Edge :
* **Détection fiable des pannes brutales** : Si la Raspberry Pi perd sa connexion réseau ou son alimentation, AWS IoT Core détecte l'expiration du keepalive TLS et émet un événement `disconnected` même si le script Python n'a pas pu envoyer son payload d'arrêt.
* **Indépendant de Mosquitto** : Aucune configuration de bridge de topic sortant supplémentaire n'est requise pour le statut.

---

## 2. Règle AWS IoT Core

* **Nom de la règle IoT :** `healthkicks_edge_legacy_presence_rule`
* **Requête SQL :**
```sql
SELECT clientId, eventType, timestamp FROM '$aws/events/presence/+/+' WHERE startswith(clientId, 'HK-')
```

---

## 3. Fonction AWS Lambda (`healthkicks_presence_handler`)

### Variables d'environnement
| Variable | Obligatoire | Valeur par défaut / Exemple | Description |
| :--- | :---: | :--- | :--- |
| `BACKEND_URL` | Oui | `https://healthkicks.duckdns.org:8443/api/v1/internal/device-presence` | Endpoint FastAPI pour la mise à jour du statut. |
| `INGEST_TOKEN` | Oui | `xxxxxxxxxx` | Jeton secret passé dans l'en-tête `X-Ingest-Token`. |

### Code source Python (`lambda_function.py`)
```python
import json
import os
import urllib.request
from datetime import datetime, timezone


def lambda_handler(event, context):
    client_id = event.get("clientId", "")
    event_type = event.get("eventType")  # "connected" ou "disconnected"
    timestamp_ms = event.get("timestamp", 0)

    # Extraction de l'ID du device (ex: "HK-2-edge" -> "HK-2" ou "HK-2" -> "HK-2")
    device_id = client_id.split("-edge")[0] if "-edge" in client_id else client_id
    new_status = "online" if event_type == "connected" else "offline"
    event_time = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).isoformat()

    api_url = os.environ.get(
        "BACKEND_URL",
        "https://healthkicks.duckdns.org:8443/api/v1/internal/device-presence"
    )
    ingest_token = os.environ.get("INGEST_TOKEN", "dev-token")

    payload = {
        "device_id": device_id,
        "status": new_status,
        "timestamp": event_time
    }

    req = urllib.request.Request(
        api_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Ingest-Token": ingest_token,
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            print(f"Updated {device_id} to {new_status} via Backend: {response.status}")
            return {"status": "ok", "device_id": device_id, "state": new_status}
    except Exception as e:
        print(f"Failed to update device status via API: {e}")
        raise e
```
