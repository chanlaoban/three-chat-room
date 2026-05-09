"""
反向代理：将端口 8000 的请求转发到三人群聊服务（端口 8891）
配合 natapp 隧道实现外网访问
使用 http.server + urllib（零依赖）
"""
import http.server
import urllib.request
import urllib.error
import os
import signal
import sys
import select

TARGET = "http://127.0.0.1:8891"
BIND_HOST = "0.0.0.0"
BIND_PORT = 8000

class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def _proxy(self, method):
        target_url = f"{TARGET}{self.path}"
        body = None
        content_length = self.headers.get("Content-Length")
        if content_length and int(content_length) > 0:
            body = self.rfile.read(int(content_length))
        
        # 构建转发请求
        req = urllib.request.Request(
            target_url,
            data=body,
            headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "transfer-encoding")},
            method=method,
        )
        
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for k, v in e.headers.items():
                if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(e.read() if hasattr(e, 'read') else b'')
        except urllib.error.URLError as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"代理错误: {str(e.reason)}".encode())
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(f"代理错误: {str(e)}".encode())
    
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            return
        self._proxy("GET")
    
    def do_POST(self):
        self._proxy("POST")
    
    def do_PUT(self):
        self._proxy("PUT")
    
    def do_DELETE(self):
        self._proxy("DELETE")
    
    def do_PATCH(self):
        self._proxy("PATCH")
    
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}", flush=True)


if __name__ == "__main__":
    server = http.server.HTTPServer((BIND_HOST, BIND_PORT), ProxyHandler)
    print(f"✅ 反向代理已启动: http://{BIND_HOST}:{BIND_PORT} → {TARGET}")
    print(f"   外网访问方式: natapp 隧道指向端口 {BIND_PORT}")
    print("   按 Ctrl+C 停止")
    
    def shutdown(sig, frame):
        print("\n🛑 停止服务...")
        server.shutdown()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    server.serve_forever()
