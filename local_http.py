"""Loopback HTTP transport without DNS, proxies, or HTTPS trust-store setup."""
from http.server import ThreadingHTTPServer
from socketserver import TCPServer
from urllib.request import OpenerDirector, HTTPHandler, HTTPDefaultErrorHandler, HTTPErrorProcessor


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer normally reverse-resolves the bound address via getfqdn.
        # A local-only daemon needs neither DNS nor a public server name.
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]


def local_opener():
    # build_opener creates HTTPSHandler and loads system trust even for HTTP.
    # Local authenticated calls only require HTTP, and must never use proxies.
    opener = OpenerDirector()
    for handler in (HTTPHandler(), HTTPDefaultErrorHandler(), HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener
