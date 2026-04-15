from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from vision_satellite import frames
from vision_satellite.frames import Frame, FrameType, encode, read_frame
from vision_satellite.runtime import SatelliteRuntimeClient


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def mini_ca(tmp_path: Path) -> dict:
    """Crée mini CA self-signed + server cert + client cert pour tests."""
    import datetime as _dt
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    ca_dir = tmp_path / "ca"
    ca_dir.mkdir()

    # Root Ed25519
    root_key = ed25519.Ed25519PrivateKey.generate()
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name).issuer_name(root_name).public_key(root_key.public_key())
        .serial_number(1).not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(root_key, algorithm=None)
    )
    ca_pem = root_cert.public_bytes(serialization.Encoding.PEM)
    (ca_dir / "vision-ca.crt").write_bytes(ca_pem)

    # Server cert (EKU=SERVER_AUTH)
    srv_priv = ec.generate_private_key(ec.SECP256R1())
    import ipaddress
    srv_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-server")]))
        .issuer_name(root_name).public_key(srv_priv.public_key())
        .serial_number(100).not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"), x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]), critical=False)
        .sign(root_key, algorithm=None)
    )
    srv_cert_path = ca_dir / "server.crt"
    srv_key_path = ca_dir / "server.key"
    srv_cert_path.write_bytes(srv_cert.public_bytes(serialization.Encoding.PEM))
    srv_key_path.write_bytes(srv_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))

    # Client cert (EKU=CLIENT_AUTH)
    cli_priv = ec.generate_private_key(ec.SECP256R1())
    sat_uuid = str(uuid4())
    cli_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"sat-{sat_uuid}")]))
        .issuer_name(root_name).public_key(cli_priv.public_key())
        .serial_number(200).not_valid_before(now - _dt.timedelta(minutes=5))
        .not_valid_after(now + _dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.UniformResourceIdentifier(f"vision-sat://sat-{sat_uuid}"),
        ]), critical=False)
        .sign(root_key, algorithm=None)
    )
    cli_cert_path = ca_dir / "device.crt"
    cli_key_path = ca_dir / "device.key"
    cli_cert_path.write_bytes(cli_cert.public_bytes(serialization.Encoding.PEM))
    cli_key_path.write_bytes(cli_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))

    return {
        "ca_dir": ca_dir, "ca_pem": ca_pem,
        "srv_cert_path": srv_cert_path, "srv_key_path": srv_key_path,
        "cli_cert_path": cli_cert_path, "cli_key_path": cli_key_path,
        "satellite_id": sat_uuid,
    }


class _FakeServer:
    """Serveur mTLS minimal qui attend HELLO, répond HELLO_ACK + enregistre frames reçues."""
    def __init__(self, ctx: ssl.SSLContext, port: int):
        self.ctx = ctx
        self.port = port
        self.received: list[Frame] = []
        self._server: asyncio.Server | None = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=self.port, ssl=self.ctx,
        )

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            hello = await asyncio.wait_for(read_frame(reader), timeout=5)
            self.received.append(hello)
            if hello.type == FrameType.HELLO:
                ack = Frame(type=FrameType.HELLO_ACK, payload={"accepted_capabilities": ["audio"]})
                writer.write(encode(ack))
                await writer.drain()
            while True:
                try:
                    f = await read_frame(reader)
                except asyncio.IncompleteReadError:
                    return
                self.received.append(f)
                if f.type == FrameType.PING:
                    writer.write(encode(Frame(type=FrameType.PONG, payload={})))
                    await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_hello_and_ack_received(mini_ca):
    # Server SSL ctx (trusts client CA pour mTLS)
    srv_ctx = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH, cafile=str(mini_ca["ca_dir"] / "vision-ca.crt"))
    srv_ctx.load_cert_chain(certfile=str(mini_ca["srv_cert_path"]), keyfile=str(mini_ca["srv_key_path"]))
    srv_ctx.verify_mode = ssl.CERT_REQUIRED
    srv_ctx.minimum_version = ssl.TLSVersion.TLSv1_3

    port = _free_port()
    server = _FakeServer(srv_ctx, port)
    await server.start()
    try:
        client = SatelliteRuntimeClient(
            runtime_uri=f"mtls://127.0.0.1:{port}",
            device_cert_path=mini_ca["cli_cert_path"],
            device_key_path=mini_ca["cli_key_path"],
            vision_ca_path=mini_ca["ca_dir"] / "vision-ca.crt",
            satellite_id=mini_ca["satellite_id"],
            capabilities={"audio": {}},
            satellite_version="1.0.0",
            audio_cmd=None,  # pas d'arecord dans le test
        )
        task = asyncio.create_task(client._session())
        # Laisse le temps de faire HELLO + ACK
        await asyncio.sleep(1.5)
        client.stop()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        assert len(server.received) >= 1
        assert server.received[0].type == FrameType.HELLO
        assert server.received[0].payload["satellite_id"] == mini_ca["satellite_id"]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_uri_parsing_invalid_scheme(mini_ca):
    client = SatelliteRuntimeClient(
        runtime_uri="http://x:9444",
        device_cert_path=mini_ca["cli_cert_path"],
        device_key_path=mini_ca["cli_key_path"],
        vision_ca_path=mini_ca["ca_dir"] / "vision-ca.crt",
        satellite_id="x", capabilities={}, satellite_version="1.0.0",
    )
    with pytest.raises(ValueError, match="mtls"):
        client._parse_uri()


def test_ssl_context_loads_certs(mini_ca):
    client = SatelliteRuntimeClient(
        runtime_uri="mtls://127.0.0.1:9444",
        device_cert_path=mini_ca["cli_cert_path"],
        device_key_path=mini_ca["cli_key_path"],
        vision_ca_path=mini_ca["ca_dir"] / "vision-ca.crt",
        satellite_id="x", capabilities={}, satellite_version="1.0.0",
    )
    ctx = client._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3
