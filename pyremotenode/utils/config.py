import configparser
import logging
import os
import re

DATA_KEYS=(
    'args',
    'invoke_args'
)

ARRAY_SECTIONS=(
    'actions',
)


class ConfigurationError(BaseException):
    pass


class Configuration(object):
    RE_KEY_NUMS = re.compile(r'^(.+[^0-9])([0-9]+)$')

    class __Configuration:
        def __init__(self, path):
            self._path = path

            self.config = {}
            self.parse()

        def parse(self):
            ini = configparser.ConfigParser(delimiters=['='])
            ini.read(self._path)

            # TODO: ini.defaults().items()

            for section in ini.sections():
                if section not in self.config:
                    if section in ARRAY_SECTIONS:
                        self.config[section] = []
                    else:
                        self.config[section] = {}

                cur_section = self.config[section]

                for k in ini.options(section):
                    if type(cur_section) is list:
                        (key, index) = Configuration.RE_KEY_NUMS.match(k).groups()
                        while len(cur_section) < int(index):
                            cur_section.append({})
                        cur_section = self.config[section][int(index)-1]
                    else:
                        key = k
                    cur_section[key] = self.__process_value(ini.get(section, k), key)
                    cur_section = self.config[section]

            if 'read_attempts' in self.config['ModemConnection']:
                raise ConfigurationError("read_attempts is deprecated, serial reads max out based on msg_timeout")

        def __process_value(self, value, key=None):
            force = False
            if key and re.sub(r'[0-9]$', '', key) in DATA_KEYS:
                force = True

            split_data = re.split(r'\s*[={0}]\s*'.format(os.linesep), value.strip(), flags=re.MULTILINE)

            if len(split_data) <= 1 and not force:
                return value
            else:
                b = []
                if len(split_data) % 2 != 0:
                    logging.error("Incorrect set of values for tuple-dict conversion")
                    raise ValueError

                for i in range(0, len(split_data), 2):
                    b.append((split_data[i], split_data[i+1]))
                return dict(b)

    instance = None

    def __init__(self, path=None):
        if not Configuration.instance:
            if not path:
                raise RuntimeError("Configuration has not been instantiated")
            Configuration.instance = Configuration.__Configuration(path)

    def __getattr__(self, item):
        return getattr(self.instance, item)

    @staticmethod
    def check_file(path):
        logging.debug("Checking {0} is a valid configuration to use".format(path))

        if not os.path.exists(path):
            error = "{0} is not a file...".format(path)
            logging.error(error)
            raise OSError(error)
        return path
