import logging
import os
import socketserver
import time

from http.client import HTTPResponse


class JSONDataReceiver(socketserver.ThreadingTCPServer):
    def __init__(self, server_address, handler, output_dir):
        socketserver.TCPServer.__init__(self,
                                        server_address,
                                        handler,
                                        True)
        self._dir = output_dir

        if not os.path.exists(self._dir):
            logging.warning("{} doesn't exist, creating".format(self._dir))
            os.makedirs(self._dir)

    @property
    def output_dir(self):
        return self._dir

    def verify_request(self, request, client_address):
        # TODO: this is where we're going to access control the request
        # TODO: IP filter (though this should be doubled up via network layer)
        # TODO: URL path requested, drop immediately if not a decicated unit path
        return True


class DataReceiverHandler(socketserver.BaseRequestHandler):
    def setup(self):
        pass

    def handle(self):
        payload = bytearray()
        request_time = time.time()
        logging.debug("Received: {} from {}".format(self.request, self.client_address))

        try:
            data = self.request.recv(1024)
            while data is not None and len(data) > 0:
                payload += data
                data = self.request.recv(1024)
        except OSError as e:
            logging.warning("Issue collecting further data: errno {}".format(e.errno))

        self.request.sendall("HTTP/1.1 200".encode("utf-8"))
        logging.debug("Sent HTTP 200 response")

        # TODO: this is grim at present, handle requests between threads portably
        #  based on parsing the messages for real
        output_file = os.path.join(self.server.output_dir, "{:020.6f}".format(request_time))
        with open(output_file, "wb") as fh:
            fh.write(payload)
        logging.debug("Written {}".format(output_file))


class DataReceiverConfigurationError(Exception):
    pass


class DataReceiverRuntimeError(Exception):
    pass
