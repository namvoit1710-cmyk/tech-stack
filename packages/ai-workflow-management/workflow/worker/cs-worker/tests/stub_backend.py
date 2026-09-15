"""A stub of RequestorService, so the FLOW's branches can be proven without credentials.

Why bother when the node has unit tests: those test the node. The workflow's SWITCH rules
are a separate thing, they compare strings, and a wrong string there fails SILENTLY -- the
run completes and simply takes another branch. That is the failure this stub catches.

Serves the bodies the live API returned on 2026-09-07, chosen by reqID:

    CR_OK        -> 200 {"message": "Request has been cancelled"}
    CR_LOCK      -> 400 CAN_NOT_LOCK_REQUEST      (a refusal)
    CR_NOACTION  -> 404 Invalid resource path     (a landscape without the action)
    anything else-> 401 Unauthorized              (a real fault)
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OK_BODY = {"@odata.context": "$metadata#core.common.CommonActionOutput",
           "data": '"CR_OK"', "message": "Request has been cancelled"}
CANNOT_LOCK = {"error": {"message": "CAN_NOT lock request",
                         "code": "CAN_NOT_LOCK_REQUEST",
                         "@Common.numericSeverity": 4}}
NO_ACTION = {"error": {
    "message": 'Invalid resource path '
               '"core.service.process.requestor.RequestorService.cancelRequest"',
    "code": "404", "@Common.numericSeverity": 4}}
UNAUTHORIZED = {"error": {"message": "Unauthorized", "code": "401"}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, doc):
        raw = json.dumps(doc).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8") if n else ""
        if self.path.endswith("/oauth/token"):
            return self._json(200, {"access_token": "stub-jwt", "expires_in": 3600})
        if self.path.endswith("/cancelRequest"):
            try:
                req = json.loads(body or "{}")
            except ValueError:
                req = {}
            rid = str(req.get("reqID") or "")
            print("[stub] cancelRequest reqID=%s comment=%r" % (rid, req.get("comment")))
            if rid == "CR_OK":
                return self._json(200, OK_BODY)
            if rid == "CR_LOCK":
                return self._json(400, CANNOT_LOCK)
            if rid == "CR_NOACTION":
                return self._json(404, NO_ACTION)
            return self._json(401, UNAUTHORIZED)
        self._json(404, {"error": "no such path"})


if __name__ == "__main__":
    print("[stub] listening on 127.0.0.1:8799")
    ThreadingHTTPServer(("127.0.0.1", 8799), H).serve_forever()
