import crypt
import gzip
import logging
import os
import re
import shlex
import subprocess

from datetime import datetime

from pyremotenode.tasks.iridium import SBDSender, IMTSender


class MessageProcessor:
    def __init__(self, cfg, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._archive = cfg["general"]["msg_archive"] if "msg_archive" in cfg["general"] else \
            os.path.join(os.sep, "data", "pyremotenode", "messages", "archive")
        self._source = cfg["general"]["msg_inbox"] if "msg_inbox" in cfg["general"] else \
            os.path.join(os.sep, "data", "pyremotenode", "messages")

        self._sender = SBDSender \
            if "type" not in cfg["ModemConnection"] or cfg["ModemConnection"]["type"] != "certus" \
            else IMTSender

    def ingest(self):
        # TODO: currently available commands, ideally the messageprocessor should gain a list of messages from a
        #  pyremotenode.messages factory and process message headers against their abstract .header_re() method
        # TODO: Check for configurations updates
        re_command = re.compile(b'^(EXECUTE|DOWNLOAD)(?:\s(.+))?\n')

        filelist = os.listdir(self._source)
        sorted_msgs = sorted([f for f in filelist if os.path.isfile(os.path.join(self._source, f))],
                             key=lambda x: datetime.strptime(x[x.index("_")+1:-4], "%d%m%Y%H%M%S"))

        for msg_filename in sorted_msgs:
            try:
                msg_file = os.path.join(self._source, msg_filename)
                logging.info("Processing message file {}".format(msg_file))

                # We read the entire file at this point, currently only single SBDs are the source
                # but if you extend this in the future, you might want to reconsider
                with open(msg_file, "rb") as fh:
                    content = fh.read(os.stat(msg_file).st_size)

                logging.debug("Got content length {}".format(len(content)))

                header_match = re_command.match(content)

                if not header_match:
                    logging.warning("Don't understand directives in {}".format(msg_file))
                    self.move_to(msg_file, "invalid_header")
                    continue

                (command, arg_str) = header_match.groups()
                msg_body = content[header_match.end():]

                try:
                    command = command.decode()
                    arg_str = arg_str.decode()
                except UnicodeDecodeError:
                    logging.exception("Could not decode header information for command")
                    self.move_to(msg_file, "invalid_header")
                    continue

                command = "run_{}".format(command.lower())

                try:
                    func = getattr(self, "{}".format(command))
                except AttributeError:
                    logging.exception("No command available: {}".format(command))
                    self.move_to(msg_file, "invalid_header")
                    continue

                if func(arg_str, msg_body):
                    self.move_to(msg_file)
                else:
                    self.move_to(msg_file, "cmd_failed")
            except Exception:
                logging.exception("Problem encountered processing message {}".format(msg_file))
                self.move_to(msg_file, "failed")

    def move_to(self, msg, reason="processed"):
        try:
            if not os.path.exists(self._archive):
                os.makedirs(self._archive, exist_ok=True)

            os.rename(msg, os.path.join(
                self._archive, "{}.{}".format(os.path.basename(msg), reason)))
        except OSError as e:
            logging.exception("Cannot move error producing message to {}: {}".format(self._archive, e.strerror))
            # If we can't remove, allow the exception to propagate to the caller
            os.unlink(msg)

    def run_execute(self, cmd_str, body, key="pyljXHFxDg58."):
        executed = False
        result = bytearray()

        try:
            if crypt.crypt(body.decode().strip(), 'pyremotenode') != key:
                result += "Invalid execution key\n".encode()
            else:
                result = subprocess.check_output(cmd_str, shell=True)
                logging.info("Successfully executed command {}".format(cmd_str))
                executed = True
        except subprocess.CalledProcessError as e:
            result = "Could not execute command: rc {}".format(e.returncode).encode()
            logging.exception(result)
        except UnicodeDecodeError as e:
            result = "Could not encode return from command : {}".format(e.reason).encode()
            logging.exception(result)

        sbd = self._sender(id="message_execute", binary=True)
        sbd.send_message(result, include_date=True)
        return executed

    def run_download(self, arg_str, body, **kwargs):
        # Format: gzipped? <filename>
        args = shlex.split(arg_str)
        filename = None
        gzipped = False
        chmod = None
        result = []
        downloaded = False

        try:
            filename = args.pop()
        except IndexError:
            result.append("FAILURE: No filename provided")

        while len(args):
            arg = args.pop()
            chmod_match = re.match(r"(\d{3})", arg)

            if arg == "gzip":
                gzipped = True
            elif chmod_match:
                chmod = chmod_match.group(1)
            else:
                result.append("InvArg: {}".format(arg))

        if gzipped:
            body_length = len(body)
            body = gzip.decompress(body)
            logging.info("Decompressed body of {} bytes to one of {} bytes".format(body_length, len(body)))

        if not os.path.exists(filename):
            try:
                logging.info("Outputting file to {}".format(filename))
                with open(filename, "wb") as fh:
                    fh.write(body)

                if chmod:
                    logging.info("Setting {} on {}".format(chmod, filename))
                    os.chmod(filename, int(chmod, 8))
            except (TypeError, ValueError) as e:
                msg = "Conversion error when outputting {}".format(filename)
                logging.exception(msg)
                result.append(msg)
            except OSError as e:
                msg = "OS error {} when outputting {}".format(e.strerror, filename)
                logging.exception(msg)
                result.append(msg)
            else:
                msg = "OK: written {} bytes to {}".format(len(body), filename)
                result.append(msg)
                logging.info(msg)
                downloaded = True
        else:
            msg = "Path already exists, not writing {} bytes to {}".format(len(body), filename)
            result.append(msg)
            logging.info(msg)

        sbd = self._sender(id='message_download', binary=True)
        sbd.send_message("\n{}".format(result), include_date=True)
        return downloaded
