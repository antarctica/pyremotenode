import logging
import os
import signal
import time as tm
import sys

from datetime import datetime, time, timedelta
from pprint import pformat
from pytz import utc

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.events import EVENT_SCHEDULER_START, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, EVENT_JOB_ERROR

import pyremotenode
import pyremotenode.tasks

from pyremotenode.messaging import MessageProcessor
from pyremotenode.utils.system import pid_file


class Scheduler(object):
    """
        :type Scheduler

        Master scheduler, MUST be run via the main thread, not derived from anything else and wraps APScheduler
        Doesn't necessarily needs to be a singleton though, just only one starts at a time...
    """

    def __init__(self, configuration,
                 start_when_fail=False,
                 pid_file=None):
        """
        Constructor for the scheduler, needs to be instantiated for PyRemoteNode

        :param configuration:       pyremotenode.utils.config.Configuration instance
        :param start_when_fail:     allows scheduler to start even when scheuling planning fails
                                    (eg. with invalid tasks): sounds good, but be careful as you might be ignoring
                                    items that will bite you in the arse later!
        :param pid_file:            PID file that the scheduler should manage during its lifetime
        """
        logging.info("Creating scheduler")
        self._cfg = configuration
        self._pid = pid_file

        self._running = False
        self._start_when_fail = start_when_fail

        self._schedule = BackgroundScheduler(timezone=utc)
        self._schedule_events = []
        self._schedule_action_instances = {}
        self._schedule_task_instances = {}

        self.init()

    def init(self):
        """
        Actual initialiser, called from constructor, configures signal trap handling, task instances and schedule plan.
        Initial checks are also meant to be run (labelled with on_start)

        :exception: ScheduleRunError if we can't plan or initialise the scheduler with tasks
        :return:    None
        """
        self._configure_signals()
        self._configure_instances()

        if self._start_when_fail or self.initial_checks():
            self._plan_schedule()
        else:
            raise ScheduleRunError("Failed on an unhealthy initial check, avoiding scheduler startup...")

    def initial_checks(self):
        """
        Run checks labelled with on_start
        TODO: How do we execute arbitary actions via the same mechanism as apscheduler?

        :return: Boolean for initial checks are successful
        """
        for action in ['on_start' in cfg and cfg['on_start'] for cfg in self._cfg['actions']]:
            pass
        return True

    def run(self):
        """
        This is the primary scheduling implementation, which utilises the BackgroundScheduler to process actions leaving
        the main thread active to process MT messages that might arrive (initiating new actions in the plan, potentially),
        configuration updates and any other activities that might be implemented in the future to control the scheduler.

        In theory this should not exit at present, unless activities are added allowing shutdown

        :return:    None
        """
        hk_sleep = 60 if "housekeeping_sleep" not in self.settings else \
            int(self.settings['housekeeping_sleep'])

        logging.info("Starting scheduler")
        logging.debug("Housekeeping at {} second intervals".format(hk_sleep))

        try:
            with pid_file(self._pid):
                msg_processor = MessageProcessor(self._cfg)
                self._running = True

                self._schedule.print_jobs()
                self._schedule.start()

                while self._running:
                    try:
                        tm.sleep(int(hk_sleep))
                        msg_processor.ingest()
                    except Exception:
                        logging.exception("Error in main thread, something very wrong, schedule will continue...")
        finally:
            # TODO: I don't think this ever applies thanks to the context manager
            if self._pid and os.path.exists(self._pid):
                os.unlink(self._pid)

    def stop(self):
        self._running = False
        # TODO: Check shutdown process
        self._schedule.shutdown()

    def add_ok(self, job_id):
        action = self._build_configuration(job_id, 'ok')
        if action:
            logging.debug("Submitting ok-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def add_warning(self, job_id):
        action = self._build_configuration(job_id, 'warn')
        if action:
            logging.debug("Submitting warning-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def add_critical(self, job_id):
        action = self._build_configuration(job_id, 'crit')
        if action:
            logging.debug("Submitting critical-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def add_invalid(self, job_id):
        action = self._build_configuration(job_id, 'invalid')
        if action:
            logging.debug("Submitting invalid-status invocation id {}".format(action['id']))
            self.schedule_immediate_action(action['obj'], action['id'], action['args'])

    def schedule_immediate_action(self, obj, job_id, args):
        if obj and job_id:
            # NOTE: 3.0.6 suffers from apscheduler issue #133 if
            # system datetime is not UTC
            return self._schedule.add_job(obj,
                                          id=job_id,
                                          coalesce=False,
                                          max_instances=1,
                                          misfire_grace_time=1200,
                                          kwargs=args)

    @property
    def settings(self):
        return self._cfg['general']

    # ==================================================================================

    def _configure_instances(self):
        logging.info("Configuring tasks from defined actions")

        for idx, cfg in enumerate(self._cfg['actions']):
            logging.debug("Configuring action instance {0}: type {1}".format(idx, cfg['task']))
            action = SchedulerAction(cfg)
            args = dict() if 'args' not in cfg else cfg['args']
            obj = TaskInstanceFactory.get_item(
                id=cfg['id'],
                scheduler=self,
                task=cfg['task'],
                **args)

            # TODO: Do we want to retain the processing order past here?
            #  It is not a logic driver so no is my inclination
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

        kwargs = action["{}_args".format(task_type)] if "{}_args".format(task_type) in action else {}
        kwargs['invoking_task'] = self._schedule_task_instances[id]

        id = "{}_{}".format(action['id'], datetime.utcnow().strftime("%H%m%s"))

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
        # TODO: Next scheduler planning should be configurable
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

        self._plan_schedule_tasks(next_schedule)

    def _plan_schedule_tasks(self, until):
        # TODO: This needs to take account of wide spanning controls!
        # TODO: grace period for datetime.utcnow()
        start = datetime.utcnow()

        try:
            for job_id, action in self._schedule_action_instances.items():
                logging.info("Planning {}".format(job_id))
                self._plan_schedule_task(until, action)
        except:
            raise ScheduleConfigurationError

    def _plan_schedule_task(self, until, action):
        logging.debug("Got item {0}".format(action))
        timings = []
        cron_args = ('year', 'month', 'day', 'week', 'day_of_week', 'hour',
                     'minute', 'second', 'start_date', 'end_date')

        # NOTE: Copy this before changing, or when passing!
        kwargs = action['args']

        obj = self._schedule_task_instances[action['id']]
        job = None

        misfire_grace_time = None

        if 'misfire_secs' in action:
            logging.info("Grace time on job ID {} will be {} seconds".format(action['id'], action['misfire_secs']))
            misfire_grace_time = int(action['misfire_secs'])

        if 'onboot' in action:
            job = self.schedule_immediate_action(obj,
                                                 "onboot_{}".format(action['id']),
                                                 kwargs)

        if 'interval' in action:
            logging.debug("Scheduling interval based job")

            job = self._schedule.add_job(obj,
                                   id=action['id'],
                                   trigger='interval',
                                   minutes=int(action['interval']),
                                   coalesce=True,
                                   max_instances=1,
                                   misfire_grace_time=misfire_grace_time,
                                   kwargs=kwargs)
        elif 'interval_secs' in action:
            logging.debug("Scheduling seconds based interval job")

            job = self._schedule.add_job(obj,
                                   id=action['id'],
                                   trigger='interval',
                                   seconds=int(action['interval_secs']),
                                   coalesce=True,
                                   max_instances=1,
                                   misfire_grace_time=misfire_grace_time,
                                   kwargs=kwargs)
        elif 'date' in action or 'time' in action:
            logging.debug("Scheduling standard job")

            dt = Scheduler.parse_datetime(action['date'], action['time'])

            if datetime.utcnow() > dt:
                logging.info("Job ID: {} needs to be scheduled tomorrow, it is prior to current time".format(action['id']))
                dt += timedelta(days=1)

            if dt > until:
                logging.info(
                    "Job ID: {} does not need to be scheduled as it is after the next schedule planning time".
                    format(action['id']))
            else:
                job = self._schedule.add_job(obj,
                                       id=action['id'],
                                       trigger='date',
                                       coalesce=True,
                                       max_instances=1,
                                       run_date=dt,
                                       misfire_grace_time=misfire_grace_time,
                                       kwargs=kwargs)
        elif any(k in cron_args for k in action):
            logging.debug("Scheduling cron style job")

            job_args = dict([(k, action[k]) for k in cron_args if k in action])
            logging.debug(job_args)
            job = self._schedule.add_job(obj,
                                   id=action['id'],
                                   trigger='cron',
                                   coalesce=True,
                                   max_instances=1,
                                   misfire_grace_time=misfire_grace_time,
                                   kwargs=kwargs,
                                   **job_args)
        else:
            if 'onboot' not in action:
                logging.error("No compatible timing schedule present for this configuration")
                raise ScheduleConfigurationError
            else:
                logging.warning("{} will only be run at startup".format(action['id']))

        # TODO: Waiton is not working for script executions as the schedule job is not active during execution!
        if job and 'waiton' in action:
            # TODO: We can add further parameters for checking the event, at the mo we just care that it's run
            def resume_job(evt):
                if (evt.job_id == action['waiton'] or evt.job_id == "onboot_{}".format(action['waiton']))\
                        and evt.code == EVENT_JOB_EXECUTED:
                    logging.info("Resuming execution of job ID {}".format(action['id']))
                    job.resume()

            def pause_job(evt):
                logging.info("Setting job ID {} to wait for {}".format(action['id'], action['waiton']))
                job.pause()

            self._schedule.add_listener(resume_job, EVENT_JOB_EXECUTED)
            self._schedule.add_listener(pause_job, EVENT_SCHEDULER_START)

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
                year = datetime.utcnow().year
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

        # TODO: add possibility for plugins via broad include
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

