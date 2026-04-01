"""
Nexmon CSI Frame Parser (BCM43455 / Raspberry Pi 4)
Parst rohe UDP-Pakete vom Nexmon-Treiber.
"""

import struct
import time
from dataclasses import dataclass, field

NEXMON_MAGIC  = 0x11111111
HEADER_SIZE   = 19
MAX_SUBCARRIERS = 256


@dataclass
class CSIFrame:
    timestamp: float
    rssi: int
    src_mac: str
    seq_num: int
    channel_spec: int
    amplitudes: list[float]
    phases: list[float]

    @property
    def num_subcarriers(self) -> int:
        return len(self.amplitudes)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "rssi": self.rssi,
            "src_mac": self.src_mac,
            "seq_num": self.seq_num,
            "num_subcarriers": self.num_subcarriers,
            "amplitudes": self.amplitudes,
        }


def parse(raw: bytes) -> CSIFrame | None:
    """Parst einen rohen Nexmon UDP-Frame. Gibt None bei ungültigem Frame zurück."""
    if len(raw) < HEADER_SIZE + 4:
        return None

    magic = struct.unpack_from("<I", raw, 0)[0]
    if magic != NEXMON_MAGIC:
        return None

    rssi_raw = struct.unpack_from("B", raw, 4)[0]
    rssi = struct.unpack("b", bytes([rssi_raw]))[0]
    src_mac = ":".join(f"{b:02x}" for b in raw[6:12])
    seq_num = struct.unpack_from("<H", raw, 12)[0]
    channel_spec = struct.unpack_from("<H", raw, 15)[0]

    payload = raw[HEADER_SIZE:]
    n = min(len(payload) // 4, MAX_SUBCARRIERS)
    if n == 0:
        return None

    import cmath, math
    amplitudes = []
    phases = []
    for i in range(n):
        re, im = struct.unpack_from("<hh", payload, i * 4)
        c = complex(re, im)
        amplitudes.append(abs(c))
        phases.append(cmath.phase(c))

    return CSIFrame(
        timestamp=time.time(),
        rssi=rssi,
        src_mac=src_mac,
        seq_num=seq_num,
        channel_spec=channel_spec,
        amplitudes=amplitudes,
        phases=phases,
    )
