import configparser
import logging
import os
import re

DATA_KEYS=(
    'invoke_args',
)

class Configuration(object):
    class __Configuration:
        def __init__(self, path):
            self._path = path

            self.config = {}
            self.parse()

        def parse(self):
            ini = configparser.ConfigParser()
            ini.read(self._path)

            for key, value in ini.defaults().items():
                self.config[key] = self.__process_value(value)

            for section in ini.sections():
                if section not in self.config:
                    self.config[section] = {}

                for key in ini.options(section):
                    self.config[section][key] = self.__process_value(ini.get(section, key), key)

        def __process_value(self, value, key=None):
            force = False
            if key and re.sub(r'[0-9]$', '', key) in DATA_KEYS:
                force = True

            split_data = re.split(r'\s*[=:{0}]\s*'.format(os.linesep), value, flags=re.MULTILINE)

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

    def __init__(self, path):
        if not Configuration.instance:
            Configuration.instance = Configuration.__Configuration(path)

    def __getattr__(self, item):
        return getattr(self.instance, item)

    @staticmethod
    def check_file(path):
        logging.debug("Checking {0} is a valid configuration to use".format(path))
        return path



