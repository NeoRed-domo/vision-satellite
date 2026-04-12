# ESP32 Audio Satellite

Firmware ESP32 pour micro I2S (INMP441, SPH0645, etc.) → TCP vers Vision.

## TODO

- Firmware Arduino/ESP-IDF : capture I2S 16kHz mono → WiFi TCP
- Même protocole que le satellite Linux (header 8 octets + PCM int16)
- Config WiFi + IP serveur via portail captif ou fichier
- OTA updates

## Hardware supporté (cible)

| Micro | Interface | Notes |
|-------|-----------|-------|
| INMP441 | I2S | Le plus courant, bon rapport qualité/prix |
| SPH0645 | I2S | Bonne sensibilité |
| MSM261S4030H0 | I2S | Large bande passante |
| PDM MEMS | PDM→I2S | Via ESP32-S3 natif |

## Câblage INMP441 → ESP32

```
INMP441    ESP32
VDD    →   3.3V
GND    →   GND
SD     →   GPIO 32 (data)
WS     →   GPIO 25 (word select / LRCK)
SCK    →   GPIO 33 (clock)
L/R    →   GND (mono left)
```
