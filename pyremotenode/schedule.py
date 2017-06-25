import importlib
import logging
import os
import pkgutil
import sched
import signal
import sys
import time

from datetime import datetime, timedelta

import pyremotenode.communications
import pyremotenode.monitor
from pyremotenode.base import BaseItem, ScheduleConfigurationError
from pyremotenode.utils.system import pid_file

from pprint import pprint


PID_FILE=os.path.join(os.sep, "tmp", "{0}.pid".format(__name__))


class MasterSchedule(object):
    """
        Master scheduler, MUST be run via the main thread
        Doesn't necessarily needs to be a singleton though, just only one starts at a time...

        TODO:
            - dynamic configuration updates. The reason we don't do this yet is I think it's dangerous if comms tunnels
              exist and are actively used - configuration might be better updated through another agent that pulls updates
            - decompose the communicator / monitor structure for the scheduler?
    """

    PRIORITY_SCHEDULE = 1
    PRIORITY_MONITOR = 2
    PRIORITY_COMMUNICATE = 3
    PRIORITY_OTHER = 99

    def __init__(self, configuration):
        logging.debug("Creating scheduler")
        self._cfg = configuration

        self._running = False
        self._communicators = []
        self._monitors = []
        self._schedule = sched.scheduler(
            timefunc=time.time,
            delayfunc=time.sleep)
        self._next_schedule_task_id = None

        self.init()

    def init(self):
        self._check_thread()
        self._configure_signals()
        self._configure_monitors()

        if self.initial_monitor_checks():
            self._configure_communicators()
            self._plan_schedule()
        else:
            logging.warning("Failed on an unhealthy initial check, avoiding scheduler startup...")

    def initial_monitor_checks(self):
        for mon in self._monitors:
            if mon['ref'].monitor() != BaseItem.OK:
                return False
        return True

    def invoke_additional(self):
        """
        Use this to schedule another activity from external sources,
        such as items, immediately
        :return:
        """
        raise NotImplementedError

    def run(self):
        logging.info("Starting scheduler")

        try:
            with pid_file(PID_FILE):
                self._running = True

                while self._running:
                    delay = self._schedule.run(blocking=False)
                    logging.debug("We have {0} seconds until next event...".format(delay))

                    for mon in self._monitors:
                        if mon['ref'].last_status != BaseItem.OK:
                            self.invoke_additional()

        finally:
            if os.path.exists(PID_FILE):
                os.unlink(PID_FILE)

    def stop(self):
        self._running = False

    ################################

    def _check_thread(self):
        """
            TODO: Checks scheduler is running in the main execution thread
        """
        pass

    def _configure_communicators(self):
        logging.info("Configuring communicators")

        for idx, comm in enumerate(self._cfg['communications']):
            comm['args']['scheduler'] = self
            logging.debug("Configuring communicator {0}: type {1}".format(idx, comm['type']))
            obj = CommunicationsItemFactory.get_item(comm['type'], **comm['args'])
            self._communicators.append({ 'conf': comm, 'ref': obj })

    def _configure_monitors(self):
        logging.info("Configuring monitors")

        for idx, monitor in enumerate(self._cfg['monitor']):
            monitor['args']['scheduler'] = self
            logging.debug("Configuring monitor {0}: type {1}".format(idx, monitor['type']))
            obj = MonitorItemFactory.get_item(monitor['type'], **monitor['args'])
            self._monitors.append({ 'conf': monitor, 'ref': obj })

    def _configure_signals(self):
        signal.signal(signal.SIGTERM, self._sig_handler)
        signal.signal(signal.SIGINT, self._sig_handler)

    def _plan_schedule(self):
        # If after 11pm, we plan to the next day
        # If before 11pm, we plan to the end of today
        # We then schedule another _plan_schedule for 11:01pm
        reference = datetime.today()
        next_schedule = reference.replace(hour=23, minute=1, second=0, microsecond=0)
        remaining = next_schedule - reference

        if remaining.days < 0:
            next_schedule = next_schedule + timedelta(days=1)
        elif remaining.days > 0:
            logging.error("Too long until next schedule: {0}".format(remaining))
            sys.exit(1)

        self._schedule.enterabs(
            time=next_schedule.timestamp(),
            action=self._plan_schedule,
            priority=1,
        )

        self._process_schedule_items(reference, next_schedule, self._monitors, MasterSchedule.PRIORITY_MONITOR)
        self._process_schedule_items(reference, next_schedule, self._communicators, MasterSchedule.PRIORITY_COMMUNICATE)

    def _process_absolute_time(self, timestr, start, until):
        (hour, minute) = (int(timestr[:2]), int(timestr[2:]))
        dt = start.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if dt < start:
            dt = dt + timedelta(days=1)

        if dt > until or dt < start:
            logging.error("{0} configuration start time does not fall between {1} and {2}".format(
                timestr, start, until))
            raise ScheduleConfigurationError

        return dt

    def _process_schedule_items(self, start, until, items, priority = PRIORITY_OTHER):
        try:
            for item in items:
                for (dt, action, action_args) in self._process_item_timing(start, until, item):
                    self._schedule.enterabs(
                        time=dt.timestamp(),
                        action=action,
                        priority=priority,
                        kwargs=action_args
                    )
        except:
            raise ScheduleConfigurationError

    def _process_item_timing(self, start, until, item):
        logging.debug("Got item {0}".format(item))
        config = item['conf']
        timings = []
        arguments = {'name': None}

        if 'repeat' in config:
            dt = start + timedelta(minutes=int(config['repeat']))
            arguments['name'] = 'check'

            while dt <= until:
                timings.append([dt, item['ref'].action, arguments])
                dt = dt + timedelta(minutes=int(config['repeat']))
        elif 'start' in config and 'end' in config:
            arguments['name'] = 'start'
            dt_start = self._process_absolute_time(config['start'], start, until)
            timings.append([dt_start, item['ref'].action, arguments])

            arguments['name'] = 'end'
            dt_end = self._process_absolute_time(config['end'], start, until)
            timings.append([dt_end, item['ref'].action, arguments])

            if 'check_interval' in config:
                dt = dt_start + timedelta(minutes=int(config['check_interval']))
                arguments['name'] = 'check'
                while dt < dt_end:
                    timings.append([dt, item['ref'].action, arguments])
                    dt += timedelta(minutes=int(config['check_interval']))
        else:
            logging.error("No compatible timing schedule present for this configuration")
            raise ScheduleConfigurationError

        return timings

    def _sig_handler(self, sig, stack):
        logging.debug("Signal handling {0} at frame {1}".format(sig, stack.f_code))
        self.stop()


class ScheduleItemFactory(object):
    @classmethod
    def get_item(cls, package, type, *args, **kwargs):
        klass_name = ScheduleItemFactory.get_klass_name(type)

        for mod in pkgutil.walk_packages(package.__path__):
            imported = importlib.import_module(".".join([package.__name__, mod[1]]))
            if hasattr(imported, klass_name):
                return getattr(imported, klass_name)(*args, **kwargs)

        logging.error("No class named {0} found".format(klass_name))
        raise ReferenceError

    @classmethod
    def get_klass_name(cls, name):
        return "".join([seg.capitalize() for seg in name.split("_")])


class CommunicationsItemFactory(ScheduleItemFactory):
    @classmethod
    def get_item(cls, type, *args, **kwargs):
        return ScheduleItemFactory.get_item(pyremotenode.communications, type, *args, **kwargs)


class MonitorItemFactory(ScheduleItemFactory):
    @classmethod
    def get_item(cls, type, *args, **kwargs):
        return ScheduleItemFactory.get_item(pyremotenode.monitor, type, *args, **kwargs)
