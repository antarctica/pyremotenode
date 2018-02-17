import glob
import logging
import os
import re

from datetime import datetime, timedelta, time
from operator import itemgetter
from pyremotenode.tasks import BaseTask

from pyremotenode.tasks.iridium import SBDSender


# TODO: Might be better to thread this, and add an execution check for the pre-processing
class SendLoHBaselines(BaseTask):
    _re_stations = re.compile(r'(\w{4})_\d{6}_(\w{4})_.+\.csv')
    _re_bs_data = re.compile(r'')

    def __init__(self, source, **kwargs):
        super(SendLoHBaselines, self).__init__(**kwargs)
        self._source = source

    def default_action(self, fields, days_behind=1, **kwargs):
        logging.info("Processing LoH baseline data to send via SBD")
        sbd = SBDSender(id='loh_baseline_sbd', **kwargs)
        data_fields = ('dt', 'tm', 'e', 'n', 'u', 'q', 'ns', 'sde', 'sdn', 'sdu', 'sden', 'sdnu', 'sdue', 'age', 'ratio')
        field_selection = []
        for x in fields.split(","):
            field_selection.append(data_fields.index(x))
        df = itemgetter(*field_selection)

        dt = datetime.now() - timedelta(days=days_behind)
        (year, month, day) = (str(dt.year)[2:], "{:02d}".format(dt.month), "{:02d}".format(dt.day))

        date_str = month + day + year
        files = glob.glob(os.path.join(self._source, "*_{}_*_*.csv".format(date_str)))
        logging.info("Grabbed {} files in {} matching date pattern {}".format(len(files), self._source, date_str))

        for bs_file in files:
            logging.info("Processing file {}".format(bs_file))
            filename = os.path.basename(bs_file)

            match = self._re_stations.match(filename)
            if not match:
                logging.warning("Could not process details for {}".format(filename))
                continue

            details = [match.group(1), match.group(2)]

            with open(bs_file) as gps_data:
                for line in gps_data:
                    if line.startswith('%') or not len(line):
                        continue

                    data = list(df(line.split()))

                    # Sanitise the data a little, get rid of subsecond times
                    data[data_fields.index('tm')] = data[data_fields.index('tm')][:-4]

                    # Encode the data, save a little data...
                    data_str = ",".join(details + data)

                    if len(data_str) > 120:
                        logging.warning("Message is too long: {}".format(data_str))
                    sbd.send_message(data_str, include_date=False)



