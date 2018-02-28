import logging
import os
import signal
import time as tm
import sys

import pyremotenode
import pyremotenode.tasks

from apscheduler.schedulers.background import BlockingScheduler
from datetime import date, datetime, time, timedelta
from pprint import pformat
from pyremotenode.utils.system import pid_file
from pytz import utc

PID_FILE = os.path.join(os.sep, "tmp", "{0}.pid".format(__name__))


class Scheduler(object):
    """
        Master scheduler, MUST be run via the main thread
        Doesn't necessarily needs to be a singleton though, just only one starts at a time...
    """

    def __init__(self, configuration,
                 start_when_fail=False,
                 pid_file=PID_FILE):
        logging.info("Creating scheduler")
        self._cfg = configuration
        self._pid = pid_file

        self._running = False
        self._start_when_fail = start_when_fail

        self._schedule = BlockingScheduler(timezone=utc)
        self._schedule_events = []
        self._schedule_action_instances = {}
        self._schedule_task_instances = {}

        self.init()

    def init(self):
        self._configure_signals()
        self._configure_instances()

        if self._start_when_fail or self.initial_checks():
            self._plan_schedule()
        else:
            raise ScheduleRunError("Failed on an unhealthy initial check, avoiding scheduler startup...")

    def initial_checks(self):
        for action in ['on_start' in cfg and cfg['on_start'] for cfg in self._cfg['actions']]:
            # TODO: How do we execute arbitary actions via the same mechanism as apscheduler?
            pass
        return True

    def run(self):
        logging.info("Starting scheduler")

        try:
            with pid_file(self._pid):
                self._running = True

                self._schedule.print_jobs()
                self._schedule.start()
# TODO: BackgroundScheduler - use once we need it...
#                while self._running:
#                    try:
#                        logging.debug("Scheduler sleeping")
#                        tm.sleep(10)
#
#                        # TODO: Check for configurations / updates
#                    except Exception:
#                        logging.error("Error in main thread, something very wrong!")
        finally:
            if os.path.exists(PID_FILE):
                os.unlink(PID_FILE)

    def stop(self):
        self._running = False
        # TODO: Check shutdown process
        self._schedule.shutdown()

    def add_ok(self, id):
        action = self._build_configuration(id, 'ok')
        if action:
            logging.debug("Submitting ok-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def add_warning(self, id):
        action = self._build_configuration(id, 'warn')
        if action:
            logging.debug("Submitting warning-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def add_critical(self, id):
        action = self._build_configuration(id, 'crit')
        if action:
            logging.debug("Submitting critical-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def add_invalid(self, id):
        action = self._build_configuration(id, 'invalid')
        if action:
            logging.debug("Submitting invalid-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def schedule_immediate_action(self, obj, id, args):
        if obj and id:
            self._schedule.add_job(obj,
                                   id=id,
                                   coalesce=False,
                                   max_instances=1,
                                   misfire_grace_time=60,
                                   kwargs=args)

    # ==================================================================================

    def _configure_instances(self):
        logging.info("Configuring tasks from defined actions")

        for idx, cfg in enumerate(self._cfg['actions']):
            logging.debug("Configuring action instance {0}: type {1}".format(idx, cfg['task']))
            action = SchedulerAction(cfg)
            obj = TaskInstanceFactory.get_item(
                id=cfg['id'],
                scheduler=self,
                task=cfg['task'],
                **cfg['args'])

            # TODO: Do we want to retain the processing order past here? It is not a logic driver so no is my inclination
            self._schedule_action_instances[cfg['id']] = action
            self._schedule_task_instances[cfg['id']] = obj

    def _configure_signals(self):
        signal.signal(signal.SIGTERM, self._sig_handler)
        signal.signal(signal.SIGINT, self._sig_handler)

    def _build_configuration(self, id, task_type):
        """
        Build an alternate action configuration based on the original
        :param type:
        :return: an alternate configuration
        """
        # TODO: Allow multiple actions per configuration, limited at present
        action = self._schedule_action_instances[id]
        if not action[task_type]:
            return None

        kwargs = action["{}_args".format(type)] if "{}_args".format(type) in action else {}
        kwargs['invoking_task'] = self._schedule_task_instances[id]

        id = "{}_{}".format(action['id'], datetime.now().strftime("%H%m%s"))

        # NOTE: We don't provide scheduler, triggered actions can't invoke further events (yet)
        obj = TaskInstanceFactory.get_item(
            id=id,
            task=action[task_type],
            **kwargs
        )
        return {
            'id':   id,
            'task': task_type,
            'obj':  obj,
            'args': kwargs
        }

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

        self._schedule.remove_all_jobs()

        job = self._schedule.add_job(self._plan_schedule,
                                     id='next_schedule',
                                     next_run_time=next_schedule,
                                     replace_existing=True)

        self._schedule_events.append(job)

        self._plan_schedule_tasks(reference, next_schedule)

    def _plan_schedule_tasks(self, start, until):
        # TODO: This needs to take account of wide spanning controls!
        # TODO: grace period for datetime.now()
        start = datetime.now()

        try:
            for id, action in self._schedule_action_instances.items():
                logging.info("Planning {}".format(id))
                self._plan_schedule_task(start, until, action)
        except:
            raise ScheduleConfigurationError

    def _plan_schedule_task(self, start, until, action):
        logging.debug("Got item {0}".format(action))
        timings = []
        cron_args = ('year','minute','day','week','day_of_week','hour','minute','second','start_date','end_date')

        # NOTE: Copy this before changing, or when passing!
        kwargs = action['args']

        obj = self._schedule_task_instances[action['id']]

        if 'onboot' in action:
            self._schedule.add_job(obj,
                                   id="boot_{}".format(action['id']),
                                   coalesce=False,
                                   max_instances=1,
                                   misfire_grace_time=60,
                                   kwargs=kwargs)

        if 'interval' in action:
            logging.debug("Scheduling interval based job")

            self._schedule.add_job(obj,
                                   id=action['id'],
                                   trigger='interval',
                                   minutes=int(action['interval']),
                                   coalesce=True,
                                   max_instances=1,
                                   kwargs=kwargs)
        elif 'interval_secs' in action:
            logging.debug("Scheduling seconds based interval job")

            self._schedule.add_job(obj,
                                   id=action['id'],
                                   trigger='interval',
                                   seconds=int(action['interval_secs']),
                                   coalesce=True,
                                   max_instances=1,
                                   kwargs=kwargs)
        elif 'date' in action or 'time' in action:
            logging.debug("Scheduling standard job")

            dt = Scheduler.parse_datetime(action['date'], action['time'])

            if datetime.now() > dt:
                logging.info("Job ID: {} does not need to be scheduled as it is prior to current time".format(action['id']))
            elif dt > until:
                logging.info(
                    "Job ID: {} does not need to be scheduled as it is after the next schedule planning time".
                    format(action['id']))
            else:
                self._schedule.add_job(obj,
                                       id=action['id'],
                                       trigger='date',
                                       coalesce=True,
                                       max_instances=1,
                                       run_date=dt,
                                       kwargs=kwargs)
        elif any(k in cron_args for k in action):
            logging.debug("Scheduling cron style job")

            job_args = dict([(k, action[k]) for k in cron_args if k in action])
            logging.debug(job_args)
            self._schedule.add_job(obj,
                                   id=action['id'],
                                   trigger='cron',
                                   coalesce=True,
                                   max_instances=1,
                                   kwargs=kwargs,
                                   **job_args)
        else:
            if 'onboot' not in action:
                logging.error("No compatible timing schedule present for this configuration")
                raise ScheduleConfigurationError
            else:
                logging.warning("{} will only be run at startup".format(action['id']))
        return timings

    @staticmethod
    def parse_datetime(date_str, time_str):
        logging.debug("Parsing date: {} and time: {}".format(date_str, time_str))

        try:
            if time_str is not None:
                tm = datetime.strptime(time_str, "%H%M").time()
            else:
                # TODO: Make this sit within the operational window for the day in question
                tm = time(12)

            if date_str is not None:
                parsed_dt = datetime.strptime(date_str, "%d%m").date()
                year = datetime.now().year
                dt = datetime(year=year, month=parsed_dt.month, day=parsed_dt.day)
            else:
                dt = datetime.today().date()
        except ValueError:
            raise ScheduleConfigurationError("Date: {} Time: {} not valid in configuration file".format(date_str,
                                                                                                        time_str))

        return datetime.combine(dt, tm)

    def _sig_handler(self, sig, stack):
        logging.debug("Signal handling {0} at frame {1}".format(sig, stack.f_code))
        self.stop()


class SchedulerAction(object):
    def __init__(self, action_config):
        self._cfg = action_config

    def __setitem__(self, key, value):
        self._cfg[key] = value

    def __getitem__(self, key):
        try:
            return self._cfg[key]
        except KeyError:
            return None

    def __iter__(self):
        self.__iter = iter(self._cfg)
        return iter(self._cfg)

    def __next__(self):
        return self.__iter.next()

    def __str__(self):
        return "SchedulerAction config {}".format(pformat(self._cfg))


class TaskInstanceFactory(object):
    @classmethod
    def get_item(cls, id, task, scheduler=None, **kwargs):
        klass_name = TaskInstanceFactory.get_klass_name(task)

        if hasattr(pyremotenode.tasks, klass_name):
            # TODO: warning and critical object creation or configuration supply
            return getattr(pyremotenode.tasks, klass_name)(
                id=id, scheduler=scheduler, **kwargs)

        logging.error("No class named {0} found in pyremotenode.tasks".format(klass_name))
        raise ReferenceError

    @classmethod
    def get_klass_name(cls, name):
        return name.split(":")[-1]


class ScheduleRunError(Exception):
    pass


class ScheduleConfigurationError(Exception):
    pass

