"""
ZeroMQ PUB/SUB message bus (no broker).

  * Topic of every message = its msg_type (camera_frame, track, risk_alert, ...).
  * Each message is sent as two frames: [topic, json line]. The json line is
    exactly msg.to_json() of the data contract.
  * The endpoint comes from config/visionpath.toml (datalogger.config), never
    from the code.

    from datalogger.bus import Publisher, Subscriber
    endpoints = bus_endpoints(load_config())    # from datalogger.config
    pub = Publisher(endpoints[0])               # binds
    pub.send(msg.to_json())                     # topic taken from msg_type
    sub = Subscriber(endpoints)                 # connects, all topics
    line = sub.recv()                           # json line, or None after the timeout

pyzmq is imported only when a socket is created, so the rest of the datalogger
(and the CI tests) work without it.
"""

from __future__ import annotations

import json

DEFAULT_HWM = 10_000          # messages queued per socket before ZeroMQ drops (about 3 min of traffic)


def _zmq():
    try:
        import zmq
    except ImportError:
        raise RuntimeError("pyzmq is not installed. Run: pip install pyzmq") from None
    return zmq


def _text(frame) -> str:
    if isinstance(frame, memoryview):
        frame = frame.tobytes()
    if isinstance(frame, (bytes, bytearray)):
        return bytes(frame).decode("utf-8", errors="replace")
    return str(frame)


def parse_frames(frames) -> tuple[str | None, str]:
    """
    (topic, json line) from one received ZeroMQ message. Accepts:
      [topic, json]   two frames (what Publisher sends)
      ["topic json"]  one frame, topic and json separated by a space
      ["json"]        one frame, json only
    """
    parts = [_text(f) for f in frames]
    if len(parts) >= 2:
        return parts[0], parts[-1].strip()
    text = parts[0].strip() if parts else ""
    if text.startswith("{") or " " not in text:
        return None, text
    topic, line = text.split(" ", 1)
    return topic, line.strip()


def topic_of(line: str) -> str:
    """msg_type of a json line ('' when it has none)."""
    try:
        return str(json.loads(line).get("msg_type") or "")
    except (ValueError, AttributeError):
        return ""


class Subscriber:
    def __init__(self, endpoints, topics=("",), recv_timeout_ms: int = 200, bind: bool = False,
                 hwm: int = DEFAULT_HWM, context=None):
        """
        endpoints       : list of "tcp://host:port" (a single string also works)
        topics          : topic prefixes; ("",) = every message on the bus
        recv_timeout_ms : recv() returns None after this long without a message
        bind            : False = connect to publishers that bind (the usual case)
        """
        zmq = _zmq()
        self._zmq = zmq
        self.endpoints = [endpoints] if isinstance(endpoints, str) else list(endpoints)
        self.socket = (context or zmq.Context.instance()).socket(zmq.SUB)
        self.socket.setsockopt(zmq.RCVHWM, hwm)
        self.socket.setsockopt(zmq.RCVTIMEO, recv_timeout_ms)
        self.socket.setsockopt(zmq.LINGER, 0)
        for t in topics:
            self.socket.setsockopt(zmq.SUBSCRIBE, t.encode("utf-8"))
        for ep in self.endpoints:
            (self.socket.bind if bind else self.socket.connect)(ep)

    def recv(self) -> str | None:
        """Next json line, or None when nothing arrived within the timeout."""
        try:
            frames = self.socket.recv_multipart()
        except self._zmq.Again:
            return None
        return parse_frames(frames)[1]

    def close(self) -> None:
        self.socket.close(linger=0)


class Publisher:
    def __init__(self, endpoint: str, bind: bool = True, hwm: int = DEFAULT_HWM, context=None):
        """endpoint from the config file; with bind=True, a port of "*" picks a free port (tests)."""
        zmq = _zmq()
        self.socket = (context or zmq.Context.instance()).socket(zmq.PUB)
        self.socket.setsockopt(zmq.SNDHWM, hwm)
        self.socket.setsockopt(zmq.LINGER, 1000)
        if bind:
            self.socket.bind(endpoint)
            self.endpoint = self.socket.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")
        else:
            self.socket.connect(endpoint)
            self.endpoint = endpoint

    def send(self, line: str, topic: str | None = None) -> None:
        topic = topic if topic is not None else topic_of(line)
        self.socket.send_multipart([topic.encode("utf-8"), line.encode("utf-8")])

    def close(self) -> None:
        self.socket.close()
