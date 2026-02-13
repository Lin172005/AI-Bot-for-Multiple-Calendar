from flask import Flask, request, jsonify
from flask_sock import Sock
from datetime import datetime
from queue import Queue, Empty
import json
import threading
import time
from pathlib import Path

app = Flask(__name__)
sock = Sock(app)

# WebSocket subscribers: each connection gets its own outbound queue.
_clients_lock = threading.Lock()
_clients: dict[object, Queue[str]] = {}


def _broadcast(payload: dict) -> None:
    """Broadcast a JSON payload to all connected WS clients."""
    msg = json.dumps(payload, ensure_ascii=False)
    with _clients_lock:
        queues = list(_clients.values())
    for q in queues:
        try:
            q.put_nowait(msg)
        except Exception:
            # If a queue is misbehaving, skip it.
            pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CAPTION_FILE = DATA_DIR / "captions.log"

@app.route('/captions', methods=['POST'])
def receive_caption():
    try:
        data = request.get_json(force=True, silent=True) or {}
        text = (data.get("text") or "").strip()
        speaker = (data.get("speaker") or "").strip()
        ts = data.get("ts", data.get("timestamp"))

        # Normalize timestamp: accept seconds or milliseconds, default to now
        ts_sec: float
        if ts is None:
            ts_sec = time.time()
        else:
            try:
                ts_val = float(ts)
                # If value looks like milliseconds, convert to seconds
                ts_sec = ts_val / 1000.0 if ts_val > 1e10 else ts_val
            except Exception:
                ts_sec = time.time()

        if not text:
            return jsonify({"ok": False, "error": "empty text"}), 400

        time_str = datetime.fromtimestamp(ts_sec).strftime("%H:%M:%S")
        line = f"[{time_str}] {speaker + ': ' if speaker else ''}{text}\n"

        with CAPTION_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
        print("Saved caption:", line.strip())

        # Push live updates to any WS subscribers
        _broadcast({
            "type": "caption",
            "ts": ts_sec,
            "time": time_str,
            "speaker": speaker,
            "text": text,
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@sock.route('/ws/captions')
def captions_ws(ws):
    """WebSocket stream of captions.

    Client connects to: ws://localhost:5000/ws/captions
    Receives JSON messages: {type:"caption", ts, time, speaker, text}
    """
    q: Queue[str] = Queue()
    with _clients_lock:
        _clients[ws] = q

    try:
        ws.send(json.dumps({"type": "hello", "ts": time.time()}))
        while True:
            try:
                msg = q.get(timeout=30)
                ws.send(msg)
            except Empty:
                # Keep-alive so proxies don't close the socket.
                try:
                    ws.send(json.dumps({"type": "ping", "ts": time.time()}))
                except Exception:
                    break
            except Exception:
                break
    finally:
        with _clients_lock:
            _clients.pop(ws, None)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)