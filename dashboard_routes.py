import base64
import hashlib
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    # Runtime values are late-bound by _bind_app_globals() to avoid the
    # dashboard_server/dashboard_routes import cycle.
    from dashboard_server import (
        AI_ENDPOINTS,
        CUM_PATH,
        DASHBOARD_REFRESH_MS,
        DASHBOARD_SCHEMA_VERSION,
        DASHBOARD_TOKEN,
        MAX_OHLCV_LIMIT,
        REFRESH_MS,
        RUNTIME_PATH,
        SERVER_INSTANCE_ID,
        STATE_PATH,
        STATIC_DIR,
        STATUS_PATH,
        SUPPORTED_INTERVALS,
        _sync_ai_sidecar_for_state,
        ai_endpoint_payload,
        apply_market_price_to_status,
        apply_status_price_to_ohlcv,
        build_chart_tick_payload,
        build_event_patch,
        build_market_payload,
        build_order_patch,
        coerce_state_patch,
        datetime,
        freshness_seconds,
        get_intelligence,
        get_ohlcv,
        latest_ohlcv_price,
        next_sequence,
        normalize_interval,
        read_events,
        read_json,
        render_initial_dashboard_html,
        strip_orders_from_runtime,
        timezone,
        update_history,
        update_state_locked,
        validate_dashboard_payload,
        validate_market_payload,
    )


def _bind_app_globals() -> None:
    import dashboard_server as app
    globals().update({name: getattr(app, name) for name in dir(app) if not name.startswith("__")})


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str) -> None:
        _bind_app_globals()
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            return

    def _send_static(self, parsed) -> None:
        _bind_app_globals()
        name = Path(parsed.path).name
        target = STATIC_DIR / name
        if not target.exists() or not target.is_file():
            self._send(404, b'Not found', 'text/plain; charset=utf-8')
            return
        content_type = 'application/javascript; charset=utf-8' if target.suffix == '.js' else 'text/css; charset=utf-8'
        body = target.read_bytes()
        try:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=300")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except BrokenPipeError:
            return

    def _send_live_events(self, qs: dict[str, list[str]]) -> None:
        _bind_app_globals()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            first = True
            last_event_cursor = 0
            last_order_map = None
            while True:
                payload = build_market_payload(qs)
                events = payload.get("events") or []
                runtime = payload.get("runtime") or {}
                orders = ((runtime.get("grid") or {}).get("orders") or [])
                events_patch = build_event_patch(events, last_event_cursor, snapshot=first)
                orders_patch, last_order_map = build_order_patch(orders, last_order_map, snapshot=first)
                last_event_cursor = max(last_event_cursor, int(events_patch.get("cursor") or 0))
                payload["eventsPatch"] = events_patch
                payload["ordersPatch"] = orders_patch
                payload["events"] = []
                payload["runtime"] = strip_orders_from_runtime(runtime)
                payload = validate_market_payload(payload)
                body = json.dumps(payload, separators=(",", ":"))
                self.wfile.write(f"event: market\ndata: {body}\n\n".encode("utf-8"))
                self.wfile.flush()
                first = False
                time.sleep(max(0.25, REFRESH_MS / 1000.0))
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_ws_frame(self, payload: dict) -> None:
        _bind_app_globals()
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        size = len(body)
        if size < 126:
            header = bytes([0x81, size])
        elif size < 65536:
            header = bytes([0x81, 126]) + size.to_bytes(2, "big")
        else:
            header = bytes([0x81, 127]) + size.to_bytes(8, "big")
        self.wfile.write(header + body)
        self.wfile.flush()

    def _send_chart_websocket(self, qs: dict[str, list[str]]) -> None:
        _bind_app_globals()
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._send(400, b'Missing Sec-WebSocket-Key', 'text/plain; charset=utf-8')
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        try:
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            while True:
                self._send_ws_frame(build_chart_tick_payload(qs))
                time.sleep(max(0.25, REFRESH_MS / 1000.0))
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _read_json_body(self) -> dict:
        _bind_app_globals()
        try:
            length = int(self.headers.get('Content-Length', '0') or '0')
        except Exception:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _mutation_authorized(self, parsed) -> bool:
        _bind_app_globals()
        if not DASHBOARD_TOKEN:
            return True
        qs = parse_qs(parsed.query)
        supplied = self.headers.get('X-Tradebot-Token') or qs.get('token', [''])[0]
        return hmac.compare_digest(str(supplied), DASHBOARD_TOKEN)

    def do_POST(self):
        _bind_app_globals()
        try:
            parsed = urlparse(self.path)
            if not self._mutation_authorized(parsed):
                self._send(403, b'Forbidden', 'text/plain; charset=utf-8')
                return
            if parsed.path == '/api/control':
                body = self._read_json_body()
                state = update_state_locked(lambda current: {**current, 'paused': bool(body.get('paused'))})
                self._send(200, json.dumps({'ok': True, 'paused': state['paused']}).encode('utf-8'), 'application/json; charset=utf-8')
                return
            if parsed.path == '/api/config':
                body = self._read_json_body()
                patch = coerce_state_patch(body)
                state = update_state_locked(lambda current: {**current, **patch})
                _sync_ai_sidecar_for_state(state, patch)
                self._send(200, json.dumps({'ok': True, 'state': state}).encode('utf-8'), 'application/json; charset=utf-8')
                return
            self._send(404, b'Not found', 'text/plain; charset=utf-8')
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({'error': str(e)}).encode('utf-8'), 'application/json; charset=utf-8')

    def do_GET(self):
        _bind_app_globals()
        try:
            parsed = urlparse(self.path)
            if parsed.path.startswith('/static/'):
                self._send_static(parsed)
                return
            if parsed.path == '/ws/chart':
                self._send_chart_websocket(parse_qs(parsed.query))
                return
            if parsed.path in ('/', '/index.html'):
                qs = parse_qs(parsed.query)
                status = read_json(STATUS_PATH)
                state = read_json(STATE_PATH)
                interval = normalize_interval(qs.get('interval', [status.get('interval') or state.get('interval', '1m')])[0])
                self._send(200, render_initial_dashboard_html(interval).encode('utf-8'), 'text/html; charset=utf-8')
                return
            if parsed.path == '/api/market':
                payload = build_market_payload(parse_qs(parsed.query))
                self._send(200, json.dumps(payload).encode('utf-8'), 'application/json; charset=utf-8')
                return
            if parsed.path == '/api/live/events':
                self._send_live_events(parse_qs(parsed.query))
                return
            if parsed.path == '/api/dashboard':
                qs = parse_qs(parsed.query)
                status = read_json(STATUS_PATH)
                state = read_json(STATE_PATH)
                runtime = read_json(RUNTIME_PATH)
                cumulative = read_json(CUM_PATH)
                symbol = status.get('symbol') or state.get('symbol', 'BTCUSDT')
                requested_interval = normalize_interval(qs.get('interval', [status.get('interval') or state.get('interval', '1m')])[0])
                raw_limit = qs.get('limit', [SUPPORTED_INTERVALS[requested_interval]['default_limit']])[0]
                raw_offset = qs.get('offset', [0])[0]
                try:
                    limit = max(30, min(MAX_OHLCV_LIMIT, int(raw_limit)))
                except Exception:
                    limit = SUPPORTED_INTERVALS[requested_interval]['default_limit']
                try:
                    offset = max(0, int(raw_offset))
                except Exception:
                    offset = 0
                include_ohlcv = qs.get('ohlcv', ['1'])[0] not in {'0', 'false', 'no'}
                ohlcv = get_ohlcv(symbol, requested_interval, limit=limit, offset=offset) if include_ohlcv else []
                if include_ohlcv:
                    ohlcv = apply_status_price_to_ohlcv(ohlcv, status, requested_interval)
                status = apply_market_price_to_status(status, latest_ohlcv_price(ohlcv))
                intelligence = get_intelligence(state, status, ohlcv, force=qs.get('refreshIntelligence', ['0'])[0] in {'1', 'true', 'yes'})
                ai_endpoint, ai_endpoint_models = ai_endpoint_payload(state)
                state_payload = dict(state)
                state_payload['aiEndpointKey'] = ai_endpoint['key']
                payload = {
                    'schemaVersion': DASHBOARD_SCHEMA_VERSION,
                    'serverInstanceId': SERVER_INSTANCE_ID,
                    'channel': 'dashboard',
                    'seq': next_sequence('dashboard'),
                    'serverTimeUtc': datetime.now(timezone.utc).isoformat(),
                    'status': status,
                    'state': state_payload,
                    'runtime': runtime,
                    'cumulative': cumulative,
                    'events': read_events(),
                    'history': update_history(status),
                    'ohlcv': ohlcv,
                    'intelligence': intelligence,
                    'chartInterval': requested_interval,
                    'chartLimit': limit,
                    'chartOffset': offset,
                    'supportedIntervals': list(SUPPORTED_INTERVALS.keys()),
                    'aiEndpoints': AI_ENDPOINTS,
                    'aiEndpointKey': ai_endpoint['key'],
                    'aiEndpointLabel': ai_endpoint['label'],
                    'aiEndpointModels': ai_endpoint_models,
                    'aiModels': ai_endpoint_models.get(ai_endpoint['key'], []),
                    'freshnessSeconds': freshness_seconds(status.get('tsUtc')),
                    'refreshMs': REFRESH_MS,
                    'dashboardRefreshMs': DASHBOARD_REFRESH_MS,
                }
                self._send(200, json.dumps(validate_dashboard_payload(payload)).encode('utf-8'), 'application/json; charset=utf-8')
                return
            if parsed.path == '/health':
                self._send(200, b'{"status":"ok"}', 'application/json; charset=utf-8')
                return
            self._send(404, b'Not found', 'text/plain; charset=utf-8')
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, json.dumps({'error': str(e)}).encode('utf-8'), 'application/json; charset=utf-8')

    def log_message(self, format, *args):
        _bind_app_globals()
        return
