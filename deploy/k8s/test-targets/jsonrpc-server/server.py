from flask import Flask, jsonify, request

app = Flask(__name__)

HISTORY = [
    {"method": "add", "params": {"a": 1, "b": 2}, "result": 3, "timestamp": "2024-01-15T10:30:00Z"},
    {"method": "subtract", "params": {"a": 10, "b": 3}, "result": 7, "timestamp": "2024-01-15T10:31:00Z"},
    {"method": "add", "params": {"a": 5, "b": 5}, "result": 10, "timestamp": "2024-01-15T10:32:00Z"},
]

OPENRPC_SPEC = {
    "openrpc": "1.0.0",
    "info": {"title": "Calculator", "version": "1.0.0"},
    "methods": [
        {
            "name": "add",
            "params": [
                {"name": "a", "required": True, "schema": {"type": "number"}},
                {"name": "b", "required": True, "schema": {"type": "number"}},
            ],
            "result": {"name": "sum", "schema": {"type": "number"}},
        },
        {
            "name": "subtract",
            "params": [
                {"name": "a", "required": True, "schema": {"type": "number"}},
                {"name": "b", "required": True, "schema": {"type": "number"}},
            ],
            "result": {"name": "difference", "schema": {"type": "number"}},
        },
        {
            "name": "get_history",
            "params": [
                {"name": "limit", "required": False, "schema": {"type": "integer"}},
            ],
            "result": {"name": "history", "schema": {"type": "array", "items": {"type": "object"}}},
        },
        {
            "name": "delete_history",
            "params": [],
            "result": {"name": "success", "schema": {"type": "boolean"}},
        },
    ],
}


def _error(req_id, code, message):
    return jsonify({"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id})


@app.route('/healthz')
def healthz():
    return 'ok', 200


@app.route('/openrpc.json')
def openrpc():
    return jsonify(OPENRPC_SPEC)


@app.route('/rpc', methods=['POST'])
def rpc():
    body = request.get_json(force=True)
    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    if not method:
        return _error(req_id, -32600, "Invalid Request"), 400

    if method == "add":
        a = params.get("a") if isinstance(params, dict) else params[0] if len(params) > 0 else None
        b = params.get("b") if isinstance(params, dict) else params[1] if len(params) > 1 else None
        if a is None or b is None:
            return _error(req_id, -32602, "Invalid params: a and b are required"), 400
        return jsonify({"jsonrpc": "2.0", "result": a + b, "id": req_id})

    if method == "subtract":
        a = params.get("a") if isinstance(params, dict) else params[0] if len(params) > 0 else None
        b = params.get("b") if isinstance(params, dict) else params[1] if len(params) > 1 else None
        if a is None or b is None:
            return _error(req_id, -32602, "Invalid params: a and b are required"), 400
        return jsonify({"jsonrpc": "2.0", "result": a - b, "id": req_id})

    if method == "get_history":
        limit = params.get("limit") if isinstance(params, dict) else (params[0] if params else None)
        result = HISTORY[:limit] if limit else HISTORY
        return jsonify({"jsonrpc": "2.0", "result": result, "id": req_id})

    if method == "delete_history":
        HISTORY.clear()
        return jsonify({"jsonrpc": "2.0", "result": True, "id": req_id})

    return _error(req_id, -32601, f"Method not found: {method}"), 400


if __name__ == '__main__':
    print("JSON-RPC 2.0 + OpenRPC mock server listening on :8000")
    app.run(host='0.0.0.0', port=8000)
