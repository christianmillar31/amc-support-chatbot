"""Minimal preview server using only stdlib — serves static/index.html for UI preview."""
import http.server
import os
import sys

PORT = 8001
os.chdir(os.path.dirname(os.path.abspath(__file__)))

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.path = '/static/index.html'
        elif not self.path.startswith('/static/'):
            # Serve static files for any /static/* request as-is
            pass
        return super().do_GET()

    def log_message(self, format, *args):
        pass  # Quiet

print(f"Preview server on http://localhost:{PORT}", flush=True)
http.server.HTTPServer(('', PORT), Handler).serve_forever()
