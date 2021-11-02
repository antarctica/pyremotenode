import glob
import logging
import os
import pickle
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
        self._proclist_path = os.path.join(self._source, "proclist.pickle")
        self._proclist = {}

        self._load_proclist()

    def _load_proclist(self):
        if not os.path.exists(self._proclist_path):
            logging.warning("No available processed files list, we'll start a new one")
            return

        try:
            logging.debug("Loading processed files list from {}".format(self._proclist_path))
            with open(self._proclist_path, "rb") as pf:
                self._proclist = pickle.load(pf)
        except pickle.PickleError:
            logging.warning("Unable to load the list, a new one should get generated...")
        except (OSError, IOError):
            logging.warning("Something went wrong accessing the proclist file, abandoning")

    def _save_proclist(self):
        try:
            logging.debug("Saving process files list to {}".format(self._proclist_path))
            with open(self._proclist_path, "wb") as pf:
                pickle.dump(self._proclist, pf)
        except (OSError, IOError, pickle.PickleError):
            logging.warning("Something went wrong pickling the data structure out, abandoning and unlinking")
            if os.path.exists(self._proclist_path):
                os.unlink(self._proclist_path)

    def default_action(self, fields, days_behind=1, **kwargs):
        logging.info("Processing LoH baseline data to send via SBD")
        sbd = SBDSender(id='loh_baseline_sbd', **kwargs)
        data_fields = ('dt', 'tm', 'e', 'n', 'u', 'q', 'ns', 'sde', 'sdn', 'sdu', 'sden', 'sdnu', 'sdue', 'age', 'ratio')
        aggr_fields = ('e', 'n', 'u', 'q', 'ns', 'sde', 'sdn', 'sdu', 'sden', 'sdnu', 'sdue', 'age', 'ratio')
        field_selection = []
        for x in fields.split(","):
            field_selection.append(data_fields.index(x))
        df = itemgetter(*field_selection)

        days_behind = int(days_behind)
        current_day = 1
        min_dt = datetime.combine((datetime.utcnow() - timedelta(days=days_behind)).date(), time(hour=0, minute=0, second=0))

        self._proclist = {fn: fn_dt for fn, fn_dt in self._proclist.items() if fn_dt >= min_dt}

        while current_day <= days_behind:
            logging.info("Processing files from {} days ago".format(current_day))

            dt = datetime.utcnow() - timedelta(days=current_day)
            (year, month, day) = (str(dt.year)[2:], "{:02d}".format(dt.month), "{:02d}".format(dt.day))
            source_path = os.path.join(self._source, str(dt.year), str(dt.month), str(dt.day))

            date_str = month + day + year
            files = glob.glob(os.path.join(source_path, "*_{}_*_*.csv".format(date_str)))
            logging.info("Grabbed {} files in {} matching date pattern {}".format(len(files), source_path, date_str))

            if not len(files):
                current_day += 1
                continue

            data = {}

            for bs_file in files:
                filename = os.path.basename(bs_file)

                if filename in self._proclist.keys():
                    logging.debug("Skipping file {} as it has already been processed".format(filename))
                    continue
                logging.info("Processing file {}".format(bs_file))

                match = self._re_stations.match(filename)
                if not match:
                    logging.warning("Could not process details for {}".format(filename))
                    continue

                bs_key = (match.group(1), match.group(2))
                if bs_key not in data:
                    data[bs_key] = []

                with open(bs_file) as gps_data:
                    for line in gps_data:
                        if line.startswith('%') or not len(line):
                            continue

                        data[bs_key].append(line.split())

                self._proclist[filename] = dt

            aggregates = {}

            for bs_pair in data.keys():
                bs_data = []

                for q in range(1, 3):
                    # Get Q rated data
                    bs_data = [d for d in data[bs_pair] if int(d[data_fields.index('q')]) == q]
                    if len(bs_data):
                        logging.debug("Got Q{} data to aggregate for {} - {}".format(q, bs_pair[0], bs_pair[1]))
                        aggregates[bs_pair] = []

                        for idx, f in enumerate(data_fields):
                            if f in aggr_fields:
                                aggregates[bs_pair].append(0.)
                            else:
                                aggregates[bs_pair].append(None)
                        break

                if not len(bs_data):
                    logging.debug("No valid quality data for pairing {} - {}".format(bs_pair[0], bs_pair[1]))
                    continue

                for d in bs_data:
                    for idx, f in enumerate(data_fields):
                        if f in aggr_fields:
                            aggregates[bs_pair][idx] += float(d[idx])
                        else:
                            aggregates[bs_pair][idx] = d[idx]

                for f in aggr_fields:
                    aggregates[bs_pair][data_fields.index(f)] = \
                        round(aggregates[bs_pair][data_fields.index(f)] / len(bs_data), 4)

            for bs_pair, bs_agg_data in aggregates.items():
                logging.debug("Sending message for aggregated data {} - {}".format(bs_pair[0], bs_pair[1]))
                agg_data = list(bs_pair) + list(df(bs_agg_data))
                data_str = ",".join([str(df) for df in agg_data])

                if len(data_str) > 1920:
                    logging.warning("Message is too long: {}".format(data_str))
                sbd.send_message(data_str, include_date=False)

            current_day += 1

        self._save_proclist()

