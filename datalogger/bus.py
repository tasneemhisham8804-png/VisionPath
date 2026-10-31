"""
ZeroMQ PUB/SUB message bus.

  * The bus process binds two endpoints and every module connects:
        [bus] pub_endpoint : modules connect a PUB socket here to send
        [bus] sub_endpoint : modules connect a SUB socket here to receive
    Both come from config/visionpath.toml (datalogger.config), never from the code.
  * Topic of every message = its msg_type (camera_frame, track, risk_alert, ...).
  * Each message is sent as two frames: [topic, json line]. The json line is
    exactly msg.to_json() of the data contract.

    from datalogger.config import load_config, pub_endpoint, sub_endpoints
    cfg = load_config()
    pub = Publisher(pub_endpoint(cfg))          # connects
    pub.send(msg.to_json())                     # topic taken from msg_type
    sub = Subscriber(sub_endpoints(cfg))        # connects, all topics
    line = sub.recv()                           # json line, or None after the timeout

Development bus process (until the team's own bus process exists):
    python -m datalogger.bus

pyzmq is imported only when a socket is created, so the rest of the datalogger
(and the CI tests) work without it.
"""

from __future__ import annotations

import json
import sys
import time

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
        bind            : False = connect to the bus process (the usual case)
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
    def __init__(self, endpoint: str, bind: bool = False, hwm: int = DEFAULT_HWM, context=None):
        """endpoint from the config file ([bus] pub_endpoint). bind=True only for tests without a bus process."""
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


class BusProxy:
    """
    Minimal bus process: an XSUB socket where publishers connect and an XPUB
    socket where subscribers connect, forwarding messages one way and
    subscriptions the other way. A port of "*" picks a free port (tests).
    """

    def __init__(self, pub_endpoint: str, sub_endpoint: str, hwm: int = DEFAULT_HWM, context=None):
        zmq = _zmq()
        self._zmq = zmq
        ctx = context or zmq.Context.instance()
        self.frontend = ctx.socket(zmq.XSUB)           # publishers connect here
        self.frontend.setsockopt(zmq.RCVHWM, hwm)
        self.frontend.setsockopt(zmq.LINGER, 0)
        self.frontend.bind(pub_endpoint)
        self.backend = ctx.socket(zmq.XPUB)            # subscribers connect here
        self.backend.setsockopt(zmq.SNDHWM, hwm)
        self.backend.setsockopt(zmq.LINGER, 0)
        self.backend.bind(sub_endpoint)
        self.pub_endpoint = self.frontend.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")
        self.sub_endpoint = self.backend.getsockopt(zmq.LAST_ENDPOINT).decode("utf-8")
        self._poller = zmq.Poller()
        self._poller.register(self.frontend, zmq.POLLIN)
        self._poller.register(self.backend, zmq.POLLIN)
        self.forwarded = 0

    def step(self, timeout_ms: int = 200) -> int:
        """Forward whatever is waiting (at most timeout_ms of waiting). Returns messages forwarded."""
        zmq = self._zmq
        ready = dict(self._poller.poll(timeout_ms))
        n = 0
        if self.frontend in ready:                     # messages: publishers -> subscribers
            n = self._drain(self.frontend, self.backend)
            self.forwarded += n
        if self.backend in ready:                      # subscriptions: subscribers -> publishers
            self._drain(self.backend, self.frontend)
        return n

    def _drain(self, src, dst) -> int:
        n = 0
        while True:
            try:
                frames = src.recv_multipart(self._zmq.NOBLOCK)
            except self._zmq.Again:
                return n
            dst.send_multipart(frames)
            n += 1

    def close(self) -> None:
        self.frontend.close(linger=0)
        self.backend.close(linger=0)


def main(argv=None) -> int:
    """python -m datalogger.bus : run the development bus process with the endpoints in config/visionpath.toml."""
    import argparse
    from datalogger.config import ConfigError, load_config, pub_endpoint, sub_endpoints

    ap = argparse.ArgumentParser(description="Development bus process (XSUB/XPUB proxy).")
    ap.add_argument("--config", help="config file (default: VISIONPATH_CONFIG or config/visionpath.toml)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        pub_ep, sub_eps = pub_endpoint(cfg), sub_endpoints(cfg)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2
    if len(sub_eps) != 1:
        print("Config error: the bus process needs exactly one [bus] sub_endpoint", file=sys.stderr)
        return 2
    try:
        proxy = BusProxy(pub_ep, sub_eps[0])
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"Bus running. Publishers connect to {proxy.pub_endpoint}, "
          f"subscribers connect to {proxy.sub_endpoint}. Ctrl+C to stop.")
    last_status, last_count = time.monotonic(), 0
    try:
        while True:
            proxy.step()
            if time.monotonic() - last_status >= 5 and proxy.forwarded != last_count:
                last_status, last_count = time.monotonic(), proxy.forwarded
                print(f"  {proxy.forwarded} messages forwarded")
    except KeyboardInterrupt:
        pass
    finally:
        proxy.close()
    print(f"Bus stopped. {proxy.forwarded} messages forwarded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
