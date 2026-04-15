"""Runtime client mTLS : connecte à Vision, stream audio via arecord."""
from __future__ import annotations

import asyncio
import logging
import ssl
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from vision_satellite.frames import Frame, FrameType, encode, read_frame

log = logging.getLogger("vision.satellite.runtime")

RECONNECT_DELAY_S = 2
MAX_RECONNECT_DELAY_S = 30
PING_INTERVAL_S = 30


class SatelliteRuntimeClient:
    def __init__(
        self,
        *,
        runtime_uri: str,
        device_cert_path: Path,
        device_key_path: Path,
        vision_ca_path: Path,
        satellite_id: str,
        capabilities: dict,
        satellite_version: str,
        audio_cmd: Optional[list[str]] = None,
    ):
        self.runtime_uri = runtime_uri
        self.device_cert_path = Path(device_cert_path)
        self.device_key_path = Path(device_key_path)
        self.vision_ca_path = Path(vision_ca_path)
        self.satellite_id = satellite_id
        self.capabilities = capabilities
        self.satellite_version = satellite_version
        self.audio_cmd = audio_cmd  # ex: ["arecord", "-D", "hw:2,0", "-r", "16000", ...]
        self._stop = False

    def _parse_uri(self) -> tuple[str, int]:
        parsed = urlparse(self.runtime_uri)
        if parsed.scheme != "mtls":
            raise ValueError(f"scheme mtls attendu, got {parsed.scheme}")
        return parsed.hostname, parsed.port or 9444

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context(cafile=str(self.vision_ca_path))
        ctx.load_cert_chain(
            certfile=str(self.device_cert_path),
            keyfile=str(self.device_key_path),
        )
        # On vérifie le cert serveur via la CA Vision (pas besoin de check_hostname
        # strict puisque l'identité serveur est prouvée cryptographiquement)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        return ctx

    async def run(self) -> None:
        """Boucle principale : connect → HELLO → stream → reconnect on error."""
        delay = RECONNECT_DELAY_S
        while not self._stop:
            try:
                await self._session()
                delay = RECONNECT_DELAY_S  # reset backoff on clean exit
            except ConnectionRefusedError:
                log.warning("connexion refusée — retry dans %ds", delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_S)
            except Exception as exc:
                log.warning("session error (%s), retry dans %ds", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY_S)

    def stop(self) -> None:
        self._stop = True

    async def _session(self) -> None:
        host, port = self._parse_uri()
        ctx = self._ssl_context()
        reader, writer = await asyncio.open_connection(
            host=host, port=port, ssl=ctx,
            server_hostname=host,  # pour SNI
        )
        log.info("connecté à %s:%d", host, port)
        try:
            # HELLO
            hello = Frame(type=FrameType.HELLO, payload={
                "satellite_id": self.satellite_id,
                "capabilities": self.capabilities,
                "satellite_version": self.satellite_version,
            })
            writer.write(encode(hello))
            await writer.drain()

            ack = await asyncio.wait_for(read_frame(reader), timeout=5)
            if ack.type != FrameType.HELLO_ACK:
                raise RuntimeError(f"got {ack.type.name} instead of HELLO_ACK")
            accepted = ack.payload.get("accepted_capabilities", [])
            log.info("HELLO_ACK — accepted: %s", accepted)

            # Lance les tâches concurrentes : audio streaming + read loop + ping
            tasks = [asyncio.create_task(self._read_loop(reader))]
            if "audio" in accepted and self.audio_cmd:
                tasks.append(asyncio.create_task(self._audio_loop(writer)))
            tasks.append(asyncio.create_task(self._ping_loop(writer)))

            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        """Lit les frames entrantes (PONG, CONTROL, etc.)."""
        while not self._stop:
            try:
                frame = await read_frame(reader)
            except asyncio.IncompleteReadError:
                log.info("serveur a fermé la connexion")
                return
            if frame.type == FrameType.PONG:
                log.debug("PONG reçu")
            elif frame.type == FrameType.CONTROL:
                log.info("CONTROL frame: %s", frame.payload)
            elif frame.type == FrameType.ERROR:
                log.warning("ERROR: %s", frame.payload)
            else:
                log.debug("frame inattendue: %s", frame.type.name)

    async def _audio_loop(self, writer: asyncio.StreamWriter) -> None:
        """Lance arecord subprocess et forward chaque chunk en AUDIO_FRAME."""
        if not self.audio_cmd:
            return
        proc = await asyncio.create_subprocess_exec(
            *self.audio_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        chunk_size = 1280 * 2  # 80ms @ 16kHz int16 par défaut
        try:
            while not self._stop:
                data = await proc.stdout.read(chunk_size)
                if not data:
                    log.warning("arecord stdout EOF")
                    return
                frame = Frame(type=FrameType.AUDIO_FRAME, payload=data)
                writer.write(encode(frame))
                try:
                    await writer.drain()
                except (BrokenPipeError, ConnectionResetError):
                    return
        finally:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

    async def _ping_loop(self, writer: asyncio.StreamWriter) -> None:
        while not self._stop:
            await asyncio.sleep(PING_INTERVAL_S)
            try:
                writer.write(encode(Frame(type=FrameType.PING, payload={})))
                await writer.drain()
            except Exception:
                return
