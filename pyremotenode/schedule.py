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
import pyremotenode.invocations
import pyremotenode.monitor
from pyremotenode.base import BaseItem, ScheduleConfigurationError, ScheduleRunError
from pyremotenode.utils.system import pid_file


PID_FILE = os.path.join(os.sep, "tmp", "{0}.pid".format(__name__))


class MasterSchedule(object):
    """
        Master scheduler, MUST be run via the main thread
        Doesn't necessarily needs to be a singleton though, just only one starts at a time...

        TODO (to evaluate OR implement):
            - mutual exclusion is currently the responsibility of implementations
            - defer monitor events that run past time - thus avoiding multiple invocations of handlers
            - handler identification - one invocation per monitor in warning / critical scenarios!
    """

    PRIORITY_IMMEDIATE = 1
    PRIORITY_SCHEDULE = 2
    PRIORITY_MONITOR = 3
    PRIORITY_COMMUNICATE = 4
    PRIORITY_GENERIC = 5
    PRIORITY_OTHER = 99

    start_when_fail = False

    def __init__(self, configuration,
                 start_when_fail=start_when_fail):
        logging.debug("Creating scheduler")
        self._cfg = configuration

        self.start_when_fail = start_when_fail

        self._running = False

        self._schedule_events = []
        self._communicators = []
        self._monitors = []
        self._invocations = []

        self._schedule = sched.scheduler(
            timefunc=time.time,
            delayfunc=time.sleep)
        self._next_schedule_task_id = None

        self.init()

    def init(self):
        self._check_thread()
        self._configure_signals()
        self._configure_monitors()

        if self.start_when_fail or self.initial_monitor_checks():
            self._configure_communicators()
            self._plan_schedule()
        else:
            raise ScheduleRunError("Failed on an unhealthy initial check, avoiding scheduler startup...")

    def initial_monitor_checks(self):
        for mon in self._monitors:
            if mon['ref'].monitor() != BaseItem.OK:
                return False
        return True

    def invoke_warning(self, item):
        """
        Use this to schedule warning invocation tasks from monitors, this will directly
        affect the schedule...
        :return:
        """

        # TODO: What's our behaviour in this situation (cancel comms to prioritise messaging!?!)
        self.invoke(item['conf'], key='warn', priority=MasterSchedule.PRIORITY_IMMEDIATE)

    def invoke_critical(self, item):
        """
        User this to schedule critical invocation tasks from monitors
        :return:
        """

        self._wipe_schedule()
        # We should now have an empty list, schedule the critical invocation
        self.invoke(item['conf'], key='crit', priority=MasterSchedule.PRIORITY_IMMEDIATE)

    def invoke(self, cfg,
               key='invoke', future=0, priority=PRIORITY_GENERIC):
        """
        This can be called (internally or externally) to schedule items
        either immediately (default) or in the future
        :param cfg:
        :param key:
        :param future:
        :param priority:
        :return:
        """

        obj = InvocationsItemFactory.get_item(cfg[key], **cfg["{}_args".format(key)])
        action_args = {}

        evt = self._schedule.enterabs(
            time=datetime.now().timestamp() + future,
            action=obj.action,
            priority=priority,
            kwargs=action_args
        )
        # TODO: Link / group invocations together, especially for communications, reduce conflicts of ongoing events
        self._invocations.append(evt)

    def run(self):
        logging.info("Starting scheduler")

        try:
            with pid_file(PID_FILE):
                self._running = True

                while self._running:
                    delay = self._schedule.run(blocking=False)
                    logging.debug("We have {0} seconds until next event...".format(delay))

                    time_start = time.time()

                    worst_mon = None

                    for mon in self._monitors:
                        if worst_mon and mon['ref'].last_status > worst_mon['ref'].last_status:
                            worst_mon = mon
                        else:
                            worst_mon = mon

                    if worst_mon['ref'].last_status == BaseItem.WARNING:
                        self.invoke_warning(worst_mon)
                    elif worst_mon['ref'].last_status == BaseItem.CRITICAL:
                        self.invoke_critical(worst_mon)
                        continue

                    # NOTE: no current action for invalid statuses, I'm just assuming the
                    # check is knackered and can be rerun but this may not be optimal

                    time_spent = time.time() - time_start

                    # We spent too long handling monitor states
                    if time_spent > delay:
                        continue
                    else:
                        time.sleep(delay - time_spent)
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
            self._communicators.append({
                'conf': comm,
                'events': [],
                'ref': obj
            })

    def _configure_monitors(self):
        logging.info("Configuring monitors")

        for idx, monitor in enumerate(self._cfg['monitor']):
            monitor['args']['scheduler'] = self
            logging.debug("Configuring monitor {0}: type {1}".format(idx, monitor['type']))
            obj = MonitorItemFactory.get_item(monitor['type'], **monitor['args'])
            self._monitors.append({
                'conf': monitor,
                'events': [],
                'ref': obj
            })

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

        # TODO: Clear the event records from previous plans
        #       - this involves sorting the Event objects, as per the heap queue
        #         then popping them out of the arrays until only valid items are left

        evt = self._schedule.enterabs(
            time=next_schedule.timestamp(),
            action=self._plan_schedule,
            priority=1,
        )
        self._schedule_events.append(evt)

        self._process_schedule_items(reference, next_schedule, self._monitors, MasterSchedule.PRIORITY_MONITOR)
        self._process_schedule_items(reference, next_schedule, self._communicators, MasterSchedule.PRIORITY_COMMUNICATE)

    def _process_absolute_time(self, timestr, start, until):
        (hour, minute) = (int(timestr[:2]), int(timestr[2:]))
        dt = start.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if dt < start:
            dt = dt + timedelta(days=1)

        if dt > until or dt < start:
            logging.warning("{0} configuration start time does not fall between {1} and {2}".format(
                timestr, start, until))
            return None

        return dt

    def _process_schedule_items(self, start, until, items, priority = PRIORITY_OTHER):
        try:
            for item in items:
                for (dt, action, action_args) in self._process_item_timing(start, until, item):
                    evt = self._schedule.enterabs(
                        time=dt.timestamp(),
                        action=action,
                        priority=priority,
                        kwargs=action_args
                    )
                    item['events'].append(evt)
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
            if dt_start:
                timings.append([dt_start, item['ref'].action, arguments.copy()])

                arguments['name'] = 'end'
                dt_end = self._process_absolute_time(config['end'], start, until)
                timings.append([dt_end, item['ref'].action, arguments.copy()])

                if 'check_interval' in config:
                    dt = dt_start + timedelta(minutes=int(config['check_interval']))
                    arguments['name'] = 'check'
                    while dt < dt_end:
                        timings.append([dt, item['ref'].action, arguments])
                        dt += timedelta(minutes=int(config['check_interval']))
            else:
                logging.debug("No available start time could be determined so not scheduling events for this item")
        else:
            logging.error("No compatible timing schedule present for this configuration")
            raise ScheduleConfigurationError

        return timings

    def _sig_handler(self, sig, stack):
        logging.debug("Signal handling {0} at frame {1}".format(sig, stack.f_code))
        self.stop()

    def _wipe_schedule(self):
        for item in self._communicators:
            item['ref'].action('stop')

        for event in self._invocations + self._schedule_events + \
                [items['events'] for items in self._communicators + self._monitors]:
            if event in self._schedule.queue:
                self._schedule.cancel(event)


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


class InvocationsItemFactory(ScheduleItemFactory):
    @classmethod
    def get_item(cls, type, *args, **kwargs):
        return ScheduleItemFactory.get_item(pyremotenode.invocations, type, *args, **kwargs)


class MonitorItemFactory(ScheduleItemFactory):
    @classmethod
    def get_item(cls, type, *args, **kwargs):
        return ScheduleItemFactory.get_item(pyremotenode.monitor, type, *args, **kwargs)
