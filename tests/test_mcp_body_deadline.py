"""Bounded local socket tests for MCP body framing and elapsed time."""
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_mcp
import server


class McpBodyDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.deadline = patch.object(server, "MCP_BODY_READ_TIMEOUT_SECONDS", 0.15, create=True)
        self.deadline.start()
        self.httpd = server.VoltaHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.sockets = []

    def tearDown(self):
        for connection in self.sockets:
            connection.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1)
        self.deadline.stop()

    def connect(self, length):
        connection = socket.create_connection(("127.0.0.1", self.httpd.server_address[1]), timeout=0.6)
        self.sockets.append(connection)
        connection.sendall(("POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
                            f"Content-Length: {length}\r\nContent-Type: application/json\r\n\r\n").encode())
        return connection

    def response(self, connection):
        data = bytearray()
        while chunk := connection.recv(8192):
            data.extend(chunk)
        return bytes(data)

    def test_incomplete_body_times_out_and_closes_connection(self):
        connection = self.connect(2)
        connection.sendall(b"{")
        response = self.response(connection)
        self.assertIn(b" 408 ", response.split(b"\r\n", 1)[0])
        self.assertEqual(json.loads(response.split(b"\r\n\r\n", 1)[1])["jsonrpc"], "2.0")

    def test_trickled_bytes_do_not_reset_total_deadline(self):
        connection = self.connect(100)
        stopped = threading.Event()

        def trickle():
            while not stopped.wait(0.03):
                try:
                    connection.sendall(b" ")
                except OSError:
                    return

        writer = threading.Thread(target=trickle, daemon=True)
        started = time.monotonic()
        writer.start()
        try:
            response = self.response(connection)
            self.assertIn(b" 408 ", response.split(b"\r\n", 1)[0])
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            stopped.set()
            writer.join(timeout=1)

    def test_short_eof_is_rejected_not_dispatched(self):
        connection = self.connect(10)
        connection.sendall(b"{}")
        connection.shutdown(socket.SHUT_WR)
        with patch.object(agent_mcp, "handle_http_post", return_value=(200, {})) as dispatch:
            response = self.response(connection)
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        dispatch.assert_not_called()

    def test_over_cap_declaration_is_rejected_without_reading(self):
        response = self.response(self.connect(agent_mcp.MAX_BODY_BYTES + 1))
        self.assertIn(b" 413 ", response.split(b"\r\n", 1)[0])

    def test_complete_request_still_reaches_protocol_dispatch(self):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        connection = self.connect(len(body))
        connection.sendall(body)
        response = self.response(connection)
        self.assertIn(b" 200 ", response.split(b"\r\n", 1)[0])
        self.assertEqual(json.loads(response.split(b"\r\n\r\n", 1)[1])["result"], {})


if __name__ == "__main__":
    unittest.main()
