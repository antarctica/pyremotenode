import crypt
import gzip
import logging
import os
import re
import shlex
import stat
import subprocess

from datetime import datetime

from pyremotenode.tasks.iridium import SBDSender


class MessageProcessor(object):
    @staticmethod
    def ingest(scheduler):
        # TODO: no need to pass scheduler unless setting up tasks, use cfg = Configuration().config
        # TODO: currently available commands, ideally the messageprocessor should gain a list of messages from a
        # pyremotenode.messages factory and individually process message headers against their abstract .header_re()
        # method
        # TODO: Check for configurations updates
        re_command = re.compile(b'^(EXECUTE|DOWNLOAD)(?:\s(.+))?\n')

        msg_source = scheduler.settings['msg_inbox'] \
            if 'msg_inbox' in scheduler.settings else os.path.join(
            os.sep, "data", "pyremotenode", "messages")
        msg_archive = scheduler.settings['msg_archive'] \
            if 'msg_archive' in scheduler.settings else os.path.join(
            os.sep, "data", "pyremotenode", "messages", "archive"
        )

        filelist = os.listdir(msg_source)
        sortedmsgs = sorted([f for f in filelist if os.path.isfile(os.path.join(msg_source, f))],
                            key=lambda x: datetime.strptime(x[x.index("_")+1:-4], "%d%m%Y%H%M%S"))

        for msg_filename in sortedmsgs:
            try:
                msg_file = os.path.join(msg_source, msg_filename)
                logging.info("Processing message file {}".format(msg_file))

                # We read the entire file at this point, currently only single SBDs are the source
                # but if you extend this in the future, you might want to reconsider
                with open(msg_file, "rb") as fh:
                    content = fh.read(os.stat(msg_file).st_size)

                logging.debug("Got content length {}".format(len(content)))

                header_match = re_command.match(content)

                if not header_match:
                    MessageProcessor.move_to(msg_archive, msg_file, "invalid_header")
                    continue

                (command, arg_str) = header_match.groups()
                msg_body = content[header_match.end():]

                try:
                    command = command.decode()
                    arg_str = arg_str.decode()
                except UnicodeDecodeError:
                    logging.exception("Could not decode header information for command")
                    MessageProcessor.move_to(msg_archive, msg_file, "invalid_header")
                    continue

                command = "Run{}".format(command.capitalize())

                try:
                    func = getattr(MessageProcessor, "{}".format(command))
                except AttributeError:
                    logging.exception("No command available: {}".format(command))
                    MessageProcessor.move_to(msg_archive, msg_file, "invalid_header")
                    continue

                if func(arg_str, msg_body):
                    MessageProcessor.move_to(msg_archive, msg_file)
                else:
                    MessageProcessor.move_to(msg_archive, msg_file, "cmd_failed")
            except Exception:
                logging.exception("Problem encountered processing message {}".format(msg_file))
                MessageProcessor.move_to(msg_archive, msg_file, "failed")

    @staticmethod
    def move_to(dst, msg, reason="processed"):
        try:
            if not os.path.exists(dst):
                os.makedirs(dst)

            os.rename(msg, os.path.join(
                dst, "{}.{}".format(os.path.basename(msg), reason)))
        except OSError as e:
            logging.exception("Cannot move error producing message to {}: {}".format(dst, e.strerror))
            # If we can't remove, allow the exception to propagate to the caller
            os.unlink(msg)

    @staticmethod
    def RunExecute(cmd_str, body, key="pyljXHFxDg58."):
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
            result += "Could not execute command: rc {}".format(e.returncode).encode()
            logging.exception(result)
        except UnicodeDecodeError as e:
            result += "Could not encode return from command : {}".format(e.reason).encode()
            logging.exception(result)

        sbd = SBDSender(id='message_execute', binary=True)
        sbd.send_message(result[:1920], include_date=True)
        return executed

    @staticmethod
    def RunDownload(arg_str, body, **kwargs):
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

        sbd = SBDSender(id='message_download')
        sbd.send_message("\n".join(result)[:1920], include_date=True)
        return downloaded
