# Vision Audio Satellite

Streame l'audio d'un micro USB vers le serveur [Vision](https://github.com/NeoRed/vision) via TCP.

Conçu pour Jetson Nano, Raspberry Pi, ou tout Linux avec un micro USB.

## Installation (une commande)

```bash
git clone https://github.com/NeoRed/vision-audio-satellite.git
cd vision-audio-satellite
sudo ./install.sh --host 192.168.1.100
```

Options :
- `--host` : IP du serveur Vision (requis)
- `--port` : Port TCP (défaut: 9999)
- `--device` : Device ALSA (auto-détecté si omis)

## Ce que ça fait

1. Capture audio depuis un micro USB via ALSA (16kHz, mono, int16)
2. Envoie les chunks PCM bruts en TCP au serveur Vision
3. Auto-reconnexion en cas de coupure réseau
4. Démarre automatiquement au boot (systemd)

## Specs techniques

| Paramètre | Valeur |
|-----------|--------|
| Sample rate | 16 000 Hz |
| Canaux | 1 (mono) |
| Format | int16 LE (PCM brut) |
| Chunk | 1280 samples (80ms) |
| Latence | ~80ms (capture) + <1ms (TCP LAN) |
| CPU | < 1% |
| RAM | < 20 MB |
| Dépendances | `pyalsaaudio`, `libasound2-dev` |

## Compatibilité

- Debian 11+
- Ubuntu 20.04+
- Raspberry Pi OS (Bullseye+)
- NVIDIA JetPack 5.x / 6.x
- Tout Linux ARM64 ou x86_64 avec ALSA

## Commandes utiles

```bash
# Status
systemctl status vision-satellite

# Logs temps réel
journalctl -u vision-satellite -f

# Restart
sudo systemctl restart vision-satellite

# Lister les micros disponibles
python3 vision_satellite.py --list-devices

# Test manuel (sans systemd)
python3 vision_satellite.py --host 192.168.1.100 --verbose

# Désinstaller
sudo systemctl stop vision-satellite
sudo systemctl disable vision-satellite
sudo rm -rf /opt/vision-satellite /etc/systemd/system/vision-satellite.service
```

## Protocole TCP

1. Connexion TCP au serveur
2. Header 8 octets : `sample_rate` (uint32 LE) + `chunk_size` (uint32 LE)
3. Stream continu de chunks PCM int16 LE (1280 samples = 2560 octets par chunk)

Compatible avec `core/perception/audio_tcp.py` du projet Vision.

## Licence

MIT

## Sécurité

**En production, la connexion DOIT être chiffrée.** L'audio brut sur le réseau est interceptable.

Options :
- **WireGuard tunnel** (recommandé) — les satellites rejoignent le VPN Vision, le TCP reste simple
- **TLS** — chiffrement au niveau applicatif (`ssl.wrap_socket`)

Le chiffrement sera implémenté avant tout déploiement hors LAN de test.
