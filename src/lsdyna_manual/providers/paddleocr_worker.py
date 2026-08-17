"""Standalone local PaddleOCR worker used by the isolated runtime venv."""

from __future__ import annotations

import argparse
import json
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    """Serialize NumPy scalars/arrays emitted by PaddleX result objects."""
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _result_value(result: Any) -> Any:
    # PaddleX result objects subclass dict but expose a normalized `.json`
    # representation. Treat only a plain dict as already serialized.
    if type(result) is dict:
        return result
    for name in ("json", "to_json", "to_dict"):
        value = getattr(result, name, None)
        if value is None:
            continue
        try:
            value = value() if callable(value) else value
        except TypeError:
            continue
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict):
            return value
    save = getattr(result, "save_to_json", None)
    if callable(save):
        with tempfile.TemporaryDirectory(prefix="paddleocr-worker-") as directory:
            save(save_path=directory)
            files = sorted(Path(directory).glob("*.json"))
            if files:
                return json.loads(files[0].read_text(encoding="utf-8"))
    raise TypeError(f"unsupported PaddleOCR result object: {type(result).__name__}")


def _layout_result(result: Any) -> dict[str, Any]:
    payload = _result_value(result)
    if not isinstance(payload, dict):
        raise TypeError("PaddleOCR returned a non-object result")
    layouts = payload.get("layoutParsingResults")
    if isinstance(layouts, list):
        if len(layouts) != 1:
            raise ValueError("single-page request returned multiple layout results")
        return _layout_result(layouts[0])
    for key in ("result", "res"):
        if isinstance(payload.get(key), dict):
            return _layout_result(payload[key])
    if "prunedResult" in payload:
        return payload
    if "parsing_res_list" in payload:
        normalized = dict(payload)
        normalized["prunedResult"] = {
            "parsing_res_list": payload.get("parsing_res_list", [])
        }
        return normalized
    raise ValueError("PaddleOCR result has no parsing result blocks")


class WorkerServer(HTTPServer):
    pipeline: Any


class Handler(BaseHTTPRequestHandler):
    server: WorkerServer

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            default=_json_default,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path == "/health":
            self._json_response(200, {"status": "ready"})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler API
        if self.path != "/predict":
            self._json_response(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            input_path = Path(request["path"])
            if not input_path.is_file():
                raise FileNotFoundError(input_path)
            outputs = list(self.server.pipeline.predict(str(input_path)))
            if len(outputs) != 1:
                raise ValueError(
                    f"single-page input returned {len(outputs)} PaddleOCR results"
                )
            self._json_response(200, {"layout_result": _layout_result(outputs[0])})
        except Exception as exc:
            self._json_response(
                500,
                {"error": f"{type(exc).__name__}: {exc}"},
            )

    def log_message(self, format: str, *args: Any) -> None:
        print(f"paddleocr-worker: {format % args}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--pipeline-version", default="v1.6")
    parser.add_argument("--llama-server-url", required=True)
    args = parser.parse_args()

    from paddleocr import PaddleOCRVL

    pipeline = PaddleOCRVL(
        pipeline_version=args.pipeline_version,
        vl_rec_backend="llama-cpp-server",
        vl_rec_server_url=args.llama_server_url,
        vl_rec_max_concurrency=1,
    )
    server = WorkerServer((args.host, args.port), Handler)
    server.pipeline = pipeline
    print(f"paddleocr-worker ready on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
