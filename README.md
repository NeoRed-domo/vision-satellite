# Vision Satellite

Satellite déporté pour le serveur [Vision](https://github.com/NeoRed-domo/vision) — streame **audio**, **vidéo** et événements **Bluetooth / Zigbee / Z-Wave** vers le serveur central, via un canal mTLS chiffré et mutuellement authentifié.

Un satellite = un hardware dédié (Raspberry Pi, Jetson, PC Linux…) avec un ou plusieurs périphériques (micro USB, caméra V4L2, dongle Zigbee, etc.). Il s'annonce au serveur Vision avec ses capabilities réelles détectées au boot.

Compatible **Debian / Ubuntu / Raspberry Pi OS / JetPack**, ARM64 et x86_64.

---

## Installation — 2 commandes

### Étape 1 — Côté Vision (admin)

Dans l'interface admin Vision, ouvre **Satellites** → **+ Nouveau satellite**, donne un nom (ex. "Salon"). Le serveur génère :

- un **QR code** contenant `vision-enroll://IP:9444?token=XXX&fp=YYY&name=Salon&v=1`
- ou l'équivalent en copy-paste

Le token est valide **10 minutes** et **one-shot** (usage unique, non-réutilisable).

### Étape 2 — Sur le satellite

```bash
git clone https://github.com/NeoRed-domo/vision-satellite.git
cd vision-satellite
sudo ./install.sh
```

Le **wizard interactif** (TUI whiptail) te guide :

1. Détection OS
2. Détection hardware (micro, caméra, Bluetooth, Zigbee, Z-Wave) — validation
3. Test connectivité + config WiFi si besoin (`nmcli`)
4. Saisie/paste du QR code d'enrollment
5. Récap + confirmation
6. Génération keypair ECDSA P-256 + POST `/enroll` avec pinning du cert TLS serveur
7. Stockage cert signé dans `/opt/vision-satellite/`
8. Service systemd `vision-satellite` installé

**Mode scripté** (CI / déploiement à distance) :

```bash
sudo ./install.sh --enroll 'vision-enroll://IP:9444?token=XXX&fp=YYY&name=Salon&v=1'
# ou
sudo ./install.sh --yes --enroll '...'
```

---

## Sécurité

### Enrollment
- Token 128-bit CSPRNG, stocké bcrypt-hashé côté serveur, one-shot, TTL 10min
- TLS 1.3 obligatoire côté client, **cert pinning** via SHA-256 du cert serveur embarqué dans le QR (anti-MITM même sur réseau hostile)
- Pas de downgrade possible (satellite et serveur exigent TLS 1.3 min)

### Runtime
- **mTLS** : satellite et serveur s'authentifient mutuellement via certs signés par la CA interne Vision (Ed25519 root + intermediate, ECDSA P-256 device certs)
- Révocation instantanée via CRL serveur (supprime satellite dans admin → cert révoqué → refusé à la prochaine reconnexion)
- Certs TTL 30 jours, rotation automatique 7 jours avant expiry (frame `RENEW_REQUEST` sur le canal mTLS)
- Frames CBOR typées, max 10MB par frame (anti-DoS)

Sources conceptuelles : Matter 1.5 (PASE/CASE), Tailscale auth keys, NIST FIPS 203/204 (roadmap post-quantique v1.1).

---

## Capabilities supportées

| Capability | Détection | Statut |
|---|---|---|
| Audio | `arecord -l` + `/proc/asound/cards` + test live | ✅ v1.0 |
| Vidéo | `v4l2-ctl --all` | ✅ détection ; stream roadmap |
| Bluetooth | `hciconfig` | ✅ détection ; events roadmap |
| Zigbee | `/dev/serial/by-id/*` (Sonoff CC2652P, ConBee II, CC2531, zig-a-zig-ah) | ✅ détection ; events roadmap |
| Z-Wave | `/dev/serial/by-id/*` (Aeotec Z-Stick, Silicon Labs UZB-1, Zooz S700) | ✅ détection ; events roadmap |

Le satellite **redétecte ses capabilities à chaque boot** — débranche le micro, le serveur Vision sera notifié au prochain HELLO.

---

## Spécifications techniques

### Audio
| Paramètre | Valeur |
|---|---|
| Sample rate | Détecté natif (16 kHz préféré, fallback 32/44.1/48 kHz) |
| Canaux | 1 (mono) |
| Format | int16 LE (PCM brut) |
| Chunk | 80 ms (1280 samples @ 16 kHz) |
| Latence | ~80 ms capture + <1 ms mTLS LAN |

Le serveur Vision resample côté pipeline si nécessaire (16 kHz cible).

### Protocole mTLS
- TLS 1.3 client + serveur auth
- Frames binaires : `[length:u32 LE][type:u8][payload]`
- 13 types de frames (HELLO, HELLO_ACK, AUDIO, VIDEO, ZB/ZW/BT events, PING/PONG, RENEW_REQUEST/RESPONSE, ERROR, CONTROL)
- Payload CBOR pour dicts, raw bytes pour audio/vidéo

### Ressources (streaming audio seul)
- CPU : < 2 %
- RAM : < 30 MB
- Disque : ~10 MB install + certs

---

## Compatibilité hardware

| Plateforme | Statut |
|---|---|
| Raspberry Pi 4 / Zero 2W | ✅ recommandé |
| Raspberry Pi 3B+ | ✅ |
| Jetson Orin Nano | ✅ (JetPack 6.x) |
| Jetson Nano (original) | ⚠️ USB mic flaky — cf. [troubleshooting](#jetson-nano-original) |
| x86_64 Linux (Debian/Ubuntu) | ✅ |
| WSL2 | ✅ (dev uniquement — audio via WSLg) |

Python 3.7+ (le wizard nécessite `cryptography`, donc pas Python 3.6).

---

## Commandes utiles

```bash
# Status du service
systemctl status vision-satellite

# Logs temps réel
journalctl -u vision-satellite -f

# Restart
sudo systemctl restart vision-satellite

# Lister les capabilities détectées
python3 -m vision_satellite.main --list-capabilities

# Lancer le runtime en foreground (debug)
python3 -m vision_satellite.main --runtime --verbose

# Ré-enrollment (nouveau cert, ancien révoqué côté serveur)
sudo ./install.sh --reenroll 'vision-enroll://...'

# Désinstaller complètement
sudo systemctl stop vision-satellite
sudo systemctl disable vision-satellite
sudo rm -rf /opt/vision-satellite /etc/systemd/system/vision-satellite.service
sudo systemctl daemon-reload
```

---

## Mise à jour

```bash
cd ~/vision-satellite && git pull \
    && sudo cp -r vision_satellite wizard.py /opt/vision-satellite/ \
    && sudo systemctl restart vision-satellite
```

Si `install.sh` a changé (service systemd, règles udev, nouvelles deps) :

```bash
cd ~/vision-satellite && git pull && sudo ./install.sh --reenroll '<uri>'
```

---

## Structure du projet

```
vision-satellite/
├── install.sh                 # orchestrateur (wizard ou scripté)
├── wizard.py                  # TUI whiptail (9 écrans guidés)
└── vision_satellite/
    ├── main.py                # CLI : --list-capabilities, --enroll, --runtime
    ├── identity.py            # keypair ECDSA P-256 + storage sécurisé
    ├── enrollment.py          # client POST /enroll + TLS cert pinning
    ├── runtime.py             # client mTLS asyncio (HELLO + stream audio + reconnect)
    ├── qr_parse.py            # parser URI vision-enroll://
    ├── frames.py              # codec CBOR (13 types)
    └── capabilities/
        ├── audio.py           # arecord + /proc/asound
        ├── camera.py          # v4l2-ctl
        ├── bluetooth.py       # hciconfig
        ├── zigbee.py          # USB serial (Sonoff, ConBee, CC2531, zig-a-zig-ah)
        └── zwave.py           # USB serial (Aeotec, Silicon Labs, Zooz)
```

**49 tests automatisés** (pytest + mocks subprocess + vrai serveur mTLS local).

---

## Troubleshooting

### Le service ne démarre pas
```bash
journalctl -u vision-satellite -n 50
```

### "CA non initialisée" au POST /enroll
Le serveur Vision n'a pas encore bootstrappé sa CA. Redémarre l'API Vision → vérifie les logs `Vision satellite CA bootstrappée`.

### Fingerprint mismatch
Le QR contient un fingerprint ancien (cert serveur a changé, ou QR d'un autre serveur). Régénère un QR depuis l'admin Vision.

### Token invalide / expiré / utilisé
Le token a un TTL de 10 minutes et est **one-shot**. Régénère un nouveau depuis l'admin Vision.

### Jetson Nano (original)
Le Jetson Nano avec JetPack 4.x a des bugs connus côté USB audio (isochronous ASYNC endpoint). Pour un satellite audio fiable, **préfère un Raspberry Pi 4 ou Zero 2W** — le code tourne tel quel. Le Nano reste excellent pour la **vidéo** (NVENC hardware) et l'**inférence légère** (YOLO nano via TensorRT).

### PulseAudio grab le micro USB
`install.sh` neutralise PulseAudio. Si tu fais une installation manuelle sans passer par le script :
```bash
echo 'autospawn = no' | sudo tee -a /etc/pulse/client.conf
sudo systemctl --global mask pulseaudio.service pulseaudio.socket
sudo pkill -9 -f pulseaudio
```

### USB autosuspend kick le mic après ~10s
`install.sh` le désactive via `/sys/module/usbcore/parameters/autosuspend=-1` + udev rule persistante `/etc/udev/rules.d/90-vision-satellite-usb-audio.rules`.

---

## Roadmap

**v1.1** :
- Streaming vidéo (H.264 via NVENC pour Jetson)
- Events Zigbee/Z-Wave relayés vers Vision (zigbee2mqtt / Z-Wave JS-compatible)
- Post-quantique hybride (ML-KEM-768 + ML-DSA-65 en TLS 1.3)
- WireGuard underlay obligatoire pour les satellites hors-LAN

**v2.0** :
- ESP32 satellite (I2S mic + ESP-TLS)
- Hardware attestation via Secure Element (ATECC608A, OPTIGA Trust M)
- Multi-site avec FIDO Device Onboard (FDO) rendezvous

---

## Licence

MIT
