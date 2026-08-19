# SentinelCam deployment summary callback
#
# An aggregate callback, so it runs alongside the `yaml` stdout callback rather than
# replacing it: the full task detail still goes to the deployment log, and this records
# a structured record of what each playbook actually did. One JSON object per
# ansible-playbook invocation, appended, so a pipeline run that walks five playbooks
# leaves five records for `render-deployment-summary.py` to fold into one table.
#
# Reporting must never be able to fail a deployment. Every hook is wrapped, and any
# error here is swallowed -- a missing summary is an annoyance, a failed deploy is not.

from __future__ import annotations

import json
import os
from datetime import datetime

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = '''
    name: sentinelcam_summary
    type: aggregate
    short_description: records per-playbook deployment outcomes as JSON lines
    description:
      - Appends one JSON record per playbook run, capturing changed counts, failures,
        and which services were restarted by handlers.
      - Runs alongside the configured stdout callback; it prints nothing itself.
    requirements:
      - enable in configuration: callbacks_enabled = sentinelcam_summary
    options:
      summary_path:
        description: File to append JSON records to.
        env:
          - name: SENTINELCAM_DEPLOY_SUMMARY
        default: logs/deployment_summary.jsonl
'''


class CallbackModule(CallbackBase):

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'sentinelcam_summary'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._path = os.environ.get('SENTINELCAM_DEPLOY_SUMMARY',
                                    os.path.join('logs', 'deployment_summary.jsonl'))
        self._playbook = None
        self._task_name = ''
        self._in_handler = False
        self._hosts = {}

    # -- helpers ---------------------------------------------------------------
    def _host(self, name):
        return self._hosts.setdefault(name, {
            'changed': 0, 'ok': 0, 'failures': 0, 'unreachable': 0,
            'restarted': [], 'errors': [],
        })

    @staticmethod
    def _service_from_handler(task_name):
        """'sentinelcam_base : restart camwatcher' -> 'camwatcher'."""
        name = task_name.split(':')[-1].strip()
        return name[8:].strip() if name.lower().startswith('restart ') else name

    @staticmethod
    def _reason(result):
        for key in ('msg', 'stderr', 'reason', 'module_stderr'):
            value = result.get(key)
            if value:
                return ' '.join(str(value).split())[:300]
        return 'no message reported'

    # -- hooks -----------------------------------------------------------------
    def v2_playbook_on_start(self, playbook):
        try:
            self._playbook = os.path.basename(playbook._file_name)
        except Exception:
            self._playbook = 'unknown'

    def v2_playbook_on_task_start(self, task, is_conditional):
        try:
            self._task_name, self._in_handler = task.get_name(), False
        except Exception:
            pass

    def v2_playbook_on_handler_task_start(self, task):
        try:
            self._task_name, self._in_handler = task.get_name(), True
        except Exception:
            pass

    def v2_runner_on_ok(self, result):
        try:
            host = self._host(result._host.get_name())
            if result._result.get('changed', False):
                host['changed'] += 1
                if self._in_handler:
                    # The handler is the actual production impact -- a restarted
                    # service is the one thing worth spotting in 200 lines of output.
                    service = self._service_from_handler(self._task_name)
                    if service not in host['restarted']:
                        host['restarted'].append(service)
            else:
                host['ok'] += 1
        except Exception:
            pass

    def v2_runner_on_failed(self, result, ignore_errors=False):
        try:
            if ignore_errors:
                return
            host = self._host(result._host.get_name())
            host['failures'] += 1
            host['errors'].append({'task': self._task_name,
                                   'reason': self._reason(result._result)})
        except Exception:
            pass

    def v2_runner_on_unreachable(self, result):
        try:
            host = self._host(result._host.get_name())
            host['unreachable'] += 1
            host['errors'].append({'task': self._task_name or 'connect',
                                   'reason': self._reason(result._result)})
        except Exception:
            pass

    def v2_playbook_on_stats(self, stats):
        try:
            playbook = self._playbook or 'unknown'
            component = playbook
            if component.startswith('deploy-'):
                component = component[len('deploy-'):]
            component = component.rsplit('.', 1)[0]

            for name in stats.processed:      # hosts ansible touched but we did not
                self._host(name)

            record = {
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'playbook': playbook,
                'component': component,
                'hosts': self._hosts,
            }
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self._path, 'a') as out:
                out.write(json.dumps(record) + '\n')
        except Exception:
            pass
