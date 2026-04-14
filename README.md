# Vision Satellite

Satellite déporté pour le serveur [Vision](https://github.com/NeoRed-domo/vision) : streame des flux (aujourd'hui audio, demain vidéo) vers le serveur via TCP.

Conçu pour **Jetson Nano / Orin**, **Raspberry Pi**, ou tout Linux (ARM64 ou x86_64) avec micro USB.

> **État actuel** : streaming audio uniquement. Le support vidéo (caméra USB / CSI) est prévu et réutilisera la même installation.

---

## Installation rapide (2 commandes)

Sur le satellite (Jetson Nano, Pi, etc.), remplace `192.168.1.100` par l'IP du serveur Vision :

```bash
git clone https://github.com/NeoRed-domo/vision-satellite.git
cd vision-satellite && sudo ./install.sh --host 192.168.1.100
```

C'est tout. Le satellite démarre et se connecte automatiquement.

### Variante une ligne (sans clone)

```bash
curl -sSL https://raw.githubusercontent.com/NeoRed-domo/vision-satellite/main/install.sh | sudo bash -s -- --host 192.168.1.100
```

---

## Options d'installation

| Option | Défaut | Description |
|--------|--------|-------------|
| `--host` | *(requis)* | IP du serveur Vision |
| `--port` | `9999` | Port TCP du streamer Vision |
| `--device` | *(auto)* | Device ALSA (ex: `hw:1,0`) — utile si plusieurs micros |

**Lister les micros disponibles** avant install :
```bash
arecord -l
```

**Exemple avec micro spécifique** :
```bash
sudo ./install.sh --host 192.168.1.100 --device hw:2,0
```

---

## Ce que l'installeur fait

1. Détecte l'OS (Debian / Ubuntu / JetPack / Raspberry Pi OS)
2. Installe les dépendances système (`python3`, `libasound2-dev`, `alsa-utils`)
3. Copie le script dans `/opt/vision-satellite/`
4. Crée un venv Python et installe `pyalsaaudio`
5. Écrit la config dans `/opt/vision-satellite/config.env`
6. Crée et active un service systemd `vision-satellite`
7. Démarre le service et vérifie qu'il tourne

---

## Mise à jour

Pour récupérer la dernière version et relancer le service :

```bash
cd ~/vision-satellite && git pull && sudo cp vision_satellite.py /opt/vision-satellite/ && sudo systemctl restart vision-satellite
```

Si `install.sh` a changé (nouveau service systemd, nouvelle règle udev...), relance l'installation complète pour propager les changements :

```bash
cd ~/vision-satellite && git pull && sudo ./install.sh --host <IP_VISION>
```

---

## Vérifier que ça marche

```bash
# Status du service
systemctl status vision-satellite

# Logs temps réel
journalctl -u vision-satellite -f
```

Tu dois voir des logs du type :
```
Connected to 192.168.1.100:9999
Streaming 16000 Hz mono int16...
```

---

## Specs techniques

| Paramètre | Valeur |
|-----------|--------|
| Sample rate | 16 000 Hz |
| Canaux | 1 (mono) |
| Format | int16 LE (PCM brut) |
| Chunk | 1280 samples (80 ms) |
| Latence | ~80 ms (capture) + <1 ms (TCP LAN) |
| CPU | < 1 % |
| RAM | < 20 MB |
| Dépendances | `pyalsaaudio`, `libasound2-dev` |

---

## Compatibilité

- ✅ NVIDIA Jetson Nano / Orin (JetPack 5.x / 6.x)
- ✅ Raspberry Pi (OS Bullseye ou plus récent)
- ✅ Debian 11+
- ✅ Ubuntu 20.04+
- ✅ Tout Linux ARM64 ou x86_64 avec ALSA

---

## Commandes utiles

```bash
# Restart
sudo systemctl restart vision-satellite

# Stop / Start
sudo systemctl stop vision-satellite
sudo systemctl start vision-satellite

# Désactiver l'auto-démarrage (sans désinstaller)
sudo systemctl disable vision-satellite

# Modifier la config (IP serveur, port, device)
sudo nano /opt/vision-satellite/config.env
sudo systemctl restart vision-satellite

# Test manuel sans systemd (utile pour debug)
sudo systemctl stop vision-satellite
/opt/vision-satellite/venv/bin/python3 /opt/vision-satellite/vision_satellite.py \
    --host 192.168.1.100 --verbose

# Lister les micros
python3 /opt/vision-satellite/vision_satellite.py --list-devices
```

---

## Désinstallation

```bash
sudo systemctl stop vision-satellite
sudo systemctl disable vision-satellite
sudo rm -rf /opt/vision-satellite
sudo rm /etc/systemd/system/vision-satellite.service
sudo systemctl daemon-reload
```

---

## Troubleshooting

### Le service ne démarre pas
```bash
journalctl -u vision-satellite -n 50
```

### "No such device" / erreur ALSA
Vérifie que le micro est détecté :
```bash
arecord -l
```
Puis passe explicitement le device :
```bash
sudo ./install.sh --host <IP> --device hw:<N>,0
```

### Connexion refusée / timeout TCP
- Vérifie que Vision tourne côté serveur : `nc -zv <VISION_IP> 9999`
- Vérifie firewall (`ufw status` côté serveur)
- Ping réseau : `ping <VISION_IP>`

### Jetson Nano — micro USB non reconnu
```bash
sudo usermod -a -G audio $USER
sudo reboot
```

---

## Protocole TCP

1. Connexion TCP au serveur Vision
2. Header de 8 octets : `sample_rate` (uint32 LE) + `chunk_size` (uint32 LE)
3. Stream continu de chunks PCM int16 LE (1280 samples = 2560 octets)

Compatible avec `core/perception/audio_tcp.py` du serveur Vision.

---

## Sécurité

⚠️ **En production, la connexion DOIT être chiffrée.** L'audio brut sur le réseau est interceptable.

Options prévues :
- **WireGuard tunnel** (recommandé) — les satellites rejoignent le VPN Vision, TCP reste simple
- **TLS** — chiffrement applicatif (`ssl.wrap_socket`)

Le chiffrement sera implémenté avant tout déploiement hors LAN de test.

---

## ESP32 (à venir)

Un satellite ESP32 avec micro I2S (INMP441) est en cours de conception — voir `esp32/README.md`.

---

## Licence

MIT
