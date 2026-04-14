"""Codec CBOR pour les frames du protocole mTLS satellite (client côté).

Wire format : [length:u32 LE][type:u8][payload (length-1 bytes)]
Payload : raw bytes (AUDIO_FRAME, VIDEO_FRAME) ou dict CBOR.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import IntEnum
from typing import Union

import cbor2

MAX_FRAME_LEN = 10 * 1024 * 1024


class FrameType(IntEnum):
    HELLO = 0x01
    HELLO_ACK = 0x02
    AUDIO_FRAME = 0x03
    VIDEO_FRAME = 0x04
    ZIGBEE_EVENT = 0x05
    ZWAVE_EVENT = 0x06
    BT_EVENT = 0x07
    PING = 0x08
    PONG = 0x09
    RENEW_REQUEST = 0x0A
    RENEW_RESPONSE = 0x0B
    ERROR = 0x0C
    CONTROL = 0x0D


_RAW_BYTES_TYPES = frozenset({FrameType.AUDIO_FRAME, FrameType.VIDEO_FRAME})


class FrameProtocolError(Exception):
    pass


@dataclass
class Frame:
    type: FrameType
    payload: Union[dict, bytes]


def encode(frame: Frame) -> bytes:
    if frame.type in _RAW_BYTES_TYPES:
        if not isinstance(frame.payload, (bytes, bytearray)):
            raise FrameProtocolError(f"raw bytes expected for {frame.type.name}")
        payload_bytes = bytes(frame.payload)
    else:
        if not isinstance(frame.payload, dict):
            raise FrameProtocolError(f"dict expected for {frame.type.name}")
        payload_bytes = cbor2.dumps(frame.payload)
    total_len = 1 + len(payload_bytes)
    if total_len > MAX_FRAME_LEN:
        raise FrameProtocolError(f"frame too large: {total_len}")
    return total_len.to_bytes(4, "little") + bytes([int(frame.type)]) + payload_bytes


async def read_frame(reader: asyncio.StreamReader) -> Frame:
    header = await reader.readexactly(4)
    total_len = int.from_bytes(header, "little")
    if total_len == 0:
        raise FrameProtocolError("type byte missing (len=0)")
    if total_len > MAX_FRAME_LEN:
        raise FrameProtocolError(f"frame too large: {total_len}")
    type_byte = await reader.readexactly(1)
    try:
        frame_type = FrameType(type_byte[0])
    except ValueError:
        raise FrameProtocolError(f"unknown type: 0x{type_byte[0]:02X}")
    payload_len = total_len - 1
    payload_raw = await reader.readexactly(payload_len) if payload_len > 0 else b""
    if frame_type in _RAW_BYTES_TYPES:
        return Frame(type=frame_type, payload=payload_raw)
    try:
        payload = cbor2.loads(payload_raw) if payload_raw else {}
    except Exception as exc:
        raise FrameProtocolError(f"CBOR decode: {exc}")
    if not isinstance(payload, dict):
        raise FrameProtocolError(f"dict expected, got {type(payload)}")
    return Frame(type=frame_type, payload=payload)
